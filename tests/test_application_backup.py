"""Recovery safeguards and real offline disk conversion; no VM/cloud calls."""
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_application_backup', SCRIPTS / 'backup_application.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApplicationBackupTests(unittest.TestCase):
    def test_disk_selection_rejects_foreign_paths_extra_disks_and_nvram(self):
        server_id = str(uuid4())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'INSTANCES_ROOT', Path(directory)):
            folder = Path(directory) / server_id
            folder.mkdir()
            disk = folder / 'disk'
            disk.write_bytes(b'owned test data')
            xml = f'<domain type="kvm"><uuid>{server_id}</uuid><os/><devices><disk device="disk" type="file"><source file="{disk}"/></disk></devices></domain>'
            self.assertEqual(setup.disk_from_xml(xml, server_id)[0], disk)
            for changed in (xml.replace(str(disk), '/unrelated/disk'), xml.replace(server_id, str(uuid4()), 1),
                            xml.replace('<os/>', '<os><nvram>/unrelated/vars</nvram></os>'),
                            xml.replace('</devices>', '<disk device="cdrom" type="file"/></devices>')):
                with self.assertRaises(RuntimeError):
                    setup.disk_from_xml(changed, server_id)

    @unittest.skipUnless(shutil.which('qemu-img'), 'offline qemu-img utility unavailable')
    def test_real_offline_flatten_preserves_nonzero_guest_bytes_without_backing_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'INSTANCES_ROOT', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            root = Path(directory)
            base = root / 'base.raw'
            marker = b'nonzero protected guest test data'
            with base.open('xb') as stream:
                stream.truncate(1024 ** 3)
                stream.seek(1234)
                stream.write(marker)
            disk = root / 'disk'
            subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(base), str(disk)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            saved = root / 'saved.qcow2'
            self.assertEqual(setup.flatten_disk(disk, saved), 'qcow2')
            info = json.loads(subprocess.check_output(['qemu-img', 'info', '--output=json', str(saved)]))
            self.assertNotIn('backing-filename', info)
            restored = root / 'restored.raw'
            subprocess.run(['qemu-img', 'convert', '-O', 'raw', str(saved), str(restored)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with restored.open('rb') as stream:
                stream.seek(1234)
                self.assertEqual(stream.read(len(marker)), marker)
            with self.assertRaisesRegex(RuntimeError, 'never overwrite'):
                setup.flatten_disk(disk, saved)

    def test_sqlite_backup_api_produces_independent_integrity_checked_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            source = Path(directory) / 'application-jobs.sqlite'
            with sqlite3.connect(source) as database:
                database.execute('CREATE TABLE evidence (value TEXT)')
                database.execute("INSERT INTO evidence VALUES ('before')")
            output = Path(directory) / 'snapshot.sqlite'
            setup.snapshot_journal(output)
            with sqlite3.connect(source) as database:
                database.execute("UPDATE evidence SET value='after'")
            with sqlite3.connect(output) as database:
                self.assertEqual(database.execute('SELECT value FROM evidence').fetchone(), ('before',))
                self.assertEqual(database.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_nontransactional_database_prevents_dump_and_ovsdb_backup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.common, 'sql', side_effect=[
                '\n'.join(setup.DATABASES), 'nontransactional_table']), patch.object(setup, 'command_to_file') as run:
            with self.assertRaisesRegex(RuntimeError, 'nontransactional'):
                setup.snapshot_databases(Path(directory))
            run.assert_not_called()

    def test_failed_backup_preserves_private_diagnostic_without_echoing_its_contents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.subprocess, 'run',
                return_value=NS(returncode=1, stderr=b'private diagnostic sentinel')):
            output = Path(directory) / 'backup.sql'
            with self.assertRaises(RuntimeError) as error:
                setup.command_to_file(['mariadb-dump'], output)
            self.assertNotIn('private diagnostic sentinel', str(error.exception))
            self.assertEqual((Path(directory) / 'backup.sql.stderr.log').read_bytes(), b'private diagnostic sentinel')
            self.assertTrue(output.exists())

    def test_operational_archive_preserves_secrets_privately_and_excludes_active_db_contents(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.guest, 'protected_file'):
            root = Path(directory)
            config = root / 'config'
            config.mkdir()
            (config / 'service.conf').write_text('nonsecret test configuration')
            (config / 'active.db').write_text('must not copy an active database')
            (config / 'db-link').symlink_to(config / 'active.db')
            (root / 'run.json').write_text('{}')
            secret = root / 'secrets'
            secret.write_text('sample private file')
            with patch.object(setup.launch, 'DATA_DIR', root), patch.object(setup, 'DATA_FILES', ('run.json',)), \
                 patch.object(setup.common, 'SECRET_FILE', secret), patch.object(setup, 'CONFIGS', (str(config),)):
                output = root / 'operational.tar.gz'
                setup.snapshot_operational_files(output)
            with tarfile.open(output) as archive:
                self.assertNotIn(str(config / 'active.db').lstrip('/'), archive.getnames())
                self.assertTrue(archive.getmember(str(config / 'db-link').lstrip('/')).issym())
                self.assertEqual(archive.extractfile(str(secret).lstrip('/')).read(), b'sample private file')
            setup.sync_bundle(root)  # Real file/directory fsync on owned temporary artifacts.

    def test_running_guest_guard_prevents_even_creating_backup_directory(self):
        state = {'ids': {'server': str(uuid4())}}
        with patch.object(setup.tempfile, 'mkdtemp') as create:
            def running():
                raise RuntimeError('guest still running')
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                setup.create_backup(state, None, running)
            create.assert_not_called()

    def test_bundle_rejects_missing_artifacts_or_changed_bytes(self):
        server_id = str(uuid4())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            folder = Path(directory) / ('recovery-' + server_id + '-test')
            folder.mkdir(mode=0o700)
            required = {'domain.xml', 'guest-root.qcow2', 'application-jobs.sqlite', 'cloud-databases.sql',
                        'operational-files.tar.gz', 'RECOVERY.md'} | {entry[0] for entry in setup.OVSDB}
            for name in required:
                (folder / name).write_bytes(b'protected artifact')
            record = {'server_id': server_id, 'verified': True, 'disk_check': True, 'disk_compare': True,
                      'sha256': {name: setup.checksum(folder / name) for name in required}}
            (folder / 'recovery.json').write_text(json.dumps(record))
            with patch.object(type(folder), 'lstat', return_value=NS(st_mode=stat.S_IFDIR | 0o700, st_uid=0)):
                setup.verify_bundle(folder, server_id)
                (folder / 'domain.xml').write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                    setup.verify_bundle(folder, server_id)
                record['sha256'] = {}
                (folder / 'recovery.json').write_text(json.dumps(record))
                with self.assertRaisesRegex(RuntimeError, 'lacks required'):
                    setup.verify_bundle(folder, server_id)


if __name__ == '__main__':
    unittest.main()
