import importlib.util
from contextlib import nullcontext
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_rabbitmq_hardening', SCRIPTS / 'harden_rabbitmq.py')
    broker = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(broker)
finally:
    sys.path.remove(str(SCRIPTS))


def ss_row(address, port):
    return f'LISTEN 0 128 {address}:{port} 0.0.0.0:*'


class RabbitMQHardeningTests(unittest.TestCase):
    def test_loopback_configuration_is_minimal_and_preserves_node_name(self):
        self.assertEqual(broker.RABBIT_CONFIG_TEXT, (
            'listeners.tcp.default = 127.0.0.1:5672\n'
            'distribution.listener.interface = 127.0.1.1\n'
            'distribution.listener.port_range.min = 25672\n'
            'distribution.listener.port_range.max = 25672\n'))
        self.assertIn('ListenStream=127.0.0.1:4369', broker.SOCKET_CONFIG_TEXT)
        self.assertIn('ListenStream=127.0.1.1:4369', broker.SOCKET_CONFIG_TEXT)
        self.assertNotIn('ListenStream=[::]', broker.SOCKET_CONFIG_TEXT)

    def test_preflight_rejects_unexpected_client_before_inspecting_units_or_writing(self):
        data = {'hostname': broker.HOST, 'hostname_ipv4': [broker.HOST_IP],
                'running_nodes': [broker.NODE], 'disk_nodes': [broker.NODE], 'ram_nodes': [],
                'epmd_nodes': [{'name': 'rabbit', 'port': 25672}], 'connection_count': 1,
                'all_connections_loopback': False, 'only_poc_connection_users': True, 'config_files': []}
        with patch.object(broker.api, 'rabbit_preflight', return_value=data), \
             patch.object(broker, 'systemd_properties') as units:
            with self.assertRaises(RuntimeError):
                broker.preflight()
        units.assert_not_called()

    def test_listener_inventory_requires_each_expected_port_and_rejects_wildcard(self):
        listeners = '\n'.join((ss_row('*', 4369), ss_row('*', 5672), ss_row('0.0.0.0', 25672)))
        with patch.object(broker.common, 'run', return_value=listeners):
            report = broker.listener_inventory()
        self.assertEqual(set(report), {'4369', '5672', '25672'})
        self.assertTrue(all(not entries[0]['loopback'] for entries in report.values()))
        with patch.object(broker.common, 'run', return_value='\n'.join((
                ss_row('127.0.0.1', 4369), ss_row('*', 5672), ss_row('0.0.0.0', 25672)))):
            with self.assertRaises(RuntimeError):
                broker.listener_inventory()

    def test_queue_inventory_accepts_only_empty_backlog_and_valid_schema(self):
        rows = '[{"name":"rpc","messages":0,"consumers":2,"durable":false}]'
        with patch.object(broker.common, 'run', return_value=rows):
            summary = broker.queue_inventory()
        self.assertEqual(summary, {'queue_count': 1, 'pending_messages': 0, 'durable_queue_count': 0})
        with patch.object(broker.common, 'run', return_value='[{"name":"rpc","messages":1,"consumers":2,"durable":false}]'):
            with self.assertRaisesRegex(RuntimeError, 'queued messages'):
                broker.queue_inventory()
        with patch.object(broker.common, 'run', return_value='[{"name":"rpc","messages":"0","consumers":2,"durable":false}]'):
            with self.assertRaisesRegex(RuntimeError, 'fields require review'):
                broker.queue_inventory()

    def test_definitions_export_uses_private_staging_then_root_bundle_copy(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            root = Path(temp)
            staging_parent = root / 'staging'
            staging_parent.mkdir(mode=0o700)
            bundle = root / 'bundle'
            bundle.mkdir(mode=0o700)
            target = bundle / 'rabbitmq-definitions.json'
            account = NS(pw_uid=os.getuid(), pw_gid=os.getgid())
            exported = '{"users":[]}'
            source_paths = []

            def fake_export(command, **_kwargs):
                source = Path(command[-1])
                info = source.parent.stat()
                source_paths.append((source, info.st_mode & 0o777, info.st_uid, info.st_gid))
                source.write_text(exported)
                source.chmod(0o664)
                return NS(returncode=0)

            with patch.object(broker, 'STAGING_PARENT', staging_parent), \
                 patch.object(broker.pwd, 'getpwnam', return_value=account), \
                 patch.object(broker.subprocess, 'run', side_effect=fake_export), \
                 patch.object(broker.guest, 'protected_file'), \
                 patch.object(broker, 'progress'):
                size = broker.export_definitions_backup(target)

            self.assertEqual(size, len(exported))
            self.assertEqual(target.read_text(), exported)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source_paths[0][1:], (0o700, account.pw_uid, account.pw_gid))
            self.assertFalse(source_paths[0][0].exists())
            self.assertEqual(list(staging_parent.iterdir()), [])

    def test_definitions_export_failure_keeps_broker_untouched_and_cleans_private_staging(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            root = Path(temp)
            staging_parent = root / 'staging'
            staging_parent.mkdir(mode=0o700)
            account = NS(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(broker, 'STAGING_PARENT', staging_parent), \
                 patch.object(broker.pwd, 'getpwnam', return_value=account), \
                 patch.object(broker.subprocess, 'run', return_value=NS(returncode=65)), \
                 patch.object(broker, 'progress'), \
                 self.assertRaisesRegex(RuntimeError, 'definitions export failed'):
                broker.export_definitions_backup(root / 'bundle' / 'rabbitmq-definitions.json')
            self.assertEqual(list(staging_parent.iterdir()), [])

    def test_verified_backup_gate_precedes_any_live_configuration_write(self):
        with patch.object(broker, 'verify_recovery', side_effect=RuntimeError('backup invalid')), \
             patch.object(broker, 'atomic_create') as create:
            with self.assertRaises(RuntimeError):
                broker.write_configuration({'phase': 'prepared', 'verified': True})
        create.assert_not_called()

    def test_write_configuration_creates_only_reviewed_targets_with_private_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            conf_dir = root / 'rabbitmq'
            conf_dir.mkdir()
            dropin_dir = root / 'epmd.socket.d'
            with patch.object(broker, 'RABBIT_CONFIG', conf_dir / 'rabbitmq.conf'), \
                 patch.object(broker, 'SOCKET_DROPIN_DIR', dropin_dir), \
                 patch.object(broker, 'SOCKET_DROPIN', dropin_dir / '50-openstack-poc-loopback.conf'), \
                 patch.object(broker, 'verify_recovery'), \
                 patch.object(broker.pwd, 'getpwnam', return_value=type('PW', (), {'pw_gid': 128})()), \
                 patch.object(broker.os, 'fchown'), \
                 patch.object(broker.launch, 'atomic_state'):
                record = {'phase': 'prepared', 'verified': True}
                broker.write_configuration(record)
                self.assertEqual(record['phase'], 'files_written')
                self.assertEqual((conf_dir / 'rabbitmq.conf').read_text(), broker.RABBIT_CONFIG_TEXT)
                self.assertEqual((dropin_dir / '50-openstack-poc-loopback.conf').read_text(), broker.SOCKET_CONFIG_TEXT)
                self.assertEqual((conf_dir / 'rabbitmq.conf').stat().st_mode & 0o777, 0o640)
                self.assertEqual((dropin_dir / '50-openstack-poc-loopback.conf').stat().st_mode & 0o777, 0o644)

    def test_verify_listeners_requires_exact_loopback_addresses(self):
        rows = []
        for port, addresses in broker.PORTS.items():
            rows.extend({'port': port, 'address': address, 'loopback': True} for address in addresses)
        with patch.object(broker.common, 'run', return_value='unused'), \
             patch.object(broker.review, 'listeners', return_value=rows):
            self.assertEqual(set(broker.verify_listeners()), {'4369', '5672', '25672'})
        bad = [dict(row) for row in rows]
        bad[0]['loopback'] = False
        with patch.object(broker.common, 'run', return_value='unused'), \
             patch.object(broker.review, 'listeners', return_value=bad):
            with self.assertRaises(RuntimeError):
                broker.verify_listeners()

    def test_post_restart_recovery_uses_flat_read_only_preflight_metadata(self):
        metadata = {'running_nodes': [broker.NODE], 'disk_nodes': [broker.NODE], 'ram_nodes': [],
                    'connection_count': 54, 'all_connections_loopback': True,
                    'only_poc_connection_users': True}
        active = {'ActiveState': 'active', 'SubState': 'running'}
        units = {name: active for name in
                 ('epmd.socket', 'epmd.service', 'rabbitmq-server.service')}
        with patch.object(broker, 'systemd_properties', side_effect=lambda name: units[name]), \
             patch.object(broker, 'verify_listeners', return_value={'5672': []}), \
             patch.object(broker.api, 'rabbit_preflight', return_value=metadata), \
             patch.object(broker.common, 'connection', return_value=nullcontext(object())), \
             patch.object(broker.review, 'cloud_state', return_value={'authenticated': True}), \
             patch.object(broker.review, 'cpu_smoke', return_value={'verified': True}):
            result = broker.wait_runtime({}, {})
        self.assertEqual(result['broker'], metadata)
        self.assertTrue(result['guest_cpu']['verified'])

    def test_verified_rerun_uses_flat_preflight_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / 'rabbitmq-hardening.json'
            record = {'phase': 'verified', 'verified': True, 'sha256': {}, 'plan': {}, 'prechange': {}}
            state_path.write_text(json.dumps(record))
            metadata = {'running_nodes': [broker.NODE], 'connection_count': 54}
            with patch.object(broker, 'STATE', state_path), \
                 patch.object(broker.guest, 'protected_file'), \
                 patch.object(broker, 'verify_recovery'), \
                 patch.object(broker, 'verify_files'), \
                 patch.object(broker, 'verify_listeners', return_value={'5672': []}), \
                 patch.object(broker.api, 'rabbit_preflight', return_value=metadata), \
                 patch.object(broker, 'progress') as progress:
                broker.apply()
            self.assertTrue(progress.called)
            self.assertIn('previously verified loopback state remains intact', progress.call_args.args[0])

    def test_rollback_deletes_only_files_matching_task_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rabbitmq.conf'
            path.write_text('operator edit')
            info = NS(st_mode=stat.S_IFREG | 0o644, st_uid=0)
            with patch.object(Path, 'lstat', return_value=info), \
                 self.assertRaisesRegex(RuntimeError, 'drifted'):
                broker.remove_task_file(path, broker.digest(broker.RABBIT_CONFIG_TEXT.encode()))
            self.assertEqual(path.read_text(), 'operator edit')
            path.write_text(broker.RABBIT_CONFIG_TEXT)
            with patch.object(Path, 'lstat', return_value=info):
                broker.remove_task_file(path, broker.digest(broker.RABBIT_CONFIG_TEXT.encode()))
            self.assertFalse(path.exists())

    def test_recovery_verification_rejects_corrupt_artifact(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            directory = Path(temp) / 'rabbitmq-recovery-test'
            directory.mkdir(mode=0o700)
            # Exercise the integrity gate without manufacturing the protected bundle.
            record = {'backup_directory': str(directory), 'sha256': {}}
            with patch.object(broker, 'DATA_DIR', directory.parent), \
                 self.assertRaises(RuntimeError):
                broker.verify_recovery(record)


if __name__ == '__main__':
    unittest.main()
