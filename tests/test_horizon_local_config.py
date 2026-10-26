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
        self.assertIn('awk \'$1 == "Candidate:" {candidate=$2} END {if (candidate == "") exit 1; print candidate}\'', content)
        self.assertNotIn("awk '/Candidate:/ {print $2; exit}'", content)
        for operation in (
            "apt-get install --no-install-recommends",
            "systemctl restart",
            "systemctl reload",
            "a2enconf",
            "a2disconf",
        ):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, content)

    def test_loopback_config_installer_is_bash_parseable_and_reload_gated(self):
        helper = DEPLOYMENT / "install-loopback-config.sh"
        subprocess.run(["bash", "-n", str(helper)], check=True)
        content = helper.read_text(encoding="utf-8")
        self.assertIn("not confirmed disabled by the site administrator", content)
        self.assertIn("default HTTP /horizon/ route is not confirmed disabled", content)
        self.assertIn("apache2ctl configtest || die", content)
        self.assertIn("cmp -s -- \"$before_routes\" \"$after_routes\"", content)
        self.assertIn("https://localhost:8443/horizon/", content)
        self.assertIn("Protected config snapshot:", content)
        self.assertIn("ports.conf.before", content)
        self.assertIn("port 443 is already in use", content)
        self.assertIn("wildcard443", content)
        for operation in (
            "systemctl restart",
            "a2enconf openstack-dashboard",
            "a2disconf openstack-dashboard",
        ):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, content)

    def test_ports_filter_removes_only_inactive_wildcard_ssl_listeners(self):
        filter_file = DEPLOYMENT / "remove-inactive-wildcard-ssl-listeners.awk"
        self.assertIn("Listen[[:space:]].*443", filter_file.read_text(encoding="utf-8"))
        ports = (
            "Listen 80\n"
            "<IfModule ssl_module>\n\tListen 443\n</IfModule>\n"
            "<IfModule mod_gnutls.c>\n\tListen 443\n</IfModule>\n"
        )
        result = subprocess.run(["awk", "-f", str(filter_file)], input=ports, text=True,
                                capture_output=True, check=True)
        self.assertIn("Listen 80", result.stdout)
        self.assertNotIn("Listen 443", result.stdout)
        for unsafe in ("Listen *:443\n", "<IfModule ssl_module>\n Listen 127.0.0.1:443\n</IfModule>\n"):
            with self.subTest(unsafe=unsafe):
                rejected = subprocess.run(["awk", "-f", str(filter_file)], input=unsafe, text=True,
                                          capture_output=True)
                self.assertEqual(rejected.returncode, 2)


if __name__ == "__main__":
    unittest.main()
