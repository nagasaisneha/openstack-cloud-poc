#!/usr/bin/python3
"""Check live POC health and optionally start the exact retained VM once.

No backup, restore, snapshot, service restart, distro power action, package
change, or resource replacement. --start-retained requires healthy existing
services, preserved resource ownership, and a settled SHUTOFF guest. A durable
start intent prevents replay after an ambiguous response.
"""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import time

import configure_nova as common
import configure_swift as swift
import inspect_cinder_readiness as cinder
import launch_test_vm as launch
import manage_cinder_loop as attachment
import review_operations as operations
import review_restart_readiness as readiness
import validate_guest as guest
import validate_lifecycle as lifecycle
import validate_service_cycle as cycle

REPORT = launch.DATA_DIR / 'return-to-service-20261027.json'
REQUIRED_UNITS = tuple(dict.fromkeys((*cycle.START_UNITS, *swift.ACTIVE_UNITS,
    'apache2.service', 'cinder-scheduler.service', 'cinder-volume.service',
    'openvswitch-switch.service', 'ovn-central.service', 'ovn-controller.service',
    'ovn-controller-vtep.service', 'ovn-northd.service', 'ovn-ovsdb-server-nb.service',
    'ovn-ovsdb-server-sb.service', 'mariadb.service', 'rabbitmq-server.service',
    'libvirtd.service', 'tgt.service')))


def save(record, phase):
    record.update(phase=phase, updated_at=cycle.now())
    launch.atomic_state(REPORT, record)


def submit_start(cloud, state, server):
    attachment.require(state['ids']['server'] == readiness.DEMO_SERVER, 'unexpected_retained_vm')
    if lifecycle.settled(server, 'ACTIVE', 1):
        return {'server_id': readiness.DEMO_SERVER, 'start_requested': False}
    attachment.require(lifecycle.settled(server, 'SHUTOFF', 4), 'retained_vm_not_settled_shutoff')
    # O_EXCL is the replay gate; never remove or reset a prior start record.
    descriptor = os.open(REPORT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    record = {'schema': 1, 'server_id': readiness.DEMO_SERVER,
              'project_id': state['project_id'], 'created_at': cycle.now(),
              'phase': 'vm_start_intent', 'start_requested': True,
              'source_upgrade_performed': False, 'full_wsl_restore_verified': False}
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    cloud.compute.start_server(readiness.DEMO_SERVER)
    save(record, 'vm_start_submitted')
    return record


def check_services(cloud):
    states = {unit: operations.systemd_state(unit)['ActiveState'] for unit in REQUIRED_UNITS}
    attachment.require(all(value == 'active' for value in states.values()), 'required_service_not_active')
    deadline = time.monotonic() + 90
    while True:
        result = cinder.readiness({'cloud': cinder.cloud_inventory(cloud),
                                   'backend_configuration': cinder.backend_configuration()})
        if result['status'] == 'ready_for_scoped_volume_test':
            break
        attachment.require(time.monotonic() < deadline, 'cinder_api_or_backend_not_ready')
        time.sleep(2)
    return {'active_required_service_count': len(states), 'cinder': result,
            'swift': swift.service_health(common.read_secrets(common.SECRET_FILE)),
            'swift_mount_verified': swift.mounted_image()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-retained', action='store_true')
    args = parser.parse_args()
    attachment.host_guard()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    with attachment.locked(attachment.MANAGEMENT_LOCK):
        with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
            state, ignored = guest.load_run(cloud, require_ssh=False)
            readiness.exclusive_demo(cloud, state)
            readiness.journal_quiescent()
            cycle.wait_controls(cloud, state)
            health = check_services(cloud)
            server = readiness.owned_demo(cloud, state)
            if not args.start_retained:
                print(json.dumps({'phase': 'read_only_health', 'server_status': server.status,
                                  'services': health, 'source_upgrade_performed': False,
                                  'full_wsl_restore_verified': False}, sort_keys=True))
                return 0
            record = submit_start(cloud, state, server)
            try:
                record['guest_state'] = cycle.wait_vm(cloud, state, 'ACTIVE', 1)
                record['core'] = readiness.wait_cloud_state(cloud, state)
                record['guest'] = cycle.pinned_guest(cloud, state)
                lifecycle.verify_acceleration(state)
                guest.probe_ping(state, record['guest']['mtu'])
                record['guest_cpu'] = operations.cpu_smoke(state)
                record['services'] = check_services(cloud)
                record.update(source_upgrade_performed=False, full_wsl_restore_verified=False,
                              source_returned_to_service=True, guest_kvm_verified=True,
                              guest_pinned_ssh_verified=True, guest_mtu_ping_verified=True)
                save(record, 'verified')
            except Exception as error:
                record.update(failure_at_phase=record.get('phase'), error_type=type(error).__name__)
                save(record, 'failed')
                raise
    print(json.dumps({'phase': 'verified', 'report': str(REPORT),
                      'active_required_service_count': len(REQUIRED_UNITS),
                      'guest_status': 'ACTIVE', 'guest_kvm_verified': True,
                      'guest_pinned_ssh_verified': True, 'full_wsl_restore_verified': False}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, attachment.GuardError) else type(error).__name__
        print('STOP: POC return to service: ' + reason + '; no automatic start replay')
        raise SystemExit(2)
