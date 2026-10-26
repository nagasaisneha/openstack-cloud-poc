import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location('source_service_audit', SCRIPTS / 'audit_deployed_services.py')
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
finally:
    sys.path.remove(str(SCRIPTS))


class SourceServiceAuditTests(unittest.TestCase):
    def test_remote_credential_query_and_wrong_port_endpoints_are_not_queried(self):
        for endpoint in ('http://example.com:9876/v2', 'http://user:password@127.0.0.1:9876/v2',
                         'http://127.0.0.1:9876/v2?token=x', 'http://127.0.0.1:5000/v2',
                         'http://127.0.0.1:9876/foreign'):
            self.assertFalse(audit.safe_endpoint(endpoint, 'load-balancer', 'a' * 32))
        self.assertTrue(audit.safe_endpoint('http://127.0.0.1:9876/v2.0', 'load-balancer', 'a' * 32))

    def test_object_scope_is_exact(self):
        self.assertTrue(audit.safe_endpoint('http://127.0.0.1:8080/v1/AUTH_' + 'a' * 32, 'object-store', 'a' * 32))
        self.assertFalse(audit.safe_endpoint('http://127.0.0.1:8080/v1/AUTH_' + 'b' * 32, 'object-store', 'a' * 32))

    def test_errors_do_not_include_raw_exception_or_response_data(self):
        error = RuntimeError('SECRET token project private endpoint')
        error.http_status = 403
        self.assertEqual(audit.error_summary(error), {'status': 'unavailable', 'error_type': 'RuntimeError', 'http_status': 403})

    def test_state_counts_do_not_export_names_or_ids(self):
        self.assertEqual(audit.state_counts([NS(status='ACTIVE', id='private', name='private')]), {'ACTIVE': 1})
        self.assertEqual(audit.state_counts([NS(status='SECRET_PRIVATE_VALUE')]), {'OTHER': 1})
