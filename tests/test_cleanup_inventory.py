"""Read-only cleanup metadata guards; no cloud or live database connections."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    import inspect_cleanup_dependencies as setup
finally:
    sys.path.remove(str(SCRIPTS))


class CleanupInventoryTests(unittest.TestCase):
    def test_invalid_or_injected_project_ids_never_reach_sql(self):
        with patch.object(setup.common, 'sql') as sql:
            for projects in ([], ['x'], ["x'; DROP TABLE ports;--"], [None]):
                with self.assertRaises(RuntimeError):
                    setup.network_resources(projects)
            sql.assert_not_called()

    def test_metadata_query_is_scoped_read_only_and_does_not_use_cloud_apis(self):
        project, identifier = uuid4().hex, str(uuid4())
        row = {'kind': 'port', 'id': identifier, 'project_id': project}
        with patch.object(setup.common, 'sql', return_value=json.dumps(row)) as sql, \
             patch.object(setup.common, 'connection', side_effect=AssertionError('no cloud session')):
            self.assertEqual(setup.network_resources([project]), [row])
        statement = sql.call_args.args[0]
        commands = [part.strip() for part in statement.split(';') if part.strip()]
        self.assertEqual(commands[:2], ['SET TRANSACTION READ ONLY', 'START TRANSACTION WITH CONSISTENT SNAPSHOT'])
        self.assertEqual(commands[-1], 'COMMIT')
        self.assertEqual(len(commands[2:-1]), 6)
        self.assertTrue(all(command.startswith('SELECT ') for command in commands[2:-1]))
        self.assertTrue(all("project_id IN ('" + project + "')" in command for command in commands[2:-1]))

    def test_foreign_or_unexpected_metadata_is_not_returned_or_exposed(self):
        project = uuid4().hex
        base = {'kind': 'port', 'id': str(uuid4()), 'project_id': project}
        for row in (dict(base, project_id=uuid4().hex), dict(base, kind='unknown'),
                    dict(base, unexpected_contents='must-not-be-exposed'), dict(base, id='not-an-id')):
            with patch.object(setup.common, 'sql', return_value=json.dumps(row)), self.assertRaises(RuntimeError):
                setup.network_resources([project])

    def test_recreated_default_metadata_is_reported_without_rule_or_config_contents(self):
        project = uuid4().hex
        row = {'kind': 'security_group', 'id': str(uuid4()), 'project_id': project,
               'default_name': 1, 'default_description': 1, 'is_project_default': 1,
               'bound_port_count': 0, 'external_reference_count': 0, 'sharing_policy_count': 0,
               'rule_count': 4, 'created_at': '2026-10-24 15:00:00', 'updated_at': None}
        with patch.object(setup.common, 'sql', return_value=json.dumps(row)):
            self.assertEqual(setup.network_resources([project]), [row])

    @unittest.skipUnless(Path('/usr/lib/python3/dist-packages/neutron/db/securitygroups_db.py').exists(),
                         'requires installed Ubuntu Neutron source')
    def test_installed_neutron_project_list_invokes_default_group_ensure(self):
        # Execute the installed list method with storage/default-ensure doubles.
        # Extracting its AST avoids importing Neutron's host-network discovery.
        source = Path('/usr/lib/python3/dist-packages/neutron/db/securitygroups_db.py')
        method = next(node for node in ast.walk(ast.parse(source.read_text()))
                      if isinstance(node, ast.FunctionDef) and node.name == 'get_security_groups')
        method.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        namespace = {'base_obj': NS(Pager=Mock()), 'sg_obj': NS(SecurityGroup=NS(get_objects=Mock(return_value=[])))}
        exec(compile(module, str(source), 'exec'), namespace)
        plugin = NS(_ensure_default_security_group=Mock())
        context = NS(tenant_id='operator-project')
        self.assertEqual(namespace['get_security_groups'](plugin, context,
            filters={'project_id': ['target-project']}), [])
        plugin._ensure_default_security_group.assert_called_once_with(context, 'target-project')


if __name__ == '__main__':
    unittest.main()
