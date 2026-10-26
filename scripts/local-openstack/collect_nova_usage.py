#!/usr/bin/python3
"""Read Nova per-project allocation-hours into a protected evidence report.

Read-only: authenticates with the existing root-only POC admin credential and
queries only the project recorded in the retained Task 11 manifest. No cloud
resources, configuration, or service state are changed.
"""

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

# Absolute-path execution sets sys.path to scripts/local-openstack. Add the
# repository root so the shared application package is importable under sudo.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import configure_nova as common
from openstack_cloud_poc.nova_usage import samples_from_nova_usage


DATA_DIR = Path('/root/openstack-poc-acceptance')
STATE = DATA_DIR / 'run.json'
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')


def parse_hours(value):
    try:
        hours = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('hours must be an integer from 1 to 168') from None
    if not 1 <= hours <= 168:
        raise argparse.ArgumentTypeError('hours must be an integer from 1 to 168')
    return hours


def protected_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('protected input must be a root-owned regular mode-0600 file')
    return json.loads(path.read_text())


def atomic_report(path, report):
    fd, temporary = tempfile.mkstemp(prefix='.nova-usage-', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=parse_hours, default=24,
                        help='lookback window in hours (default: 24; maximum: 168)')
    args = parser.parse_args()
    try:
        with LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = protected_json(STATE)
            if not isinstance(state.get('project_id'), str) or not state.get('ids', {}).get('server'):
                raise RuntimeError('retained Task 11 project and server are required')
            secrets = common.read_secrets(common.SECRET_FILE)
            end = datetime.now(timezone.utc).replace(microsecond=0)
            start = end - timedelta(hours=args.hours)
            with common.connection(secrets) as cloud:
                server = cloud.compute.get_server(state['ids']['server'])
                if server.project_id != state['project_id']:
                    raise RuntimeError('retained VM project ownership check failed')
                response = cloud.compute.get(
                    '/os-simple-tenant-usage/' + state['project_id'],
                    # Nova's usage API expects UTC timestamps without an
                    # offset suffix (unlike many other OpenStack APIs).
                    params={'start': start.strftime('%Y-%m-%dT%H:%M:%S'),
                            'end': end.strftime('%Y-%m-%dT%H:%M:%S')},
                    raise_exc=True,
                )
                samples = samples_from_nova_usage(response.json(), state['project_id'], start, end)
            report_path = DATA_DIR / 'nova-usage-latest.json'
            report = {
                'schema': 1,
                'source': 'openstack:nova.os-simple-tenant-usage',
                'provenance': 'observed',
                'scope': 'one retained Task 11 project; Nova admin usage endpoint',
                'interval_start': start.isoformat(),
                'interval_end': end.isoformat(),
                'sample_count': len(samples),
                'data_state': 'samples_present' if samples else 'no_matching_server_usage',
                'billing_grade': False,
                'items': [{
                    'project_id': x.project_id, 'resource_id': x.resource_id,
                    'metric': x.metric, 'quantity': str(x.quantity), 'unit': x.unit,
                    'interval_start': x.interval_start.isoformat(),
                    'interval_end': x.interval_end.isoformat(), 'source': x.source,
                    'provenance': x.provenance.value,
                } for x in samples],
                'limitations': [
                    'Nova reports allocated vCPU, RAM and local-disk hours; it does not report guest CPU utilization or network traffic.',
                    'Cinder volume-hours, Swift bytes, and storage I/O are not included.',
                    'No pricing, imputation, invoice, or payment is performed.',
                    'Nova usage semantics may use the latest flavor and do not fully represent resize or lifecycle history.',
                ],
            }
            atomic_report(report_path, report)
        print('POC Nova usage: PASS; read-only project allocation usage collected; '
              f"samples={len(samples)}; report={report_path}", flush=True)
        return 0
    except Exception as exc:
        print('STOP: Nova usage collection unavailable: ' + type(exc).__name__ +
              '; no cloud resources or services changed.', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
