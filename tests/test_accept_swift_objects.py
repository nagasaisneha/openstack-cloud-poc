"""Offline safety and response checks for live Swift object acceptance."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location(
        'task19_accept_swift_objects', SCRIPTS / 'accept_swift_objects.py')
    acceptance = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(acceptance)
finally:
    sys.path.remove(str(SCRIPTS))


class SwiftObjectAcceptanceTests(unittest.TestCase):
    def test_select_owned_refuses_ambiguous_or_drifted_resources(self):
        item = NS(id='one', name='task-name', description='task-marker')
        self.assertIs(acceptance.select_owned([item], 'one', 'task-name', 'task-marker'), item)
        self.assertIsNone(acceptance.select_owned([], None, 'task-name', 'task-marker'))
        with self.assertRaisesRegex(RuntimeError, 'disappeared'):
            acceptance.select_owned([], 'one', 'task-name', 'task-marker')
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            acceptance.select_owned([item, item], None, 'task-name', 'task-marker')
        with self.assertRaisesRegex(RuntimeError, 'conflict'):
            acceptance.select_owned([item], None, 'task-name', 'other-marker')

    def test_project_account_endpoint_is_strictly_loopback_and_project_scoped(self):
        endpoint = 'http://127.0.0.1:8080/v1/AUTH_' + 'a' * 32
        self.assertEqual(acceptance.swift_readiness.expected_account_endpoint(endpoint, 'a' * 32), True)
        self.assertFalse(acceptance.swift_readiness.expected_account_endpoint(
            'http://127.0.0.1:8080/v1/AUTH_' + 'b' * 32, 'a' * 32))
        self.assertFalse(acceptance.swift_readiness.expected_account_endpoint(
            'http://example.com/v1/AUTH_' + 'a' * 32, 'a' * 32))

    def test_cross_project_denial_accepts_only_403_or_404(self):
        for status in (403, 404):
            response = NS(status_code=status, close=Mock())
            cloud = NS(session=NS(request=Mock(return_value=response)))
            self.assertEqual(acceptance.assert_denied(cloud, 'GET', 'http://127.0.0.1/x'), status)
            response.close.assert_called_once()
        response = NS(status_code=401, close=Mock())
        cloud = NS(session=NS(request=Mock(return_value=response)))
        with self.assertRaisesRegex(RuntimeError, 'not denied'):
            acceptance.assert_denied(cloud, 'GET', 'http://127.0.0.1/x')

    def test_member_token_scope_allows_reader_and_reports_only_sanitized_scope(self):
        record = {'user_id': 'user-id', 'project_id': 'project-id'}
        access = NS(user_id='user-id', project_id='project-id', project_scoped=True,
                    system_scoped=False, role_names=['member', 'reader'])
        cloud = NS(session=NS(auth=NS(get_access=Mock(return_value=access))))
        acceptance.check_member_scope(cloud, record)
        access.role_names = ['member', 'admin']
        with self.assertRaisesRegex(RuntimeError, 'scope or roles') as raised:
            acceptance.check_member_scope(cloud, record)
        self.assertEqual(raised.exception.context['role_names'], ['admin', 'member'])
        self.assertNotIn('user_id', raised.exception.context)
        self.assertNotIn('project_id', raised.exception.context)

    def test_unexpected_cross_project_write_restores_task_payload(self):
        response = NS(status_code=201, close=Mock())
        cloud = NS(session=NS(request=Mock(return_value=response)))
        restore = Mock()
        with self.assertRaisesRegex(RuntimeError, 'not denied'):
            acceptance.assert_denied(cloud, 'PUT', 'http://127.0.0.1/x', restore=restore)
        restore.assert_called_once()

    def test_object_readback_checks_full_bytes_hash_and_length(self):
        body = b'task payload'
        response = NS(status_code=200, content=body, close=Mock())
        cloud = NS(session=NS(request=Mock(return_value=response)))
        state = {'object': {'length': len(body), 'sha256': acceptance.hashlib.sha256(body).hexdigest()}}
        result = acceptance.get_and_verify(cloud, state, 'http://127.0.0.1/object', body)
        self.assertEqual(result['length'], len(body))
        self.assertEqual(result['sha256'], acceptance.hashlib.sha256(body).hexdigest())
        response.close.assert_called_once()

    def test_container_name_collision_is_preserved_without_a_put(self):
        response = NS(status_code=204, headers={}, close=Mock())
        cloud = NS(session=NS(request=Mock(return_value=response)))
        state = {'run_id': 'a' * 32, 'object': {'container': 'poc-owned'}}
        with self.assertRaisesRegex(RuntimeError, 'without this run marker'):
            acceptance.ensure_container(cloud, state, 'http://127.0.0.1:8080/v1/AUTH_' + 'b' * 32)
        cloud.session.request.assert_called_once()
        response.close.assert_called_once()

    def test_object_name_collision_is_preserved_without_a_put(self):
        response = NS(status_code=200, headers={'X-Object-Meta-Poc-Run-Id': 'different'}, close=Mock())
        cloud = NS(session=NS(request=Mock(return_value=response)))
        state = {'run_id': 'a' * 32, 'object': {'container': 'poc-owned', 'name': 'payload-owned',
                                                 'length': 3}}
        with self.assertRaisesRegex(RuntimeError, 'without this run marker'):
            acceptance.put_object(cloud, state, 'http://127.0.0.1:8080/v1/AUTH_' + 'b' * 32, b'123')
        cloud.session.request.assert_called_once()
        response.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
