"""API/hook transformations and recovery gates without host/cloud mutation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_api_hardening', SCRIPTS / 'harden_local_apis.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApiHardeningTests(unittest.TestCase):
    def apache(self):
        return ('Listen 5000\n\n<VirtualHost *:5000>\n'
                ' WSGIDaemonProcess keystone-public processes=5 threads=1 user=keystone group=keystone\n'
                ' WSGIScriptAlias / /usr/bin/keystone-wsgi-public\n'
                '</VirtualHost>\nAlias /identity /usr/bin/keystone-wsgi-public\n'
                '<Location /identity>\n SetHandler wsgi-script\n</Location>\n')

    def test_apache_changes_only_named_binding_workers_and_alias_authorization(self):
        original = self.apache() + '# Retained unrelated custom directive\n'
        changed = setup.apache_config(original, 5000, 'keystone-public', '/identity')
        self.assertIn('Listen 127.0.0.1:5000\n', changed)
        self.assertIn('<VirtualHost 127.0.0.1:5000>', changed)
        self.assertIn('processes=1 threads=1 user=keystone group=keystone', changed)
        self.assertIn('Require ip 127.0.0.1 ::1', changed)
        self.assertIn('Alias /identity /usr/bin/keystone-wsgi-public', changed)
        self.assertIn('# Retained unrelated custom directive', changed)
        self.assertEqual(setup.apache_config(changed, 5000, 'keystone-public', '/identity'), changed)

    def test_ambiguous_or_conflicting_apache_scope_requires_review(self):
        for original in (self.apache() + 'Listen 5000\n', self.apache().replace('processes=5', ''),
                         self.apache().replace('<Location /identity>', '<Location /identity>\nRequire all granted')):
            with self.assertRaises(RuntimeError):
                setup.apache_config(original, 5000, 'keystone-public', '/identity')

    def test_glance_edit_preserves_database_authentication_and_unrelated_settings(self):
        text = '[DEFAULT]\nbind_host=0.0.0.0\nworkers=8\n[database]\nconnection=mysql+pymysql://user:private@127.0.0.1/glance\n[custom]\nvalue=keep\n'
        changed = setup.desired(setup.TARGETS[2], text)
        self.assertIn('bind_host = 127.0.0.1', changed)
        self.assertIn('workers = 1', changed)
        self.assertIn('connection=mysql+pymysql://user:private@127.0.0.1/glance', changed)
        self.assertIn('value=keep', changed)

    def test_hook_edit_is_bounded_idempotent_and_keeps_rotation_options(self):
        original = '/var/log/openvswitch/*.log {\n daily\n rotate 30\n postrotate\n ovs-appctl -t name vlog/reopen || :\n endscript\n}\n'
        changed = setup.desired(setup.TARGETS[3], original)
        self.assertIn('rotate 30', changed)
        self.assertIn('ovs-appctl --timeout=5 -t name vlog/reopen || :', changed)
        self.assertEqual(setup.desired(setup.TARGETS[3], changed), changed)
        self.assertIn('--timeout=5', setup.postrotate(changed))
        with self.assertRaises(RuntimeError):
            setup.postrotate(original)
        with self.assertRaises(RuntimeError):
            setup.postrotate(changed + changed)

    def test_unreviewed_timeout_override_and_unknown_paths_are_not_overwritten(self):
        self.assertEqual(setup.desired(setup.DROPIN, ''), setup.TIMEOUT_CONFIG)
        for path, text in ((setup.DROPIN, '[Service]\nTimeoutStartSec=8h\n'), (Path('/etc/unrelated'), '')):
            with self.assertRaises(RuntimeError):
                setup.desired(path, text)

    def test_live_file_drift_or_symlink_stops_before_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target, saved = base / 'config', base / 'before'
            saved.write_text('original')
            target.write_text('operator-change')
            item = dict(existed=True, before='before', old_sha256=setup.digest(b'original'),
                        new_sha256=setup.digest(b'desired'), uid=0, gid=0, mode=0o644)
            with patch.object(setup, 'protected_regular'), patch.object(setup, 'desired', return_value='desired'), \
                 patch.object(setup.os, 'replace') as replace, self.assertRaises(RuntimeError):
                setup.write_target(target, item, base)
            replace.assert_not_called()
            self.assertEqual(target.read_text(), 'operator-change')
            link = base / 'link'
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                setup.protected_regular(link)

    def test_missing_active_control_roles_prevent_any_reopen_or_hook_execution(self):
        with patch.object(Path, 'glob', return_value=[]), patch.object(setup.common, 'run') as run, \
             patch.object(setup.subprocess, 'run') as shell, self.assertRaises(RuntimeError):
            setup.verify_hooks()
        run.assert_not_called()
        shell.assert_not_called()

    def test_missing_backup_artifacts_prevent_accepting_recovery(self):
        record = {'backup_directory': '/protected/not-a-recovery-dir', 'files': {}, 'sha256': {}}
        with patch.object(Path, 'lstat', return_value=NS(st_mode=0o40700, st_uid=0)), self.assertRaises(RuntimeError):
            setup.verify_backup(record)

    def test_backup_gate_prevents_every_configuration_write_and_service_action(self):
        with patch.object(setup, 'verify_backup', side_effect=RuntimeError('damaged backup')), \
             patch.object(setup, 'write_target') as write, patch.object(setup.common, 'run') as run, \
             self.assertRaises(RuntimeError):
            setup.apply_configuration({})
        write.assert_not_called()
        run.assert_not_called()

    def test_apply_restarts_only_apache_and_glance_after_syntax_validation(self):
        record = {'backup_directory': '/protected/backup', 'files': {str(path): {} for path in setup.TARGETS}}
        with patch.object(setup, 'verify_backup'), patch.object(setup, 'write_target'), \
             patch.object(setup.launch, 'atomic_state'), patch.object(setup, 'progress'), \
             patch.object(setup.common, 'run') as run:
            setup.apply_configuration(record)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['apache2ctl', 'configtest'], ['systemctl', 'daemon-reload'],
            ['systemctl', 'restart', 'apache2.service'], ['systemctl', 'restart', 'glance-api.service']])
        self.assertEqual(record['phase'], 'services_restarted')
        with patch.object(setup, 'verify_backup'), patch.object(setup, 'write_target'), \
             patch.object(setup.launch, 'atomic_state'), patch.object(setup, 'progress'), \
             patch.object(setup.common, 'run', side_effect=RuntimeError('syntax invalid')) as run:
            with self.assertRaises(RuntimeError):
                setup.apply_configuration(record)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(record['phase'], 'files_written')

    def test_rabbit_preflight_reports_only_selected_metadata_without_configuration_contents(self):
        outputs = ['epmd: up and running on port 4369 with data:\nname rabbit at port 25672\n',
                   '{"running_nodes":["rabbit@host"],"nodes":{"disc":["rabbit@host"],"ram":[]}}',
                   '[{"user":"openstack","peer_host":"127.0.0.1"}]',
                   '{"config_files":[],"unrelated":"not-for-report"}']
        with patch.object(setup.common, 'run', side_effect=outputs), \
             patch.object(setup.socket, 'gethostbyname_ex', return_value=('host', [], ['172.19.169.140'])), \
             patch.object(setup.socket, 'gethostname', return_value='host'):
            report = setup.rabbit_preflight()
        self.assertTrue(report['only_poc_connection_users'])
        self.assertTrue(report['all_connections_loopback'])
        self.assertNotIn('not-for-report', repr(report))
        self.assertEqual(report['hostname_ipv4'], ['172.19.169.140'])


if __name__ == '__main__':
    unittest.main()
