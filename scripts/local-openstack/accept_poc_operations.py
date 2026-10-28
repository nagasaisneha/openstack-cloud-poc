#!/usr/bin/python3
"""Exercise one local monitoring alert open/recover cycle with guaranteed route restoration."""
import json
import logging
import os
from pathlib import Path
import subprocess
import time

import continue_poc as task
import manage_cinder_loop as safety

DIRECTORY = Path('/var/lib/openstack-poc-operations')
PROBE = 'http:tenant-a'
ROUTE_UNIT = 'poc-guest-a-http.service'
AGENT_UNIT = 'poc-operations.service'


def run(args, timeout=45):
    result = subprocess.run(['systemctl', *args], text=True, capture_output=True, timeout=timeout)
    safety.require(result.returncode == 0, 'operations_acceptance_systemctl_' + args[0] + '_failed')
    return result.stdout.strip()


def status():
    value = json.loads((DIRECTORY / 'status.json').read_text())
    safety.require(PROBE in value.get('probes', {}), 'tenant_a_http_probe_missing')
    return value


def tick_until(active):
    deadline = time.monotonic() + 35
    latest = None
    while time.monotonic() < deadline:
        run(['start', AGENT_UNIT], timeout=45)
        latest = status()
        if latest['probes'][PROBE]['active'] is active:
            return latest
        time.sleep(1)
    raise safety.GuardError('local_operations_alert_transition_timeout')


def apply(source):
    safety.require(run(['is-active', ROUTE_UNIT]) == 'active', 'tenant_a_http_route_not_initially_active')
    opened = resolved = None
    run(['stop', ROUTE_UNIT])
    try:
        failed = tick_until(True)
        failed_probe = failed['probes'][PROBE]
        safety.require(failed_probe['healthy'] is False and failed_probe['failures'] >= 2,
                       'tenant_a_http_failure_not_debounced')
        opened = next((event for event in failed['events'] if event.get('probe') == PROBE
                       and event.get('event') == 'opened'), None)
        run(['start', ROUTE_UNIT])
        safety.require(run(['is-active', ROUTE_UNIT]) == 'active', 'tenant_a_http_route_not_restored')
        healthy = tick_until(False)
        healthy_probe = healthy['probes'][PROBE]
        safety.require(healthy_probe['healthy'] is True and healthy_probe['successes'] >= 2,
                       'tenant_a_http_alert_not_resolved_after_recovery')
        resolved = next((event for event in healthy['events'] if event.get('probe') == PROBE
                         and event.get('event') == 'resolved'), None)
        safety.require(opened and opened.get('probe') == PROBE and opened.get('event') == 'opened'
                       and resolved and resolved.get('probe') == PROBE and resolved.get('event') == 'resolved',
                       'tenant_a_http_alert_audit_transitions_missing')
        audit = DIRECTORY / 'audit.jsonl'
        events = []
        for line in audit.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        audited = {(event.get('probe'), event.get('event'), event.get('at')) for event in events}
        safety.require((PROBE, 'opened', opened['at']) in audited and (PROBE, 'resolved', resolved['at']) in audited,
                       'tenant_a_http_audit_file_transitions_missing')
        state = task.read('operations-install', source)
        state.update(alert_acceptance={'probe': PROBE, 'opened': opened['at'], 'resolved': resolved['at'],
            'failure_samples_to_open': failed_probe['failures'], 'success_samples_to_resolve': healthy_probe['successes'],
            'route_restored': True, 'audit_file_transitions_verified': True,
            'audit_mode': oct(os.stat(audit).st_mode & 0o777), 'daily_rotations': 90})
        task.save('operations-install', state)
        print(json.dumps({'phase': 'local_operations_alert_open_and_recovery_verified',
            'probe': PROBE, 'opened': opened['at'], 'resolved': resolved['at'], 'route_restored': True,
            'daily_audit_rotations': 90}), flush=True)
    finally:
        probe = subprocess.run(['systemctl', 'is-active', '--quiet', ROUTE_UNIT], capture_output=True, timeout=5)
        if probe.returncode != 0:
            run(['start', ROUTE_UNIT])


if __name__ == '__main__':
    try:
        logging.disable(logging.CRITICAL)
        source, private = task.initialize()
        with task.locked():
            apply(source)
    except Exception as error:
        print(json.dumps({'phase': 'operations_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
