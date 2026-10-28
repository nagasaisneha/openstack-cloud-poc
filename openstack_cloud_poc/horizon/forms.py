from datetime import datetime, timezone
from decimal import Decimal
from django import forms
from ..billing import RateCard, RateRule, SUPPORTED_CURRENCY_MINOR_UNITS

METERS = (
    ('vcpu_rate', 'compute.instance_vcpu_hours', 'vCPU-hour', 'vCPU allocation hour'),
    ('memory_rate', 'compute.instance_memory_mib_hours', 'MiB-hour', 'RAM allocation MiB-hour'),
    ('disk_rate', 'compute.instance_local_disk_gib_hours', 'GiB-hour', 'Local disk allocation GiB-hour'),
    ('active_vcpu_rate', 'compute.active_vcpu_hours', 'vCPU-hour', 'Provider-confirmed active vCPU-hour'),
    ('active_memory_rate', 'compute.active_memory_mib_hours', 'MiB-hour', 'Provider-confirmed active RAM MiB-hour'),
    ('verified_disk_rate', 'compute.local_disk_gib_hours', 'GiB-hour', 'Provisioned local disk GiB-hour'),
    ('volume_rate', 'storage.volume_gib_hours', 'GiB-hour', 'Provisioned volume GiB-hour'),
    ('snapshot_rate', 'storage.snapshot_gib_hours', 'GiB-hour', 'Provisioned snapshot GiB-hour'),
    ('cpu_seconds_rate', 'compute.cpu_seconds', 'CPU-second', 'Measured domain-process CPU-second'),
    ('interface_bytes_rate', 'network.interface_bytes', 'byte', 'Combined interface byte'),
)


class RateForm(forms.Form):
    version = forms.RegexField(r'^[A-Za-z0-9_.-]{1,64}$', label='Rate version')
    currency = forms.ChoiceField(choices=[(name, name) for name in SUPPORTED_CURRENCY_MINOR_UNITS])
    effective_from = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    vcpu_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
                                  required=False, label='Per vCPU allocation hour')
    memory_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
                                    required=False, label='Per RAM MiB-hour')
    disk_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
                                  required=False, label='Per local disk GiB-hour')
    active_vcpu_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per provider-confirmed active vCPU-hour')
    active_memory_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per provider-confirmed active RAM MiB-hour')
    verified_disk_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per provisioned local disk GiB-hour')
    volume_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per provisioned volume GiB-hour')
    snapshot_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per provisioned snapshot GiB-hour')
    cpu_seconds_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per measured domain-process CPU-second (includes hypervisor overhead)')
    interface_bytes_rate = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=8,
        required=False, label='Per combined interface byte (not internet egress)')

    def clean(self):
        data = super().clean()
        if not any(data.get(field) is not None for field, *_ in METERS):
            raise forms.ValidationError('Enter at least one rate. An empty rate means unavailable, not zero.')
        return data

    def rate_card(self):
        data = self.cleaned_data
        start = datetime.combine(data['effective_from'], datetime.min.time(), timezone.utc)
        rules = tuple(RateRule(metric, unit, data[field], start)
                      for field, metric, unit, ignored in METERS if data[field] is not None)
        return RateCard(data['version'], data['currency'],
                        SUPPORTED_CURRENCY_MINOR_UNITS[data['currency']], rules)


class StatementForm(forms.Form):
    project_id = forms.ChoiceField(label='Project')
    month = forms.RegexField(r'^20[0-9]{2}-(?:0[1-9]|1[0-2])$', label='Month (UTC)',
                             widget=forms.TextInput(attrs={'type': 'month'}))
    rate_version = forms.ChoiceField(label='Rate version')

    def __init__(self, *args, projects=(), cards=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project_id'].choices = projects
        self.fields['rate_version'].choices = [(card.version, card.version) for card in cards]
