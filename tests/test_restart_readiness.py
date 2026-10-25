"""Prerequisite checks do not authorize or execute a restart."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_restart_readiness', SCRIPTS / 'review_restart_readiness.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class RestartReadinessTests(unittest.TestCase):
    def completed_rehearsal(self):
        result = {'verified': True, 'source_bundle_unchanged': True,
            'source_server': setup.recovery.SOURCE_SERVER,
            'source_bundle': str(setup.launch.DATA_DIR / setup.recovery.SOURCE_BUNDLE),
            'stages': {stage: {'verified': True} for stage in setup.STAGES}}
        result['stages']['guest_boot'].update(kvm_enabled=True, serial_login_prompt=True,
            child_stopped=True, base_disk_unchanged=True, network_devices=0)
        result['stages']['sql']['child_stopped'] = True
        for stage in ('Open_vSwitch', 'OVN_Northbound', 'OVN_Southbound'):
            result['stages'][stage]['children_stopped'] = True
        return result

    def test_partial_or_changed_recovery_never_opens_restart_gate(self):
        record = self.completed_rehearsal()
        setup.validate_rehearsal(record)
        for key in ('verified', 'source_bundle_unchanged'):
            changed = json.loads(json.dumps(record))
            changed[key] = False
            with self.assertRaises(setup.ReadinessError):
                setup.validate_rehearsal(changed)
        for stage in setup.STAGES:
            changed = json.loads(json.dumps(record))
            changed['stages'][stage]['verified'] = False
            with self.assertRaises(setup.ReadinessError):
                setup.validate_rehearsal(changed)
        for field, value in (('network_devices', 1), ('network_devices', False), ('child_stopped', False)):
            changed = json.loads(json.dumps(record))
            changed['stages']['guest_boot'][field] = value
            with self.assertRaises(setup.ReadinessError):
                setup.validate_rehearsal(changed)

    def test_new_incomplete_attempt_cannot_be_hidden_by_old_success(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'), patch.object(setup.recovery, 'source_bundle') as verify:
            root = Path(directory)
            paths = []
            for index, verified in enumerate((True, False)):
                folder = root / ('restore-rehearsal-' + str(index))
                folder.mkdir(mode=0o700)
                path = folder / 'report.json'
                record = self.completed_rehearsal()
                record['verified'] = verified
                path.write_text(json.dumps(record))
                os.utime(path, ns=(1_000_000_000 + index, 1_000_000_000 + index))
                paths.append(path)
            original_lstat = Path.lstat
            def root_metadata(path):
                info = original_lstat(path)
                return NS(st_mode=info.st_mode, st_uid=0)
            with patch.object(Path, 'lstat', root_metadata):
                with self.assertRaises(setup.ReadinessError):
                    setup.latest_rehearsal()
                verify.assert_not_called()
                paths[-1].write_text(json.dumps(self.completed_rehearsal()))
                self.assertEqual(setup.latest_rehearsal(), paths[-1])
                verify.assert_called_once()

    def test_other_nova_or_libvirt_guest_blocks_maintenance(self):
        state = {'ids': {'server': setup.DEMO_SERVER}, 'project_id': 'project', 'run_id': 'marker'}
        server = NS(id=setup.DEMO_SERVER, task_state=None, project_id='project', metadata={'task11_run_id': 'marker'})
        compute = NS(servers=Mock(return_value=[server]), get_server=Mock(return_value=server))
        cloud = NS(compute=compute)
        with patch.object(setup.common, 'run', return_value=setup.DEMO_SERVER + '\n'):
            self.assertTrue(setup.exclusive_demo(cloud, state)['sole_nova_and_libvirt_guest'])
            compute.servers.return_value = [server, NS(id='unrelated')]
            with self.assertRaises(setup.ReadinessError):
                setup.exclusive_demo(cloud, state)
        compute.servers.return_value = [server]
        with patch.object(setup.common, 'run', return_value=setup.DEMO_SERVER + '\nunrelated\n'):
            with self.assertRaises(setup.ReadinessError):
                setup.exclusive_demo(cloud, state)

    def test_journal_is_read_only_and_unresolved_jobs_block_maintenance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            source = Path(directory) / 'application-jobs.sqlite'
            Path(str(source) + '.lock').touch(mode=0o600)
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE jobs (state TEXT)')
                db.execute("INSERT INTO jobs VALUES ('succeeded')")
            digest = setup.recovery.backup.checksum(source)
            self.assertTrue(setup.journal_quiescent()['no_unresolved_jobs'])
            self.assertEqual(setup.recovery.backup.checksum(source), digest)
            with sqlite3.connect(source) as db:
                db.execute("INSERT INTO jobs VALUES ('unverified')")
            with self.assertRaises(setup.ReadinessError):
                setup.journal_quiescent()

    def test_changed_wsl_address_fails_without_any_reconfiguration_command(self):
        config = '[DEFAULT]\nmy_ip=172.19.169.140\n[database]\nconnection=PRIVATE_SENTINEL\n'
        inventory = json.dumps([{'addr_info': [{'local': '172.19.169.140', 'scope': 'global'}]}])
        with patch.object(Path, 'read_text', return_value=config), \
             patch.object(setup.common, 'run', side_effect=[inventory, '"172.19.169.140"']) as run:
            self.assertTrue(setup.addressing()['match'])
            self.assertNotIn('PRIVATE_SENTINEL', repr(run.call_args_list))
        inventory = inventory.replace('172.19.169.140', '172.19.169.141')
        with patch.object(Path, 'read_text', return_value=config), \
             patch.object(setup.common, 'run', side_effect=[inventory, '"172.19.169.140"']) as run:
            with self.assertRaises(setup.ReadinessError):
                setup.addressing()
            self.assertFalse(any('set' in call.args[0] for call in run.call_args_list))

    def test_nonroot_rejection_precedes_cloud_and_recovery_access(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.recovery, 'source_bundle') as source, \
             patch.object(setup.common, 'connection') as connect, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(), 1)
        source.assert_not_called()
        connect.assert_not_called()

    def test_inactive_required_unit_stops_before_guest_checks(self):
        with patch.object(setup, 'exclusive_demo'), patch.object(setup, 'journal_quiescent'), \
             patch.object(setup, 'addressing'), patch.object(setup, 'kvm_devices'), \
             patch.object(setup.operations, 'systemd_state', return_value={'expected_profile_match': False}), \
             patch.object(setup.operations, 'cpu_smoke') as smoke, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(setup.ReadinessError):
                setup.review(Mock(), {})
            smoke.assert_not_called()

    def test_success_is_observations_only_and_never_claims_wsl_restart_ready(self):
        with patch.object(setup, 'exclusive_demo'), patch.object(setup, 'journal_quiescent'), \
             patch.object(setup, 'addressing'), patch.object(setup, 'kvm_devices'), \
             patch.object(setup.operations, 'systemd_state', return_value={'expected_profile_match': True}), \
             patch.object(setup, 'demo_state', return_value={'nova_status': 'ACTIVE', 'task_pending': False, 'power_state': 1}), \
             patch.object(setup.operations, 'cloud_state'), patch.object(setup.operations, 'cpu_smoke'), \
             patch.object(setup.common, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            result = setup.review(Mock(), {})
            self.assertTrue(result['service_cycle_prerequisites_passed'])
            self.assertFalse(result['wsl_restart_ready'])
            run.assert_not_called()

    def test_failed_check_preserves_previous_results_and_exact_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as console:
            path = Path(directory) / 'report.json'
            report = {'verified': False}
            self.assertEqual(setup.run_check(report, 'first', lambda: {'healthy': True}, path), {'healthy': True})
            def failed():
                raise RuntimeError('PRIVATE_TOKEN_SENTINEL')
            with self.assertRaises(setup.CheckFailure) as failure:
                setup.run_check(report, 'guest_cpu', failed, path)
            saved = json.loads(path.read_text())
        self.assertEqual(saved['checks']['first']['status'], 'passed')
        self.assertEqual(saved['checks']['guest_cpu']['status'], 'failed')
        self.assertEqual(saved['failed_check'], 'guest_cpu')
        self.assertEqual(failure.exception.check, 'guest_cpu')
        self.assertFalse(saved['verified'])
        self.assertNotIn('PRIVATE_TOKEN_SENTINEL', console.getvalue())
        self.assertNotIn('PRIVATE_TOKEN_SENTINEL', json.dumps(saved))
        self.assertNotIn('PRIVATE_TOKEN_SENTINEL', str(failure.exception))

    def test_check_intent_is_saved_before_action_and_returned_credentials_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as console:
            path = Path(directory) / 'report.json'
            report = {'verified': False}
            def action():
                saved = json.loads(path.read_text())
                self.assertEqual(saved['current_check'], 'credentials')
                self.assertEqual(saved['checks']['credentials']['status'], 'running')
                return {'password': 'PRIVATE_PASSWORD_SENTINEL'}
            self.assertEqual(setup.run_check(report, 'credentials', action, path),
                             {'password': 'PRIVATE_PASSWORD_SENTINEL'})
            self.assertNotIn('PRIVATE_PASSWORD_SENTINEL', path.read_text())
            self.assertNotIn('PRIVATE_PASSWORD_SENTINEL', console.getvalue())

    def test_command_diagnostic_exposes_exit_code_without_raw_output(self):
        error = RuntimeError('ip failed (exit 255): PRIVATE_SSH_DIAGNOSTIC_SENTINEL')
        self.assertEqual(setup.safe_error(error), {'error_type': 'RuntimeError',
            'category': 'command_failed', 'command': 'ip', 'exit_code': 255})
        timeout = subprocess.TimeoutExpired(['ssh', 'PRIVATE_ARGUMENT_SENTINEL'], 20,
                                             output='PRIVATE_OUTPUT_SENTINEL')
        details = setup.safe_error(timeout)
        self.assertEqual(details['command'], 'ssh')
        self.assertEqual(details['category'], 'command_timeout')
        self.assertNotIn('PRIVATE_', repr(details))
        parser = setup.safe_error(RuntimeError('unexpected service property format'))
        self.assertEqual(parser['category'], 'service_property_format')

    def test_private_original_exception_is_retained_without_overwriting_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            try:
                raise RuntimeError('PRIVATE_ORIGINAL_EXCEPTION_SENTINEL')
            except RuntimeError as error:
                diagnostic = Path(setup.private_diagnostic(path, error))
                self.assertIn('PRIVATE_ORIGINAL_EXCEPTION_SENTINEL', diagnostic.read_text())
                self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    setup.private_diagnostic(path, error)

    def test_generic_helper_failure_still_identifies_exact_service_and_stops_later_checks(self):
        report = {'verified': False}
        def state(unit):
            if unit == 'nova-api.service':
                raise RuntimeError('PRIVATE_HELPER_SENTINEL')
            return {'expected_profile_match': True}
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'exclusive_demo', return_value={}), \
             patch.object(setup, 'journal_quiescent', return_value={}), \
             patch.object(setup, 'addressing', return_value={}), patch.object(setup, 'kvm_devices', return_value=[]), \
             patch.object(setup.operations, 'systemd_state', side_effect=state), \
             patch.object(setup.operations, 'cloud_state') as cloud, contextlib.redirect_stdout(io.StringIO()) as console:
            path = Path(directory) / 'report.json'
            with self.assertRaises(setup.CheckFailure):
                setup.review(Mock(), {}, report=report, path=path)
            saved = json.loads(path.read_text())
            cloud.assert_not_called()
        self.assertEqual(saved['failed_check'], 'service:nova-api.service')
        self.assertEqual(saved['checks']['service:glance-api.service']['status'], 'passed')
        self.assertNotIn('PRIVATE_HELPER_SENTINEL', repr(saved))
        self.assertNotIn('PRIVATE_HELPER_SENTINEL', console.getvalue())

    def demo(self, status='SHUTOFF', power=4, task=None, project='project'):
        return NS(id=setup.DEMO_SERVER, status=status, power_state=power, task_state=task,
                  project_id=project, metadata={'task11_run_id': 'run-marker'})

    def demo_scope(self):
        return {'ids': {'server': setup.DEMO_SERVER}, 'project_id': 'project', 'run_id': 'run-marker'}

    def test_demo_snapshot_reports_safe_nova_fields_without_provider_data(self):
        server = self.demo(status='PRIVATE_PROVIDER_SENTINEL', task='PRIVATE_TASK_SENTINEL')
        cloud = NS(compute=NS(get_server=Mock(return_value=server)))
        result = setup.demo_state(cloud, self.demo_scope())
        self.assertEqual(result['nova_status'], 'UNRECOGNIZED')
        self.assertTrue(result['task_pending'])
        self.assertEqual(result['power_state'], 4)
        self.assertNotIn('PRIVATE_', repr(result))

    def test_resume_requires_owned_settled_shutoff_and_never_resets_other_states(self):
        for server in (self.demo(status='ERROR'), self.demo(status='PAUSED'), self.demo(power=1),
                       self.demo(task='powering-on'), self.demo(project='foreign')):
            with self.subTest(status=server.status, task=server.task_state), tempfile.TemporaryDirectory() as directory, \
                 patch.object(setup.launch, 'DATA_DIR', Path(directory)):
                cloud = NS(compute=NS(get_server=Mock(return_value=server), start_server=Mock()))
                with self.assertRaises(RuntimeError):
                    setup.resume_demo(cloud, self.demo_scope())
                cloud.compute.start_server.assert_not_called()
                self.assertFalse((Path(directory) / 'demo-resume.json').exists())

    def test_resume_records_intent_before_single_request_and_observes_nova_plus_libvirt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.common, 'run', return_value=setup.DEMO_SERVER + '\n'), \
             patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 1024 * 1024}}), \
             contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / 'demo-resume.json'
            def start(identifier):
                self.assertEqual(identifier, setup.DEMO_SERVER)
                self.assertEqual(json.loads(path.read_text())['phase'], 'start_intent')
            compute = NS(get_server=Mock(side_effect=[self.demo(), self.demo(), self.demo('ACTIVE', 1)]),
                         start_server=Mock(side_effect=start))
            result = setup.resume_demo(NS(compute=compute), self.demo_scope())
            self.assertTrue(result['nova_active'])
            self.assertTrue(result['libvirt_running'])
            self.assertEqual(json.loads(path.read_text())['phase'], 'active')
            compute.start_server.assert_called_once_with(setup.DEMO_SERVER)

    def test_uncertain_start_is_not_replayed_and_can_be_reconciled_if_active(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.common, 'run', return_value=setup.DEMO_SERVER + '\n'), \
             patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 1024 * 1024}}), \
             patch.object(setup.guest, 'protected_file'), contextlib.redirect_stdout(io.StringIO()):
            compute = NS(get_server=Mock(return_value=self.demo()),
                         start_server=Mock(side_effect=RuntimeError('PRIVATE_RESPONSE_SENTINEL')))
            with self.assertRaises(RuntimeError):
                setup.resume_demo(NS(compute=compute), self.demo_scope())
            path = Path(directory) / 'demo-resume.json'
            self.assertEqual(json.loads(path.read_text())['phase'], 'unverified')
            self.assertNotIn('PRIVATE_RESPONSE_SENTINEL', path.read_text())
            compute.start_server.reset_mock()
            with patch.object(setup.time, 'monotonic', side_effect=[0, 181]):
                with self.assertRaises(setup.ReadinessError):
                    setup.resume_demo(NS(compute=compute), self.demo_scope())
            compute.start_server.assert_not_called()
            compute.get_server.return_value = self.demo('ACTIVE', 1)
            self.assertTrue(setup.resume_demo(NS(compute=compute), self.demo_scope())['already_active'])
            self.assertEqual(json.loads(path.read_text())['phase'], 'active')
            compute.start_server.assert_not_called()

    def test_low_capacity_or_libvirt_disagreement_prevents_start(self):
        for domains, memory in (('unrelated\n', 1024 * 1024), (setup.DEMO_SERVER + '\n', 128 * 1024)):
            with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
                 patch.object(setup.common, 'run', return_value=domains), \
                 patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': memory}}):
                compute = NS(get_server=Mock(return_value=self.demo()), start_server=Mock())
                with self.assertRaises(setup.ReadinessError):
                    setup.resume_demo(NS(compute=compute), self.demo_scope())
                compute.start_server.assert_not_called()

    def test_already_active_demo_is_observed_without_another_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)):
            compute = NS(get_server=Mock(return_value=self.demo('ACTIVE', 1)), start_server=Mock())
            self.assertTrue(setup.resume_demo(NS(compute=compute), self.demo_scope())['already_active'])
            compute.start_server.assert_not_called()

    def test_guest_readiness_waits_for_pinned_ssh_without_replaying_lifecycle(self):
        compute = NS(get_server=Mock(return_value=self.demo('ACTIVE', 1)), start_server=Mock())
        with patch.object(setup.operations, 'cpu_smoke', side_effect=[RuntimeError('not ready'), {'verified': True}]), \
             patch.object(setup.time, 'sleep'):
            self.assertTrue(setup.wait_guest_cpu(NS(compute=compute), self.demo_scope())['verified'])
        compute.start_server.assert_not_called()

    def test_saved_diagnostic_decodes_known_network_guard_without_echoing_traceback(self):
        trace = ('Traceback (most recent call last):\n'
                 '  File "/private/root/validate_guest.py", line 164, in validate_attachment\n'
                 '    raise RuntimeError("private source line")\n'
                 'RuntimeError: metadata namespace does not own the permitted probe source address\n'
                 '\nThe above exception was the direct cause of the following exception:\n'
                 '  File "/private/root/review_restart_readiness.py", line 117, in run_check\n'
                 'task11_readiness.CheckFailure: prerequisite check failed: cloud\n')
        result = setup.decode_diagnostic(trace)
        self.assertEqual(result['category'], 'metadata_namespace_address_missing')
        self.assertEqual(result['origin'], {'module': 'validate_guest.py', 'function': 'validate_attachment', 'line': 164})
        self.assertNotIn('/private/root', json.dumps(result))
        self.assertNotIn('private source line', json.dumps(result))

    def test_saved_unknown_exception_and_arguments_stay_private(self):
        result = setup.decode_diagnostic('RuntimeError: PRIVATE_TOKEN_SENTINEL\nPRIVATE_BODY_SENTINEL\n')
        self.assertEqual(result['category'], 'observation_failed')
        self.assertNotIn('PRIVATE_', repr(result))

    def test_saved_readiness_guard_is_decoded_without_exposing_traceback(self):
        trace = ('Traceback (most recent call last):\n'
                 '  File "/private/root/review_restart_readiness.py", line 311, in resume_demo\n'
                 'ReadinessError: demo stopped again after the recorded start; a new start requires review\n')
        result = setup.decode_diagnostic(trace)
        self.assertEqual(result['category'], 'prerequisite_guard')
        self.assertEqual(result['reason'], 'demo stopped again after the recorded start; a new start requires review')
        self.assertEqual(result['origin'], {'module': 'review_restart_readiness.py', 'function': 'resume_demo', 'line': 311})
        self.assertNotIn('/private/root', json.dumps(result))
        result = setup.decode_diagnostic('RuntimeError: ip failed (exit 255): PRIVATE_GUEST_OUTPUT\n')
        self.assertEqual(result['command'], 'ip')
        self.assertEqual(result['exit_code'], 255)
        self.assertNotIn('PRIVATE_', repr(result))

    def test_saved_report_decoder_uses_only_matching_protected_files_and_no_cloud_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'), patch.object(setup.common, 'connection') as connect:
            root = Path(directory)
            root.chmod(0o700)
            path = root / ('restart-readiness-' + 'a' * 32 + '.json')
            diagnostic = path.with_suffix('.diagnostic.txt')
            record = {'private_diagnostic': str(diagnostic), 'failed_check': 'cloud',
                      'resume_demo': {'nova_active': True}, 'checks': {'guest_kvm': {'status': 'passed'}}}
            path.write_text(json.dumps(record))
            diagnostic.write_text('RuntimeError: expected exactly one compute port on the owned network\n')
            original_lstat = Path.lstat
            def protected_metadata(item):
                info = original_lstat(item)
                return NS(st_mode=info.st_mode, st_uid=0)
            with patch.object(Path, 'lstat', protected_metadata):
                result = setup.explain_report(path)
                self.assertEqual(result['failure']['category'], 'compute_port_count_mismatch')
                self.assertTrue(result['demo_start_observed'])
                self.assertTrue(result['guest_kvm_passed'])
                record['private_diagnostic'] = '/unrelated/protected/file'
                path.write_text(json.dumps(record))
                with self.assertRaises(setup.ReadinessError):
                    setup.explain_report(path)
            connect.assert_not_called()
            with self.assertRaises(setup.ReadinessError):
                setup.explain_report(Path('/unrelated/readiness.json'))

    def test_diagnostic_mode_does_not_run_preflight_or_mutations(self):
        with patch.object(setup.os, 'geteuid', return_value=0), \
             patch.object(setup, 'explain_report', return_value={'failed_check': 'cloud'}) as explain, \
             patch.object(setup, 'review') as review, patch.object(setup, 'resume_demo') as resume, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.main(['--explain-report', '/root/example.json']), 0)
            explain.assert_called_once_with(Path('/root/example.json'))
            review.assert_not_called()
            resume.assert_not_called()
            self.assertIn('POC readiness diagnostic:', output.getvalue())

    def test_metadata_address_delay_waits_for_a_complete_successful_cloud_observation(self):
        missing = RuntimeError('metadata namespace does not own the permitted probe source address')
        cloud = NS(compute=NS(start_server=Mock(), stop_server=Mock()))
        with patch.object(setup.operations, 'cloud_state', side_effect=[missing, {'all_checks_passed': True}]) as observe, \
             patch.object(setup.time, 'monotonic', side_effect=[0, 1]), \
             patch.object(setup.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.wait_cloud_state(cloud, {}), {'all_checks_passed': True})
            self.assertEqual(observe.call_count, 2)
            sleep.assert_called_once_with(2)
        cloud.compute.start_server.assert_not_called()
        cloud.compute.stop_server.assert_not_called()

    def test_persistent_metadata_address_absence_keeps_failure_after_deadline(self):
        missing = RuntimeError('metadata namespace does not own the permitted probe source address')
        with patch.object(setup.operations, 'cloud_state', side_effect=missing) as observe, \
             patch.object(setup.time, 'monotonic', side_effect=[0, 1, 61]), \
             patch.object(setup.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                setup.wait_cloud_state(Mock(), {})
            self.assertIs(caught.exception, missing)
            self.assertEqual(observe.call_count, 2)
            self.assertEqual(setup.safe_error(caught.exception)['category'], 'metadata_namespace_address_missing')

    def test_cloud_wait_never_retries_scope_policy_api_or_command_failures(self):
        for error in (RuntimeError('resource belongs to another project; preserving it'),
                      RuntimeError('guest port ownership/security-group/port-security drift detected'),
                      RuntimeError('PRIVATE_API_FAILURE_SENTINEL'),
                      subprocess.TimeoutExpired(['ip', 'private argument'], 10)):
            with self.subTest(error_type=type(error).__name__), \
                 patch.object(setup.operations, 'cloud_state', side_effect=error) as observe, \
                 patch.object(setup.time, 'sleep') as sleep:
                with self.assertRaises(type(error)):
                    setup.wait_cloud_state(Mock(), {})
                observe.assert_called_once()
                sleep.assert_not_called()

    def test_cloud_wait_rechecks_scope_and_stops_on_drift_during_reconciliation(self):
        missing = RuntimeError('metadata namespace does not own the permitted probe source address')
        drift = RuntimeError('resource ownership marker does not match this run; preserving it')
        with patch.object(setup.operations, 'cloud_state', side_effect=[missing, drift, {'must_not_pass': True}]) as observe, \
             patch.object(setup.time, 'monotonic', side_effect=[0, 1]), \
             patch.object(setup.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as caught:
                setup.wait_cloud_state(Mock(), {})
            self.assertIs(caught.exception, drift)
            self.assertEqual(observe.call_count, 2)


if __name__ == '__main__':
    unittest.main()
