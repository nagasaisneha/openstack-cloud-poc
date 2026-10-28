#!/usr/bin/python3
"""Bounded local health probes; local alerts and 90 daily audit rotations.

Runs as Horizon, makes no provider writes, sends no external notifications.
Billing/source evidence remains immutable and is never expired by this agent.
"""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import ssl
import stat
import subprocess
import tempfile
import time
from urllib.request import ProxyHandler, HTTPSHandler, build_opener

from openstack_cloud_poc.operations import transition
from openstack_cloud_poc.provider_usage import read_meter
from openstack_cloud_poc.poc_metering import month_bounds

CONFIG = Path('/etc/openstack-poc-operations/config.json')
DIRECTORY = Path('/var/lib/openstack-poc-operations')


def probes(config):
    result = {}
    for unit in config['units']:
        try:
            response = subprocess.run(['systemctl', 'is-active', '--quiet', unit], capture_output=True, timeout=2)
            good = response.returncode == 0
        except Exception:
            good = False
        result['service:' + unit] = {'healthy': good, 'detail': 'active' if good else 'inactive_or_unavailable'}
    client = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile='/etc/ssl/certs/horizon-poc-local-ca.crt')))
    for name, probe in config['http'].items():
        begin = time.monotonic()
        try:
            with client.open(probe['url'], timeout=2) as response:
                if 'backend_ids' in probe:
                    body = json.loads(response.read(4096))
                    good = response.status == 200 and body['server_id'] in probe['backend_ids'] and body['tenant'] == probe['tenant']
                else:
                    good = response.status == 200
        except Exception:
            good = False
        result['http:' + name] = {'healthy': good, 'detail': 'identity_and_http_200' if good else 'http_or_identity_failed',
                                  'latency_ms': round((time.monotonic() - begin) * 1000)}
    now = datetime.now(timezone.utc)
    start, end = month_bounds(now)
    for project in config['project_ids']:
        try:
            report = read_meter(project, start, end)
            age = (now - datetime.fromisoformat(report['latest_source_timestamp'])).total_seconds()
            good = -2 <= age <= 90 and report.get('source_clock_checked') is True
        except Exception:
            good, age = False, None
        result['meter:' + project] = {'healthy': good, 'detail': 'source_fresh' if good else 'source_stale_or_unavailable',
                                      'age_seconds': round(age) if age is not None else None}
    return result


def main():
    os.umask(0o077)
    info = CONFIG.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        raise ValueError('unsafe_operations_config')
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('unsafe_operations_state')
    config = json.loads(CONFIG.read_text())
    path = DIRECTORY / 'status.json'
    previous = {}
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('unsafe_existing_operations_status')
        previous = json.loads(path.read_text()).get('probes', {})
    now = datetime.now(timezone.utc).isoformat()
    current, events = transition(previous, probes(config), now)
    status = {'schema': 1, 'generated_at': now, 'probes': current, 'events': events,
              'active_alerts': sum(item['active'] for item in current.values()),
              'delivery': 'local_dashboard_and_private_audit_log', 'audit_retention': '90 daily rotations; billing evidence retained indefinitely'}
    log = logging.getLogger('poc.operations.audit')
    log.setLevel(logging.INFO)
    handler = TimedRotatingFileHandler(DIRECTORY / 'audit.jsonl', when='midnight', backupCount=90, utc=True, encoding='utf-8')
    log.addHandler(handler)
    for event in events:
        log.info(json.dumps(event, sort_keys=True))
    log.info(json.dumps({'at': now, 'event': 'observation', 'active_alerts': status['active_alerts'], 'probe_count': len(current)}))
    handler.close()
    log.removeHandler(handler)
    descriptor, temporary = tempfile.mkstemp(prefix='.status-', dir=DIRECTORY)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(status, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({'phase': 'operations_observed', 'probe_count': len(current), 'active_alerts': status['active_alerts']}), flush=True)


if __name__ == '__main__':
    main()
