#!/usr/bin/python3
"""Real workload, exact source boundaries and independent closed-period reconciliation."""
import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

import browser_resources as controls
import continue_poc as task
import manage_cinder_loop as safety
import verify_poc_guest_access as guest

NAME = 't50-closed-billing'
JOURNAL = '/var/lib/openstack-poc-swift-meter/events.sqlite'


def request(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(20)
        client.connect('/run/openstack-poc-meter/read.sock')
        client.sendall(json.dumps(payload).encode() + b'\n')
        output = bytearray()
        while len(output) < 8 * 1024**2:
            block = client.recv(65536)
            if not block:
                break
            output.extend(block)
    value = json.loads(output)
    safety.require(not value.get('error'), 'meter_request_failed:' + value.get('reason', 'unavailable'))
    return value


def collect(project):
    now = datetime.now(timezone.utc)
    request({'project_id': project, 'collect': True, 'start': (now - timedelta(minutes=1)).isoformat(),
             'end': (now + timedelta(minutes=1)).isoformat()})
    with sqlite3.connect('file:/var/lib/openstack-poc-meter/evidence.sqlite?mode=ro', uri=True) as db:
        row = db.execute('SELECT id,payload FROM snapshots WHERE project_id=? ORDER BY observed_at DESC LIMIT 1',
                         (project,)).fetchone()
    return row[0], json.loads(row[1])


def inventory(cloud, project):
    base = 'http://127.0.0.1:8080/v1/AUTH_' + project
    with sqlite3.connect('file:' + JOURNAL + '?mode=ro', uri=True) as db:
        before = db.execute('SELECT coalesce(max(sequence),0) FROM mutations WHERE project_id=?', (project,)).fetchone()[0]
    objects = {}
    containers = cloud.object_store.get(base + '?format=json', raise_exc=True).json()
    safety.require(len(containers) < 100, 'bounded_billing_container_inventory_exceeded')
    for container in containers:
        name = container['name']
        safety.require(name.isascii(), 'non_ascii_swift_inventory_requires_path_review')
        metadata = cloud.object_store.head(base + '/' + name, raise_exc=True)
        safety.require(not any(metadata.headers.get(key) for key in
            ('X-Versions-Location', 'X-History-Location', 'X-Container-Sysmeta-Versions-Enabled')),
            'swift_versioning_requires_separate_billing_policy')
        safety.require(not metadata.headers.get('X-Container-Write'), 'shared_write_inventory_requires_policy_review')
        values = cloud.object_store.get(base + '/' + name + '?format=json', raise_exc=True).json()
        safety.require(len(values) < 1000, 'bounded_billing_object_inventory_exceeded')
        for value in values:
            path = '/v1/AUTH_' + project + '/' + name + '/' + value['name']
            safety.require(path.isascii() and value['name'], 'unsupported_swift_path')
            key = hashlib.sha256(path.encode()).hexdigest()
            head = cloud.object_store.head('http://127.0.0.1:8080' + path, raise_exc=True)
            safety.require(head.headers.get('X-Static-Large-Object', '').lower() != 'true'
                and not head.headers.get('X-Object-Manifest') and int(head.headers['Content-Length']) == int(value['bytes']),
                'large_object_or_size_inventory_requires_policy_review')
            objects[key] = int(value['bytes'])
    captured_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect('file:' + JOURNAL + '?mode=ro', uri=True) as db:
        after = db.execute('SELECT coalesce(max(sequence),0) FROM mutations WHERE project_id=?', (project,)).fetchone()[0]
        pending = db.execute('SELECT count(*) FROM requests r LEFT JOIN completions c USING(request_id) '
                             'WHERE r.project_id=? AND c.request_id IS NULL', (project,)).fetchone()[0]
    safety.require(before == after and pending == 0, 'swift_inventory_concurrent_mutation')
    return {'objects': objects, 'captured_at': captured_at, 'journal_sequence': after}


def independent(project, first, last, sealed, initial):
    values = {(row['resource_id'], row['metric']): Decimal(row['quantity']) for row in sealed['batch']['samples']}
    cpu = network = 0
    previous = {row['resource_id']: row for row in first['counters']}
    for row in last['counters']:
        old = previous[row['resource_id']]
        safety.require(old['generation'] == row['generation'], 'independent_counter_generation_changed')
        c, n = row['cpu_ns'] - old['cpu_ns'], row['network_bytes'] - old['network_bytes']
        safety.require(c >= 0 and n >= 0 and values[(row['resource_id'], 'compute.cpu_seconds')] == Decimal(c) / 1000000000
            and values[(row['resource_id'], 'network.interface_bytes')] == n, 'independent_counter_endpoint_mismatch')
        cpu += c
        network += n
    start = datetime.fromisoformat(sealed['batch']['interval_start'])
    end = datetime.fromisoformat(sealed['batch']['interval_end'])
    def duration(a, b):
        delta = b - a
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
    # Independent per-object residence segments; the compiler integrates account
    # state instead. Both use actual durable successful-response timestamps.
    lives = {key: (size, start) for key, size in initial['objects'].items()}
    total = Decimal(0)
    with sqlite3.connect('file:' + JOURNAL + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        events = [dict(row) for row in db.execute('SELECT * FROM mutations WHERE project_id=? AND at>=? AND at<? '
            'ORDER BY at,sequence', (project, start.isoformat(), end.isoformat()))]
    for event in events:
        at = datetime.fromisoformat(event['at'])
        key = event['object_key']
        if event['method'] in ('PUT', 'DELETE'):
            if key in lives:
                size, since = lives.pop(key)
                total += size * duration(since, at)
            if event['method'] == 'PUT':
                lives[key] = (event['size_bytes'], at)
    for size, since in lives.values():
        total += size * duration(since, end)
    safety.require(values[('swift-account-' + project, 'storage.object_byte_hours')] == total / 3600,
                   'independent_swift_byte_residence_mismatch')
    safety.require(cpu > 0 and network > 0 and len(events) >= 2, 'real_controlled_workload_not_observed')
    return {'cpu_delta_ns': cpu, 'interface_delta_bytes': network, 'swift_byte_seconds': str(total),
            'swift_mutations_verified': len(events), 'source_endpoint_reconciliation': True}


def main():
    global NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    safety.require(re.fullmatch('[a-z0-9-]{1,32}', args.run_id), 'invalid_billing_run_id')
    NAME += '-' + args.run_id
    logging.disable(logging.CRITICAL)
    source, private = task.initialize()
    project = source['projects']['a']['id']
    with task.locked(), controls.connection(source, private, 'a') as cloud:
        state = task.read(NAME, source)
        safety.require(not state['intents'], 'billing_epoch_preserved_no_replay')
        prefix = 'poc-t50-billing-' + uuid4().hex[:10]
        base = 'http://127.0.0.1:8080/v1/AUTH_' + project + '/' + prefix
        state['container_url'] = base
        task.intent(NAME, state, 'new-workload-container', 'create_only_marked_billing_workload_objects')
        cloud.object_store.put(base, headers={'X-Container-Meta-Poc': state['marker']}, raise_exc=True)
        try:
            cloud.object_store.put(base + '/measured', data=b'A' * 4096, raise_exc=True)
            initial = inventory(cloud, project)
            first_id, first = collect(project)
            state.update(first_snapshot_id=first_id, initial_objects=initial, start=first['observed_at'])
            task.save(NAME, state)
            ingress = task.read('connectivity', source)
            output = guest.ssh(source, ingress, 'a',
                'set -eu\ni=0\ntotal=0\nwhile [ "$i" -lt 50000 ]; do total=$((total+i)); i=$((i+1)); done\nprintf "%s\\n" "$total"\n',
                pin_only=True)
            safety.require(output.strip() == '1249975000', 'guest_cpu_workload_result_mismatch')
            client = build_opener(ProxyHandler({}))
            for ignored in range(10):
                with client.open('http://127.0.0.1:8081/', timeout=3) as response:
                    safety.require(response.status == 200, 'workload_guest_http_failed')
                    identity = json.loads(response.read(4096))
                    safety.require(identity['server_id'] == source['resources']['a']['server']['id'],
                                   'workload_http_guest_identity_changed')
            time.sleep(3)
            cloud.object_store.put(base + '/measured', data=b'B' * 8192, raise_exc=True)
            collect(project)
            time.sleep(3)
            cloud.object_store.delete(base + '/measured', raise_exc=True)
            collect(project)
            time.sleep(3)
            last_id, last = collect(project)
            terminal = inventory(cloud, project)
            result = request({'project_id': project, 'seal': True, 'first_snapshot_id': first_id,
                'last_snapshot_id': last_id, 'initial_objects': initial, 'terminal_objects': terminal})
            safety.require(result['batch']['complete'] and not result['batch']['incomplete_reasons'],
                           'closed_period_not_complete')
            checks = independent(project, first, last, result, initial)
            state.update(phase='closed_source_period_independently_verified', start=result['batch']['interval_start'],
                end=result['batch']['interval_end'], sealed_period=result['measurement_policy']['sealed_period'],
                checks=checks, sample_count=len(result['batch']['samples']),
                metrics=sorted({row['metric'] for row in result['batch']['samples']}))
            task.save(NAME, state)
        finally:
            head = cloud.object_store.head(base, raise_exc=True)
            safety.require(head.headers.get('X-Container-Meta-Poc') == state['marker'], 'billing_cleanup_owner_changed')
            cloud.object_store.delete(base + '/measured', raise_exc=False)
            cloud.object_store.delete(base, raise_exc=True)
        state['checks']['test_container_removed'] = cloud.object_store.head(base, raise_exc=False).status_code == 404
        task.save(NAME, state)
        print(json.dumps({'phase': state['phase'], 'start': state['start'], 'end': state['end'],
                          'metrics': state['metrics'], 'checks': state['checks']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'closed_billing_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__,
            'historical_statements_remain_provisional': True}), flush=True)
        raise SystemExit(2)
