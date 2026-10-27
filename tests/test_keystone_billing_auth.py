from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from openstack_cloud_poc.billing import BillingError, BillingErrorCode, BillingService
from openstack_cloud_poc.keystone_billing_auth import KeystoneBillingPrincipalAdapter


class KeystoneBillingPrincipalAdapterTests(unittest.TestCase):
    def test_real_sdk_project_token_without_system_stanza_is_project_scoped(self):
        from keystoneauth1.access import AccessInfoV3
        access = AccessInfoV3({'token': {
            'user': {'id': 'user-1'}, 'project': {'id': 'project-1'},
            'roles': [{'name': 'member'}, {'name': 'billing_viewer'}],
        }})
        self.assertIsNone(access.system_scoped)
        principal = KeystoneBillingPrincipalAdapter(self.session(access)).principal_for('user-1', 'project-1')
        self.assertEqual(principal.project_bindings[0].roles, frozenset({'billing_viewer'}))
        self.assertEqual(principal.system_roles, frozenset())

    def test_real_sdk_system_token_does_not_create_project_membership(self):
        from keystoneauth1.access import AccessInfoV3
        access = AccessInfoV3({'token': {
            'user': {'id': 'user-1'}, 'system': {'all': True},
            'roles': [{'name': 'cloud_admin'}],
        }})
        principal = KeystoneBillingPrincipalAdapter(self.session(access)).principal_for('user-1')
        self.assertEqual(principal.project_bindings, ())
        self.assertEqual(principal.system_roles, frozenset({'cloud_admin'}))

    def session(self, access):
        session = SimpleNamespace()
        session.auth = SimpleNamespace(
            invalidate=Mock(),
            get_access=Mock(return_value=access),
        )
        return session

    @staticmethod
    def access(
        *,
        user_id="user-1",
        project_id="project-1",
        project_scoped=True,
        system_scoped=False,
        role_names=("member",),
    ):
        return SimpleNamespace(
            user_id=user_id,
            project_id=project_id,
            project_scoped=project_scoped,
            system_scoped=system_scoped,
            role_names=role_names,
        )

    def test_project_scope_maps_only_explicit_billing_roles_and_refreshes_access(self):
        access = self.access(role_names=("member", "billing_viewer", "admin"))
        session = self.session(access)

        principal = KeystoneBillingPrincipalAdapter(session).principal_for("user-1", "project-1")

        self.assertEqual(principal.subject_id, "user-1")
        self.assertTrue(principal.authenticated)
        self.assertEqual(len(principal.project_bindings), 1)
        self.assertEqual(principal.project_bindings[0].project_id, "project-1")
        self.assertEqual(principal.project_bindings[0].roles, frozenset({"billing_viewer"}))
        self.assertEqual(principal.system_roles, frozenset())
        session.auth.invalidate.assert_called_once_with()
        session.auth.get_access.assert_called_once_with(session)

    def test_project_scoped_admin_name_does_not_become_a_system_role(self):
        access = self.access(role_names=("admin", "cloud_admin", "project_admin"))

        principal = KeystoneBillingPrincipalAdapter(self.session(access)).principal_for("user-1", "project-1")

        self.assertEqual(principal.project_bindings[0].roles, frozenset({"project_admin"}))
        self.assertEqual(principal.system_roles, frozenset())

    def test_system_scope_maps_only_explicit_system_billing_roles(self):
        access = self.access(
            project_id=None,
            project_scoped=False,
            system_scoped=True,
            role_names=("admin", "cloud_admin", "billing_collector"),
        )

        principal = KeystoneBillingPrincipalAdapter(self.session(access)).principal_for("user-1")

        self.assertEqual(principal.project_bindings, ())
        self.assertEqual(principal.system_roles, frozenset({"cloud_admin", "billing_collector"}))

    def test_missing_billing_role_yields_authenticated_but_unprivileged_principal(self):
        access = self.access(role_names=("member", "reader"))

        principal = KeystoneBillingPrincipalAdapter(self.session(access)).principal_for("user-1", "project-1")

        self.assertEqual(principal.project_bindings[0].roles, frozenset())
        self.assertEqual(principal.system_roles, frozenset())

    def test_user_and_project_scope_must_match_trusted_horizon_context(self):
        adapter = KeystoneBillingPrincipalAdapter(self.session(self.access()))
        with self.assertRaises(BillingError) as wrong_user:
            adapter.principal_for("user-2", "project-1")
        self.assertEqual(wrong_user.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        with self.assertRaises(BillingError) as wrong_project:
            adapter.principal_for("user-1", "project-2")
        self.assertEqual(wrong_project.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

    def test_system_token_cannot_be_used_with_a_project_selected_in_the_ui(self):
        access = self.access(project_id=None, project_scoped=False, system_scoped=True, role_names=("cloud_admin",))

        with self.assertRaises(BillingError) as raised:
            KeystoneBillingPrincipalAdapter(self.session(access)).principal_for("user-1", "project-1")

        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

    def test_unscoped_and_contradictory_contexts_are_denied(self):
        for access in (
            self.access(project_id=None, project_scoped=False, system_scoped=False),
            self.access(project_scoped=True, system_scoped=True),
        ):
            with self.subTest(access=access), self.assertRaises(BillingError) as raised:
                KeystoneBillingPrincipalAdapter(self.session(access)).principal_for("user-1")
            self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)

    def test_keystone_errors_are_sanitized_and_do_not_echo_tokens(self):
        session = self.session(None)
        session.auth.get_access.side_effect = RuntimeError("Authorization: Bearer token-secret")

        with self.assertRaises(BillingError) as raised:
            KeystoneBillingPrincipalAdapter(session).principal_for("user-1", "project-1")

        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHENTICATION_REQUIRED)
        self.assertNotIn("token-secret", str(raised.exception))

    def test_invalid_expected_identity_fails_before_keystone_access(self):
        session = self.session(self.access())

        with self.assertRaises(BillingError) as raised:
            KeystoneBillingPrincipalAdapter(session).principal_for("bad\nidentity", "project-1")

        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHENTICATION_REQUIRED)
        session.auth.invalidate.assert_not_called()
        session.auth.get_access.assert_not_called()

    def test_adapter_principal_is_enforced_by_billing_service_before_foreign_project_read(self):
        access = self.access(role_names=("billing_viewer", "member"))
        session = self.session(access)
        ledger = SimpleNamespace(
            list_project_statements=Mock(return_value=()),
            record_audit_event=Mock(),
        )
        source = SimpleNamespace(collect=Mock())
        service = BillingService(ledger, source)
        principal = KeystoneBillingPrincipalAdapter(session).principal_for("user-1", "project-1")

        self.assertEqual(service.project_statements(principal, "project-1"), ())
        with self.assertRaises(BillingError) as raised:
            service.project_statements(principal, "project-2")

        self.assertEqual(raised.exception.code, BillingErrorCode.AUTHORIZATION_DENIED)
        ledger.list_project_statements.assert_called_once_with("project-1")
        self.assertEqual(ledger.record_audit_event.call_count, 2)


if __name__ == "__main__":
    unittest.main()
