from datetime import datetime, timezone
from decimal import Decimal
import unittest

from openstack_cloud_poc.nova_usage import SOURCE, samples_from_nova_usage
from openstack_cloud_poc.usage import UsageProvenance


START = datetime(2026, 10, 25, tzinfo=timezone.utc)
END = datetime(2026, 10, 26, tzinfo=timezone.utc)


def payload():
    return {'tenant_usage': {
        'tenant_id': 'project-a',
        'server_usages': [{
            'instance_id': 'server-1', 'hours': 2.5,
            'vcpus': 2, 'memory_mb': 512, 'local_gb': 1,
        }],
    }}


class NovaUsageTests(unittest.TestCase):
    def test_maps_allocation_hours_with_measured_provenance_and_units(self):
        samples = samples_from_nova_usage(payload(), 'project-a', START, END)
        self.assertEqual([s.metric for s in samples], [
            'compute.vcpu_hours', 'compute.memory_mb_hours', 'compute.local_disk_gb_hours'])
        self.assertEqual([s.quantity for s in samples], [
            Decimal('5.0'), Decimal('1280.0'), Decimal('2.5')])
        self.assertEqual({s.source for s in samples}, {SOURCE})
        self.assertTrue(all(s.provenance is UsageProvenance.OBSERVED for s in samples))
        self.assertTrue(all(s.project_id == 'project-a' and s.resource_id == 'server-1' for s in samples))

    def test_empty_server_usage_is_empty_not_zero_samples(self):
        document = {'tenant_usage': {'tenant_id': 'project-a', 'server_usages': []}}
        self.assertEqual(samples_from_nova_usage(document, 'project-a', START, END), ())

    def test_project_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'project does not match'):
            samples_from_nova_usage(payload(), 'project-b', START, END)

    def test_missing_server_allocation_measurement_is_rejected(self):
        document = payload()
        document['tenant_usage']['server_usages'][0].pop('memory_mb')
        with self.assertRaisesRegex(ValueError, 'memory_mb'):
            samples_from_nova_usage(document, 'project-a', START, END)

    def test_negative_and_non_finite_values_are_rejected(self):
        for hours in (-1, 'NaN', 'Infinity'):
            with self.subTest(hours=hours):
                document = payload()
                document['tenant_usage']['server_usages'][0]['hours'] = hours
                with self.assertRaises(ValueError):
                    samples_from_nova_usage(document, 'project-a', START, END)

    def test_empty_project_id_is_rejected_by_sample_validation(self):
        with self.assertRaises(ValueError):
            samples_from_nova_usage(payload(), '', START, END)


if __name__ == '__main__':
    unittest.main()
