"""Request-layer scope, CSRF, and non-disclosing statement boundaries."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from django.conf import settings
if not settings.configured:
    settings.configure(SECRET_KEY='offline-test-only', USE_I18N=False,
                       INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions'],
                       LOGIN_URL='/horizon/auth/login/', ALLOWED_HOSTS=['testserver'],
                       ROOT_URLCONF='openstack_cloud_poc.horizon.project.urls')
import django
django.setup()
from django.test import RequestFactory

from openstack_cloud_poc.billing import (BillingError, BillingLedger, BillingPrincipal, BillingService,
    ProjectRoleBinding, RateCard, RateRule, UsageBatch)
from openstack_cloud_poc.usage import UsageProvenance
from openstack_cloud_poc.horizon import runtime, views
from openstack_cloud_poc.horizon.forms import RateForm

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, tzinfo=timezone.utc)


class Source:
    def collect(self, project, start, end):
        return UsageBatch(project, start, end, 'fixture:boundary', UsageProvenance.SIMULATED,
                          False, (), START, ('test_incomplete',))


class BrowserBillingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ledger = BillingLedger(Path(self.directory.name) / 'ledger.sqlite')
        self.service = BillingService(self.ledger, Source())
        self.admin = BillingPrincipal('admin', True, system_roles=frozenset({'cloud_admin'}))
        self.viewer = BillingPrincipal('user-a', True, (ProjectRoleBinding('project-a', frozenset({'billing_viewer'})),))
        self.card = RateCard('test-v1', 'INR', 2,
                             (RateRule('compute.instance_vcpu_hours', 'vCPU-hour', Decimal('1'), START),))
        self.service.configure_rate_card(self.admin, self.card)
        self.factory = RequestFactory()

    def tearDown(self):
        self.ledger.close()
        self.directory.cleanup()

    def request(self, path='/', method='get', data=None):
        request = getattr(self.factory, method)(path, data=data or {})
        request.user = NS(is_authenticated=True, id='user-a', tenant_id='project-a', tenant_name='Project A')
        return request

    def test_forged_project_parameter_is_denied_before_ledger_or_collector_access(self):
        request = self.request(data={'project_id': 'project-b'})
        with patch.object(runtime, 'principal', return_value=self.viewer), \
             patch.object(runtime, 'service') as service, patch.object(views, 'collect_project') as collect:
            self.assertEqual(views.project_index(request).status_code, 403)
            service.assert_not_called()
            collect.assert_not_called()

    def test_generic_admin_flag_does_not_authorize_billing_administration(self):
        request = self.request()
        request.user.is_superuser = True
        with patch.object(runtime, 'principal', return_value=BillingPrincipal('admin', True)), \
             patch.object(runtime, 'service', return_value=self.service), \
             patch.object(runtime, 'project_choices') as projects:
            self.assertEqual(views.admin_index(request).status_code, 403)
            projects.assert_not_called()

    def test_rate_post_requires_csrf_before_any_identity_or_mutation(self):
        request = self.request(method='post', data={'action': 'rate', 'version': 'forged'})
        with patch.object(runtime, 'principal') as identity:
            self.assertEqual(views.admin_index(request).status_code, 403)
            identity.assert_not_called()

    def test_foreign_and_unknown_statement_ids_have_identical_response(self):
        foreign = self.service.generate_statement(self.admin, 'project-b', START, END, self.card.version)
        with patch.object(runtime, 'principal', return_value=self.viewer), \
             patch.object(runtime, 'service', return_value=self.service):
            response = views.project_download(self.request(), foreign.statement_id, 'csv')
            unknown = views.project_download(self.request(), '00000000-0000-0000-0000-000000000000', 'csv')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, unknown.content)

    def test_own_project_download_is_a_scoped_attachment(self):
        statement = self.service.generate_statement(self.admin, 'project-a', START, END, self.card.version)
        with patch.object(runtime, 'principal', return_value=self.viewer), \
             patch.object(runtime, 'service', return_value=self.service):
            response = views.project_download(self.request(), statement.statement_id, 'json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn(b'project-a', response.content)
        self.assertIn('private', response['Cache-Control'])

    def test_rate_list_requires_explicit_project_billing_or_system_admin_role(self):
        with patch.object(self.ledger, 'list_rate_cards', wraps=self.ledger.list_rate_cards) as read:
            self.assertEqual(self.service.available_rate_cards(self.viewer, 'project-a'), (self.card,))
            read.reset_mock()
            for project in ('project-b', None):
                with self.assertRaises(BillingError):
                    self.service.available_rate_cards(self.viewer, project)
            read.assert_not_called()

    def test_empty_rates_are_not_silently_zero_and_explicit_zero_is_supported(self):
        data = {'version': 'demo', 'currency': 'INR', 'effective_from': '2026-10-01'}
        self.assertFalse(RateForm(data).is_valid())
        form = RateForm(dict(data, vcpu_rate='0'))
        self.assertTrue(form.is_valid())
        self.assertEqual(form.rate_card().rules[0].rate_per_unit, Decimal('0'))


if __name__ == '__main__':
    unittest.main()
