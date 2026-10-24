"""API/hook transformations and recovery gates without host/cloud mutation."""
import importlib.util
import contextlib
import io
import json
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
import zipfile
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
                   '{"running_nodes":["rabbit@host"],"disk_nodes":["rabbit@host"],"ram_nodes":[]}',
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

    def metadata(self, **extra):
        return dict(running_nodes=['rabbit@host'], disk_nodes=['rabbit@host'], ram_nodes=[], **extra)

    def test_release_39_flat_fields_are_used_without_exposing_other_fields(self):
        report = setup.rabbit_metadata(self.metadata(unrelated='private-value'),
            [{'user': 'openstack', 'peer_host': '127.0.0.1', 'password': 'private-value'}],
            {'config_files': ['/etc/rabbitmq/rabbitmq.conf'], 'environment': 'private-value'})
        self.assertEqual(report['disk_nodes'], ['rabbit@host'])
        self.assertEqual(report['ram_nodes'], [])
        self.assertNotIn('private-value', repr(report))

    def test_missing_or_nested_cluster_fields_are_not_assumed_empty(self):
        for cluster in ({}, {'running_nodes': [], 'nodes': {'disc': [], 'ram': []}},
                        dict(self.metadata(), disk_nodes=None), dict(self.metadata(), ram_nodes=['malformed'])):
            with self.assertRaises(ValueError):
                setup.rabbit_metadata(cluster, [], {'config_files': []})

    def test_external_or_unexpected_consumers_are_reported_as_flags(self):
        report = setup.rabbit_metadata(self.metadata(),
            [{'user': 'another-app', 'peer_host': '172.19.169.1'}], {'config_files': []})
        self.assertFalse(report['all_connections_loopback'])
        self.assertFalse(report['only_poc_connection_users'])
        self.assertEqual(report['connection_count'], 1)
        local = setup.rabbit_metadata(self.metadata(), [{'user': 'openstack', 'peer_host': '::ffff:127.0.0.1'}], {'config_files': []})
        remote = setup.rabbit_metadata(self.metadata(), [{'user': 'openstack', 'peer_host': '::ffff:172.19.169.1'}], {'config_files': []})
        self.assertTrue(local['all_connections_loopback'])
        self.assertFalse(remote['all_connections_loopback'])

    def test_malformed_connections_or_config_paths_stop_before_output(self):
        for connections, status in (({}, {'config_files': []}), ([{'user': 'openstack'}], {'config_files': []}),
                                    ([], {}), ([], {'config_files': 'contents'}),
                                    ([], {'config_files': ['https://unexpected.invalid']})):
            with self.assertRaises(ValueError):
                setup.rabbit_metadata(self.metadata(), connections, status)

    def test_empty_valid_connections_are_distinct_from_missing_metadata(self):
        report = setup.rabbit_metadata(self.metadata(), [], {'config_files': []})
        self.assertEqual(report['connection_count'], 0)
        self.assertTrue(report['all_connections_loopback'])
        self.assertTrue(report['only_poc_connection_users'])

    def test_metadata_structure_reports_all_selected_types_without_values(self):
        cluster = dict(self.metadata(), ram_nodes='', password='SECRET_SENTINEL')
        connections = [{'user': 'SECRET_SENTINEL', 'peer_host': 'SECRET_SENTINEL'}]
        report = setup.rabbit_shapes(cluster, connections, {'config_files': ['SECRET_SENTINEL'], 'environment': 'SECRET_SENTINEL'})
        self.assertEqual(report['cluster_fields']['ram_nodes'], {'present': True, 'type': 'str', 'length': 0})
        self.assertEqual(report['connection_fields']['valid_ip_rows'], 0)
        self.assertEqual(report['status_fields']['config_files']['absolute_path_rows'], 0)
        self.assertNotIn('SECRET_SENTINEL', repr(report))
        self.assertNotIn('password', repr(report))
        self.assertNotIn('environment', repr(report))

    def test_shape_report_handles_missing_and_malformed_sections_without_guessing(self):
        report = setup.rabbit_shapes([], None, {'config_files': None})
        self.assertEqual(report['cluster_fields']['disk_nodes'], {'present': False})
        self.assertEqual(report['connections'], {'type': 'NoneType'})
        self.assertEqual(report['status_fields']['config_files']['type'], 'NoneType')

    def test_invalid_peer_uses_static_error_code_not_raw_exception_contents(self):
        with self.assertRaises(setup.RabbitMetadataError) as error:
            setup.rabbit_metadata(self.metadata(), [{'user': 'openstack', 'peer_host': 'SECRET_SENTINEL'}], {'config_files': []})
        self.assertEqual(str(error.exception), 'peer_address')
        self.assertNotIn('SECRET_SENTINEL', str(error.exception))

    def test_failed_preflight_emits_shapes_and_does_not_relax_validation(self):
        outputs = ['epmd: up and running\nname rabbit at port 25672\n',
                   '{"running_nodes":["rabbit@host"],"disk_nodes":["rabbit@host"],"ram_nodes":""}',
                   '[]', '{"config_files":[]}']
        output = io.StringIO()
        with patch.object(setup.common, 'run', side_effect=outputs) as run, contextlib.redirect_stdout(output), \
             self.assertRaises(setup.RabbitMetadataError):
            setup.rabbit_preflight()
        self.assertIn('"validation_code": "cluster_schema"', output.getvalue())
        self.assertIn('"ram_nodes": {"length": 0, "present": true, "type": "str"}', output.getvalue())
        self.assertFalse(any('restart' in call.args[0] or 'start' in call.args[0] for call in run.call_args_list))

    def test_erlang_ipv4_ipv6_and_mapped_peers_preserve_scope(self):
        cases = [([127, 0, 0, 1], True), ([172, 19, 169, 1], False),
                 ([0, 0, 0, 0, 0, 0, 0, 1], True),
                 ([0, 0, 0, 0, 0, 65535, 32512, 1], True),
                 ([0, 0, 0, 0, 0, 65535, 44051, 43265], False)]
        for value, expected in cases:
            report = setup.rabbit_metadata(self.metadata(), [{'user': 'openstack', 'peer_host': value}], {'config_files': []})
            self.assertEqual(report['all_connections_loopback'], expected)
        for value in ([], [127, 0, 1], [True, 0, 0, 1], [256, 0, 0, 1], [-1, 0, 0, 1],
                      [0, 0, 0, 0, 0, 0, 0, 65536], ['127', 0, 0, 1], 2130706433):
            with self.assertRaises(setup.RabbitMetadataError):
                setup.rabbit_peer(value)

    @unittest.skipUnless(shutil.which('erl') and Path('/usr/lib/rabbitmq/lib/rabbitmq_server-3.9.27/escript/rabbitmqctl').is_file(),
                         'requires installed RabbitMQ 3.9.27 formatter and Erlang')
    def test_installed_formatter_output_parses_without_live_broker_or_distribution(self):
        program = '''T=#{running_nodes=>['rabbit@host'],disk_nodes=>['rabbit@host'],ram_nodes=>[]},
io:format("~ts~n",['Elixir.RabbitMQ.CLI.Formatters.Json':format_output(T,#{})]),
io:format("~ts~n",['Elixir.RabbitMQ.CLI.Formatters.Json':format_output([{user,<<"openstack">>},{peer_host,{127,0,0,1}}],#{})]),
io:format("~ts~n",['Elixir.RabbitMQ.CLI.Formatters.Json':format_output(#{config_files=>[]},#{})]),halt().'''
        with tempfile.TemporaryDirectory(prefix='task11-formatter-test-') as directory:
            with zipfile.ZipFile('/usr/lib/rabbitmq/lib/rabbitmq_server-3.9.27/escript/rabbitmqctl') as archive:
                for name in archive.namelist():
                    if Path(name).name == name and name.endswith('.beam'):
                        (Path(directory) / name).write_bytes(archive.read(name))
            output = subprocess.check_output(['erl', '+S', '1:1', '-noshell', '-pa', directory, '-eval', program],
                cwd=directory, text=True, stderr=subprocess.STDOUT, timeout=20)
        cluster, connection, status = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(connection['peer_host'], [127, 0, 0, 1])
        report = setup.rabbit_metadata(cluster, [connection], status)
        self.assertTrue(report['all_connections_loopback'])
        self.assertTrue(report['only_poc_connection_users'])

    def test_preflight_only_mode_never_reads_secrets_or_repeats_apply_and_hooks(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['harden_local_apis.py', '--rabbitmq-preflight-only']), \
             patch.object(setup.os, 'geteuid', return_value=0), \
             patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="22.04"\n'), \
             patch.object(setup.platform, 'release', return_value='microsoft-standard-WSL2'), \
             patch.object(setup, 'rabbit_preflight', return_value={'connection_count': 0}), \
             patch.object(setup.os, 'open', side_effect=AssertionError('no lock/configuration files')), \
             patch.object(setup.common, 'read_secrets', side_effect=AssertionError('no credentials')), \
             patch.object(setup, 'prepare', side_effect=AssertionError('no backup replay')), \
             patch.object(setup, 'apply_configuration', side_effect=AssertionError('no service changes')), \
             patch.object(setup, 'verify_hooks', side_effect=AssertionError('no hook replay')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(setup.main(), 0)
        self.assertIn('metadata only; timer unchanged', output.getvalue())

    def idle_rotation(self, **extra):
        return dict(dict(ActiveState='inactive', Result='success', ExecMainStatus='15', InvocationID='old',
                         Job='', ExecMainStartTimestampMonotonic='10'), **extra)

    def test_rotation_state_accepts_observed_systemd_empty_idle_job_format(self):
        text = ('Result=success\nExecMainStartTimestampMonotonic=79436492966\nExecMainStatus=15\n'
                'ActiveState=inactive\nJob=\nInvocationID=' + 'a' * 32 + '\n')
        with patch.object(setup.common, 'run', return_value=text):
            observed = setup.rotation_state()
        self.assertEqual(observed['Job'], '')
        self.assertEqual(observed['ExecMainStartTimestampMonotonic'], '79436492966')

    def test_timer_monitor_distinguishes_old_cancelled_invocation_from_new_rotation(self):
        before = self.idle_rotation()
        with patch.object(setup.review, 'systemd_state', return_value={'ActiveState': 'active', 'SubState': 'waiting'}), \
             patch.object(setup, 'rotation_state', return_value=before), patch.object(setup.time, 'sleep'):
            result = setup.monitor_timer(before)
        self.assertFalse(result['new_rotation_observed'])
        self.assertTrue(result['timer_active'])

    def test_timer_monitor_tracks_new_start_timestamp_when_invocation_id_is_retained(self):
        before = self.idle_rotation()
        after = self.idle_rotation(ExecMainStatus='0', ExecMainStartTimestampMonotonic='20')
        with patch.object(setup.review, 'systemd_state', return_value={'ActiveState': 'active', 'SubState': 'waiting'}), \
             patch.object(setup, 'rotation_state', return_value=after), patch.object(setup.time, 'sleep'):
            self.assertTrue(setup.monitor_timer(before)['new_rotation_observed'])

    def test_timer_monitor_rejects_failure_and_new_nonzero_completion(self):
        before = self.idle_rotation()
        for after in (self.idle_rotation(ActiveState='failed'), self.idle_rotation(Result='timeout'),
                      self.idle_rotation(ExecMainStartTimestampMonotonic='20')):
            with patch.object(setup.review, 'systemd_state', return_value={'ActiveState': 'active', 'SubState': 'waiting'}), \
                 patch.object(setup, 'rotation_state', return_value=after), self.assertRaises(RuntimeError):
                setup.monitor_timer(before)

    def test_timer_damaged_backup_prevents_activation(self):
        record = {'verified': True, 'logrotate_validation': dict(ovs_postrotate='PASS', ovn_postrotate='PASS', parser_debug='PASS')}
        with patch.object(setup.guest, 'protected_file'), patch.object(Path, 'read_text', return_value=json.dumps(record)), \
             patch.object(setup, 'verify_backup', side_effect=RuntimeError('damaged backup')), \
             patch.object(setup.common, 'run') as run, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            setup.resume_timer()
        run.assert_not_called()

    def timer_activation(self, *, fail=False):
        record = {'verified': True, 'logrotate_validation': dict(ovs_postrotate='PASS', ovn_postrotate='PASS', parser_debug='PASS'),
                  'files': {str(setup.TARGETS[3]): {'new_sha256': 'new'}, str(setup.DROPIN): {'new_sha256': 'new'}}}
        result = {'timer_active': True, 'new_rotation_observed': False, 'rotation_state': self.idle_rotation()}
        observed_exception = None
        with patch.object(setup.guest, 'protected_file'), patch.object(Path, 'read_text', return_value=json.dumps(record)), \
             patch.object(setup, 'verify_backup'), patch.object(setup, 'protected_regular'), \
             patch.object(setup.backup, 'checksum', side_effect=['new', 'new', '92658854cf515358aadae10f63d16135f4ff98ffb15ed314a578424d248e58b4']), \
             patch.object(setup.review, 'systemd_state', return_value={'UnitFileState': 'enabled', 'ActiveState': 'inactive'}), \
             patch.object(setup, 'rotation_state', return_value=self.idle_rotation()), \
             patch.object(setup, 'monitor_timer', return_value=result, side_effect=RuntimeError('rotation failed') if fail else None), \
             patch.object(setup.common, 'run', side_effect=['5min', '15s', '', '']) as run, \
             patch.object(setup.launch, 'atomic_state') as save, contextlib.redirect_stdout(io.StringIO()):
            try:
                setup.resume_timer()
            except RuntimeError as exc:
                observed_exception = exc
        return run, save, observed_exception

    def test_new_timer_failure_rolls_back_only_its_activation(self):
        run, save, error = self.timer_activation(fail=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual([call.args[0] for call in run.call_args_list][-2:],
                         [['systemctl', 'start', 'logrotate.timer'], ['systemctl', 'stop', 'logrotate.timer']])
        save.assert_not_called()

    def test_timer_success_preserves_enablement_and_does_not_force_rotation(self):
        run, save, error = self.timer_activation()
        self.assertIsNone(error)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[-1], ['systemctl', 'start', 'logrotate.timer'])
        self.assertFalse(any('enable' in command or 'reset-failed' in command or 'logrotate' == command[0] for command in commands))
        self.assertTrue(save.call_args.args[1]['timer_active'])


if __name__ == '__main__':
    unittest.main()
