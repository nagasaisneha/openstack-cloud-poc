#!/usr/bin/python3
"""Read-only, sanitized source-cloud inventory for maintained-release migration.

Queries the existing loopback catalog and APIs with protected POC credentials.
Does not list security groups (the source release can create defaults on list),
create resources, install packages, restart services or change configuration.
Only a new protected evidence report and the shared audit lock are written.
"""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
from urllib.parse import urlsplit

import configure_nova as common

DATA = Path('/root/openstack-poc-acceptance')
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
PORTS = {'identity': 5000, 'compute': 8774, 'image': 9292, 'network': 9696,
         'volumev3': 8776, 'object-store': 8080, 'load-balancer': 9876,
         'placement': 8778}


def safe_endpoint(url, kind, project_id):
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1'
                or parts.port != PORTS[kind] or parts.username or parts.password
                or parts.query or parts.fragment):
            return False
        allowed = {'identity': {'/v3'}, 'compute': {'/v2.1'}, 'image': {'', '/v2'},
                   'network': {'', '/v2.0'}, 'volumev3': {'/v3', '/v3/' + project_id},
                   'object-store': {'/v1/AUTH_' + project_id},
                   'load-balancer': {'', '/v2', '/v2.0'}, 'placement': {''}}
        return parts.path.rstrip('/') in allowed[kind]
    except (ValueError, KeyError, TypeError):
        return False


def error_summary(error):
    result = {'status': 'unavailable', 'error_type': type(error).__name__}
    status = getattr(error, 'http_status', getattr(error, 'status_code', None))
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    return result


def state_counts(resources, field='status'):
    allowed = {'ACTIVE', 'SHUTOFF', 'ERROR', 'BUILD', 'REBOOT', 'HARD_REBOOT',
               'PAUSED', 'SUSPENDED', 'SHELVED', 'SHELVED_OFFLOADED', 'UNKNOWN',
               'AVAILABLE', 'IN-USE', 'CREATING', 'DELETING', 'DOWNLOADING',
               'QUEUED', 'SAVING', 'DEACTIVATED', 'PENDING_DELETE', 'KILLED',
               'PENDING_CREATE', 'PENDING_UPDATE', 'DELETED'}
    statuses = (str(getattr(item, field, 'UNKNOWN')).upper() for item in resources)
    return dict(Counter(value if value in allowed else 'OTHER' for value in statuses))


def cloud_inventory(cloud):
    from keystoneauth1.exceptions.catalog import EndpointNotFound

    project = cloud.session.get_project_id()
    if not isinstance(project, str) or len(project) != 32 or any(c not in '0123456789abcdef' for c in project):
        raise RuntimeError('unexpected project scope')
    report = {'keystone_auth': True, 'scope': 'protected POC administrator project', 'services': {}}
    for kind in PORTS:
        try:
            endpoint = cloud.session.get_endpoint(service_type=kind, interface='internal', region_name='RegionOne')
            report['services'][kind] = {'catalog': 'present', 'safe_loopback_endpoint': safe_endpoint(endpoint, kind, project)}
        except EndpointNotFound:
            report['services'][kind] = {'catalog': 'absent'}
        except Exception as error:
            report['services'][kind] = error_summary(error)

    queries = {
        'compute': lambda: {'server_status_counts': state_counts(cloud.compute.servers()),
                           'service_count': len(list(cloud.compute.services()))},
        'image': lambda: {'image_status_counts': state_counts(cloud.image.images(owner=project))},
        'network': lambda: {
            'project_network_count': len(list(cloud.network.networks(project_id=project))),
            'external_network_count': len(list(cloud.network.networks(is_router_external=True))),
            'project_router_count': len(list(cloud.network.routers(project_id=project))),
            'project_floating_ip_count': len(list(cloud.network.ips(project_id=project)))},
        'volumev3': lambda: {'volume_status_counts': state_counts(cloud.block_storage.volumes()),
                            'service_count': len(list(cloud.block_storage.services()))},
        'object-store': lambda: {'account_head_completed': cloud.object_store.get_account_metadata() is not None},
        'load-balancer': lambda: {'provider_names': sorted(
            name if name in {'ovn', 'amphora'} else 'other'
            for name in (p.name for p in cloud.load_balancer.providers())),
            'load_balancer_status_counts': state_counts(cloud.load_balancer.load_balancers(project_id=project), 'provisioning_status')},
    }
    for kind, query in queries.items():
        entry = report['services'][kind]
        if entry.get('safe_loopback_endpoint') is not True:
            entry['api'] = {'status': 'not_queried'}
            continue
        try:
            entry['api'] = {'status': 'observed', **query()}
        except Exception as error:
            entry['api'] = error_summary(error)
    return report


def main():
    if os.geteuid() != 0:
        print('STOP: run sudo /usr/bin/python3 -B ' + str(Path(__file__).resolve()))
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        info = DATA.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('unsafe evidence directory')
        fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError('unsafe audit lock')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = {'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
                      'purpose': 'preserved source migration inventory; not final maintained acceptance',
                      'security_group_list_called': False, 'cloud_mutations': False}
            try:
                secrets = common.read_secrets(common.SECRET_FILE)
                with common.connection(secrets) as cloud:
                    report['cloud'] = cloud_inventory(cloud)
            except Exception as error:
                report['cloud'] = error_summary(error)
            fd, name = tempfile.mkstemp(prefix='source-service-audit-', suffix='.json', dir=DATA)
            with os.fdopen(fd, 'w') as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            print('POC source service audit: ' + json.dumps(report, sort_keys=True) + '; report=' + name, flush=True)
            return 0 if report['cloud'].get('keystone_auth') is True else 2
    except Exception as error:
        print('STOP: source service audit: ' + json.dumps(error_summary(error), sort_keys=True) + '; no services or resources changed.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
