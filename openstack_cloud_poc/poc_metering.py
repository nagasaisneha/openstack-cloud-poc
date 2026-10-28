"""Read real scoped provider usage and current allocations with explicit gaps."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from .billing import BillingError, BillingErrorCode, UsageBatch
from .nova_billing_usage import NovaBillingUsageSource
from .usage import UsageProvenance
from .provider_usage import read_meter, SOURCE as VERIFIED_SOURCE
from .usage_history import batch_from_wire


def month_bounds(value):
    start = value.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def collector_config(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    data = json.loads(Path(path).read_text())
    if (data.get('schema') != 1 or data.get('auth_url') != 'http://127.0.0.1:5000/v3/'
            or not isinstance(data.get('project_ids'), list) or not data.get('user_id') or not data.get('password')):
        raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    return data


def collector_connection(config, project_id):
    if project_id not in config['project_ids']:
        raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)
    from keystoneauth1 import session
    from keystoneauth1.identity import v3
    from openstack.connection import Connection
    auth = v3.Password(auth_url=config['auth_url'], user_id=config['user_id'],
                       password=config['password'], project_id=project_id)
    return Connection(session=session.Session(auth=auth, timeout=10),
                      region_name='RegionOne', interface='internal')


def bounded(values, maximum=250):
    result = []
    for value in values:
        if len(result) >= maximum:
            raise ValueError('inventory_limit')
        result.append(value)
    return result


def verified_batch(config, project_id, start, end, clock):
    path = config.get('verified_meter_socket')
    if path != '/run/openstack-poc-meter/read.sock':
        raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    result = read_meter(project_id, start, end, path)
    batch = batch_from_wire(result['batch'])
    if (batch.project_id != project_id or batch.interval_start != start or batch.interval_end != end
            or batch.source != VERIFIED_SOURCE or batch.provenance is not UsageProvenance.OBSERVED
            or batch.complete
            or batch.collected_at is None or (clock() - batch.collected_at).total_seconds() > 90
            or (batch.collected_at - clock()).total_seconds() > 2
            or not result.get('source_clock_checked')):
        raise BillingError(BillingErrorCode.USAGE_SOURCE_UNAVAILABLE)
    return batch, result['measurement_policy']


def collect_project(config, project_id, start, end, clock=None):
    current = clock or (lambda: datetime.now(timezone.utc))
    with collector_connection(config, project_id) as cloud:
        inventory = {'collected_at': current().isoformat(), 'project_id': project_id,
                     'instances': None, 'volumes': None, 'volume_gib': None,
                     'object_bytes': None, 'object_count': None, 'container_count': None,
                     'gaps': ['active_compute_intervals_unavailable', 'network_bytes_unavailable']}
        # Authenticate and verify the explicit collector role before any query.
        from .keystone_billing_auth import KeystoneBillingPrincipalAdapter
        principal = KeystoneBillingPrincipalAdapter(cloud.session).principal_for(config['user_id'], project_id)
        if not any(binding.project_id == project_id and 'billing_collector' in binding.roles
                   for binding in principal.project_bindings):
            raise BillingError(BillingErrorCode.AUTHORIZATION_DENIED)
        try:
            batch = NovaBillingUsageSource(cloud, config['user_id'], clock=current).collect(project_id, start, end)
        except BillingError:
            batch = UsageBatch(project_id, start, end, 'openstack:service-inventory',
                               UsageProvenance.OBSERVED, False, (), current(), ('nova_usage_api_unavailable',))
        try:
            servers = bounded(cloud.compute.servers())
            if any(server.project_id != project_id for server in servers):
                raise ValueError('foreign_project')
            inventory['instances'] = [{'id': server.id, 'name': server.name, 'status': server.status} for server in servers]
        except Exception:
            inventory['gaps'].append('compute_inventory_unavailable')
        try:
            volumes = bounded(cloud.block_storage.volumes())
            if any(getattr(volume, 'project_id', None) not in (None, project_id) for volume in volumes):
                raise ValueError('foreign_project')
            inventory['volumes'] = [{'id': volume.id, 'name': volume.name, 'size_gib': volume.size,
                                      'status': volume.status} for volume in volumes]
            inventory['volume_gib'] = sum(volume.size for volume in volumes)
        except Exception:
            inventory['gaps'].append('volume_inventory_unavailable')
        try:
            metadata = cloud.object_store.get_account_metadata()
            for key, attribute in (('object_bytes', 'account_bytes_used'), ('object_count', 'account_object_count'),
                                   ('container_count', 'account_container_count')):
                value = int(getattr(metadata, attribute))
                if value < 0:
                    raise ValueError('negative_counter')
                inventory[key] = value
        except Exception:
            for key in ('object_bytes', 'object_count', 'container_count'):
                inventory[key] = None
            inventory['gaps'].append('object_account_usage_unavailable')
        if config.get('verified_meter_socket'):
            try:
                measured, policy = verified_batch(config, project_id, start, end, current)
                batch = measured
                inventory['measurement_policy'] = policy
                present = {sample.metric for sample in batch.samples}
                if 'compute.active_vcpu_hours' in present:
                    inventory['gaps'].remove('active_compute_intervals_unavailable')
                if 'network.interface_bytes' in present:
                    inventory['gaps'].remove('network_bytes_unavailable')
            except Exception:
                inventory['gaps'].append('verified_measurement_source_unavailable')
        return batch, inventory
