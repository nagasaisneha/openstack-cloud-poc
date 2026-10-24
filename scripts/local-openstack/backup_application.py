"""Protected recovery point before deleting the task-created application VM.

Called only by the root lifecycle stage with the VM verified shut off.
Never copies active OVSDB files or a running guest disk. No restoration here.
"""
import contextlib
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest

DATABASES = ('keystone', 'glance', 'placement', 'nova_api', 'nova', 'nova_cell0', 'neutron')
INSTANCES_ROOT = Path('/var/lib/nova/instances')
OVSDB = (('open_vswitch.ovsdb', 'unix:/run/openvswitch/db.sock', 'Open_vSwitch'),
         ('ovn_northbound.ovsdb', 'unix:/run/ovn/ovnnb_db.sock', 'OVN_Northbound'),
         ('ovn_southbound.ovsdb', 'unix:/run/ovn/ovnsb_db.sock', 'OVN_Southbound'))
CONFIGS = ('/etc/keystone', '/etc/glance', '/etc/placement', '/etc/nova', '/etc/neutron',
           '/etc/libvirt', '/etc/openvswitch', '/etc/ovn', '/etc/default/openvswitch-switch',
           '/etc/default/ovn-central', '/etc/default/ovn-host', '/etc/apache2', '/etc/mysql',
           '/etc/rabbitmq', '/etc/netplan', '/etc/logrotate.d/ovn-common',
           '/etc/systemd/system/kvm-device-permissions.service')
DATA_FILES = ('run.json', 'application.json', 'tenant-secrets.json', 'id_ed25519', 'id_ed25519.pub',
              'known_hosts', 'application-known_hosts', 'application-cirros-source.qcow2')


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sync_bundle(directory):
    for path in directory.iterdir():
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in (directory, directory.parent):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def command_to_file(command, path, timeout=120):
    with path.open('xb') as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, timeout=timeout)
        output.flush()
        os.fsync(output.fileno())
    if result.returncode:
        # Backup commands may contain confidential SQL on stderr. Emit only
        # the executable and status, never the raw diagnostic body.
        diagnostic = path.with_name(path.name + '.stderr.log')
        with diagnostic.open('xb') as stream:
            stream.write(result.stderr or b'')
        raise RuntimeError(f'{Path(command[0]).name} backup failed (exit {result.returncode}); partial file and private diagnostic retained at {diagnostic}')
    guest.protected_file(path, 0o600)
    if not path.stat().st_size:
        raise RuntimeError('backup command produced an empty file')


def disk_from_xml(xml, server_id):
    domain = ET.fromstring(xml)
    if domain.get('type') != 'kvm' or domain.findtext('uuid') != server_id or domain.find('os/nvram') is not None:
        raise RuntimeError('unsupported guest identity/type/NVRAM; no disk backup or deletion')
    disks = domain.findall('devices/disk')
    if (len(disks) != 1 or disks[0].get('device') != 'disk' or disks[0].get('type') != 'file'
            or disks[0].find('readonly') is not None or domain.find('devices/tpm') is not None
            or domain.find('devices/filesystem') is not None or domain.find('devices/hostdev') is not None):
        raise RuntimeError('expected one writable file-backed test root disk; no deletion')
    disk = Path(disks[0].find('source').get('file'))
    expected = INSTANCES_ROOT / server_id / 'disk'
    if disk != expected or disk.is_symlink() or disk.resolve() != expected:
        raise RuntimeError('guest root disk is outside its exact task instance path')
    info = disk.lstat()
    parent = disk.parent.lstat()
    if not stat.S_ISREG(info.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RuntimeError('guest root disk/instance path is not a regular file/directory')
    return disk, {'source_disk': str(disk), 'uid': info.st_uid, 'gid': info.st_gid,
        'mode': stat.S_IMODE(info.st_mode), 'directory_uid': parent.st_uid,
        'directory_gid': parent.st_gid, 'directory_mode': stat.S_IMODE(parent.st_mode)}


def flatten_disk(disk, output):
    if output.exists() or output.is_symlink():
        raise RuntimeError('recovery disk destination exists; never overwrite it')
    chain = json.loads(common.run(['qemu-img', 'info', '--backing-chain', '--output=json', str(disk)], timeout=30))
    if (not isinstance(chain, list) or not 1 <= len(chain) <= 8
            or chain[0].get('virtual-size') != 1024 ** 3 or chain[0].get('format') not in ('qcow2', 'raw')):
        raise RuntimeError('unexpected root image chain/size/format; no deletion')
    for entry in chain:
        path = Path(entry['filename'])
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(INSTANCES_ROOT)):
            raise RuntimeError('image chain contains an unexpected/nonlocal source; no deletion')
    common.run(['qemu-img', 'convert', '-f', chain[0]['format'], '-O', 'qcow2', str(disk), str(output)], timeout=120)
    guest.protected_file(output, 0o600)
    common.run(['qemu-img', 'check', '-f', 'qcow2', '--output=json', str(output)], timeout=60)
    saved = json.loads(common.run(['qemu-img', 'info', '--output=json', str(output)], timeout=30))
    if saved.get('backing-filename') or saved.get('virtual-size') != 1024 ** 3 or saved.get('format') != 'qcow2':
        raise RuntimeError('recovery disk is not self-contained expected-size qcow2')
    common.run(['qemu-img', 'compare', '-f', chain[0]['format'], '-F', 'qcow2', str(disk), str(output)], timeout=120)
    return chain[0]['format']


def snapshot_journal(output):
    source = launch.DATA_DIR / 'application-jobs.sqlite'
    guest.protected_file(source, 0o600)
    with output.open('xb'):
        pass
    with contextlib.closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as source_db, \
         contextlib.closing(sqlite3.connect(output)) as target_db:
        source_db.backup(target_db)
        if target_db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise RuntimeError('SQLite backup integrity check failed')
    guest.protected_file(output, 0o600)


def snapshot_databases(directory):
    names = ','.join("'" + name + "'" for name in DATABASES)
    actual = common.sql('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN (' + names + ')')
    if set(actual.splitlines()) != set(DATABASES):
        raise RuntimeError('required cloud database is missing; no deletion')
    if common.sql("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA IN (" + names
                  + ") AND TABLE_TYPE='BASE TABLE' AND COALESCE(ENGINE,'') <> 'InnoDB'"):
        raise RuntimeError('nontransactional cloud tables prevent the planned consistent dump; no deletion')
    output = directory / 'cloud-databases.sql'
    command_to_file(['mariadb-dump', '--no-defaults', '--protocol=socket', '--user=root',
        '--single-transaction', '--quick', '--routines', '--events', '--triggers', '--hex-blob',
        '--databases', *DATABASES], output)
    seen = set()
    with output.open('rb') as stream:
        for line in stream:
            for name in DATABASES:
                if line.strip() == f'USE `{name}`;'.encode():
                    seen.add(name)
        stream.seek(max(0, output.stat().st_size - 4096))
        trailer = stream.read()
    if seen != set(DATABASES) or b'-- Dump completed' not in trailer:
        raise RuntimeError('SQL backup lacks expected database sections/completion trailer')
    for filename, endpoint, database in OVSDB:
        target = directory / filename
        command_to_file(['ovsdb-client', 'backup', endpoint, database], target, timeout=30)
        if common.run(['ovsdb-tool', 'db-name', str(target)], timeout=15).strip() != database:
            raise RuntimeError('OVSDB backup name verification failed')


def snapshot_operational_files(output):
    paths = [launch.DATA_DIR / name for name in DATA_FILES] + [common.SECRET_FILE]
    paths += [Path(name) for name in CONFIGS if Path(name).exists() or Path(name).is_symlink()]
    def no_active_database(member):
        if member.isfile() and member.name.endswith(('.db', '.db.~lock~')):
            return None
        return member
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz', dereference=False) as archive:
        for path in paths:
            if not path.exists() and not path.is_symlink():
                raise RuntimeError('required operational recovery file is missing')
            archive.add(path, arcname=str(path).lstrip('/'), filter=no_active_database)
    # Read through gzip's EOF to verify CRC; never print or extract secrets.
    with gzip.open(output, 'rb') as stream:
        while stream.read(1024 * 1024):
            pass
    with tarfile.open(output, 'r:gz') as archive:
        names = set(archive.getnames())
        if any(str(path).lstrip('/') not in names for path in paths):
            raise RuntimeError('operational archive is incomplete')
    guest.protected_file(output, 0o600)


def verify_bundle(directory, server_id):
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700
            or directory.parent.resolve() != launch.DATA_DIR.resolve()
            or not directory.name.startswith('recovery-' + server_id + '-')):
        raise RuntimeError('invalid protected recovery directory')
    guest.protected_file(directory / 'recovery.json', 0o600)
    record = json.loads((directory / 'recovery.json').read_text())
    if record.get('server_id') != server_id or record.get('verified') is not True:
        raise RuntimeError('recovery record identity/success mismatch')
    required = {'domain.xml', 'guest-root.qcow2', 'application-jobs.sqlite', 'cloud-databases.sql',
                'operational-files.tar.gz', 'RECOVERY.md'} | {entry[0] for entry in OVSDB}
    if (set(record.get('sha256', {})) != required or record.get('disk_check') is not True
            or record.get('disk_compare') is not True):
        raise RuntimeError('recovery bundle lacks required checked artifacts')
    for filename, expected in record['sha256'].items():
        if Path(filename).name != filename:
            raise RuntimeError('invalid recovery artifact path')
        path = directory / filename
        guest.protected_file(path, 0o600)
        if checksum(path) != expected:
            raise RuntimeError('recovery artifact checksum mismatch; no deletion')
    return record


def create_backup(state, state_path, assert_shutoff):
    assert_shutoff()
    if shutil.disk_usage(launch.DATA_DIR).free < 2 * 1024 ** 3:
        raise RuntimeError('less than 2 GiB free for protected recovery files; no deletion')
    directory = Path(tempfile.mkdtemp(prefix='recovery-' + state['ids']['server'] + '-', dir=launch.DATA_DIR))
    lifecycle = state['results']['application_lifecycle']
    lifecycle.setdefault('backup_attempts', []).append(str(directory))
    launch.atomic_state(state_path, state)
    xml = common.run(['virsh', '-c', 'qemu:///system', 'dumpxml', state['ids']['server']], timeout=15)
    (directory / 'domain.xml').write_text(xml)
    disk, metadata = disk_from_xml(xml, state['ids']['server'])
    metadata['source_format'] = flatten_disk(disk, directory / 'guest-root.qcow2')
    assert_shutoff()
    snapshot_journal(directory / 'application-jobs.sqlite')
    snapshot_databases(directory)
    snapshot_operational_files(directory / 'operational-files.tar.gz')
    recovery = '''# Protected application recovery point

This directory contains secrets. Keep root ownership and 0700/0600 modes.
The disk is a self-contained offline copy verified by qemu-img check/compare.
SQLite uses its backup API; cloud SQL uses an InnoDB consistent transaction;
each OVSDB snapshot uses its live endpoint. These are sequential snapshots,
not one distributed transaction. No restore/reboot was exercised.

Preferred data recovery: retain the original files, upload guest-root.qcow2
as a NEW private Glance image, then launch a NEW Nova UUID in the recorded
project/network using the retained key/flavor. Obtain explicit recovery scope
before executing; do not overwrite another disk or replace a live journal.

Full point-in-time rollback requires separate explicit approval because SQL
restore rewinds ALL seven cloud databases. Quiesce cloud API/compute/network
writes, preserve current data first, verify all hashes, restore SQL using the
matching MariaDB client and existing service DB users, and restore the same
OVS/OVN snapshots with matching installed schemas under the documented
service-quiescence recovery procedure. Restore operational files without
overwriting unrelated changes. To recover the SAME Nova UUID, reconstruct
only its absent instance directory with recorded ownership/mode, convert
guest-root.qcow2 to the recorded original format at a NEW temporary path,
and install it only after confirming the recorded source path is absent.
Restore its recorded libvirt domain and start through Nova after service
and database consistency checks. Never run direct SQL restore on a live
cloud or overwrite an existing VM directory. Guest boot and full metadata
restore remain UNVERIFIED; container/content checks are not a boot test.

This is an application-deletion recovery point on the existing host, not a
whole-host/Windows backup. Existing service users, RabbitMQ credentials and
the installed compatible services must remain available. Original historical
assets and pre-Caracal backups are separate and must remain preserved.
'''
    (directory / 'RECOVERY.md').write_text(recovery)
    hashes = {path.name: checksum(path) for path in directory.iterdir() if path.is_file()}
    record = dict(metadata, server_id=state['ids']['server'], verified=True, sha256=hashes,
                  sql_databases=list(DATABASES), disk_check=True, disk_compare=True, restore_exercised=False)
    (directory / 'recovery.json').write_text(json.dumps(record, indent=2) + '\n')
    sync_bundle(directory)
    verify_bundle(directory, state['ids']['server'])
    assert_shutoff()
    return directory, record
