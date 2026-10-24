"""Safe operational observation and pinned-SSH workload boundaries."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_operations', SCRIPTS / 'review_operations.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class OperationsReviewTests(unittest.TestCase):
    def test_listener_scope_distinguishes_wildcard_ipv6_and_loopback(self):
        result = setup.listeners('LISTEN 0 10 127.0.0.1:6641 0.0.0.0:*\n'
                                 'LISTEN 0 511 *:5000 *:*\n'
                                 'LISTEN 0 128 [::1]:11211 [::]:*\n'
                                 'LISTEN 0 128 [::]:5672 [::]:*\n'
                                 'LISTEN 0 10 0.0.0.0:22 0.0.0.0:*', 'tcp')
        self.assertEqual([row['loopback'] for row in result], [True, False, True, False])
        self.assertNotIn(22, [row['port'] for row in result])
        with self.assertRaises(RuntimeError):
            setup.listeners('unexpected output', 'tcp')

    def test_failed_observation_does_not_print_or_store_sensitive_exception(self):
        report = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            setup.observe(report, 'probe', Mock(side_effect=RuntimeError('SECRET_CREDENTIAL=not-for-output')))
        self.assertNotIn('SECRET', output.getvalue())
        self.assertNotIn('SECRET', repr(report))
        self.assertEqual(report['probe']['status'], 'FAILED_OR_UNAVAILABLE')

    def test_package_output_cannot_introduce_configuration_contents(self):
        with patch.object(setup.common, 'run', return_value='nova-api\t3:29.2.0-0ubuntu1~cloud0\tinstalled\n'):
            self.assertEqual(setup.package_versions()[0]['package'], 'nova-api')
        with patch.object(setup.common, 'run', return_value='unexpected configuration with spaces'):
            with self.assertRaises(RuntimeError):
                setup.package_versions()

    def test_service_observation_reports_inactive_required_service_without_false_health(self):
        text = 'LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nTimeoutStartUSec=1min 30s\n'
        with patch.object(setup.common, 'run', return_value=text) as run:
            self.assertFalse(setup.systemd_state('nova-api.service')['expected_profile_match'])
            self.assertNotIn('start', run.call_args.args[0])

    def test_guest_workload_requires_existing_pinned_key_and_exact_result(self):
        state = {'ids': {'server': 'owned-vm'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'known_hosts').write_text('retained pinned key')
            command = ['ssh', '-o', 'StrictHostKeyChecking=accept-new', 'guest', 'sh', '-s']
            with patch.object(setup.launch, 'DATA_DIR', path), patch.object(setup.guest, 'protected_file'), \
                 patch.object(setup.guest, 'ssh_command', return_value=command), \
                 patch.object(setup.common, 'run', return_value='5000050000\n') as run:
                self.assertTrue(setup.cpu_smoke(state)['verified'])
                self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])
                self.assertNotIn('StrictHostKeyChecking=accept-new', run.call_args.args[0])
                self.assertEqual(run.call_args.kwargs['timeout'], 20)
                run.return_value = 'wrong result'
                with self.assertRaises(RuntimeError):
                    setup.cpu_smoke(state)
            self.assertEqual((path / 'known_hosts').read_text(), 'retained pinned key')

    def test_bounded_cpu_workload_computes_expected_sum_without_guest_or_cloud(self):
        output = subprocess.check_output(['sh', '-s'], input=setup.CPU_COMMAND, text=True, timeout=5)
        self.assertEqual(output.strip(), '5000050000')


if __name__ == '__main__':
    unittest.main()
