"""Safety boundaries for local Swift setup, without privileged host mutations."""
import configparser
from contextlib import contextmanager
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from openstack.identity.v3.endpoint import Endpoint

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task19_configure_swift', SCRIPTS / 'configure_swift.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class SwiftSetupTests(unittest.TestCase):
    @contextmanager
    def root_metadata(self):
        # Exercise actual mkdir/open/fchmod and umask on disposable local
        # files. Only ownership metadata is simulated for a nonroot test run.
        lstat = Path.lstat
        fstat = os.fstat
        def root(info):
            fields = list(info)
            fields[4] = fields[5] = 0
            return os.stat_result(fields)
        with patch.object(Path, 'lstat', lambda path: root(lstat(path))), \
                patch.object(os, 'fstat', lambda descriptor: root(fstat(descriptor))):
            yield

    def state(self):
        return {'run_id': '1' * 32, 'filesystem_uuid': '11111111-1111-1111-1111-111111111111',
                'files': {}, 'file_intents': {}, 'steps': []}

    def ring(self):
        return NS(devs=[{'id': 0, 'region': 1, 'zone': 1, 'ip': '127.0.0.1', 'port': 6200,
                         'replication_ip': '127.0.0.1', 'replication_port': 6200, 'device': 'poc1'}],
                  replica_count=1, _part_shift=24, _replica2part2dev_id=[[0] * 256])

    def test_apt_plan_refuses_upgrade_removal_purge_and_unreviewed_dependency(self):
        for plan in ('Inst python3-eventlet [0.30] (0.33 repo)', 'Remv nova-compute [1]',
                     'Purg cinder-volume [1]', 'Inst unrelated-daemon (1 repo)'):
            with self.assertRaises(setup.GuardError):
                setup.check_apt_plan(plan)
        self.assertEqual(setup.check_apt_plan('Inst swift (2.33 repo)\nInst python3-xattr (0.9 repo)'),
                         ['swift', 'python3-xattr'])

    def test_proxy_auth_requires_project_operator_and_unused_reseller_role(self):
        text = setup.rendered_config('proxy-server.conf.tmpl', {
            'service_password': 'PRIVATE_SENTINEL', 'unused_reseller_role': 'poc_unused_123'})
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        pipeline = parser['pipeline:main']['pipeline'].split()
        self.assertLess(pipeline.index('authtoken'), pipeline.index('keystoneauth'))
        self.assertLess(pipeline.index('keystoneauth'), pipeline.index('proxy-server'))
        self.assertNotIn('tempauth', pipeline)
        self.assertEqual(parser['filter:keystoneauth']['reseller_admin_role'], 'poc_unused_123')
        self.assertEqual(parser['filter:keystoneauth']['operator_roles'], 'admin,member')
        self.assertEqual(parser['filter:keystoneauth']['system_reader_roles'], '')
        self.assertFalse(parser.getboolean('app:proxy-server', 'allow_account_management'))
        self.assertEqual(parser['DEFAULT']['bind_ip'], '127.0.0.1')

    def test_role_ids_support_nested_keystone_role_assignment_resources(self):
        assignments = [NS(role={'id': 'admin-id'}), NS(role=NS(id='service-id')),
                       NS(role=None, role_id='legacy-id')]
        self.assertEqual(setup.role_ids_from_assignments(assignments),
                         {'admin-id', 'service-id', 'legacy-id'})

    def test_keystone_endpoint_validation_uses_region_id_resource_field(self):
        endpoint = Endpoint(interface='internal', region_id='RegionOne',
                            url=setup.API_URL, enabled=True)
        setup.validate_swift_endpoint(endpoint, 'RegionOne')
        with self.assertRaisesRegex(setup.GuardError, 'unexpected_swift_endpoint'):
            setup.validate_swift_endpoint(endpoint, 'another-region')

    def test_backends_require_mount_and_restrict_workers_and_listeners(self):
        for name, port in setup.PORTS.items():
            text = setup.rendered_config('backend-server.conf.tmpl', {'server': name, 'port': port})
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
            self.assertTrue(parser.getboolean('DEFAULT', 'mount_check'))
            self.assertEqual(parser['DEFAULT']['devices'], str(setup.DEVICES))
            self.assertEqual(parser['DEFAULT']['bind_ip'], '127.0.0.1')
            self.assertEqual(parser.getint('DEFAULT', 'bind_port'), port)
            self.assertEqual(parser.getint('DEFAULT', 'workers'), 1)
            self.assertIn('app:' + name + '-server', parser)

    def test_template_refuses_missing_substitutions(self):
        with self.assertRaises(KeyError):
            setup.rendered_config('swift.conf.tmpl', {'hash_prefix': 'placeholder'})

    def test_mount_unit_uses_only_new_image_and_has_no_local_fs_ordering_cycle(self):
        text = setup.mount_text()
        self.assertIn('What=' + str(setup.IMAGE), text)
        self.assertIn('Where=' + str(setup.MOUNT), text)
        self.assertIn('Options=loop,nodev,nosuid,noexec', text)
        self.assertNotIn('/dev/loop0', text)
        self.assertNotIn('After=local-fs.target', text)
        self.assertEqual(setup.MOUNT_UNIT, r'srv-swift\x2dpoc-poc1.mount')

    def test_protected_files_reject_symlink_wrong_owner_or_public_mode(self):
        for mode, uid in ((stat.S_IFLNK | 0o600, 0), (stat.S_IFREG | 0o600, 1000),
                          (stat.S_IFREG | 0o644, 0)):
            path = Mock(lstat=Mock(return_value=NS(st_mode=mode, st_uid=uid, st_gid=0)))
            with self.assertRaises(setup.GuardError):
                setup.protected_file(path)

    def test_new_directories_receive_exact_mode_under_protective_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.umask(0o077)
            try:
                with self.root_metadata():
                    for mode in (0o700, 0o750, 0o755):
                        path = Path(directory) / str(mode)
                        setup.safe_directory(path, mode)
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                        # Existing correct directories need no permission write.
                        with patch.object(os, 'fchmod') as chmod:
                            setup.safe_directory(path, mode)
                            chmod.assert_not_called()
            finally:
                os.umask(previous)

    def test_existing_wrong_mode_directory_is_not_widened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing'
            path.mkdir(mode=0o700)
            with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
                with self.assertRaises(setup.GuardError) as raised:
                    setup.safe_directory(path, 0o755)
                self.assertEqual(raised.exception.context['directory'], str(path))
                self.assertEqual(raised.exception.context['observed_mode'], '0700')
                chmod.assert_not_called()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_symlink_directory_does_not_change_target_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.mkdir(mode=0o700)
            link = Path(directory) / 'link'
            link.symlink_to(target, target_is_directory=True)
            with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
                with self.assertRaises(setup.GuardError):
                    setup.safe_directory(link, 0o755)
                chmod.assert_not_called()
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_directory_owned_by_another_user_is_not_opened_or_changed(self):
        path = Mock()
        path.mkdir.side_effect = FileExistsError()
        path.lstat.return_value = NS(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)
        with patch.object(os, 'open') as opened, patch.object(os, 'fchmod') as chmod:
            with self.assertRaises(setup.GuardError):
                setup.safe_directory(path, 0o755)
            opened.assert_not_called()
            chmod.assert_not_called()

    @contextmanager
    def umask_repair_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            devices = root / 'swift-poc'
            devices.mkdir(mode=0o700)
            image = root / 'image'
            image.write_bytes(b'\0' * 4096)
            image.chmod(0o600)
            report = root / ('swift-readiness-' + '1' * 32 + '.json')
            report.write_text(json.dumps({'installation': {'status': 'ready_for_guarded_setup'},
                                         'host': {'paths': {str(devices): {'present': False}}}}))
            report.chmod(0o600)
            state = dict(self.state(), phase='mount_and_xattrs', steps=['packages', 'filesystem'],
                         readiness_report=str(report), filesystem_verified=True, format_intent=True,
                         image_created=True, image_create_intent=True,
                         failure={'reason': 'unsafe_directory_metadata', 'stage': 'mount_and_xattrs'})
            with patch.object(setup, 'DATA', root), patch.object(setup, 'DEVICES', devices), \
                    patch.object(setup, 'MOUNT', devices / 'poc1'), patch.object(setup, 'IMAGE', image), \
                    patch.object(setup, 'SIZE', 4096), patch.object(setup, 'MOUNT_UNIT_PATH', root / 'unit.mount'), \
                    patch.object(setup, 'filesystem_signature', return_value={
                        'TYPE': 'ext4', 'LABEL': 'swift-poc', 'UUID': state['filesystem_uuid']}), \
                    patch.object(setup.common, 'run', return_value=''), \
                    patch.object(setup.subprocess, 'run', return_value=NS(returncode=1, stdout='')), \
                    patch.object(setup, 'save'), self.root_metadata():
                yield state, devices, report

    def test_recorded_empty_premount_directory_can_be_corrected_once(self):
        with self.umask_repair_fixture() as (state, devices, report):
            snapshots = []
            with patch.object(setup, 'save', side_effect=lambda value: snapshots.append(copy.deepcopy(value))):
                setup.repair_umask_directory(state)
            self.assertEqual(stat.S_IMODE(devices.stat().st_mode), 0o755)
            self.assertEqual(snapshots[0]['umask_directory_repair']['phase'], 'intent')
            self.assertEqual(state['umask_directory_repair']['phase'], 'applied')
            # Repeating the same command after successful continuation performs
            # no chmod and requires no empty-directory adoption.
            state['phase'] = 'service_ready_pending_object_acceptance'
            (devices / 'poc1').mkdir()
            with patch.object(os, 'fchmod') as chmod:
                setup.repair_umask_directory(state)
                chmod.assert_not_called()

    def test_repair_refuses_nonempty_or_preexisting_directory(self):
        for scenario in ('nonempty', 'preexisting', 'different_failure', 'missing_format_intent', 'already_widened'):
            with self.umask_repair_fixture() as (state, devices, report):
                if scenario == 'nonempty':
                    (devices / 'preserved-data').write_text('keep')
                elif scenario == 'preexisting':
                    data = json.loads(report.read_text())
                    data['host']['paths'][str(devices)]['present'] = True
                    report.write_text(json.dumps(data))
                elif scenario == 'different_failure':
                    state['failure']['reason'] = 'different'
                elif scenario == 'missing_format_intent':
                    state['format_intent'] = False
                else:
                    devices.chmod(0o755)
                before = stat.S_IMODE(devices.stat().st_mode)
                with patch.object(os, 'fchmod') as chmod:
                    with self.assertRaises(setup.GuardError):
                        setup.repair_umask_directory(state)
                    chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(devices.stat().st_mode), before)

    def test_repair_refuses_attached_image_or_existing_mount_unit(self):
        with self.umask_repair_fixture() as (state, devices, report):
            with patch.object(setup.common, 'run', return_value='loop device already attached'), \
                    patch.object(os, 'fchmod') as chmod:
                with self.assertRaisesRegex(setup.GuardError, 'image_already_attached'):
                    setup.repair_umask_directory(state)
                chmod.assert_not_called()
        with self.umask_repair_fixture() as (state, devices, report):
            setup.MOUNT_UNIT_PATH.write_text('existing')
            with patch.object(os, 'fchmod') as chmod:
                with self.assertRaisesRegex(setup.GuardError, 'mount_unit_already_exists'):
                    setup.repair_umask_directory(state)
                chmod.assert_not_called()

    def test_interrupted_directory_repair_reuses_its_recorded_inode_and_failure(self):
        with self.umask_repair_fixture() as (state, devices, report):
            info = devices.stat()
            state['umask_directory_repair'] = {
                'directory': str(devices), 'device': info.st_dev, 'inode': info.st_ino, 'phase': 'intent',
                'original_failure': state['failure'].copy(), 'readiness_report_sha256': setup.digest_file(report)}
            state['failure'] = {'stage': 'owned_directory_resume', 'reason': 'interrupted'}
            # Model termination after fchmod but before the applied record.
            devices.chmod(0o755)
            setup.repair_umask_directory(state)
            self.assertEqual(state['umask_directory_repair']['phase'], 'applied')
            self.assertEqual(stat.S_IMODE(devices.stat().st_mode), 0o755)

    def test_interrupted_repair_refuses_a_changed_preflight_report(self):
        with self.umask_repair_fixture() as (state, devices, report):
            info = devices.stat()
            state['umask_directory_repair'] = {
                'directory': str(devices), 'device': info.st_dev, 'inode': info.st_ino, 'phase': 'intent',
                'original_failure': state['failure'].copy(), 'readiness_report_sha256': setup.digest_file(report)}
            report.write_text(report.read_text() + '\n')
            with patch.object(os, 'fchmod') as chmod:
                with self.assertRaisesRegex(setup.GuardError, 'readiness_report_changed'):
                    setup.repair_umask_directory(state)
                chmod.assert_not_called()

    def test_new_file_records_intent_before_write_and_detects_later_drift(self):
        state = self.state()
        intents = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'owned.conf'
            def record(value):
                intents.append(copy.deepcopy(value))
            with patch.object(setup, 'save', side_effect=record), patch.object(os, 'fchown'):
                setup.write_owned(path, 'expected\n', state, mode=0o600)
            self.assertIn(str(path), intents[0]['file_intents'])
            self.assertEqual(path.read_text(), 'expected\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with patch.object(setup, 'save'), patch.object(setup, 'protected_file'):
                setup.write_owned(path, 'expected\n', state, mode=0o600)
                path.write_text('changed externally\n')
                with self.assertRaises(setup.GuardError):
                    setup.write_owned(path, 'expected\n', state, mode=0o600)
            self.assertEqual(path.read_text(), 'changed externally\n')

    def test_existing_unowned_file_is_preserved_even_if_contents_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.conf'
            path.write_text('matching\n')
            with patch.object(setup, 'protected_file'), patch.object(setup, 'save'):
                with self.assertRaisesRegex(setup.GuardError, 'not_owned') as raised:
                    setup.write_owned(path, 'matching\n', self.state())
            self.assertEqual(raised.exception.context['path'], str(path))
            self.assertTrue(raised.exception.context['expected_content_matches'])
            self.assertFalse(raised.exception.context['recorded_file_matches_expected'])
            self.assertFalse(raised.exception.context['recorded_intent_matches_expected'])
            self.assertNotIn('sha256', repr(raised.exception.context))
            self.assertEqual(path.read_text(), 'matching\n')

    def test_interrupted_owned_write_is_adopted_only_with_matching_intent(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.conf'
            path.write_text('owned\n')
            state['file_intents'][str(path)] = setup.digest_file(path)
            with patch.object(setup, 'protected_file'), patch.object(setup, 'save'):
                setup.write_owned(path, 'owned\n', state)
            self.assertEqual(state['file_intents'], {})
            self.assertIn(str(path), state['files'])

    def test_filesystem_signature_must_match_type_label_and_uuid(self):
        signature = {'TYPE': 'ext4', 'LABEL': 'swift-poc', 'UUID': self.state()['filesystem_uuid']}
        setup.verify_signature(signature, self.state())
        for key in signature:
            changed = dict(signature, **{key: 'different'})
            with self.assertRaises(setup.GuardError):
                setup.verify_signature(changed, self.state())

    def test_previous_unverified_format_intent_is_not_replayed(self):
        state = dict(self.state(), image_create_intent=True, format_intent=True)
        with patch.object(setup, 'safe_directory'), patch.object(Path, 'exists', return_value=True):
            with patch.object(setup, 'protected_file', return_value=NS(st_size=setup.SIZE)), \
                    patch.object(setup, 'filesystem_signature', return_value={}), \
                    patch.object(setup.common, 'run') as run:
                with self.assertRaisesRegex(setup.GuardError, 'previous_format_unverified'):
                    setup.prepare_filesystem(state)
                run.assert_not_called()

    def test_existing_filesystem_with_wrong_uuid_is_never_formatted(self):
        state = dict(self.state(), image_create_intent=True)
        with patch.object(setup, 'safe_directory'), patch.object(Path, 'exists', return_value=True), \
                patch.object(setup, 'protected_file', return_value=NS(st_size=setup.SIZE)), \
                patch.object(setup, 'filesystem_signature', return_value={'TYPE': 'ext4', 'UUID': 'wrong'}), \
                patch.object(setup.common, 'run') as run:
            with self.assertRaisesRegex(setup.GuardError, 'unexpected_filesystem'):
                setup.prepare_filesystem(state)
            run.assert_not_called()

    def test_mount_refuses_loop_device_backed_by_cinder_or_wrong_options(self):
        output = {'filesystems': [{'source': '/dev/loop0', 'fstype': 'ext4',
                                  'options': 'rw,nodev,nosuid,noexec'}]}
        result = NS(returncode=0, stdout=json.dumps(output))
        with patch.object(setup.subprocess, 'run', return_value=result), \
                patch.object(setup.common, 'run', return_value=json.dumps({'loopdevices': [
                    {'name': '/dev/loop0', 'back-file': '/var/lib/cinder-poc/cinder-backend.img'}]})):
            with self.assertRaisesRegex(setup.GuardError, 'not_backed_by_owned'):
                setup.mounted_image()
        output['filesystems'][0]['source'] = '/dev/loop1'
        output['filesystems'][0]['options'] = 'rw'
        result.stdout = json.dumps(output)
        with patch.object(setup.subprocess, 'run', return_value=result), \
                patch.object(setup.common, 'run', return_value=json.dumps({'loopdevices': [
                    {'name': '/dev/loop1', 'back-file': str(setup.IMAGE)}]})):
            with self.assertRaisesRegex(setup.GuardError, 'options_not_enforced'):
                setup.mounted_image()

    def test_ring_rejects_foreign_device_replica_count_and_partition_assignment(self):
        setup.validate_ring(self.ring(), 'object')
        for field, value in (('replica_count', 3), ('_part_shift', 22), ('_replica2part2dev_id', [[1] * 256])):
            ring = self.ring()
            setattr(ring, field, value)
            with self.assertRaises(setup.GuardError):
                setup.validate_ring(ring, 'object')
        ring = self.ring()
        ring.devs[0]['ip'] = '192.0.2.1'
        with self.assertRaises(setup.GuardError):
            setup.validate_ring(ring, 'object')

    def test_foreign_identity_stops_before_password_role_or_catalog_changes(self):
        identity = NS(find_user=Mock(return_value=NS(description='another deployment', is_enabled=True)),
                      create_user=Mock(), assign_project_role_to_user=Mock(), create_service=Mock())
        with self.assertRaisesRegex(setup.GuardError, 'identity_not_owned'):
            setup.ensure_identity(NS(identity=identity), {}, self.state())
        identity.create_user.assert_not_called()
        identity.assign_project_role_to_user.assert_not_called()
        identity.create_service.assert_not_called()

    def test_fresh_setup_refuses_swift_name_used_for_another_service_type(self):
        report = setup.DATA / ('swift-readiness-' + '1' * 32 + '.json')
        cloud = NS(identity=NS(find_user=Mock(return_value=None),
                               services=Mock(return_value=[NS(type='image', name='swift')])))
        with patch.object(Path, 'exists', return_value=False), patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'glob', return_value=[]), \
                patch.object(setup, 'protected_json', return_value={'installation': {'status': 'ready_for_guarded_setup'}}), \
                patch.object(setup, 'fresh_observations'), \
                patch.object(setup.preflight, 'installation_readiness', return_value={'status': 'ready_for_guarded_setup'}), \
                patch.object(setup.observations, 'systemd_observation', return_value={'load': 'not-found'}), \
                patch.object(setup, 'save') as save:
            with self.assertRaisesRegex(setup.GuardError, 'existing_swift_identity_or_catalog'):
                setup.begin_or_load(report, cloud)
            save.assert_not_called()

    def test_config_drift_stops_before_service_unmask_or_start(self):
        with patch.object(setup, 'verify_owned_files', side_effect=setup.GuardError('owned_file_drifted')), \
                patch.object(setup.common, 'run') as run:
            with self.assertRaises(setup.GuardError):
                setup.start_services({}, self.state())
            run.assert_not_called()

    def test_stopped_retained_guest_is_preserved_without_start(self):
        state = dict(self.state(), preserved_configuration={})
        cloud = NS(compute=NS(get_server=Mock(return_value=NS(status='SHUTOFF', task_state=None))))
        with patch.object(setup, 'preserve_snapshot', return_value={}), \
                patch.object(setup.observations, 'systemd_observation', return_value={'active': 'active'}), \
                patch.object(setup, 'protected_json', return_value={'ids': {'server': 'retained'}}), \
                patch.object(setup.common, 'run') as run:
            with self.assertRaisesRegex(setup.GuardError, 'retained_guest_not_active'):
                setup.check_core(cloud, state)
            run.assert_not_called()

    def test_main_refuses_nonroot_before_credentials_or_commands(self):
        with patch.object(sys, 'argv', ['configure_swift.py', '--readiness-report', '/root/report']), \
                patch.object(os, 'geteuid', return_value=1000), \
                patch.object(setup.common, 'read_secrets') as read, \
                patch.object(setup.common, 'run') as run, patch.object(sys, 'stderr', io.StringIO()):
            self.assertEqual(setup.main(), 1)
            read.assert_not_called()
            run.assert_not_called()

    def test_swift_unix_account_cannot_be_root_or_change_uid(self):
        with patch.object(setup.pwd, 'getpwnam', return_value=NS(pw_uid=0, pw_gid=123, pw_shell='/usr/sbin/nologin')), \
                patch.object(setup.grp, 'getgrnam', return_value=NS(gr_gid=123)), patch.object(setup, 'save'):
            with self.assertRaisesRegex(setup.GuardError, 'unexpected_swift_unix_account'):
                setup.verify_swift_account(self.state())
        with patch.object(setup.pwd, 'getpwnam', return_value=NS(pw_uid=123, pw_gid=123, pw_shell='/usr/sbin/nologin')), \
                patch.object(setup.grp, 'getgrnam', return_value=NS(gr_gid=123)), patch.object(setup, 'save'):
            with self.assertRaisesRegex(setup.GuardError, 'identity_changed'):
                setup.verify_swift_account(dict(self.state(), unix_uid=456))

    def test_exposed_listener_stops_only_owned_swift_units_without_retry(self):
        state = self.state()
        with patch.object(setup, 'verify_owned_files'), patch.object(setup, 'mounted_image', return_value=True), \
                patch.object(setup.common, 'run') as run, patch.object(setup, 'save'), \
                patch.object(setup.observations, 'systemd_observation', return_value={'load': 'masked'}), \
                patch.object(setup, 'service_health', side_effect=setup.GuardError('swift_listener_exposed')), \
                patch.object(setup.time, 'sleep') as sleep:
            with self.assertRaisesRegex(setup.GuardError, 'swift_listener_exposed'):
                setup.start_services({}, state)
            stop = run.call_args_list[-1].args[0]
            self.assertEqual(stop[:2], ['systemctl', 'stop'])
            self.assertEqual(set(stop[2:]), set(setup.ACTIVE_UNITS))
            self.assertIn('containment', state)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
