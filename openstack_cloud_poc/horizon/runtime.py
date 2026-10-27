"""Trusted Horizon identity and protected application-state construction."""
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from ..billing import BillingError, BillingErrorCode, BillingLedger, BillingService
from ..keystone_billing_auth import KeystoneBillingPrincipalAdapter
from ..usage_history import SnapshotUsageSource, UsageHistory


def scoped_session(request, system=False):
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    if not request.user.is_authenticated or not getattr(request.user, 'token', None):
        raise BillingError(BillingErrorCode.AUTHENTICATION_REQUIRED)
    scope = {'system_scope': 'all'} if system else {'project_id': request.user.tenant_id}
    auth = v3.Token(auth_url=settings.OPENSTACK_KEYSTONE_URL, token=request.user.token.id, **scope)
    return session.Session(auth=auth, timeout=10)


def principal(request, system=False):
    adapter = KeystoneBillingPrincipalAdapter(scoped_session(request, system))
    return adapter.principal_for(request.user.id, None if system else request.user.tenant_id)


@lru_cache(maxsize=1)
def history():
    return UsageHistory(Path(settings.POC_BILLING_STATE_DIR) / 'usage.sqlite')


@lru_cache(maxsize=1)
def service():
    # The installed Horizon daemon has one process and threaded access. The
    # existing ledger enforces a single owner and serializes all operations.
    return BillingService(BillingLedger(Path(settings.POC_BILLING_STATE_DIR) / 'billing.sqlite'),
                           SnapshotUsageSource(history()))


def project_choices(request):
    from openstack.connection import Connection
    with Connection(session=scoped_session(request), region_name='RegionOne', interface='internal') as cloud:
        return sorted(((project.id, project.name) for project in cloud.identity.projects(is_enabled=True)),
                      key=lambda item: item[1].casefold())
