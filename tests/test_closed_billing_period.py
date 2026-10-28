from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from openstack_cloud_poc.closed_billing_period import compile_closed_period

START = datetime(2026, 10, 28, tzinfo=timezone.utc)
at = lambda second: (START + timedelta(seconds=second)).isoformat()


def snapshot(second):
    return {'project_id': 'a', 'observed_at': at(second), 'source_snapshot_at': at(second), 'source_clock_checked': True,
        'instances': [{'uuid': 'vm', 'project_id': 'a', 'launched_at': at(-1), 'vm_state': 'active',
            'deleted': 0, 'vcpus': 1, 'memory_mb': 256, 'root_gb': 1, 'ephemeral_gb': 0}],
        'actions': [{'id': 1, 'instance_uuid': 'vm', 'request_id': 'create', 'action': 'create'}],
        'events': [{'action_id': 1, 'result': 'Success', 'event': 'compute_create', 'finish_time': at(-1)}],
        'volumes': [], 'snapshots': [], 'counters': [{'resource_id': 'vm', 'project_id': 'a', 'generation': 'one',
            'observed_at': at(second), 'cpu_ns': second * 100000000, 'network_bytes': second * 10}]}


class ClosedPeriodTests(unittest.TestCase):
    def compile(self, snapshots=None, events=(), requests=(), initial=None, terminal=None, **kwargs):
        return compile_closed_period('a', snapshots or [snapshot(0), snapshot(10), snapshot(20)],
            initial or {}, terminal or {}, list(events), list(requests), **kwargs)

    def test_real_source_boundaries_and_object_overwrite_deletion_integrate_once(self):
        events = [dict(sequence=1, event_id='one', project_id='a', object_key='o', method='PUT', size_bytes=8, at=at(5), known=1),
                  dict(sequence=2, event_id='two', project_id='a', object_key='o', method='DELETE', size_bytes=0, at=at(15), known=1)]
        batch, proof = self.compile(initial={'o': 4}, events=events + [deepcopy(events[0])])
        self.assertTrue(batch.complete)
        values = {row.metric: row.quantity for row in batch.samples}
        self.assertEqual(values['storage.object_byte_hours'], Decimal(100) / 3600)
        self.assertEqual(values['compute.cpu_seconds'], 2)
        self.assertEqual(values['network.interface_bytes'], 200)
        self.assertFalse(proof['historical_backfill'])

    def test_missing_reset_and_deletion_never_become_complete(self):
        reset = snapshot(20)
        reset['counters'][0]['generation'] = 'two'
        batch, _ = self.compile([snapshot(0), reset])
        self.assertIn('counter_reset_generation_or_conflict', batch.incomplete_reasons)
        deleted = snapshot(20)
        deleted['counters'] = []
        batch, _ = self.compile([snapshot(0), deleted])
        self.assertIn('resource_counter_start_end_or_deletion_gap', batch.incomplete_reasons)
        batch, _ = self.compile([snapshot(0), snapshot(100)])
        self.assertIn('period_source_sampling_gap', batch.incomplete_reasons)

    def test_journal_pending_poison_and_inventory_disagreement_block_completeness(self):
        batch, _ = self.compile(requests=[{'project_id': 'a', 'at': at(5), 'status': None, 'known': None}],
                                terminal={'missing': 10}, poisoned=True)
        self.assertFalse(batch.complete)
        self.assertIn('swift_pending_or_unknown_request', batch.incomplete_reasons)
        self.assertIn('swift_journal_persistence_failure', batch.incomplete_reasons)
        self.assertIn('swift_terminal_inventory_mismatch', batch.incomplete_reasons)

    def test_foreign_evidence_and_unbounded_clock_offsets_are_rejected(self):
        row = snapshot(20)
        row['project_id'] = 'b'
        with self.assertRaisesRegex(ValueError, 'foreign_period'):
            self.compile([snapshot(0), row])
        row = snapshot(20)
        row['counters'][0]['observed_at'] = at(15)
        batch, _ = self.compile([snapshot(0), row])
        self.assertIn('counter_acquisition_offset_exceeded', batch.incomplete_reasons)
