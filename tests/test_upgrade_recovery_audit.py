"""Read-only broker inventory contracts: no private identifiers in summaries."""
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('upgrade_recovery_audit', SCRIPTS / 'audit_upgrade_recovery.py')
    audit = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(audit)
finally:
    sys.path.remove(str(SCRIPTS))


class UpgradeRecoveryAuditTests(unittest.TestCase):
    def test_summary_aggregates_all_vhosts_without_disclosing_private_names(self):
        summary = audit.broker_summary(
            [{'name': 'private-flag', 'state': 'enabled'}, {'name': 'other', 'state': 'disabled'}],
            [{'name': 'private-vhost'}],
            [{'name': 'private-queue', 'type': 'quorum', 'messages': 3, 'messages_ready': 2,
              'messages_unacknowledged': 1, 'consumers': 2}])
        self.assertEqual(summary['messages'], 3)
        self.assertEqual(summary['feature_flag_state_counts'], {'enabled': 1, 'disabled': 1})
        self.assertEqual(summary['queue_types'], {'quorum': 1})
        self.assertFalse(summary['flag_change_performed'])
        self.assertNotIn('private-', str(summary))

    def test_missing_counts_do_not_become_zero_or_ready(self):
        with self.assertRaises(RuntimeError):
            audit.broker_summary([], [], [{'name': 'q', 'type': 'classic'}])
        with self.assertRaises(RuntimeError):
            audit.broker_summary({}, [], [])
