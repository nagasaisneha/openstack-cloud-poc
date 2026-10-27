"""Checks for narrowly scoped local TGT CHAP compatibility and preservation."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task18_loopback_chap', SCRIPTS / 'cinder_loopback_chap.py')
    chap = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(chap)
finally:
    sys.path.remove(str(SCRIPTS))


CINDER = '''[DEFAULT]
enabled_backends = lvm
[lvm]
volume_driver = cinder.volume.drivers.lvm.LVMVolumeDriver
volume_group = cinder-poc-vg
volume_backend_name = CINDER_POC_LOOP
target_helper = tgtadm
target_protocol = iscsi
target_ip_address = 127.0.0.1
target_port = 3260
'''
GLOBAL = '#node.session.auth.chap_algs = MD5\n[DEFAULT]\nnode.session.auth.chap_algs = SHA3-256,SHA256\n'


class LoopbackChapTests(unittest.TestCase):
    def test_continuation_requires_explicit_reviewed_namespace_and_exact_ownership(self):
        volume, state = self.resources()
        volume.metadata = {'task49_run_id': state['run_id']}
        state['ownership_metadata_key'] = 'task49_run_id'
        chap.assert_owned(volume, state)
        for key in ('arbitrary_key', 'task18_run_id'):
            state['ownership_metadata_key'] = key
            with self.assertRaises(RuntimeError):
                chap.assert_owned(volume, state)
        state['ownership_metadata_key'] = 'task49_run_id'
        volume.project_id = 'foreign'
        with self.assertRaises(RuntimeError):
            chap.assert_owned(volume, state)

    def resources(self):
        volume_id = '11111111-2222-4333-8444-555555555555'
        state = {'project_id': 'project', 'run_id': 'a' * 32,
                 'marker': 'owned-test-marker', 'volume_id': volume_id,
                 'loopback_chap_md5': True, 'loopback_chap_algorithm': 'MD5'}
        volume = NS(id=volume_id, project_id='project',
                    metadata={'task18_run_id': state['run_id']},
                    description=state['marker'], status='available', attachments=[])
        return volume, state

    def test_strong_global_default_is_not_silently_weakened(self):
        with patch.object(chap, 'regular_text', return_value=GLOBAL), \
                patch.object(chap.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'opt-in required'):
                chap.compatibility_preflight(False)
        run.assert_not_called()
        self.assertEqual(chap.chap_algorithms(GLOBAL), ('SHA3-256', 'SHA256'))

    def test_same_algorithm_name_with_different_wire_ids_is_rejected(self):
        with patch.object(chap, 'regular_text', return_value='node.session.auth.chap_algs = SHA1\n'), \
                patch.object(chap.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'wire algorithm'):
                chap.compatibility_preflight()
            with self.assertRaisesRegex(RuntimeError, 'wire ID 6'):
                chap.compatibility_preflight(allow_sha1=True)
        run.assert_not_called()

    def test_interoperable_global_md5_needs_no_node_exception(self):
        with patch.object(chap, 'regular_text', return_value='node.session.auth.chap_algs = MD5\n'), \
                patch.object(chap.subprocess, 'run') as run:
            chap.compatibility_preflight()
        run.assert_not_called()

    def test_exception_refuses_remote_backend_or_custom_nova_interface(self):
        with self.assertRaises(RuntimeError):
            chap.validate_backend(CINDER.replace('127.0.0.1', '192.0.2.10'), '[libvirt]\n')
        with self.assertRaises(RuntimeError):
            chap.validate_backend(CINDER, '[libvirt]\niscsi_iface = unrelated-interface\n')

    def test_ambiguous_algorithm_assignments_are_rejected(self):
        with self.assertRaises(RuntimeError):
            chap.chap_algorithms(GLOBAL + 'node.session.auth.chap_algs = MD5\n')

    def test_node_mutations_are_owned_loopback_only_and_global_file_is_preserved(self):
        volume, state = self.resources()
        calls = []
        node = {'exists': False, 'algorithm': 'SHA256'}

        def execute(args, **kwargs):
            calls.append(args)
            if args[0] == 'dpkg-query':
                return NS(returncode=0, stdout='tgt 1:1.0.80-1ubuntu2\nopen-iscsi 2.1.5-1ubuntu1.1\n', stderr='')
            if args[0] == 'openssl':
                return NS(returncode=0, stdout='MD5(stdin)= d41d8cd98f00b204e9800998ecf8427e\n', stderr='')
            if args == ['iscsiadm', '-m', 'session']:
                return NS(returncode=21, stdout='', stderr='No active sessions')
            self.assertEqual(args[:3], ['iscsiadm', '-m', 'node'])
            self.assertEqual(args[args.index('-T') + 1], chap.PREFIX + 'volume-' + volume.id)
            self.assertEqual(args[args.index('-p') + 1], '127.0.0.1:3260')
            self.assertEqual(args[args.index('--interface') + 1], 'default')
            if '--op' in args:
                operation = args[args.index('--op') + 1]
                if operation == 'new':
                    node['exists'] = True
                elif operation == 'update':
                    self.assertEqual(args[args.index('-n') + 1], 'node.session.auth.chap_algs')
                    self.assertEqual(args[args.index('-v') + 1], 'MD5')
                    node['algorithm'] = 'MD5'
                else:
                    self.fail('unexpected node mutation')
                return NS(returncode=0, stdout='', stderr='')
            return NS(returncode=0 if node['exists'] else 21,
                      stdout=('node.session.auth.chap_algs = ' + node['algorithm'] + '\n'
                              'node.session.auth.password = test-only-private-value\n') if node['exists'] else '',
                      stderr='')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            global_path, cinder_path, nova_path = [root / name for name in ('iscsid.conf', 'cinder.conf', 'nova.conf')]
            global_path.write_text(GLOBAL)
            cinder_path.write_text(CINDER)
            nova_path.write_text('[libvirt]\n')
            saved = []
            with patch.object(chap, 'ISCSID_CONF', global_path), \
                    patch.object(chap, 'CINDER_CONF', cinder_path), \
                    patch.object(chap, 'NOVA_CONF', nova_path), \
                    patch.object(chap.storage, 'listen_addresses', return_value=[chap.PORTAL]), \
                    patch.object(chap.subprocess, 'run', side_effect=execute):
                chap.prepare_node(volume, state, lambda item: saved.append(json.dumps(item)))
                self.assertEqual(state['loopback_chap_nodes'][volume.id]['phase'], 'prepared')
                self.assertEqual(global_path.read_text(), GLOBAL)
                self.assertNotIn('test-only-private-value', '\n'.join(saved))
                node['exists'] = False  # Nova/os-brick removed its node after detachment.
                chap.verify_node_removed(volume, state, lambda item: saved.append(json.dumps(item)))
                self.assertEqual(state['loopback_chap_nodes'][volume.id]['phase'], 'removed_after_detach')
            mutations = [args for args in calls if '--op' in args]
            self.assertEqual(len(mutations), 2)
            self.assertEqual(global_path.read_text(), GLOBAL)

    def test_existing_node_without_run_record_is_preserved(self):
        volume, state = self.resources()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'iscsid.conf'
            path.write_text(GLOBAL)
            with patch.object(chap, 'ISCSID_CONF', path), \
                    patch.object(chap, 'compatibility_preflight'), \
                    patch.object(chap, 'assert_no_session'), \
                    patch.object(chap, 'node_command', return_value=NS(returncode=0)) as command:
                with self.assertRaisesRegex(RuntimeError, 'lacks this run ownership'):
                    chap.prepare_node(volume, state, lambda item: None)
            self.assertEqual(command.call_count, 1)
            self.assertNotIn(volume.id, state.get('loopback_chap_nodes', {}))

    def test_active_crypto_policy_is_not_bypassed(self):
        with patch.object(chap, 'regular_text', return_value=GLOBAL), \
                patch.object(chap.subprocess, 'run', side_effect=[
                    NS(returncode=0, stdout='tgt 1:1.0.80-1ubuntu2\nopen-iscsi 2.1.5-1ubuntu1.1\n'),
                    NS(returncode=1, stdout='', stderr='MD5 disabled by policy')]) as run:
            with self.assertRaisesRegex(RuntimeError, 'preserve that policy'):
                chap.compatibility_preflight(allow_md5=True)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(any(item.args[0][0] == 'iscsiadm' for item in run.call_args_list))

    def test_node_query_warning_stops_before_connector_can_recreate_record(self):
        volume, state = self.resources()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'iscsid.conf'
            path.write_text(GLOBAL)
            with patch.object(chap, 'ISCSID_CONF', path), \
                    patch.object(chap, 'compatibility_preflight'), \
                    patch.object(chap, 'assert_no_session'), \
                    patch.object(chap, 'node_command', side_effect=[
                        NS(returncode=21), NS(returncode=0), NS(returncode=0),
                        NS(returncode=0, stdout='node.session.auth.chap_algs = MD5\n',
                           stderr='test-only-private-warning')]):
                with self.assertRaisesRegex(RuntimeError, 'node query emits warnings') as error:
                    chap.prepare_node(volume, state, lambda item: None)
            self.assertNotIn('test-only-private-warning', str(error.exception))
            self.assertEqual(state['loopback_chap_nodes'][volume.id]['phase'], 'created')
            self.assertEqual(path.read_text(), GLOBAL)

    def test_active_task_target_session_prevents_node_changes(self):
        volume, _ = self.resources()
        iqn = chap.PREFIX + 'volume-' + volume.id
        with patch.object(chap.subprocess, 'run', return_value=NS(
                returncode=0, stdout='tcp: [4] 127.0.0.1:3260,1 ' + iqn + ' (non-flash)\n')):
            with self.assertRaisesRegex(RuntimeError, 'already has a host session'):
                chap.assert_no_session(iqn)

    def test_unowned_or_attached_volume_is_rejected_before_host_mutations(self):
        volume, state = self.resources()
        volume.project_id = 'another-project'
        with patch.object(chap.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                chap.prepare_node(volume, state, lambda item: None)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
