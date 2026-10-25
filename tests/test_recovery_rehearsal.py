"""Recovery isolation, real offline copies, and opt-in private daemon round trips."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_recovery_rehearsal', SCRIPTS / 'rehearse_recovery.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class RecoveryRehearsalTests(unittest.TestCase):
    def test_non_root_stops_before_reading_recovery_files_or_starting_any_process(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), patch.object(setup, 'source_bundle') as source, \
             patch.object(setup.subprocess, 'Popen') as process, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(), 1)
        source.assert_not_called()
        process.assert_not_called()

    def test_restored_sqlite_is_independent_and_preserves_exact_backup_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite'
            target = Path(directory) / 'restored.sqlite'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT)')
                db.execute("INSERT INTO jobs VALUES ('private-job', 'succeeded')")
            digest = setup.backup.checksum(source)
            result = setup.restore_journal(source, target)
            self.assertTrue(result['exact_table_contents'])
            self.assertEqual(result['jobs'], 1)
            with sqlite3.connect(target) as db:
                db.execute("UPDATE jobs SET state='changed-copy'")
            self.assertEqual(setup.backup.checksum(source), digest)
            with self.assertRaises(FileExistsError):
                setup.restore_journal(source, target)

    def archive(self, path, members):
        with tarfile.open(path, 'w:gz') as archive:
            for name, payload, kind in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
                else:
                    member.linkname = payload.decode()
                    archive.addfile(member)

    def test_archive_restores_private_files_without_following_links_or_installing_live_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'archive.tar.gz'
            self.archive(source, [('etc/service.conf', b'private sentinel', tarfile.REGTYPE),
                ('etc/outside', b'/unrelated/host/path', tarfile.SYMTYPE),
                ('etc/outside/child.conf', b'isolated child', tarfile.REGTYPE)])
            target = setup.subdirectory(root, 'restored')
            digest = setup.backup.checksum(source)
            result = setup.restore_archive(source, target)
            self.assertEqual((target / 'etc/service.conf').read_bytes(), b'private sentinel')
            self.assertEqual((target / 'etc/service.conf').stat().st_mode & 0o777, 0o600)
            self.assertFalse((target / 'etc/outside').is_symlink())
            self.assertEqual((target / 'etc/outside/child.conf').read_bytes(), b'isolated child')
            self.assertEqual(result['links_recorded_not_followed'], 1)
            self.assertEqual(setup.backup.checksum(source), digest)

    def test_archive_rejects_traversal_devices_and_duplicates_before_any_write(self):
        for unsafe in ('../../live.conf', '/etc/live.conf', 'etc/../live.conf', 'etc//live.conf', 'etc/./live.conf'):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'archive.tar.gz'
                self.archive(source, [('safe', b'first', tarfile.REGTYPE), (unsafe, b'bad', tarfile.REGTYPE)])
                target = setup.subdirectory(root, 'restored')
                with self.assertRaises(setup.RehearsalError):
                    setup.restore_archive(source, target)
                self.assertEqual(list(target.iterdir()), [])
        for kind in (tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'archive.tar.gz'
                self.archive(source, [('unsafe', b'', kind)])
                with self.assertRaises(setup.RehearsalError):
                    setup.restore_archive(source, setup.subdirectory(root, 'restored'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'archive.tar.gz'
            self.archive(source, [('same', b'one', tarfile.REGTYPE), ('same', b'two', tarfile.REGTYPE)])
            with self.assertRaises(setup.RehearsalError):
                setup.restore_archive(source, setup.subdirectory(root, 'restored'))

    @unittest.skipUnless(shutil.which('qemu-img'), 'offline qemu-img unavailable')
    def test_real_disk_round_trip_preserves_bytes_and_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'raw'
            with raw.open('xb') as stream:
                stream.truncate(1024 ** 3)
                stream.seek(8192)
                stream.write(b'restored nonzero bytes')
            source = root / 'backup.qcow2'
            subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2', str(raw), str(source)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            digest = setup.backup.checksum(source)
            target = setup.restore_disk(source, root)
            restored = root / 'roundtrip.raw'
            subprocess.run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', str(target), str(restored)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with restored.open('rb') as stream:
                stream.seek(8192)
                self.assertEqual(stream.read(22), b'restored nonzero bytes')
            self.assertEqual(setup.backup.checksum(source), digest)
            with self.assertRaises(setup.RehearsalError):
                setup.restore_disk(source, root)

    def test_failed_command_keeps_sensitive_output_only_in_private_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'DIAGNOSTIC_ROOT', Path(directory)), \
             patch.object(setup.subprocess, 'run', return_value=Mock(returncode=1,
                stdout='PRIVATE_SQL_SENTINEL', stderr='PRIVATE_CREDENTIAL_SENTINEL')):
            with self.assertRaises(setup.RehearsalError) as error:
                setup.run(['mariadb', '--no-defaults'])
            self.assertNotIn('PRIVATE_', str(error.exception))
            diagnostics = list(Path(directory).glob('command-failure-*.log'))
            self.assertEqual(len(diagnostics), 1)
            self.assertIn('PRIVATE_CREDENTIAL_SENTINEL', diagnostics[0].read_text())
            self.assertEqual(diagnostics[0].stat().st_mode & 0o777, 0o600)

    def test_failure_stops_exact_owned_child_with_bounded_kill_fallback(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('owned', 15), 0]
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.subprocess, 'Popen', return_value=process) as start:
            with self.assertRaisesRegex(ValueError, 'test failure'):
                with setup.child(['owned-child'], Path(directory)):
                    raise ValueError('test failure')
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual([call.kwargs['timeout'] for call in process.wait.call_args_list], [15, 10])
        self.assertTrue(start.call_args.kwargs['start_new_session'])

    def test_socket_targets_are_explicit_private_paths_and_guest_has_no_network(self):
        root = Path('/var/tmp/poc-test-scope')
        client = setup.sql_client(root)
        self.assertEqual(client[1], '--no-defaults')
        self.assertIn('--socket=' + str(root / 'sql.sock'), client)
        ovs = setup.ovs_server(root, root / 'restored.db')
        self.assertIn('--remote=punix:' + str(root / 'db.sock'), ovs)
        self.assertFalse(any('db:' in value or 'ptcp:' in value for value in ovs))
        command = setup.qemu_command(root, 'new-id')
        self.assertEqual(command[command.index('-nic') + 1], 'none')
        self.assertIn('-nodefaults', command)
        self.assertIn('-no-user-config', command)
        self.assertNotIn('-netdev', command)
        self.assertNotIn(str(setup.launch.DATA_DIR), ' '.join(command))

    def test_ovs_restore_rejects_changed_table_counts_and_stops_both_servers(self):
        @contextlib.contextmanager
        def process(*args, **kwargs):
            yield Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.db'
            source.write_text('synthetic snapshot')
            with patch.object(setup, 'child', side_effect=process) as start, \
                 patch.object(setup, 'wait_ready'), patch.object(setup, 'run',
                    side_effect=[json.dumps({'name': 'Test', 'tables': {}}), '', '']), \
                 patch.object(setup, 'ovs_counts', side_effect=[{'table': 3}, {'table': 2}]):
                with self.assertRaisesRegex(setup.RehearsalError, 'table counts differ'):
                    setup.restore_ovs(source, 'Test', setup.subdirectory(root, 'isolated'))
            self.assertEqual(start.call_count, 2)

    def test_source_is_bound_to_exact_backup_and_completed_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / setup.SOURCE_BUNDLE
            application = {'ids': {'server': setup.SOURCE_SERVER}, 'results': {
                'application_lifecycle': {'phase': 'deleted', 'verified': True, 'backup_directory': str(source)},
                'dependency_cleanup': {'verified': True, 'done': [0, 1], 'plan': ['a', 'b']}}}
            (root / 'application.json').write_text(json.dumps(application))
            with patch.object(setup.launch, 'DATA_DIR', root), patch.object(setup.guest, 'protected_file'), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)), \
                 patch.object(setup.backup, 'verify_bundle') as verify:
                self.assertEqual(setup.source_bundle(), source)
                verify.assert_called_once_with(source, setup.SOURCE_SERVER)
                verify.reset_mock()
                for field, value in (('backup_directory', '/unrelated/backup'), ('phase', 'started'), ('verified', False)):
                    changed = json.loads(json.dumps(application))
                    changed['results']['application_lifecycle'][field] = value
                    (root / 'application.json').write_text(json.dumps(changed))
                    with self.assertRaises(setup.RehearsalError):
                        setup.source_bundle()
                application['results']['dependency_cleanup']['done'] = [0]
                (root / 'application.json').write_text(json.dumps(application))
                with self.assertRaises(setup.RehearsalError):
                    setup.source_bundle()
                verify.assert_not_called()


@unittest.skipUnless(os.environ.get('POC_ISOLATED_DAEMON_TESTS') == '1',
                     'opt-in private daemon round trips; no live service access')
class IsolatedDaemonRecoveryTests(unittest.TestCase):
    def test_actual_seven_database_import_in_a_private_datadir(self):
        with tempfile.TemporaryDirectory(prefix='poc-test-sql-') as directory:
            root = Path(directory)
            source = root / 'sample.sql'
            source.write_text('\n'.join(f'CREATE DATABASE `{name}`; USE `{name}`; CREATE TABLE evidence (value INT); INSERT INTO evidence VALUES (42);'
                                        for name in setup.backup.DATABASES))
            digest = setup.backup.checksum(source)
            result = setup.restore_sql(source, setup.subdirectory(root, 'sql'))
            self.assertEqual(result['databases'], dict.fromkeys(setup.backup.DATABASES, 1))
            self.assertTrue(result['child_stopped'])
            self.assertEqual(setup.backup.checksum(source), digest)

    def test_actual_ovsdb_restore_into_an_empty_isolated_database(self):
        with tempfile.TemporaryDirectory(prefix='poc-test-ovs-') as directory:
            root = Path(directory)
            schema = {'name': 'POC_Recovery', 'version': '1.0.0', 'tables': {
                'Evidence': {'isRoot': True, 'columns': {'value': {'type': 'string'}}}}}
            schema_file = root / 'schema.json'
            schema_file.write_text(json.dumps(schema))
            source = root / 'snapshot.db'
            setup.run(['ovsdb-tool', 'create', str(source), str(schema_file)])
            setup.run(['ovsdb-tool', 'transact', str(source), json.dumps(['POC_Recovery',
                dict(op='insert', table='Evidence', row={'value': 'nonempty recovery evidence'})])])
            digest = setup.backup.checksum(source)
            result = setup.restore_ovs(source, 'POC_Recovery', setup.subdirectory(root, 'restore'))
            self.assertEqual(result['rows'], 1)
            self.assertTrue(result['children_stopped'])
            self.assertEqual(setup.backup.checksum(source), digest)


if __name__ == '__main__':
    unittest.main()
