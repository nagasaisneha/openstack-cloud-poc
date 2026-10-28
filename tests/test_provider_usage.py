"""Event state, source boundaries, counter resets and safe DB projection."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import unittest

from openstack_cloud_poc.provider_usage import active_intervals, compile_usage, counter_delta

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


def snapshot():
    instance = {'uuid': 'vm-a', 'project_id': 'a', 'created_at': START, 'launched_at': START,
                'deleted_at': None, 'deleted': 0, 'vm_state': 'active', 'vcpus': 2,
                'memory_mb': 256, 'root_gb': 1, 'ephemeral_gb': 0}
    actions, events = [], []
    for identifier, action, at in ((1, 'create', 0), (2, 'stop', 10), (3, 'start', 20)):
        actions.append({'id': identifier, 'instance_uuid': instance['uuid'], 'action': action,
                        'request_id': 'request-' + str(identifier)})
        events.append({'action_id': identifier, 'event': 'compute_' + action + '_instance',
                       'result': 'Success', 'finish_time': START + timedelta(seconds=at)})
    return {'project_id': 'a', 'observed_at': START + timedelta(seconds=60),
            'instances': [instance], 'actions': actions, 'events': events,
            'volumes': [], 'snapshots': []}


class ProviderUsageTests(unittest.TestCase):
    def test_stopped_interval_is_excluded_using_success_completion_times(self):
        source = snapshot()
        batch = compile_usage('a', START, END, source)
        metrics = {item.metric: item.quantity for item in batch.samples}
        self.assertEqual(metrics['compute.active_vcpu_hours'], Decimal(100) / Decimal(3600))
        self.assertEqual(metrics['compute.active_memory_mib_hours'], Decimal(50 * 256) / Decimal(3600))
        self.assertFalse(batch.complete)
        self.assertIn('period_not_closed', batch.incomplete_reasons)

    def test_duplicate_or_out_of_order_actions_do_not_double_charge(self):
        source = snapshot()
        expected = active_intervals(source['instances'][0], source['actions'], source['events'], source['observed_at'])
        source['actions'] = list(reversed(source['actions'])) + [source['actions'][0]]
        self.assertEqual(active_intervals(source['instances'][0], source['actions'], source['events'], source['observed_at']), expected)

    def test_failed_unknown_and_missing_history_never_become_complete_zero_usage(self):
        for mutate in ('missing', 'failed', 'resize'):
            source = snapshot()
            if mutate == 'missing':
                source['events'] = []
            elif mutate == 'failed':
                source['events'][1]['result'] = 'Error'
            else:
                source['actions'].append({'id': 4, 'instance_uuid': 'vm-a', 'action': 'resize', 'request_id': 'request-4'})
            batch = compile_usage('a', START, END, source)
            self.assertNotIn('compute.active_vcpu_hours', {item.metric for item in batch.samples})
            self.assertTrue(batch.incomplete_reasons)

    def test_snapshot_and_minimum_volume_lifetimes_are_clipped_and_decimal(self):
        source = snapshot()
        source['volumes'] = [{'id': 'volume', 'project_id': 'a', 'size': 1,
                              'created_at': START - timedelta(seconds=30), 'deleted_at': START + timedelta(seconds=30)}]
        source['snapshots'] = [{'id': 'snapshot', 'project_id': 'a', 'volume_size': 2,
                                'created_at': START, 'deleted_at': START + timedelta(seconds=15)}]
        metrics = {item.metric: item.quantity for item in compile_usage('a', START, END, source).samples}
        self.assertEqual(metrics['storage.volume_gib_hours'], Decimal(30) / Decimal(3600))
        self.assertEqual(metrics['storage.snapshot_gib_hours'], Decimal(30) / Decimal(3600))
        source['volumes'][0]['size'] = 2
        batch = compile_usage('a', START, END, source)
        self.assertIn('volume_size_history_unverified', batch.incomplete_reasons)
        self.assertNotIn('storage.volume_gib_hours', {item.metric for item in batch.samples})

    def test_counter_delta_rejects_reset_foreign_resource_and_clock_reversal(self):
        first = {'project_id': 'a', 'resource_id': 'vm', 'generation': 'boot:1',
                 'observed_at': START, 'cpu_ns': 10, 'network_bytes': 20}
        last = dict(first, observed_at=START + timedelta(seconds=1), cpu_ns=110, network_bytes=220)
        self.assertEqual(counter_delta(first, last), {'cpu_ns': 100, 'network_bytes': 200})
        for change in ({'cpu_ns': 0}, {'project_id': 'b'}, {'generation': 'boot:2'},
                       {'resource_id': 'foreign'}, {'observed_at': START}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                counter_delta(first, dict(last, **change))

    def test_foreign_provider_rows_are_rejected(self):
        for kind in ('instances', 'volumes', 'snapshots'):
            source = snapshot()
            source[kind] = [{'project_id': 'foreign'}]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                compile_usage('a', START, END, source)

    def test_database_views_exclude_credentials_and_filter_every_source(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
        sys.path.insert(0, str(scripts))
        try:
            import install_provider_meter as install
        finally:
            sys.path.remove(str(scripts))
        views = install.view_sql(['a' * 32, 'b' * 32])
        for query in views.values():
            self.assertIn('project_id IN (', query)
            for forbidden in ('user_data', 'key_data', 'provider_auth', 'traceback', 'encryption_key_id', 'SELECT *'):
                self.assertNotIn(forbidden, query)
        with self.assertRaises(install.safety.GuardError):
            install.view_sql(["a' OR 1=1"])


if __name__ == '__main__':
    unittest.main()
