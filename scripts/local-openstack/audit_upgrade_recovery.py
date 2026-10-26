#!/usr/bin/python3
"""Read-only upgrade/recovery inventory; writes only a new protected report.

No package changes, service stops, migrations, broker flag changes or backups.
Raw broker identifiers/SQL diagnostics are never printed. Does not certify WSL
or application recovery: those need actual snapshots and restore evidence.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import configure_nova as common

DATA = Path('/root/openstack-poc-acceptance')
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
SCHEMAS = ('keystone', 'glance', 'placement', 'nova_api', 'nova', 'nova_cell0', 'neutron', 'cinder')
PACKAGES = ('nova-common', 'neutron-common', 'keystone', 'glance-common', 'placement-api',
            'cinder-common', 'swift', 'openstack-dashboard', 'rabbitmq-server', 'erlang-base',
            'mariadb-server-10.6', 'openvswitch-common', 'ovn-common', 'libvirt-daemon', 'qemu-system-x86')
PATHS = {
    'jobs': DATA / 'application-jobs.sqlite',
    'cinder_backend': Path('/var/lib/cinder-poc/cinder-backend.img'),
    'swift_backend': Path('/var/lib/swift-poc/swift-backend.img'),
    'swift_config': Path('/etc/swift'),
    'keystone_keys': Path('/etc/keystone/fernet-keys'),
    'glance_images': Path('/var/lib/glance/images'),
    'nova_instances': Path('/var/lib/nova/instances'),
}


def run(command, timeout=30):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(Path(command[0]).name + '_observation_failed')
    return result.stdout


def protected(path, directory=False):
    info = path.lstat()
    if (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise RuntimeError('unsafe_evidence_metadata')


def database_inventory():
    names = common.sql('SHOW DATABASES').splitlines()
    present = {name: name in names for name in SCHEMAS}
    engines = common.sql("SELECT TABLE_SCHEMA, COALESCE(ENGINE,'NONE'), COUNT(*) "
                         "FROM information_schema.TABLES WHERE TABLE_TYPE='BASE TABLE' "
                         "AND TABLE_SCHEMA IN (" + ','.join("'" + name + "'" for name in SCHEMAS)
                         + ') GROUP BY TABLE_SCHEMA, ENGINE')
    nontransactional = 0
    for line in engines.splitlines():
        schema, engine, count = line.split('\t')
        if engine != 'InnoDB':
            nontransactional += int(count)
    return {'required_schema_presence': present, 'non_innodb_table_count': nontransactional,
            'all_required_present': all(present.values()), 'backup_or_restore_performed': False}


def broker_summary(flags, vhosts, queues):
    if (not isinstance(flags, list) or not isinstance(vhosts, list) or not isinstance(queues, list)
            or any(not isinstance(row, dict) for row in flags + vhosts + queues)):
        raise RuntimeError('unexpected_broker_json')
    states = {}
    for row in flags:
        state = row.get('state')
        key = state if state in ('enabled', 'disabled', 'state_changing') else 'unknown'
        states[key] = states.get(key, 0) + 1
    counts = {field: 0 for field in ('messages', 'messages_ready', 'messages_unacknowledged', 'consumers')}
    kinds = {}
    for row in queues:
        kind = row.get('type')
        key = kind if kind in ('classic', 'quorum', 'stream') else 'unknown'
        kinds[key] = kinds.get(key, 0) + 1
        for field in counts:
            value = row.get(field)
            if type(value) is not int or value < 0:
                raise RuntimeError('unexpected_broker_count')
            counts[field] += value
    return {'feature_flag_state_counts': states, 'vhost_count': len(vhosts),
            'queue_types': kinds, **counts, 'flag_change_performed': False,
            'preserve_all_vhosts_required': True}


def broker_inventory():
    flags = json.loads(run(['rabbitmqctl', '-q', 'list_feature_flags', 'name', 'state', '--formatter', 'json']))
    vhosts = json.loads(run(['rabbitmqctl', '-q', 'list_vhosts', 'name', '--formatter', 'json']))
    queues = []
    private = {}
    for row in vhosts:
        name = row.get('name')
        if not isinstance(name, str) or not name or '\x00' in name:
            raise RuntimeError('unexpected_vhost_identity')
        found = json.loads(run(['rabbitmqctl', '-q', 'list_queues', '-p', name,
                               'name', 'type', 'messages', 'messages_ready', 'messages_unacknowledged',
                               'consumers', '--formatter', 'json']))
        if not isinstance(found, list):
            raise RuntimeError('unexpected_queue_json')
        private[name] = found
        queues.extend(found)
    return broker_summary(flags, vhosts, queues), {'flags': flags, 'queues_by_vhost': private}


def file_inventory():
    result = {}
    for label, path in PATHS.items():
        try:
            info = path.lstat()
            result[label] = {'present': True, 'regular': stat.S_ISREG(info.st_mode),
                             'directory': stat.S_ISDIR(info.st_mode),
                             'symlink': stat.S_ISLNK(info.st_mode),
                             'owner_uid': info.st_uid, 'mode': format(stat.S_IMODE(info.st_mode), '04o'),
                             'bytes': info.st_size if stat.S_ISREG(info.st_mode) else None}
        except FileNotFoundError:
            result[label] = {'present': False}
    # Never read arbitrary user homes or claim a missing ledger means no billing history.
    result['application_sqlite_candidates'] = [p.name for p in sorted(DATA.iterdir())
                                               if p.is_file() and not p.is_symlink()
                                               and p.suffix in ('.sqlite', '.db')]
    result['billing_ledger_location_verified'] = False
    return result


def main():
    if os.geteuid() != 0:
        print('STOP: run sudo /usr/bin/python3 -B ' + str(Path(__file__).resolve()))
        return 1
    os.umask(0o077)
    try:
        protected(DATA, directory=True)
        fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            protected(LOCK)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            summary = {'observed_at': datetime.now(timezone.utc).isoformat(), 'schema': 1,
                       'host_or_cloud_mutations': False, 'recovery_verified': False, 'checks': {}}
            private = {}
            for label, action in (('databases', database_inventory), ('files', file_inventory)):
                try:
                    summary['checks'][label] = {'status': 'observed', **action()}
                except Exception as error:
                    summary['checks'][label] = {'status': 'unavailable', 'error_type': type(error).__name__}
            try:
                value, details = broker_inventory()
                summary['checks']['rabbitmq'] = {'status': 'observed', **value}
                private['rabbitmq'] = details
            except Exception as error:
                summary['checks']['rabbitmq'] = {'status': 'unavailable', 'error_type': type(error).__name__}
            versions = {}
            for package in PACKAGES:
                try:
                    versions[package] = run(['dpkg-query', '-W', '-f=${Version}', package]).strip()
                except Exception:
                    versions[package] = 'unavailable'
            summary['package_versions'] = versions
            summary['required_next_evidence'] = ['full_consistent_backup', 'external_wsl_backup',
                                                  'same_instance_os_rollback_rehearsal',
                                                  'application_jobs_and_billing_restore',
                                                  'cinder_and_swift_recovery']
            fd, name = tempfile.mkstemp(prefix='upgrade-recovery-audit-', suffix='.json', dir=DATA)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'summary': summary, 'private': private}, stream, indent=2, sort_keys=True)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            print('POC upgrade recovery inventory: ' + json.dumps(summary, sort_keys=True) + '; report=' + name)
            return 0 if all(item['status'] == 'observed' for item in summary['checks'].values()) else 2
    except Exception as error:
        print('STOP: upgrade recovery inventory unavailable: ' + type(error).__name__ + '; no services changed.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
