"""Offline checks for ownership and guest-device parsing in T18 acceptance."""
import importlib.util
import json
import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location(
        'task18_accept_cinder_volume', SCRIPTS / 'accept_cinder_volume.py')
    acceptance = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(acceptance)
    DIAGNOSTIC_SPEC = importlib.util.spec_from_file_location(
        'task18_inspect_cinder_volume', SCRIPTS / 'inspect_cinder_volume_acceptance.py')
    diagnostic = importlib.util.module_from_spec(DIAGNOSTIC_SPEC)
    DIAGNOSTIC_SPEC.loader.exec_module(diagnostic)
finally:
    sys.path.remove(str(SCRIPTS))


class AcceptCinderVolumeTests(unittest.TestCase):
    def retained_run(self):
        run_id = 'a' * 32
        return {'schema': 1, 'task': 'T18-Cinder-volume-acceptance',
                'run_id': run_id, 'marker': 'Task 18 Cinder volume test ' + run_id,
                'project_id': 'project', 'server_id': 'server',
                'volume_id': 'e6eacba8-44f3-457d-b3c5-7e46a0970a28',
                'phase': 'attach_intent', 'created_assets': ['volume'],
                'baseline_devices': ['vda']}

    def test_resume_requires_exact_volume_and_refuses_repeat_or_snapshot_stage(self):
        state = self.retained_run()
        acceptance.validate_resume_manifest(state, state['volume_id'])
        with self.assertRaises(RuntimeError):
            acceptance.validate_resume_manifest(state, '00000000-0000-0000-0000-000000000001')
        state['attach_retries'] = 1
        with self.assertRaises(RuntimeError):
            acceptance.validate_resume_manifest(state, state['volume_id'])
        state['attach_retries'] = 0
        state['snapshot_id'] = 'snapshot'
        with self.assertRaises(RuntimeError):
            acceptance.validate_resume_manifest(state, state['volume_id'])

    def reviewed_sha1_failure(self, digest):
        state = self.retained_run()
        state.update(attach_retries=1, phase='attach_submitted', loopback_chap_sha1=True)
        state['loopback_chap_nodes'] = {state['volume_id']: {
            'algorithm': 'SHA1', 'phase': 'prepared', 'global_config_sha256': digest}}
        return state

    def test_reviewed_md5_retry_preserves_old_counter_and_is_consumed_once(self):
        request_id = 'req-a078112c-6bd0-4e0e-9328-f86c4bb779e8'
        data = b'node.session.auth.chap_algs = SHA3-256,SHA256\n'
        state = self.reviewed_sha1_failure(hashlib.sha256(data).hexdigest())
        cloud = Mock()
        cloud.compute.server_actions.return_value = [SimpleNamespace(
            action='attach_volume', request_id=request_id, start_time='2026-10-26T01:00:00')]
        cloud.compute.get_server_action.return_value = SimpleNamespace(events=[SimpleNamespace(
            event='compute_attach_volume', result='Error', traceback='os_brick.exception.VolumeDeviceNotFound')])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'iscsid.conf'
            path.write_bytes(data)
            with patch.object(acceptance.chap, 'ISCSID_CONF', path):
                receipt = acceptance.validate_reviewed_md5_request(cloud, state, request_id)
        self.assertEqual(state['attach_retries'], 1)
        self.assertEqual(receipt['previous_algorithm'], 'SHA1')
        self.assertEqual(receipt['algorithm'], 'MD5')
        state['reviewed_md5_retry'] = receipt
        state['resume_pending'] = True
        state['loopback_chap_sha1'] = False  # A partial MD5 preparation may be resumed.
        acceptance.validate_resume_manifest(state, state['volume_id'], request_id)
        acceptance.consume_resume_retry(state)
        self.assertEqual(state['attach_retries'], 2)
        self.assertEqual(receipt['previous_attach_retries'], 1)
        self.assertEqual(receipt['request_id'], request_id)
        self.assertEqual(receipt['phase'], 'submitted')
        with self.assertRaises(RuntimeError):
            acceptance.validate_resume_manifest(state, state['volume_id'], request_id)

    def test_reviewed_request_must_be_latest_failed_attachment(self):
        request_id = 'req-a078112c-6bd0-4e0e-9328-f86c4bb779e8'
        state = self.reviewed_sha1_failure('a' * 64)
        cloud = Mock()
        cloud.compute.server_actions.return_value = [SimpleNamespace(
            action='attach_volume', request_id='req-another-request', start_time='2026-10-26T01:00:00')]
        with self.assertRaisesRegex(RuntimeError, 'not the latest'):
            acceptance.validate_reviewed_md5_request(cloud, state, request_id)
        cloud.compute.get_server_action.assert_not_called()
        cloud.compute.create_volume_attachment.assert_not_called()
        self.assertEqual(state['attach_retries'], 1)
        cloud.compute.server_actions.return_value[0].request_id = request_id
        cloud.compute.get_server_action.return_value = SimpleNamespace(events=[SimpleNamespace(
            event='compute_attach_volume', result='Success', traceback=None)])
        with self.assertRaisesRegex(RuntimeError, 'does not prove'):
            acceptance.validate_reviewed_md5_request(cloud, state, request_id)
        cloud.compute.create_volume_attachment.assert_not_called()
        self.assertEqual(state['attach_retries'], 1)

    def test_resume_reuses_owned_detached_volume_without_mutating_provider(self):
        state = self.retained_run()
        cloud = Mock()
        cloud.session.get_project_id.return_value = 'project'
        volume = SimpleNamespace(id=state['volume_id'], project_id='project',
                                 metadata={'task18_run_id': state['run_id']},
                                 description=state['marker'], status='available', size=1,
                                 attachments=[])
        cloud.block_storage.get_volume.return_value = volume
        cloud.compute.volume_attachments.return_value = []
        cloud.block_storage.attachments.return_value = []
        cloud.block_storage.snapshots.return_value = []
        actual = acceptance.validate_retained_volume(
            cloud, state, {'ids': {'server': 'server'}}, {'vda'})
        self.assertIs(actual, volume)
        cloud.block_storage.create_volume.assert_not_called()
        cloud.block_storage.delete_volume.assert_not_called()
        cloud.compute.create_volume_attachment.assert_not_called()
        cloud.block_storage.attachments.return_value = [SimpleNamespace(volume_id=volume.id)]
        with self.assertRaises(RuntimeError):
            acceptance.validate_retained_volume(cloud, state, {'ids': {'server': 'server'}}, {'vda'})
        cloud.compute.create_volume_attachment.assert_not_called()

    def test_guest_io_refuses_boot_disk_and_invalid_volume_identifier(self):
        with patch.object(acceptance.common, 'run') as run:
            with self.assertRaises(RuntimeError):
                acceptance.guest_block_io({}, 'vda', 'e6eacba8-44f3-457d-b3c5-7e46a0970a28', True)
            with self.assertRaises(ValueError):
                acceptance.guest_block_io({}, 'vdb', 'not-a-volume-uuid', True)
        run.assert_not_called()

    def test_guest_serial_guard_accepts_full_or_virtio_truncated_uuid_only(self):
        volume_id = 'e6eacba8-44f3-457d-b3c5-7e46a0970a28'
        for serial, expected in ((volume_id, 0), (volume_id[:20], 0), ('different-volume', 3)):
            with self.subTest(serial=serial):
                # Exercise the actual shell guard with fake sysfs readers;
                # no block device is opened and no root command is needed.
                fixture = '''
test() {
    case "$1" in -b|-r) return 0 ;; esac
    command test "$@"
}
cat() {
    case "$1" in
        */size) printf '2097152\\n' ;;
        */serial) printf '%s\\n' "''' + serial + '''" ;;
        *) return 2 ;;
    esac
}
'''
                result = subprocess.run(['/bin/sh'], input=fixture + acceptance.guest_volume_guard('vdb', volume_id),
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, expected)

    def test_guest_identity_failure_prevents_privileged_io(self):
        with patch.object(acceptance.guest, 'ssh_command', return_value=['ssh', 'guest', 'sh', '-s']), \
                patch.object(acceptance.common, 'run', side_effect=RuntimeError('device serial mismatch')) as run:
            with self.assertRaises(RuntimeError):
                acceptance.guest_block_io({}, 'vdb', 'e6eacba8-44f3-457d-b3c5-7e46a0970a28', True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-2:], ['sh', '-s'])

    def test_verified_guest_io_uses_passwordless_sudo(self):
        digest = 'a' * 32
        with patch.object(acceptance.guest, 'ssh_command', return_value=['ssh', 'guest', 'sh', '-s']), \
                patch.object(acceptance.common, 'run', side_effect=[
                    'TASK18_VOLUME_IDENTITY_OK\n',
                    'TASK18_DATA_DIGEST_BEGIN\n' + digest + '\nTASK18_DATA_DIGEST_END\n']) as run:
            actual = acceptance.guest_block_io(
                {}, 'vdb', 'e6eacba8-44f3-457d-b3c5-7e46a0970a28', True)
        self.assertEqual(actual, digest)
        self.assertEqual(run.call_args.args[0][-4:], ['sudo', '-n', 'sh', '-s'])

    def test_guest_devices_parse_expected_framed_output(self):
        output = ('TASK18_DEVICES_BEGIN\n'
                  'vda\nvdb\n'
                  'TASK18_DEVICES_END\n')
        self.assertEqual(acceptance.parse_guest_devices(output), {'vda', 'vdb'})

    def test_guest_device_parser_rejects_unframed_or_unexpected_devices(self):
        with self.assertRaises(RuntimeError):
            acceptance.parse_guest_devices('vda\nvdb\n')
        with self.assertRaises(RuntimeError):
            acceptance.parse_guest_devices(
                'TASK18_DEVICES_BEGIN\nloop0\nTASK18_DEVICES_END\n')

    def test_volume_ownership_requires_project_metadata_and_description(self):
        state = {'project_id': 'project', 'run_id': 'run-id', 'marker': 'owned marker'}
        volume = SimpleNamespace(project_id='project', metadata={'task18_run_id': 'run-id'},
                                 description='owned marker')
        acceptance.assert_volume_owned(volume, state)
        volume.project_id = 'other-project'
        with self.assertRaises(RuntimeError):
            acceptance.assert_volume_owned(volume, state)


class InspectCinderVolumeTests(unittest.TestCase):
    def test_failure_summary_identifies_latest_request_without_private_context(self):
        report = {'phase': 'attach_submitted', 'volume': {'status': 'available'},
                  'nova_attachments': [], 'cinder_attachments': [],
                  'private_context': 'test-only-sensitive-value',
                  'attach_failure': {
                      'latest_nova_attach': {'request_id': 'req-current', 'events': [
                          {'categories': ['volume_device_not_found'], 'traceback': 'test-only-private-trace'}]},
                      'logs': {'nova/nova-compute.log': {
                          'categories': ['iscsi_login_failed'], 'login_exit_codes': [24]}},
                      'chap_compatibility': {'opted_in': True}}}
        result = diagnostic.failure_summary(report)
        self.assertEqual(result['request_id'], 'req-current')
        self.assertEqual(result['login_exit_codes'], [24])
        self.assertEqual(result['nova_attachment_count'], 0)
        self.assertNotIn('test-only-sensitive-value', json.dumps(result))
        self.assertNotIn('test-only-private-trace', json.dumps(result))

    def test_chap_observation_distinguishes_prepared_value_from_current_node(self):
        volume_id = '11111111-2222-4333-8444-555555555555'
        global_bytes = b'node.session.auth.chap_algs = SHA3-256,SHA256\r\n'
        state = {'volume_id': volume_id, 'loopback_chap_sha1': True,
                 'loopback_chap_nodes': {volume_id: {
                     'algorithm': 'SHA1', 'global_config_sha256': hashlib.sha256(global_bytes).hexdigest()}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'iscsid.conf'
            path.write_bytes(global_bytes)
            with patch.object(diagnostic.chap, 'ISCSID_CONF', path), \
                    patch.object(diagnostic.chap, 'node_command', return_value=SimpleNamespace(
                        returncode=0, stdout='node.session.auth.chap_algs = SHA256\n'
                        'node.session.auth.password = test-only-private-chap\n', stderr='')):
                result = diagnostic.chap_compatibility_observations(state)
        node = result['nodes'][volume_id]
        self.assertTrue(node['global_hash_matches_preparation'])
        self.assertEqual(node['prepared_algorithm'], 'SHA1')
        self.assertEqual(node['current_node_algorithms'], ['SHA256'])
        self.assertNotIn('test-only-private-chap', json.dumps(result))

    def test_protocol_login_reason_keeps_status_bytes_and_excludes_private_text(self):
        summary = diagnostic.login_rejection_reasons(
            'iscsid: conn 0 login rejected: initiator error - target not found (02/03)\n'
            'private incominguser account-private chap-private-value\n')
        self.assertIn('target_not_found', summary['reasons'])
        self.assertEqual(summary['protocol_statuses'], [{'class': '02', 'detail': '03'}])
        self.assertEqual(summary['login_rejection_rows'], 1)
        self.assertNotIn('account-private', json.dumps(summary))
        self.assertNotIn('chap-private-value', json.dumps(summary))

    def test_login_report_reads_daemon_identifiers_and_target_state(self):
        rows = '\n'.join(json.dumps({'MESSAGE': message}) for message in (
            'conn 0 login rejected: initiator error - target not found (02/03)',
            'private connection_info auth_password=private-value'))
        with patch.object(diagnostic.subprocess, 'run', side_effect=[
                SimpleNamespace(returncode=0, stdout=rows),
                SimpleNamespace(returncode=0, stdout='System:\n    State: ready\n')]) as run, \
                patch.object(diagnostic.Path, 'open', side_effect=PermissionError):
            summary = diagnostic.target_login_reasons()
        journal_command = run.call_args_list[0].args[0]
        self.assertIn('SYSLOG_IDENTIFIER=iscsid', journal_command)
        self.assertIn('SYSLOG_IDENTIFIER=tgtd', journal_command)
        self.assertNotIn('--since', journal_command)
        self.assertEqual(summary['daemon_journal']['rows'], 2)
        self.assertEqual(summary['target_system']['state'], 'ready')
        self.assertNotIn('private-value', json.dumps(summary))

    def test_login_failure_code_is_distinct_from_session_cleanup_code(self):
        summary = diagnostic.classify_failure(
            'Failed to login iSCSI target iqn.example:volume on portal 127.0.0.1:3260 (exit code 24).\n'
            'Command: iscsiadm -m session\nExit code: 21\nStderr: No active sessions.\n')
        self.assertEqual(summary['login_exit_codes'], [24])
        self.assertIn('iscsi_login_failed', summary['categories'])
        self.assertEqual(summary['iscsi_commands'], [
            {'mode': 'session', 'operation': 'query', 'exit_code': 21}])

    def test_lun_missing_after_successful_login_has_its_own_category(self):
        summary = diagnostic.classify_failure(
            'LUN 1 on iSCSI portal 127.0.0.1:3260 not found on sysfs after logging in.')
        self.assertIn('scsi_lun_missing_after_login', summary['categories'])
        self.assertEqual(summary['login_exit_codes'], [])

    def test_command_summary_excludes_auth_values_and_target_names(self):
        summary = diagnostic.classify_failure(
            'Command: sudo nova-rootwrap /etc/nova/rootwrap.conf iscsiadm -m node '
            '-T iqn.private-target -p 127.0.0.1:3260 --op update '
            '-n node.session.auth.password -v sensitive-chap-value\nExit code: 21\n')
        self.assertEqual(summary['iscsi_commands'], [
            {'mode': 'node', 'operation': 'update', 'exit_code': 21}])
        self.assertNotIn('sensitive-chap-value', json.dumps(summary))
        self.assertNotIn('iqn.private-target', json.dumps(summary))

    def test_initiator_journal_uses_utc_attach_window_without_raw_messages(self):
        with patch.object(diagnostic.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='CHAP authentication failed; private-value')) as run:
            summary = diagnostic.initiator_journal('2026-10-25T20:00:00.000000')
        self.assertIn('2026-10-25 19:59:55 UTC', run.call_args.args[0])
        self.assertIn('iscsi_authentication_failed', summary['categories'])
        self.assertNotIn('private-value', json.dumps(summary))

    def test_attach_failure_summary_omits_private_connection_data(self):
        private = 'secret-chap-value-13579'
        summary = diagnostic.classify_failure(
            'oslo_concurrency.processutils.ProcessExecutionError: command failed\n'
            'Command: iscsiadm --password=' + private + '\n'
            'Exit code: 24\nStderr: iscsiadm: Could not login to target\n'
            "connection_info={'auth_password': '" + private + "'}")
        self.assertIn('iscsi_login_failed', summary['categories'])
        self.assertEqual(summary['process_exit_codes'], [24])
        self.assertIn('oslo_concurrency.processutils.ProcessExecutionError', summary['exception_types'])
        self.assertNotIn(private, json.dumps(summary))
        self.assertNotIn('connection_info', json.dumps(summary))

    def test_missing_auth_settings_and_unknown_messages_are_not_exposed(self):
        summary = diagnostic.classify_failure(
            'keystoneauth1.exceptions.auth_plugins.MissingRequiredOptions: '
            'Auth plugin requires parameters which were not given: auth_url\n'
            'private context: do-not-emit-this')
        self.assertIn('service_auth_configuration', summary['categories'])
        self.assertNotIn('do-not-emit-this', json.dumps(summary))

    def test_attachment_inspection_filters_out_other_volumes(self):
        state = {'volume_id': 'owned-volume', 'server_id': 'server', 'project_id': 'project',
                 'run_id': 'run-id', 'marker': 'owned marker', 'phase': 'attach_intent'}
        cloud = Mock()
        cloud.block_storage.get_volume.return_value = SimpleNamespace(
            id='owned-volume', project_id='project', metadata={'task18_run_id': 'run-id'},
            description='owned marker', status='available')
        cloud.compute.get_server.return_value = SimpleNamespace(
            id='server', status='ACTIVE', task_state=None)
        cloud.compute.volume_attachments.return_value = []
        cloud.block_storage.attachments.return_value = [SimpleNamespace(
            volume_id='other-volume', id='other-attachment')]
        result = diagnostic.inspect(cloud, state)
        self.assertEqual(result['cinder_attachments'], [])
        self.assertEqual(result['volume']['status'], 'available')

    def test_attachment_status_is_preserved(self):
        self.assertEqual(diagnostic.safe_state('attached'), 'attached')
        self.assertEqual(diagnostic.safe_state('error_attaching'), 'error_attaching')


if __name__ == '__main__':
    unittest.main()
