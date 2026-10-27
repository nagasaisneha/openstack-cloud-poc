import base64
import configparser
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('poc_console', SCRIPTS / 'configure_poc_console.py')
    console = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(console)
finally:
    sys.path.remove(str(SCRIPTS))

VHOST = (Path(__file__).resolve().parents[1] / 'deployment/horizon-local/horizon-loopback-ssl.conf').read_text()


class ConsoleConfigTests(unittest.TestCase):
    def test_console_listener_and_hypervisor_are_loopback_with_https_client_url(self):
        text = console.nova_options('[DEFAULT]\nexisting = preserve\n[vnc]\nenabled = false\n')
        parser = configparser.ConfigParser()
        parser.read_string(text)
        self.assertEqual(parser.get('DEFAULT', 'existing'), 'preserve')
        for field in ('server_listen', 'server_proxyclient_address', 'novncproxy_host'):
            self.assertEqual(parser.get('vnc', field), '127.0.0.1')
        self.assertEqual(parser.get('vnc', 'novncproxy_base_url'), 'https://localhost:8443/novnc/poc-console.html')
        self.assertTrue(parser.getboolean('consoleauth', 'enforce_session_timeout'))

    def test_console_query_tokens_are_excluded_from_both_access_logs(self):
        text = console.apache_options(VHOST)
        self.assertIn('horizon-local-access.log combined env=!poc_console_request', text)
        own_log = next(line for line in text.splitlines() if 'poc-console-access.log' in line)
        self.assertIn('%U', own_log)
        self.assertNotIn('%r', own_log)
        self.assertNotIn('%q', own_log)
        self.assertIn('Referrer-Policy "no-referrer"', text)

    def test_proxy_routes_preserve_existing_horizon_and_are_idempotent(self):
        once = console.apache_options(VHOST)
        self.assertEqual(console.apache_options(once), once)
        self.assertIn('WSGIScriptAlias /horizon', once)
        self.assertIn('Require local', once)
        self.assertIn('ws://127.0.0.1:6080/websockify', once)

    def test_wildcard_or_multiple_vhosts_are_refused(self):
        for text in (VHOST.replace('127.0.0.1:8443>', '*:8443>'), VHOST + VHOST):
            with self.subTest(text=text[:30]), self.assertRaises(console.safety.GuardError):
                console.apache_options(text)

    def test_malicious_archive_cannot_escape_the_owned_frontend_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'bad.tgz'
            with tarfile.open(archive, 'w:gz') as stream:
                member = tarfile.TarInfo('package/../../outside')
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
            integrity = 'sha512-' + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
            with patch.object(console, 'ARCHIVE', archive), patch.object(console, 'INTEGRITY', integrity), \
                 patch.object(console.browser, 'write_root') as write:
                with self.assertRaisesRegex(console.safety.GuardError, 'unsafe_frontend_archive'):
                    console.stage_frontend()
                write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
