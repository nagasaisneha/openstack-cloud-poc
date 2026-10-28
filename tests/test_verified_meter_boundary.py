"""Trust only the enrolled local source, matching scope/period and fresh data."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from openstack_cloud_poc.billing import BillingError, UsageBatch
from openstack_cloud_poc.usage import UsageProvenance
from openstack_cloud_poc.usage_history import batch_wire
from openstack_cloud_poc import poc_metering as meter
from openstack_cloud_poc.provider_usage import SOURCE

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)
NOW = START + timedelta(days=1)
CONFIG = {'verified_meter_socket': '/run/openstack-poc-meter/read.sock'}


def report(**overrides):
    data = dict(project_id='a', interval_start=START, interval_end=END, source=SOURCE,
                provenance=UsageProvenance.OBSERVED, complete=False, samples=(), collected_at=NOW,
                incomplete_reasons=('period_not_closed',))
    data.update(overrides)
    if data['complete']:
        data['incomplete_reasons'] = ()
    return {'batch': batch_wire(UsageBatch(**data)), 'measurement_policy': {}, 'source_clock_checked': True}


class VerifiedMeterBoundaryTests(unittest.TestCase):
    def test_matching_source_is_still_provisional(self):
        with patch.object(meter, 'read_meter', return_value=report()):
            batch, policy = meter.verified_batch(CONFIG, 'a', START, END, lambda: NOW)
        self.assertFalse(batch.complete)
        self.assertEqual(batch.source, SOURCE)

    def test_foreign_stale_future_wrong_source_or_premature_complete_is_rejected(self):
        for change in ({'project_id': 'b'}, {'collected_at': NOW - timedelta(seconds=91)},
                       {'collected_at': NOW + timedelta(seconds=3)}, {'source': 'untrusted'},
                       {'complete': True}, {'interval_start': START - timedelta(days=1)}):
            with self.subTest(change=change), patch.object(meter, 'read_meter', return_value=report(**change)):
                with self.assertRaises(BillingError):
                    meter.verified_batch(CONFIG, 'a', START, END, lambda: NOW)

    def test_caller_cannot_choose_a_different_socket(self):
        with patch.object(meter, 'read_meter') as read:
            with self.assertRaises(BillingError):
                meter.verified_batch({'verified_meter_socket': '/tmp/attacker.sock'}, 'a', START, END, lambda: NOW)
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
