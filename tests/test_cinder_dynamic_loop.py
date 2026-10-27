"""Storage identity and non-destructive migration boundaries; no live devices."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / 'scripts/local-openstack/manage_cinder_loop.py'
SPEC = importlib.util.spec_from_file_location('cinder_dynamic_loop', PATH)
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def attachment(name, image, **changes):
    return dict({'name': name, 'back-file': image, 'offset': 0, 'sizelimit': 0, 'ro': False}, **changes)


class DynamicLoopTests(unittest.TestCase):
    def test_swift_and_docker_devices_are_never_selected_for_cinder(self):
        inventory = [attachment('/dev/loop0', '/var/lib/swift-poc/swift-backend.img'),
                     attachment('/dev/loop1', '/docker/data.iso', ro=True)]
        self.assertIsNone(loop.owned_device(inventory))
        inventory.append(attachment('/dev/loop3', str(loop.IMAGE)))
        self.assertEqual(loop.owned_device(inventory), '/dev/loop3')

    def test_duplicate_image_mappings_are_rejected(self):
        with self.assertRaisesRegex(loop.GuardError, 'duplicate_attachments'):
            loop.owned_device([attachment('/dev/loop3', str(loop.IMAGE)),
                               attachment('/dev/loop4', str(loop.IMAGE))])

    def test_partial_or_readonly_image_mappings_are_rejected(self):
        for changes in ({'offset': 512}, {'sizelimit': 4096}, {'ro': True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(loop.GuardError, 'geometry_or_mode'):
                loop.owned_device([attachment('/dev/loop3', str(loop.IMAGE), **changes)])

    def test_backend_filter_accepts_only_one_validated_loop_node(self):
        self.assertIn('^/dev/loop3$', loop.lvm_filter('/dev/loop3'))
        self.assertIn('"r|.*|"', loop.lvm_filter('/dev/loop3'))
        for value in ('/dev/sda', '/dev/loop03', '/dev/loop3\n', '/dev/loop3|.*', None):
            with self.subTest(value=value), self.assertRaises(loop.GuardError):
                loop.lvm_filter(value)

    def test_existing_pv_identity_is_required_before_any_configuration_write(self):
        state = {'loop_device': '/dev/loop0', 'pv_uuid': 'original-pv'}
        with patch.object(loop, 'read_state', return_value=state), patch.object(loop, 'protected'), \
             patch.object(loop.Path, 'read_text', return_value=loop.lvm_filter('/dev/loop0')), \
             patch.object(loop.Path, 'exists', return_value=False), \
             patch.object(loop, 'inventory', return_value=[attachment('/dev/loop3', str(loop.IMAGE))]), \
             patch.object(loop, 'pv_identity', return_value={'pv_name': '/dev/loop3', 'vg_name': loop.VG,
                                                            'pv_uuid': 'foreign-pv'}), \
             patch.object(loop, 'atomic_write') as write, patch.object(loop, 'run') as run:
            with self.assertRaisesRegex(loop.GuardError, 'pv_or_vg_identity_mismatch'):
                loop.attach_existing()
            write.assert_not_called()
            run.assert_not_called()

    def test_attachment_reuses_owned_image_and_updates_only_backend_metadata(self):
        state = {'loop_device': '/dev/loop0', 'pv_uuid': 'original-pv', 'status': 'prepared'}
        with patch.object(loop, 'read_state', return_value=state), patch.object(loop, 'protected'), \
             patch.object(loop.Path, 'read_text', return_value=loop.lvm_filter('/dev/loop0')), \
             patch.object(loop.Path, 'exists', return_value=False), \
             patch.object(loop, 'inventory', return_value=[attachment('/dev/loop3', str(loop.IMAGE))]), \
             patch.object(loop, 'pv_identity', return_value={'pv_name': '/dev/loop3', 'vg_name': loop.VG,
                                                            'pv_uuid': 'original-pv'}), \
             patch.object(loop.grp, 'getgrnam', return_value=Mock(gr_gid=77)), \
             patch.object(loop, 'atomic_write') as write, patch.object(loop, 'run') as run:
            self.assertEqual(loop.attach_existing()['device'], '/dev/loop3')
            run.assert_not_called()
            self.assertEqual([call.args[0] for call in write.call_args_list], [loop.FILTER, loop.STATE])
            persisted = json.loads(write.call_args_list[1].args[1])
            self.assertEqual(persisted['pv_uuid'], 'original-pv')
            self.assertEqual(persisted['loop_device'], '/dev/loop3')

    def test_allocation_uses_nooverlap_and_never_detaches_existing_devices(self):
        state = {'loop_device': '/dev/loop0', 'pv_uuid': 'original-pv'}
        with patch.object(loop, 'read_state', return_value=state), patch.object(loop, 'protected'), \
             patch.object(loop.Path, 'read_text', return_value=loop.lvm_filter('/dev/loop0')), \
             patch.object(loop.Path, 'exists', return_value=False), \
             patch.object(loop, 'inventory', side_effect=[[], [attachment('/dev/loop3', str(loop.IMAGE))]]), \
             patch.object(loop, 'pv_identity', return_value={'pv_name': '/dev/loop3', 'vg_name': loop.VG,
                                                            'pv_uuid': 'original-pv'}), \
             patch.object(loop.grp, 'getgrnam', return_value=Mock(gr_gid=77)), \
             patch.object(loop, 'atomic_write'), patch.object(loop, 'run', return_value='/dev/loop3\n') as run:
            loop.attach_existing()
            run.assert_called_once_with(['losetup', '--find', '--show', '--nooverlap', str(loop.IMAGE)])

    def test_filter_drift_blocks_before_attachment(self):
        with patch.object(loop, 'read_state', return_value={'loop_device': '/dev/loop0'}), \
             patch.object(loop, 'protected'), patch.object(loop.Path, 'read_text', return_value='foreign config'), \
             patch.object(loop, 'inventory') as inventory:
            with self.assertRaisesRegex(loop.GuardError, 'backend_filter_drift'):
                loop.attach_existing()
            inventory.assert_not_called()

    def test_default_plan_creates_no_lock_or_runtime_files(self):
        with patch('sys.argv', ['manage_cinder_loop.py']), patch.object(loop, 'host_guard'), \
             patch.object(loop, 'migration_plan', return_value=({'action': 'plan'}, '', '')), \
             patch.object(loop.os, 'open') as open_file, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(loop.main(), 0)
            open_file.assert_not_called()


if __name__ == '__main__':
    unittest.main()
