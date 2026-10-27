import horizon
from django.utils.translation import gettext_lazy as _


class UsageBilling(horizon.Panel):
    name = _('Usage & Billing')
    slug = 'poc_billing'
