"""Persistent measured history: idempotence, reset handling and project scope."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('provider_agent_test', Path(__file__).resolve().parents[1]
    / 'scripts/local-openstack/provider_meter_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def point(second, cpu, network, generation='one'):
    now = START + timedelta(seconds=second)
    return {'project_id': 'a', 'observed_at': now.isoformat(), 'source_snapshot_at': now.isoformat(),
            'instances': [], 'actions': [], 'events': [], 'volumes': [], 'snapshots': [],
            'counters': [{'project_id': 'a', 'resource_id': 'vm-a', 'generation': generation,
                          'observed_at': now.isoformat(), 'cpu_ns': cpu, 'network_bytes': network}]}


class MeterStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        Path(self.directory.name).chmod(0o700)
        self.store = agent.Store(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_inputs_are_immutable_and_never_sum_cumulative_counters(self):
        first = point(1, 1000000000, 100)
        self.assertEqual(self.store.append(first), self.store.append(first))
        self.store.append(point(10, 3000000000, 350))
        self.store.append(point(20, 4000000000, 500))
        result = self.store.report('a', START, START + timedelta(seconds=20))
        metrics = {sample['metric']: Decimal(sample['quantity']) for sample in result['batch']['samples']}
        self.assertEqual(metrics['compute.cpu_seconds'], Decimal(3))
        self.assertEqual(metrics['network.interface_bytes'], Decimal(400))
        self.assertEqual(result['counter_observation_count'], 3)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 3)
            for sql in ('UPDATE snapshots SET project_id="b"', 'DELETE FROM snapshots'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_counter_restart_creates_gap_without_negative_or_invented_usage(self):
        for value in (point(1, 9000000000, 900), point(2, 1000000000, 100, 'two'),
                      point(3, 2000000000, 150, 'two')):
            self.store.append(value)
        result = self.store.report('a', START, START + timedelta(seconds=3))
        metrics = {sample['metric']: Decimal(sample['quantity']) for sample in result['batch']['samples']}
        self.assertEqual(metrics['compute.cpu_seconds'], Decimal(1))
        self.assertEqual(metrics['network.interface_bytes'], Decimal(50))
        self.assertIn('counter_generation_or_sequence_gap', result['batch']['incomplete_reasons'])

    def test_foreign_project_request_cannot_read_another_project_snapshot(self):
        self.store.append(point(1, 1, 1))
        with self.assertRaisesRegex(ValueError, 'no_meter_collection'):
            self.store.report('b', START, START + timedelta(seconds=3))

    def test_journal_evidence_shares_a_bounded_byte_budget(self):
        rows, remaining = agent.bounded_journal_rows(iter([{'known': 1}]), 30)
        self.assertEqual(rows, [{'known': 1}])
        self.assertLess(remaining, 30)
        with self.assertRaisesRegex(ValueError, 'period_journal_evidence_limit'):
            agent.bounded_journal_rows(iter([{'large': 'x' * 30}]), remaining)
        with self.assertRaisesRegex(ValueError, 'period_journal_evidence_limit'):
            agent.bounded_journal_rows(({} for _ in range(100001)), 1000000)

    def test_out_of_order_ingestion_keeps_source_order_and_original_values(self):
        for value in (point(30, 4000000000, 40), point(10, 1000000000, 10), point(20, 2000000000, 20)):
            self.store.append(value)
        result = self.store.report('a', START, START + timedelta(seconds=30))
        metrics = {sample['metric']: Decimal(sample['quantity']) for sample in result['batch']['samples']}
        self.assertEqual(metrics['compute.cpu_seconds'], Decimal(3))
        self.assertEqual(metrics['network.interface_bytes'], Decimal(30))


if __name__ == '__main__':
    unittest.main()
