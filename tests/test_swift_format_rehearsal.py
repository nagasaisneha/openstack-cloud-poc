import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('swift_format_rehearsal',
    Path(__file__).resolve().parents[1] / 'scripts/rehearse_swift_upgrade.py')
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)


class SwiftFormatRehearsalTests(unittest.TestCase):
    def test_component_worker_refuses_live_or_unowned_workspace_before_importing_swift(self):
        for path in ('/srv/swift-poc/poc1', '/tmp/not-owned/source-copy', '/var/lib/swift-poc'):
            with self.assertRaises(RuntimeError):
                rehearsal.worker(Path(path), 'write_source')

    def test_worker_root_rejects_symlink_and_loose_permissions(self):
        with tempfile.TemporaryDirectory(prefix='poc-swift-format-recovery-', dir='/tmp') as directory:
            root = Path(directory)
            rehearsal.check_worker_root(root / 'source-copy')
            (root / 'source-copy').symlink_to('/tmp')
            with self.assertRaises(RuntimeError):
                rehearsal.check_worker_root(root / 'source-copy')
            root.chmod(0o755)
            with self.assertRaises(RuntimeError):
                rehearsal.check_worker_root(root / 'restored-copy')

    def test_recovery_digest_checks_extended_attributes_as_well_as_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'object'
            path.write_bytes(b'object-contents')
            before = rehearsal.tree_digest(root)
            os.setxattr(path, 'user.swift-test', b'first-metadata')
            changed = rehearsal.tree_digest(root)
            self.assertNotEqual(before['sha256'], changed['sha256'])
            self.assertEqual(changed['extended_attributes'], 1)
            os.setxattr(path, 'user.swift-test', b'changed-metadata')
            self.assertNotEqual(changed['sha256'], rehearsal.tree_digest(root)['sha256'])
