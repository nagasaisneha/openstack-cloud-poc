"""Read-only Cinder preflight tests; no cloud calls or storage mutations."""
import importlib.util
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location(
        'task18_cinder_readiness', SCRIPTS / 'inspect_cinder_readiness.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class CinderReadinessTests(unittest.TestCase):
    def ready_report(self):
        return {
            'cloud': {
                'endpoint_present': True,
                'service_api_status': 'responding',
                'pool_api_status': 'responding',
                'services': [
                    {'binary': 'cinder-scheduler', 'state': 'up', 'status': 'enabled'},
                    {'binary': 'cinder-volume', 'state': 'up', 'status': 'enabled'}],
                'pools': [{'free_capacity_gb': 8.0}]},
            'backend_configuration': {'backends': [{'driver': 'cinder.volume.drivers.lvm.LVMVolumeDriver'}]}}

    def test_readiness_requires_endpoint_scheduler_volume_driver_and_real_free_capacity(self):
        report = self.ready_report()
        self.assertEqual(setup.readiness(report)['status'], 'ready_for_scoped_volume_test')
        for section, key, value in (
                ('cloud', 'endpoint_present', False),
                ('cloud', 'service_api_status', 'unavailable'),
                ('cloud', 'pools', []),
                ('backend_configuration', 'backends', [])):
            changed = {name: dict(data) for name, data in report.items()}
            changed[section][key] = value
            self.assertEqual(setup.readiness(changed)['status'], 'blocked')

    def test_cloud_inventory_sanitizes_endpoint_and_hosts_and_reports_capacity(self):
        services = [
            NS(binary='cinder-scheduler', state='up', status='enabled', availability_zone='nova', host='SECRET_HOST'),
            NS(binary='cinder-volume', state='up', status='enabled', availability_zone='nova', host='SECRET_HOST'),
            NS(binary='other-service', state='up', status='enabled', availability_zone='nova', host='SECRET_HOST')]
        response = NS(json=Mock(return_value={'pools': [{
            'name': 'SECRET_HOST@backend#pool',
            'capabilities': {'volume_backend_name': 'poc-lvm', 'vendor_name': 'OpenStack',
                             'storage_protocol': 'iSCSI', 'free_capacity_gb': 12.5,
                             'total_capacity_gb': 20.0, 'secret_value': 'SECRET_SENTINEL'}}]}))
        limits = NS(absolute={'maxTotalVolumes': 10, 'totalVolumesUsed': 1,
                              'maxTotalVolumeGigabytes': 100, 'totalGigabytesUsed': 2})
        cloud = NS(session=NS(get_endpoint=Mock(return_value='http://PRIVATE_ENDPOINT')),
                   block_storage=NS(services=Mock(return_value=services), get=Mock(return_value=response),
                                    get_limits=Mock(return_value=limits)))
        result = setup.cloud_inventory(cloud)
        encoded = repr(result)
        self.assertTrue(result['endpoint_present'])
        self.assertEqual(result['services'][0]['binary'], 'cinder-scheduler')
        self.assertEqual(result['pools'][0]['free_capacity_gb'], 12.5)
        self.assertEqual(result['quota']['maxTotalVolumes'], 10)
        for secret in ('PRIVATE_ENDPOINT', 'SECRET_HOST', 'SECRET_SENTINEL'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(cloud.block_storage.get.call_count, 1)

    def test_missing_endpoint_stops_before_block_storage_api_calls(self):
        cloud = NS(session=NS(get_endpoint=Mock(return_value=None)),
                   block_storage=NS(services=Mock(), get=Mock(), get_limits=Mock()))
        result = setup.cloud_inventory(cloud)
        self.assertFalse(result['endpoint_present'])
        cloud.block_storage.services.assert_not_called()
        cloud.block_storage.get.assert_not_called()
        cloud.block_storage.get_limits.assert_not_called()

    def test_cinder_config_reads_only_nonsecret_backend_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cinder.conf'
            path.write_text('[DEFAULT]\nenabled_backends = test-lvm\nconnection = PRIVATE_DB_URL\n'
                            '[test-lvm]\nvolume_driver = cinder.volume.drivers.lvm.LVMVolumeDriver\n'
                            'volume_group = cinder-volumes\n')
            original_lstat = Path.lstat
            def root_metadata(candidate):
                info = original_lstat(candidate)
                if candidate == path:
                    return NS(st_mode=stat.S_IFREG | 0o600, st_uid=0)
                return info
            with patch.object(Path, 'lstat', root_metadata):
                result = setup.backend_configuration(path)
        self.assertTrue(result['parseable'])
        self.assertEqual(result['enabled_backend_count'], 1)
        self.assertEqual(result['backends'][0]['driver'], 'cinder.volume.drivers.lvm.LVMVolumeDriver')
        self.assertTrue(result['backends'][0]['volume_group_configured'])
        self.assertNotIn('PRIVATE_DB_URL', repr(result))

    def test_api_failures_expose_only_type_and_http_status(self):
        error = RuntimeError('PRIVATE_ENDPOINT and TOKEN_SENTINEL')
        error.status_code = 403
        cloud = NS(session=NS(get_endpoint=Mock(side_effect=error)), block_storage=NS())
        result = setup.cloud_inventory(cloud)
        self.assertEqual(result['endpoint_status'], {
            'status': 'unavailable', 'error_type': 'RuntimeError', 'http_status': 403})
        self.assertNotIn('PRIVATE_', repr(result))
        self.assertNotIn('TOKEN_SENTINEL', repr(result))


if __name__ == '__main__':
    unittest.main()
