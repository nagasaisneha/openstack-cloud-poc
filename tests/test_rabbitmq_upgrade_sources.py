import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('rabbitmq_sources',
    Path(__file__).resolve().parents[1] / 'scripts/audit_rabbitmq_upgrade_sources.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class RabbitMQUpgradeSourceTests(unittest.TestCase):
    def test_revoked_expired_or_unrecognized_signatures_fail_closed(self):
        valid = '[GNUPG:] VALIDSIG subkey details ' + audit.FINGERPRINT + '\n'
        for status in ('', valid + '[GNUPG:] EXPKEYSIG expired\n', valid.replace(audit.FINGERPRINT, 'A' * 40)):
            with patch.object(audit, 'command', return_value=status):
                with self.assertRaises(RuntimeError):
                    audit.verify_signed(Path('/signature'), Path('/keyring'), Path('/private'))
        with patch.object(audit, 'command', return_value=valid):
            audit.verify_signed(Path('/signature'), Path('/keyring'), Path('/private'))

    def test_signed_repository_must_identify_the_expected_vendor_and_os(self):
        for value in ('Origin: Other\nCodename: jammy\n', 'Origin: RabbitMQ\nCodename: noble\n'):
            with self.assertRaises(RuntimeError):
                audit.index_identity(value)
        self.assertEqual(audit.index_identity('Origin: RabbitMQ\nCodename: jammy\n')['Codename'], 'jammy')

    def test_record_parser_preserves_all_versions_and_folded_dependencies(self):
        values = list(audit.records('Package: erlang-base\nVersion: 1:24.3\nDepends: libc6,\n libssl3\n\n'
                                   'Package: erlang-base\nVersion: 1:25.3\n'))
        self.assertEqual([value['Version'] for value in values], ['1:24.3', '1:25.3'])
        self.assertEqual(values[0]['Depends'], 'libc6, libssl3')

    def test_incomplete_erlang_stage_is_rejected_before_any_download(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(audit, 'fetch') as fetch:
            with self.assertRaises(RuntimeError):
                audit.download_intermediates({'repositories': {'rabbitmq-erlang': {
                    'status': 'metadata_verified', 'repository_base': 'https://example.invalid/',
                    'selected_packages': [{'Package': 'erlang-base', 'Version': '1:24.3.4.17-1'}]}}}, Path(directory))
            fetch.assert_not_called()
