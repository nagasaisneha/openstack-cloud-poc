#!/usr/bin/python3
"""Observed application stop/start, protected backup, and scoped VM deletion.

Requires the exact approved task VM UUID on the command line. Never removes
disks/directories manually, deletes other resources, or restores backups.
"""
import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time

import backup_application as backup
import configure_nova as common
import launch_test_vm as launch
import validate_application_guest as application
import validate_guest as guest
import validate_lifecycle as infrastructure
import validate_tenants as tenants

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openstack_cloud_poc.real_openstack import RealLifecycleManagementService, RealOpenStackAdapter

PHASES = ('baseline', 'stopped', 'started', 'backup_stopped', 'backup_verified', 'delete_intent', 'deleted')


def progress(message):
    print('Task 11 application lifecycle: ' + message, flush=True)


def save(state, path, phase):
    if phase not in PHASES:
        raise RuntimeError('invalid application lifecycle evidence phase')
    state['results']['application_lifecycle']['phase'] = phase
    launch.atomic_state(path, state)


def owned_server(adapter, state):
    server = adapter.server(state['project_id'], state['ids']['server'])
    if ((server.metadata or {}).get('poc_run_id') != state['run_id']
            or server.metadata.get('poc_job_id') != state['create_job_id'] or server.name != state['server_name']):
        raise RuntimeError('application server no longer matches its task create record')
    return server


def assert_stopped(adapter, state):
    if not infrastructure.settled(owned_server(adapter, state), 'SHUTOFF', 4):
        raise RuntimeError('guest is not stably SHUTOFF; no offline backup or deletion')
    stopped = common.run(['virsh', '-c', 'qemu:///system', 'list', '--all', '--uuid', '--state-shutoff'], timeout=15).splitlines()
    if state['ids']['server'] not in stopped:
        raise RuntimeError('libvirt does not confirm task guest shutoff; no backup or deletion')


def wait_attachment(admin, cloud, state, original):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            return application.application_attachment(admin, cloud, state, original)
        except RuntimeError:
            time.sleep(2)
    raise RuntimeError('application attachment did not recover after start; no backup/deletion performed')


def run_job(service, state, path, operation, key):
    record = state['results']['application_lifecycle']
    accepted = service.submit({'operation': operation, 'project_id': state['project_id'],
                              'instance_id': state['ids']['server']}, idempotency_key=key)
    record.setdefault('jobs', {})[key] = accepted['job_id']
    launch.atomic_state(path, state)
    progress(operation + ' accepted job=' + accepted['job_id'] + '; awaiting observed completion')
    deadline = time.monotonic() + 220
    previous = None
    while time.monotonic() < deadline:
        job = service.get_job(accepted['job_id'], state['project_id'])
        if job['instance_id'] != state['ids']['server']:
            raise RuntimeError('application operation job associated with another VM')
        if job['state'] != previous:
            progress(operation + ' job state=' + job['state'])
            previous = job['state']
        if job['state'] == 'succeeded':
            if service.submit({'operation': operation, 'project_id': state['project_id'],
                    'instance_id': state['ids']['server']}, idempotency_key=key) != accepted:
                raise RuntimeError('application idempotent operation lookup changed job identity')
            return job
        if job['state'] in ('failed', 'unverified'):
            raise RuntimeError('application operation unresolved; category=' + str(job['failure_code'])
                               + '; existing job was not replayed')
        time.sleep(2)
    raise RuntimeError('application job wait timed out; journal/evidence retained')


def is_absent(get, identifier):
    try:
        get(identifier)
    except Exception as exc:
        if getattr(exc, 'status_code', None) == 404:
            return True
        raise RuntimeError('resource absence was not established by HTTP 404') from None
    return False


def verify_deleted(admin, cloud, state, recovery, timeout=120):
    server_id = state['ids']['server']
    directory = Path('/var/lib/nova/instances') / server_id
    if recovery['source_disk'] != str(directory / 'disk'):
        raise RuntimeError('recovery source path differs from exact task instance path')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        api_absent = is_absent(admin.compute.get_server, server_id)
        port_absent = is_absent(admin.network.get_port, state['ids']['port'])
        ports = list(admin.network.ports(device_id=server_id))
        domains = common.run(['virsh', '-c', 'qemu:///system', 'list', '--all', '--uuid'], timeout=15).splitlines()
        allocations = admin.placement.get('/allocations/' + server_id).json()['allocations']
        limits = cloud.compute.get_limits().absolute
        if (api_absent and port_absent and not ports and server_id not in domains
                and not directory.exists() and not directory.is_symlink()
                and not Path(recovery['source_disk']).exists() and allocations == {}
                and all(getattr(limits, key) == 0 for key in ('total_instances_used', 'total_cores_used', 'total_ram_used'))):
            return {'nova_admin_404': True, 'neutron_port_admin_404': True, 'no_compute_ports': True,
                    'no_libvirt_domain': True, 'instance_directory_absent': True,
                    'placement_allocations_empty': True, 'project_compute_usage_zero': True,
                    'checked_at': application.now()}
        time.sleep(2)
    raise RuntimeError('independent deletion/cleanup checks incomplete; no manual disk/port removal performed')


def execute(admin, cloud, original, state, path):
    record = state['results'].setdefault('application_lifecycle', {'verified': False})
    if record.get('phase') is not None and record['phase'] not in PHASES:
        raise RuntimeError('unknown lifecycle phase; preserving state')
    adapter = RealOpenStackAdapter(cloud, state['project_id'], state['user_id'])
    journal = launch.DATA_DIR / 'application-jobs.sqlite'
    with RealLifecycleManagementService(adapter, journal, state['run_id']) as service:
        if 'phase' not in record:
            network, port = application.application_attachment(admin, cloud, state, original)
            report = guest.inspect_guest(state, network.mtu, port.mac_address,
                expected_name=state['server_name'], known_hosts=application.trust_file())
            record['baseline_boot_id'] = report['boot_id']
            save(state, path, 'baseline')
        if record['phase'] == 'baseline':
            run_job(service, state, path, 'compute.stop', 'task11-application-stop')
            assert_stopped(adapter, state)
            record['stop'] = {'verified': True, 'nova_shutoff': True, 'libvirt_shutoff': True}
            save(state, path, 'stopped')
            progress('application stop: Nova SHUTOFF/power=4 and libvirt shutoff: PASS')
        if record['phase'] == 'stopped':
            run_job(service, state, path, 'compute.start', 'task11-application-start')
            network, port = wait_attachment(admin, cloud, state, original)
            report = guest.inspect_guest(state, network.mtu, port.mac_address,
                expected_name=state['server_name'], known_hosts=application.trust_file())
            if report['boot_id'] == record['baseline_boot_id']:
                raise RuntimeError('start did not produce a new guest boot ID')
            guest.probe_ping(state, network.mtu)
            infrastructure.verify_acceleration(state)
            record['start'] = dict(report, verified=True, kvm_enabled=True)
            save(state, path, 'started')
            progress('application start: new boot ID, SSH/DHCP/metadata/MTU and KVM recovery: PASS')
        if record['phase'] == 'started':
            run_job(service, state, path, 'compute.stop', 'task11-application-stop-for-backup')
            assert_stopped(adapter, state)
            save(state, path, 'backup_stopped')
        if record['phase'] == 'backup_stopped':
            directory, recovery = backup.create_backup(state, path, lambda: assert_stopped(adapter, state))
            record['backup_directory'] = str(directory)
            record['backup_verified'] = True
            save(state, path, 'backup_verified')
            progress('offline disk, SQLite, cloud SQL, live OVSDB and operational archive backup checks: PASS; path=' + str(directory))
        if record['phase'] == 'backup_verified':
            save(state, path, 'delete_intent')
        if record['phase'] == 'delete_intent':
            recovery = backup.verify_bundle(Path(record['backup_directory']), state['ids']['server'])
            if not is_absent(admin.compute.get_server, state['ids']['server']):
                assert_stopped(adapter, state)
            run_job(service, state, path, 'compute.delete', 'task11-application-delete')
            record['deletion'] = verify_deleted(admin, cloud, state, recovery)
            record['verified'] = True
            save(state, path, 'deleted')
            progress('application deletion: operator Nova/Neutron absence, libvirt/disk cleanup, empty Placement and zero quota usage: PASS')
        elif record['phase'] == 'deleted':
            recovery = backup.verify_bundle(Path(record['backup_directory']), state['ids']['server'])
            verify_deleted(admin, cloud, state, recovery)
            progress('previous complete application lifecycle evidence retained; no operation replayed')
    if (record['phase'] != 'deleted' or not record.get('verified') or not record.get('backup_verified')
            or not record.get('stop', {}).get('verified') or not record.get('start', {}).get('verified')):
        raise RuntimeError('application lifecycle evidence incomplete')
    preserved = admin.compute.get_server(original['ids']['server'])
    launch.assert_owned(preserved, original, '', server=True)
    if not infrastructure.settled(preserved, 'ACTIVE', 1):
        raise RuntimeError('original infrastructure-test VM is no longer ACTIVE; lifecycle completed but preservation check needs review')
    progress('original infrastructure-test VM remains ACTIVE; images, network dependencies and all backups preserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delete-owned-test-vm', required=True, metavar='UUID', help='exact manifest-owned application test VM to back up and delete')
    args = parser.parse_args()
    secrets = {}
    try:
        if os.geteuid() != 0:
            raise RuntimeError('run with sudo /usr/bin/python3 -B in the actual Ubuntu 22.04 WSL terminal')
        os.umask(0o077)
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this stage is scoped to Ubuntu 22.04 WSL')
        for command in ('virsh', 'qemu-img', 'mariadb-dump', 'ovsdb-client', 'ovsdb-tool'):
            if not shutil.which(command):
                raise RuntimeError('required recovery command unavailable: ' + command)
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as admin:
                original, _ = guest.load_run(admin, require_ssh=False)
                path = launch.DATA_DIR / 'application.json'
                guest.protected_file(path, 0o600)
                state = json.loads(path.read_text())
                application.validate_manifest(state, original)
                application.verify_journal(state)
                if args.delete_owned_test_vm != state['ids']['server']:
                    raise RuntimeError('explicit delete UUID differs from the owned application test VM')
                if not all(state['results'].get(key, {}).get('verified') for key in ('application_guest', 'packet_isolation')):
                    raise RuntimeError('verified guest and bounded packet evidence required before lifecycle/deletion')
                secret_path = launch.DATA_DIR / 'tenant-secrets.json'
                guest.protected_file(secret_path, 0o600)
                passwords = tenants.tenant_passwords(secret_path)
                secrets.update({'TENANT_' + key: value for key, value in passwords.items()})
                progress('BEGIN; exact task VM=' + state['ids']['server'])
                with tenants.tenant_connection(original['results']['tenant_api']['tenants']['a'], passwords['a']) as cloud:
                    execute(admin, cloud, original, state, path)
                progress('COMPLETE (application stop/start/delete only; final dependency cleanup and operational closeout remain pending)')
        return 0
    except Exception as exc:
        print('STOP: ' + common.redact(f'{type(exc).__name__}: {exc}', secrets), file=sys.stderr)
        print('Evidence/backups retained. No manual resource removal or automatic rollback; the test VM may remain stopped.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
