import importlib.util
import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

PATH = Path(__file__).resolve().parents[1] / 'scripts/audit_uca_upgrade_sources.py'
SPEC = importlib.util.spec_from_file_location('audit_uca_upgrade_sources', PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class ArchiveAuditTests(unittest.TestCase):
    def test_signed_alias_cannot_be_mistaken_for_the_requested_release(self):
        metadata = b'Origin: Canonical\nCodename: jammy-updates/caracal\n'
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(audit, 'download', return_value=metadata) as download, patch.object(
                    audit.subprocess, 'run', return_value=Mock(returncode=0)):
                report = audit.inspect('jammy-updates/gazpacho', Path(directory) / 'repo')
        self.assertEqual(report['status'], 'release_identity_mismatch')
        self.assertNotIn('selected_packages', report)
        download.assert_called_once()

    def test_package_digest_uses_sha256_not_the_unrelated_md5_section(self):
        metadata = ('MD5Sum:\n abc 1 main/binary-amd64/Packages.gz\nSHA256:\n '
                    + 'a' * 64 + ' 22 main/binary-amd64/Packages.gz\nSHA512:\n')
        self.assertEqual(audit.package_digest(metadata), ('a' * 64, 22))

    def test_selected_package_parser_preserves_dependency_and_artifact_identity(self):
        packages = ('Package: python3-nova\nVersion: 33.0\nDepends: python3:any,\n python3-foo\n'
                    'Filename: pool/main/n/nova/pkg.deb\nSHA256: abc\n\nPackage: unrelated\nVersion: 1\n')
        result = audit.selected_packages(packages)
        self.assertEqual(set(result), {'python3-nova'})
        self.assertEqual(result['python3-nova']['Depends'], 'python3:any, python3-foo')
        self.assertEqual(result['python3-nova']['Filename'], 'pool/main/n/nova/pkg.deb')

    def test_missing_package_index_cannot_pass_metadata_validation(self):
        with self.assertRaises(ValueError):
            audit.package_digest('SHA256:\n abc 1 main/source/Sources.gz\n')

    def test_unverified_signature_prevents_fetching_or_reporting_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(audit, 'download', return_value=b'untrusted') as download, patch.object(
                    audit.subprocess, 'run', return_value=Mock(returncode=1)):
                report = audit.inspect('noble-updates/gazpacho', Path(directory) / 'repo')
        self.assertEqual(report['status'], 'signature_unverified')
        self.assertNotIn('selected_packages', report)
        download.assert_called_once()

    def test_signed_metadata_does_not_justify_a_mismatched_package_index(self):
        index = gzip.compress(b'Package: swift\nVersion: wrong\n')
        metadata = ('Codename: noble-updates/gazpacho\nSHA256:\n ' + 'a' * 64
                    + ' ' + str(len(index)) + ' main/binary-amd64/Packages.gz\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(audit, 'download', side_effect=[metadata, index]), patch.object(
                    audit.subprocess, 'run', return_value=Mock(returncode=0)):
                report = audit.inspect('noble-updates/gazpacho', Path(directory) / 'repo')
        self.assertEqual(report['status'], 'inspection_unavailable')
        self.assertNotIn('selected_packages', report)
