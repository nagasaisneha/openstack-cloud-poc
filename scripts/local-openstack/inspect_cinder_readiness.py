#!/usr/bin/python3
"""Read-only Cinder endpoint, service, backend-capacity and quota preflight.

Scoped to the existing Ubuntu 22.04 WSL POC. Uses the protected Nova POC
credential file, writes a protected sanitized report, and sends GET requests
only. It does not create, attach, detach, snapshot, delete, or configure volumes.
"""
import configparser
import datetime
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import uuid

import configure_nova as common
import launch_test_vm as launch


LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
UNITS = ('cinder-api.service', 'cinder-scheduler.service', 'cinder-volume.service',
         'cinder-backup.service')
POOL_FIELDS = ('volume_backend_name', 'vendor_name', 'storage_protocol',
               'total_capacity_gb', 'free_capacity_gb', 'reserved_percentage')
QUOTA_FIELDS = ('maxTotalVolumes', 'totalVolumesUsed', 'maxTotalVolumeGigabytes',
                'totalGigabytesUsed', 'maxTotalSnapshots', 'totalSnapshotsUsed')


def fixed_api_failure(error):
    details = {'status': 'unavailable', 'error_type': type(error).__name__}
    status = getattr(error, 'status_code', None)
    if type(status) is int and 100 <= status <= 599:
        details['http_status'] = status
    return details


def numeric(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return round(float(value), 3)


def systemd_observation(unit):
    fields = 'LoadState,ActiveState,SubState'
    try:
        output = common.run(['systemctl', '--no-pager', 'show', unit,
                             '--property=' + fields], timeout=15)
    except Exception:
        return {'load': 'unavailable', 'active': 'unknown', 'sub': 'unknown'}
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition('=')
        if separator and key in {'LoadState', 'ActiveState', 'SubState'}:
            if re.fullmatch(r'[A-Za-z0-9_. -]{0,64}', value):
                values[key] = value
    if set(values) != {'LoadState', 'ActiveState', 'SubState'}:
        return {'load': 'unavailable', 'active': 'unknown', 'sub': 'unknown'}
    return {'load': values['LoadState'], 'active': values['ActiveState'],
            'sub': values['SubState']}


def backend_configuration(path=Path('/etc/cinder/cinder.conf')):
    if not path.exists() and not path.is_symlink():
        return {'present': False, 'backends': []}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        return {'present': True, 'safe_to_inspect': False, 'backends': []}
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(path.read_text())
    except (configparser.Error, UnicodeError, OSError):
        return {'present': True, 'safe_to_inspect': True, 'parseable': False,
                'backends': []}
    names = []
    if parser.has_option('DEFAULT', 'enabled_backends'):
        names = [name.strip() for name in parser.get('DEFAULT', 'enabled_backends').split(',')
                 if re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name.strip())]
    if not names:
        names = ['DEFAULT']
    records = []
    for name in names:
        section = name if name != 'DEFAULT' and parser.has_section(name) else 'DEFAULT'
        driver = parser.get(section, 'volume_driver', fallback='').strip()
        if driver and not re.fullmatch(r'[A-Za-z0-9_.$]{1,160}', driver):
            driver = 'configured_unrecognized'
        records.append({'backend_name_configured': name != 'DEFAULT',
                        'driver': driver or None,
                        'volume_group_configured': bool(parser.get(section, 'volume_group', fallback='').strip()),
                        'target_helper_configured': bool(parser.get(section, 'target_helper', fallback='').strip())})
    return {'present': True, 'safe_to_inspect': True, 'parseable': True,
            'enabled_backend_count': len(names) if names != ['DEFAULT'] else 0,
            'backends': records}


def pool_records(cloud):
    response = cloud.block_storage.get('/scheduler-stats/get_pools?detail=True',
                                        raise_exc=True, microversion='3.0')
    body = response.json()
    pools = body.get('pools', []) if isinstance(body, dict) else []
    result = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        capabilities = pool.get('capabilities', {})
        if not isinstance(capabilities, dict):
            capabilities = {}
        record = {}
        for field in POOL_FIELDS:
            value = capabilities.get(field)
            if field in {'total_capacity_gb', 'free_capacity_gb', 'reserved_percentage'}:
                record[field] = numeric(value)
            elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_. -]{1,80}', value):
                record[field] = value
        result.append(record)
    return result


def quota_observation(cloud):
    limits = cloud.block_storage.get_limits()
    absolute = getattr(limits, 'absolute', None) or {}
    result = {}
    for field in QUOTA_FIELDS:
        value = absolute.get(field) if isinstance(absolute, dict) else getattr(absolute, field, None)
        if type(value) is int and value >= -1:
            result[field] = value
    return result


def cloud_inventory(cloud):
    try:
        endpoint = cloud.session.get_endpoint(service_type='volumev3',
                                              interface='internal',
                                              region_name='RegionOne')
    except Exception as error:
        return {'endpoint_present': False, 'endpoint_status': fixed_api_failure(error),
                'services': [], 'pools': [], 'quota': {}}
    if not endpoint:
        return {'endpoint_present': False, 'endpoint_status': {'status': 'not_in_catalog'},
                'services': [], 'pools': [], 'quota': {}}

    result = {'endpoint_present': True, 'endpoint_status': {'status': 'catalogued'},
              'services': [], 'pools': [], 'quota': {}}
    try:
        services = list(cloud.block_storage.services())
        for service in services:
            binary = getattr(service, 'binary', None)
            if binary not in {'cinder-scheduler', 'cinder-volume', 'cinder-backup'}:
                continue
            state = getattr(service, 'state', None)
            status = getattr(service, 'status', None)
            zone = getattr(service, 'availability_zone', None)
            result['services'].append({
                'binary': binary,
                'state': state if state in {'up', 'down'} else 'unknown',
                'status': status if status in {'enabled', 'disabled'} else 'unknown',
                'availability_zone': zone if isinstance(zone, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', zone) else None,
            })
        result['service_api_status'] = 'responding'
    except Exception as error:
        result['service_api_status'] = fixed_api_failure(error)

    try:
        result['pools'] = pool_records(cloud)
        result['pool_api_status'] = 'responding'
    except Exception as error:
        result['pool_api_status'] = fixed_api_failure(error)
    try:
        result['quota'] = quota_observation(cloud)
        result['quota_api_status'] = 'responding'
    except Exception as error:
        result['quota_api_status'] = fixed_api_failure(error)
    return result


def readiness(report):
    cloud = report.get('cloud', {})
    services = cloud.get('services', [])
    scheduler_up = any(item['binary'] == 'cinder-scheduler' and item['state'] == 'up'
                       and item['status'] == 'enabled' for item in services)
    volume_up = any(item['binary'] == 'cinder-volume' and item['state'] == 'up'
                    and item['status'] == 'enabled' for item in services)
    free_capacity = any((pool.get('free_capacity_gb') or 0) > 0 for pool in cloud.get('pools', []))
    drivers = [item.get('driver') for item in report.get('backend_configuration', {}).get('backends', [])]
    driver_configured = any(bool(value) for value in drivers)
    checks = {'volumev3_endpoint': cloud.get('endpoint_present') is True,
              'service_api': cloud.get('service_api_status') == 'responding',
              'scheduler_up_enabled': scheduler_up, 'volume_up_enabled': volume_up,
              'backend_pool_reported': cloud.get('pool_api_status') == 'responding' and bool(cloud.get('pools')),
              'positive_reported_free_capacity': free_capacity,
              'backend_driver_configured': driver_configured}
    return {'status': 'ready_for_scoped_volume_test' if all(checks.values()) else 'blocked',
            'checks': checks}


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    release = Path('/etc/os-release').read_text()
    if (not re.search(r'^ID=ubuntu$', release, re.M)
            or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
            or 'microsoft' not in platform.release().lower()):
        print('STOP: this read-only inspector is scoped to the existing Ubuntu 22.04 WSL POC.', file=sys.stderr)
        return 1
    report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'mode': 'read_only', 'host_units': {}, 'backend_configuration': {}, 'cloud': {}}
    try:
        descriptor = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = launch.DATA_DIR.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise RuntimeError('protected acceptance directory required')
            path = launch.DATA_DIR / ('cinder-readiness-' + uuid.uuid4().hex + '.json')
            report['host_capacity'] = {
                'mem_available_kib': int(re.search(
                    r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1)),
                'acceptance_filesystem_free_bytes': shutil.disk_usage(launch.DATA_DIR).free,
            }
            for unit in UNITS:
                report['host_units'][unit] = systemd_observation(unit)
            report['backend_configuration'] = backend_configuration()
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                cloud.session.get_project_id()
                report['cloud'] = cloud_inventory(cloud)
            report['acceptance'] = readiness(report)
            launch.atomic_state(path, report)
            summary = {key: report[key] for key in ('host_units', 'backend_configuration', 'cloud', 'acceptance')}
            print('POC Cinder preflight: summary=' + json.dumps(summary, sort_keys=True), flush=True)
            print('POC Cinder preflight: ' + report['acceptance']['status'], flush=True)
            print('POC Cinder preflight: report=' + str(path), flush=True)
            return 0 if report['acceptance']['status'] == 'ready_for_scoped_volume_test' else 2
    except Exception as error:
        # Never print arbitrary SDK/command errors, which may contain URLs or auth details.
        details = fixed_api_failure(error)
        print('STOP: Cinder preflight incomplete: ' + json.dumps(details, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
