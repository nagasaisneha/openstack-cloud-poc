import ast
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment" / "horizon-local"


class HorizonLocalConfigTests(unittest.TestCase):
    def test_settings_block_is_python_and_uses_loopback_keystone(self):
        settings = (DEPLOYMENT / "local_settings.py.block").read_text(encoding="utf-8")
        ast.parse(settings)
        self.assertIn('ALLOWED_HOSTS = ["localhost", "127.0.0.1"]', settings)
        self.assertIn('OPENSTACK_KEYSTONE_URL = "http://127.0.0.1:5000/v3/"', settings)
        self.assertNotIn("ALLOWED_HOSTS = ['*']", settings)

    def test_settings_block_requires_tls_and_secure_session_cookies(self):
        settings = (DEPLOYMENT / "local_settings.py.block").read_text(encoding="utf-8")
        for setting in (
            "USE_SSL = True",
            "CSRF_COOKIE_SECURE = True",
            "SESSION_COOKIE_SECURE = True",
            "SESSION_COOKIE_HTTPONLY = True",
            'SESSION_COOKIE_SAMESITE = "Lax"',
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, settings)

    def test_apache_config_is_loopback_tls_only_and_has_bounded_wsgi(self):
        config = (DEPLOYMENT / "horizon-loopback-ssl.conf").read_text(encoding="utf-8")
        self.assertIn("Listen 127.0.0.1:8443", config)
        self.assertIn("<VirtualHost 127.0.0.1:8443>", config)
        self.assertIn("SSLEngine on", config)
        self.assertIn("SSLProtocol -all +TLSv1.2 +TLSv1.3", config)
        self.assertIn("WSGIDaemonProcess horizon user=horizon group=horizon processes=1 threads=5", config)
        self.assertIn("WSGIScriptAlias /horizon ", config)
        self.assertIn("Require local", config)
        self.assertNotIn("Listen 80", config)
        self.assertNotIn("<VirtualHost *:", config)

    def test_templates_contain_no_secret_material(self):
        for path in (DEPLOYMENT / "local_settings.py.block", DEPLOYMENT / "horizon-loopback-ssl.conf"):
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("SECRET_KEY =", content)
                self.assertNotIn("PASSWORD =", content)
                self.assertNotIn("BEGIN PRIVATE KEY", content)

    def test_certificate_helper_parses_and_checks_local_certificate_scope(self):
        helper = DEPLOYMENT / "generate-local-cert.sh"
        subprocess.run(["bash", "-n", str(helper)], check=True)
        content = helper.read_text(encoding="utf-8")
        self.assertIn("flock -n 9", content)
        self.assertIn("openssl verify -verify_hostname localhost", content)
        self.assertIn("openssl verify -verify_ip 127.0.0.1", content)
        self.assertIn("-checkend", content)
        self.assertIn("DNS:localhost", content)
        self.assertIn("IP:127.0.0.1/255.255.255.255", content)
        self.assertIn("ln -- \"$source_path\" \"$destination_path\"", content)
        self.assertIn("refusing to overwrite a partial set", content)

    def test_package_rollback_helper_is_bash_parseable_and_snapshot_only(self):
        helper = DEPLOYMENT / "prepare-package-rollback.sh"
        subprocess.run(["bash", "-n", str(helper)], check=True)
        content = helper.read_text(encoding="utf-8")
        self.assertIn("apt-get -s --no-install-recommends install", content)
        self.assertIn("18 newly installed, 0 to remove", content)
        self.assertIn("python3-yaml_5.4.1-1ubuntu1_amd64.deb", content)
        self.assertIn("6.0.1-2build2~cloud0", content)
        self.assertIn("7ef9b534046bb838f7357091bafb368f6d1f32fb7ad2887d7c377ff4c0081b32", content)
        self.assertIn("apache2-before.tar.gz", content)
        self.assertIn("BEGIN: %s\\n", content)
        self.assertIn("PASS: %s\\n", content)
        self.assertIn("rollback preparation exited during", content)
        for operation in (
            "apt-get install --no-install-recommends",
            "systemctl restart",
            "systemctl reload",
            "a2enconf",
            "a2disconf",
        ):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, content)


if __name__ == "__main__":
    unittest.main()
