import horizon
from django.utils.translation import gettext_lazy as _


class BillingAdmin(horizon.Panel):
    name = _('Usage & Billing')
    slug = 'poc_billing'
    permissions = ('openstack.roles.admin',)
