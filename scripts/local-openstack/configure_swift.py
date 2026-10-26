#!/usr/bin/python3
"""Guarded, journalled single-device Swift setup for the existing WSL POC.

Creates only new Swift assets. Preserves the current core, Cinder, global
credentials and unrelated services. No object/container acceptance is claimed.
"""
import argparse
import configparser
import datetime
import fcntl
import grp
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import shutil
import stat
import string
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

import configure_nova as common
import inspect_cinder_readiness as observations
import inspect_swift_readiness as preflight
import launch_test_vm as launch


DATA = launch.DATA_DIR
STATE = DATA / 'swift-setup.json'
PRIVATE = DATA / 'swift-setup-private'
IMAGE_DIR = Path('/var/lib/swift-poc')
IMAGE = IMAGE_DIR / 'swift-backend.img'
SIZE = 8 * 1024**3
DEVICE = 'poc1'
DEVICES = Path('/srv/swift-poc')
MOUNT = DEVICES / DEVICE
MOUNT_UNIT = r'srv-swift\x2dpoc-poc1.mount'
MOUNT_UNIT_PATH = Path('/etc/systemd/system') / MOUNT_UNIT
CONF = Path('/etc/swift')
TEMPLATES = Path(__file__).resolve().parents[2] / 'deploy/local-swift'
VERSION = '2.33.0-0ubuntu1.1~cloud0'
API_URL = 'http://127.0.0.1:8080/v1/AUTH_%(project_id)s'
PORTS = {'account': 6202, 'container': 6201, 'object': 6200}
NAMES = ('proxy', 'account', 'account-auditor', 'account-reaper', 'account-replicator',
         'container', 'container-auditor', 'container-reconciler', 'container-replicator',
         'container-sharder', 'container-sync', 'container-updater',
         'object', 'object-auditor', 'object-reconstructor', 'object-replicator', 'object-updater')
ALL_UNITS = tuple('swift-' + name + '.service' for name in NAMES)
ACTIVE_UNITS = tuple('swift-' + name + '.service' for name in
                     ('account', 'container', 'object', 'container-updater', 'object-updater', 'proxy'))
PRESERVED = tuple(Path(path) for path in (
    '/etc/nova/nova.conf', '/etc/nova/nova-compute.conf', '/etc/neutron/neutron.conf',
    '/etc/cinder/cinder.conf', '/etc/iscsi/iscsid.conf', '/etc/apache2/ports.conf',
    str(common.SECRET_FILE)))


class GuardError(RuntimeError):
    """A static non-secret reason suitable for the terminal and report."""


def require(condition, reason):
    if not condition:
        raise GuardError(reason)


def progress(message):
    print('POC Swift setup: ' + message, flush=True)


def protected_file(path, mode=0o600, gid=None):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == mode
            and (gid is None or info.st_gid == gid), 'unsafe_file_metadata')
    return info


def protected_json(path):
    protected_file(path)
    return json.loads(path.read_text())


def safe_directory(path, mode=0o700):
    created = False
    try:
        path.mkdir(mode=mode)
        created = True
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (not created and stat.S_IMODE(info.st_mode) != mode):
        error = GuardError('unsafe_directory_metadata')
        error.context = {'directory': str(path), 'expected_mode': format(mode, '04o'),
                         'observed_mode': format(stat.S_IMODE(info.st_mode), '04o'),
                         'root_owned': info.st_uid == 0, 'is_directory': stat.S_ISDIR(info.st_mode)}
        raise error
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor)
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'directory_changed_during_open')
        if created:
            # mkdir's mode is filtered by the protective process umask. Apply
            # the exact mode only to the new directory, through its open fd.
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        require(stat.S_IMODE(os.fstat(descriptor).st_mode) == mode, 'directory_mode_not_applied')
    finally:
        os.close(descriptor)


def save(state):
    launch.atomic_state(STATE, state)
    descriptor = os.open(STATE.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_config_preimage(path, state, actual_digest):
    """Prove an existing Swift sample file came from this run's package backup."""
    allowed = {'swift.conf', 'proxy-server.conf', 'account-server.conf',
               'container-server.conf', 'object-server.conf'}
    if (path.parent != CONF or path.name not in allowed
            or state.get('package_config_backed_up') is not True):
        return None
    report_path = Path(state.get('readiness_report', ''))
    if (report_path.parent != DATA
            or not re.fullmatch(r'swift-readiness-[0-9a-f]{32}\.json', report_path.name)):
        return None
    try:
        report = protected_json(report_path)
        original = report.get('host', {}).get('paths', {}).get(str(CONF), {})
        if (report.get('installation', {}).get('status') != 'ready_for_guarded_setup'
                or original.get('present') is not False):
            return None
        backup_dir = PRIVATE / 'package-config'
        directory = backup_dir.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
                or directory.st_mode & 0o022):
            return None
        backup = backup_dir / path.name
        info = backup.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022):
            return None
        digest = digest_file(backup)
        return digest if digest == actual_digest else None
    except (GuardError, OSError, ValueError, KeyError, TypeError):
        return None


def preserve_snapshot():
    result = {}
    for path in PRESERVED:
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o002,
                'unsafe_existing_core_configuration')
        result[str(path)] = digest_file(path)
    return result


def check_apt_plan(output):
    require(not re.search(r'^(Remv|Purg)\s', output, re.M), 'package_removal_refused')
    require(not re.search(r'^Inst\s+\S+\s+\[', output, re.M), 'package_upgrade_refused')
    installs = re.findall(r'^Inst\s+(\S+)\s', output, re.M)
    allowed = set(preflight.SERVER_PACKAGES) | {
        'liberasurecode1', 'libgf-complete1', 'libjerasure2', 'python3-pyeclib', 'python3-xattr'}
    require(set(installs).issubset(allowed), 'unreviewed_package_dependency')
    return installs


def fresh_observations(cloud):
    return {'host': {
        'mem_available_kib': int(re.search(r'^MemAvailable:\s+(\d+)',
            Path('/proc/meminfo').read_text(), re.M).group(1)),
        'filesystem': preflight.filesystem_observation(),
        'storage_parents': {name: preflight.path_observation(Path(name)) for name in ('/srv', '/var/lib')},
        'loop_control_present': stat.S_ISCHR(Path('/dev/loop-control').lstat().st_mode),
        'paths': {name: preflight.path_observation(Path(name)) for name in preflight.PATHS},
        'packages': preflight.package_inventory(), 'package_candidates': preflight.package_candidates(),
        'units': {name: observations.systemd_observation(name) for name in preflight.UNITS},
        'listeners': preflight.listener_inventory(common.run(['ss', '-H', '-ltn'], timeout=15)),
    }, 'cloud': preflight.cloud_inventory(cloud)}


def begin_or_load(report_path, cloud):
    if STATE.exists() or STATE.is_symlink():
        state = protected_json(STATE)
        require(state.get('schema') == 1 and state.get('task') == 'T19-local-swift'
                and re.fullmatch(r'[0-9a-f]{32}', state.get('run_id', ''))
                and state.get('image') == str(IMAGE) and state.get('mount') == str(MOUNT),
                'unexpected_setup_journal')
        require(state['preserved_configuration'] == preserve_snapshot(), 'core_configuration_changed')
        return state
    require(report_path.parent == DATA and re.fullmatch(r'swift-readiness-[0-9a-f]{32}\.json', report_path.name),
            'unexpected_readiness_report_path')
    prior = protected_json(report_path)
    require(prior.get('installation', {}).get('status') == 'ready_for_guarded_setup',
            'readiness_report_not_ready')
    current = fresh_observations(cloud)
    require(preflight.installation_readiness(current)['status'] == 'ready_for_guarded_setup',
            'fresh_prerequisites_not_ready')
    require(all(observations.systemd_observation(unit)['load'] == 'not-found' for unit in ALL_UNITS),
            'existing_swift_service_unit')
    require(not any(Path('/etc/systemd/system').glob('swift-*'))
            and not any(Path('/run/systemd/system').glob('swift-*'))
            and not any(Path('/lib/systemd/system').glob('swift-*')), 'existing_swift_unit_override')
    require(cloud.identity.find_user('swift', domain_id='default') is None
            and not [service for service in cloud.identity.services()
                     if service.type == 'object-store' or service.name == 'swift'],
            'existing_swift_identity_or_catalog')
    require(not PRIVATE.exists() and not PRIVATE.is_symlink(), 'unowned_private_setup_directory')
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup('swift')
        except KeyError:
            pass
        else:
            raise GuardError('existing_unix_swift_account_or_group')
    state = {'schema': 1, 'task': 'T19-local-swift', 'run_id': uuid.uuid4().hex,
             'phase': 'initializing', 'image': str(IMAGE), 'mount': str(MOUNT),
             'filesystem_uuid': str(uuid.uuid4()), 'steps': [], 'files': {}, 'file_intents': {},
             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'readiness_report': str(report_path), 'preserved_configuration': preserve_snapshot()}
    save(state)
    return state


def write_owned(path, content, state, mode=0o644, gid=0):
    """Exact-content idempotency; an interrupted write needs its prior intent."""
    encoded = content.encode() if isinstance(content, str) else content
    expected = hashlib.sha256(encoded).hexdigest()
    key = str(path)
    write_required = False
    if path.exists() or path.is_symlink():
        info = protected_file(path, mode, gid)
        actual = digest_file(path)
        recorded_file = state['files'].get(key)
        recorded_intent = state['file_intents'].get(key)
        owned_content = ((recorded_file or recorded_intent) == expected and actual == expected)
        adoptions = state.get('package_config_adoptions', {})
        adoption = adoptions.get(key)
        if (not owned_content and adoption is not None and adoption.get('phase') == 'intent'
                and recorded_file is None and recorded_intent == expected):
            backup_digest = package_config_preimage(path, state, actual)
            require(backup_digest == adoption.get('backup_sha256')
                    and adoption.get('expected_sha256') == expected,
                    'package_config_adoption_drifted')
            write_required = True
        elif not owned_content and recorded_file is None and recorded_intent is None:
            backup_digest = package_config_preimage(path, state, actual)
            if backup_digest is not None:
                adoptions = state.setdefault('package_config_adoptions', {})
                adoption = adoptions.get(key)
                expected_adoption = {'backup_sha256': backup_digest,
                                     'expected_sha256': expected}
                if adoption is None:
                    adoptions[key] = dict(expected_adoption, phase='intent')
                    state['file_intents'][key] = expected
                    save(state)
                    write_required = True
                else:
                    require(all(adoption.get(field) == value
                                for field, value in expected_adoption.items()),
                            'package_config_adoption_drifted')
                    if actual == expected:
                        adoption['phase'] = 'verified'
                        owned_content = True
                        save(state)
                    elif adoption.get('phase') == 'intent':
                        state['file_intents'][key] = expected
                        save(state)
                        write_required = True
        if not owned_content and not write_required:
            error = GuardError('existing_file_not_owned_or_drifted')
            error.context = {
                'path': key,
                'expected_content_matches': actual == expected,
                'recorded_file_matches_expected': recorded_file == expected,
                'recorded_intent_matches_expected': recorded_intent == expected,
                'recorded_file_matches_actual': recorded_file == actual,
                'recorded_intent_matches_actual': recorded_intent == actual,
                'owner_uid': info.st_uid,
                'group_gid': info.st_gid,
                'mode': format(mode, '04o'),
            }
            raise error
    else:
        require(key not in state['files'], 'owned_file_missing')
        state['file_intents'][key] = expected
        save(state)
        write_required = True
    if write_required:
        descriptor, temporary = tempfile.mkstemp(prefix='.swift-poc-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(encoded)
                os.fchown(stream.fileno(), 0, gid)
                os.fchmod(stream.fileno(), mode)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)  # Only this invocation's temporary file.
    state['files'][key] = expected
    state['file_intents'].pop(key, None)
    adoption = state.get('package_config_adoptions', {}).get(key)
    if adoption is not None and adoption.get('expected_sha256') == expected:
        adoption['phase'] = 'verified'
    save(state)


def verify_swift_account(state):
    account = pwd.getpwnam('swift')
    group = grp.getgrnam('swift')
    require(account.pw_uid > 0 and group.gr_gid > 0 and account.pw_gid == group.gr_gid
            and account.pw_shell in {'/usr/sbin/nologin', '/sbin/nologin', '/bin/false'},
            'unexpected_swift_unix_account')
    require(state.get('unix_uid', account.pw_uid) == account.pw_uid
            and state.get('unix_gid', group.gr_gid) == group.gr_gid, 'swift_unix_identity_changed')
    state.update(unix_uid=account.pw_uid, unix_gid=group.gr_gid)
    save(state)


def install_packages(state):
    packages = preflight.package_inventory()
    for name in preflight.SERVER_PACKAGES:
        require(not packages[name]['installed'] or packages[name].get('version') == VERSION,
                'unexpected_installed_swift_version')
    if 'packages' in state['steps']:
        require(all(packages[name]['installed'] for name in preflight.SERVER_PACKAGES), 'owned_package_missing')
        verify_swift_account(state)
        return
    available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M).group(1))
    require(available >= preflight.MIN_AVAILABLE_KIB, 'memory_headroom_lost')
    require(shutil.disk_usage('/var/lib').free >= preflight.MIN_FREE_BYTES, 'disk_headroom_lost')
    args = ['--no-install-recommends', '--no-remove', 'install'] + [name + '=' + VERSION for name in preflight.SERVER_PACKAGES]
    plan = common.run(['apt-get', '-s', *args], timeout=120)
    state['package_plan'] = check_apt_plan(plan)
    state['package_install_intent'] = True
    save(state)
    for unit in ALL_UNITS:
        mask = Path('/etc/systemd/system') / unit
        if mask.exists() or mask.is_symlink():
            require(mask.is_symlink() and os.readlink(mask) == '/dev/null', 'unexpected_swift_unit_mask')
        else:
            os.symlink('/dev/null', mask)
    common.run(['systemctl', 'daemon-reload'], timeout=30)
    output = common.run(['env', 'DEBIAN_FRONTEND=noninteractive', 'NEEDRESTART_MODE=l',
                         'apt-get', '-y', '--no-upgrade', *args], timeout=600)
    write_owned(PRIVATE / ('package-install-' + uuid.uuid4().hex + '.log'), output, state, mode=0o600)
    installed = preflight.package_inventory()
    require(all(installed[name]['installed'] and installed[name].get('version') == VERSION
                for name in preflight.SERVER_PACKAGES), 'package_install_not_verified')
    require(all(observations.systemd_observation(unit)['active'] == 'inactive' for unit in ALL_UNITS),
            'swift_started_during_package_install')
    require(not any(item['count'] for item in preflight.listener_inventory(
        common.run(['ss', '-H', '-ltn'], timeout=15)).values()), 'swift_ports_occupied_before_configuration')
    verify_swift_account(state)


def filesystem_signature():
    result = subprocess.run(['blkid', '-p', '-o', 'export', str(IMAGE)],
                            text=True, capture_output=True, timeout=20, check=False)
    if result.returncode == 2 and not result.stdout.strip():
        return {}
    require(result.returncode == 0, 'filesystem_probe_failed')
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def verify_signature(signature, state):
    require(signature.get('TYPE') == 'ext4' and signature.get('UUID') == state['filesystem_uuid']
            and signature.get('LABEL') == 'swift-poc', 'unexpected_filesystem_signature')


def prepare_filesystem(state):
    safe_directory(IMAGE_DIR)
    if IMAGE.exists() or IMAGE.is_symlink():
        info = protected_file(IMAGE)
        require(state.get('image_create_intent') is True and info.st_size == SIZE, 'unowned_or_wrong_size_image')
    else:
        require(not state.get('image_created'), 'owned_image_missing')
        require(shutil.disk_usage('/var/lib').free >= preflight.MIN_FREE_BYTES, 'disk_headroom_lost')
        state['image_create_intent'] = True
        save(state)
        descriptor = os.open(IMAGE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(descriptor, SIZE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        state['image_created'] = True
        save(state)
    signature = filesystem_signature()
    if not signature:
        require(not state.get('format_intent'), 'previous_format_unverified_review_required')
        require(not common.run(['losetup', '-j', str(IMAGE)], timeout=15).strip(), 'image_already_attached')
        state['format_intent'] = True
        save(state)
        common.run(['mkfs.ext4', '-F', '-q', '-m', '0', '-U', state['filesystem_uuid'],
                    '-L', 'swift-poc', str(IMAGE)], timeout=180)
        signature = filesystem_signature()
    verify_signature(signature, state)
    if 'filesystem' not in state['steps']:
        require(not common.run(['losetup', '-j', str(IMAGE)], timeout=15).strip(), 'image_attached_before_first_mount')
        common.run(['e2fsck', '-f', '-n', str(IMAGE)], timeout=180)
    state['filesystem_verified'] = True
    save(state)


def mount_text():
    return ('[Unit]\nDescription=Bounded local OpenStack Swift POC storage\n'
            'RequiresMountsFor=' + str(IMAGE_DIR) + '\n\n[Mount]\nWhat=' + str(IMAGE) + '\nWhere=' + str(MOUNT) +
            '\nType=ext4\nOptions=loop,nodev,nosuid,noexec\nTimeoutSec=60\n\n'
            '[Install]\nWantedBy=multi-user.target\n')


def mounted_image():
    result = subprocess.run(['findmnt', '--json', '--mountpoint', str(MOUNT),
                             '--output', 'SOURCE,FSTYPE,OPTIONS'], text=True,
                            capture_output=True, timeout=15, check=False)
    if result.returncode == 1 and not result.stdout.strip():
        return False
    require(result.returncode == 0, 'mount_observation_failed')
    rows = json.loads(result.stdout)['filesystems']
    require(len(rows) == 1 and rows[0].get('fstype') == 'ext4', 'unexpected_storage_mount')
    source = rows[0].get('source', '')
    require(re.fullmatch(r'/dev/loop[0-9]+', source) is not None, 'unexpected_mount_source')
    devices = json.loads(common.run(['losetup', '--json', '--list', '--output', 'NAME,BACK-FILE', source], timeout=15))['loopdevices']
    require(len(devices) == 1 and devices[0].get('name') == source
            and devices[0].get('back-file') == str(IMAGE), 'mount_not_backed_by_owned_image')
    options = set(rows[0].get('options', '').split(','))
    require({'rw', 'nodev', 'nosuid', 'noexec'}.issubset(options), 'storage_mount_options_not_enforced')
    return True


def repair_umask_directory(state):
    """Review and correct only the old installer's empty pre-mount directory.

    This migration requires the protected failure journal and original absent-
    path preflight. It does not adopt arbitrary existing directories, alter the
    backend image, or relax the usual guard for existing directories.
    """
    review = state.get('umask_directory_repair')
    info = DEVICES.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0,
            'repair_directory_not_root_owned_directory')
    if review:
        require(review.get('directory') == str(DEVICES)
                and review.get('phase') in {'intent', 'applied'}
                and (review.get('device'), review.get('inode')) == (info.st_dev, info.st_ino),
                'repair_directory_identity_changed')
        if review['phase'] == 'applied':
            require(stat.S_IMODE(info.st_mode) == 0o755, 'repaired_directory_mode_changed')
            return
        failure = review['original_failure']
    else:
        failure = state.get('failure', {})
    require(state.get('phase') == 'mount_and_xattrs' and state.get('steps') == ['packages', 'filesystem']
            and failure.get('stage') == 'mount_and_xattrs'
            and failure.get('reason') == 'unsafe_directory_metadata'
            and state.get('filesystem_verified') is True and state.get('format_intent') is True
            and state.get('image_created') is True and state.get('image_create_intent') is True,
            'repair_not_the_recorded_premount_failure')
    report_path = Path(state['readiness_report'])
    require(report_path.parent == DATA and re.fullmatch(r'swift-readiness-[0-9a-f]{32}\.json', report_path.name),
            'repair_readiness_report_path_invalid')
    report = protected_json(report_path)
    require(report.get('installation', {}).get('status') == 'ready_for_guarded_setup'
            and report.get('host', {}).get('paths', {}).get(str(DEVICES)) == {'present': False},
            'repair_directory_was_not_verified_absent')
    require(not review or review.get('readiness_report_sha256') == digest_file(report_path),
            'repair_readiness_report_changed')
    require(protected_file(IMAGE).st_size == SIZE, 'repair_image_size_changed')
    verify_signature(filesystem_signature(), state)
    require(not common.run(['losetup', '-j', str(IMAGE)], timeout=15).strip(), 'repair_image_already_attached')
    require(not MOUNT.exists() and not MOUNT.is_symlink(), 'repair_mount_path_already_exists')
    unit = MOUNT_UNIT_PATH
    require(not unit.exists() and not unit.is_symlink(), 'repair_mount_unit_already_exists')
    mount = subprocess.run(['findmnt', '--noheadings', '--mountpoint', str(DEVICES)],
                           text=True, capture_output=True, timeout=15, check=False)
    require(mount.returncode == 1 and not mount.stdout.strip(), 'repair_directory_is_mounted_or_unknown')
    descriptor = os.open(DEVICES, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor)
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
                and stat.S_IMODE(current.st_mode) in ({0o700, 0o755} if review else {0o700}),
                'repair_directory_metadata_changed')
        require(os.listdir(descriptor) == [], 'repair_directory_not_empty')
        if not review:
            review = {'directory': str(DEVICES), 'device': current.st_dev, 'inode': current.st_ino,
                      'original_mode': '0700', 'target_mode': '0755', 'phase': 'intent',
                      'original_failure': failure.copy(), 'readiness_report_sha256': digest_file(report_path)}
            state['umask_directory_repair'] = review
            save(state)
        os.fchmod(descriptor, 0o755)
        os.fsync(descriptor)
        require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o755, 'repair_directory_mode_not_applied')
        review['phase'] = 'applied'
        review['applied_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(state)
    finally:
        os.close(descriptor)


def mount_storage(state):
    safe_directory(DEVICES, 0o755)
    if not mounted_image():
        safe_directory(MOUNT, 0o755)
        require(next(MOUNT.iterdir(), None) is None, 'nonempty_unmounted_storage_directory')
    unit = MOUNT_UNIT_PATH
    write_owned(unit, mount_text(), state)
    common.run(['systemctl', 'daemon-reload'], timeout=30)
    common.run(['systemctl', 'enable', '--now', MOUNT_UNIT], timeout=90)
    require(mounted_image(), 'storage_mount_missing')
    require(os.statvfs(MOUNT).f_blocks * os.statvfs(MOUNT).f_frsize <= SIZE, 'storage_size_unexpected')
    os.chown(MOUNT, pwd.getpwnam('swift').pw_uid, grp.getgrnam('swift').gr_gid)
    os.chmod(MOUNT, 0o750)
    # The owned scratch file lives on the actual bounded backend, runs as Swift,
    # and is deleted by its own finally block. No existing object path is used.
    probe = ('import os,tempfile\nfrom pathlib import Path\n'
             'fd,name=tempfile.mkstemp(prefix=".task19-xattr-",dir="' + str(MOUNT) + '")\n'
             'try:\n os.setxattr(fd,"user.swift_poc_probe",b"xattr-ok")\n'
             ' assert os.getxattr(fd,"user.swift_poc_probe")==b"xattr-ok"\n'
             'finally:\n os.close(fd)\n Path(name).unlink()\n')
    common.run(['runuser', '-u', 'swift', '--', '/usr/bin/python3', '-c', probe], timeout=20)
    state['xattr_probe_passed'] = True
    save(state)


def setup_secrets(state):
    path = PRIVATE / 'secrets.json'
    if path.exists() or path.is_symlink():
        values = protected_json(path)
        require(values.get('run_id') == state['run_id'] and all(
            re.fullmatch(r'[0-9a-f]{64}', values.get(key, ''))
            for key in ('SWIFT_PASS', 'hash_prefix', 'hash_suffix')), 'unexpected_swift_secrets')
        if not state.get('secrets_created'):
            state['secrets_created'] = True
            save(state)
        return values
    require(not state.get('secrets_created'), 'retained_swift_secrets_missing')
    values = {'run_id': state['run_id'], **{key: secrets.token_hex(32)
              for key in ('SWIFT_PASS', 'hash_prefix', 'hash_suffix')}}
    launch.atomic_state(path, values)
    state['secrets_created'] = True
    save(state)
    return values


def role_ids_from_assignments(assignments):
    """Read Keystone role IDs without relying on SDK's flattened role_id."""
    role_ids = set()
    for assignment in assignments:
        role = getattr(assignment, 'role', None)
        if isinstance(role, dict):
            role_id = role.get('id')
        else:
            role_id = getattr(role, 'id', None)
        if role_id is None:
            role_id = getattr(assignment, 'role_id', None)
        if role_id:
            role_ids.add(role_id)
    return role_ids


def validate_swift_endpoint(endpoint, region_id, service_id):
    interface_valid = endpoint.interface in {'public', 'internal', 'admin'}
    region_matches = endpoint.region_id == region_id
    url_matches = endpoint.url == API_URL
    enabled = endpoint.is_enabled is True
    service_matches = endpoint.service_id == service_id
    if not (interface_valid and region_matches and url_matches and enabled and service_matches):
        error = GuardError('unexpected_swift_endpoint')
        error.context = {
            'endpoint_id': endpoint.id,
            'interface': endpoint.interface,
            'interface_valid': interface_valid,
            'region_id_present': bool(endpoint.region_id),
            'region_id_matches_expected': region_matches,
            'url_matches_expected_loopback_template': url_matches,
            'enabled': enabled,
            'service_id_matches_task_service': service_matches,
        }
        raise error


def repair_regionless_owned_endpoint(identity, endpoint, region, service, state):
    """Repair only endpoints created by this run with the old region field."""
    require(regionless_endpoint_matches(endpoint, service)
            and state.get('service_create_intent') is True
            and state.get('service_id') == service.id, 'unexpected_swift_endpoint')

    readiness_path = Path(state.get('readiness_report', ''))
    require(readiness_path.parent == DATA
            and re.fullmatch(r'swift-readiness-[0-9a-f]{32}\.json', readiness_path.name),
            'swift_endpoint_repair_missing_preflight')
    readiness = protected_json(readiness_path)
    require(readiness.get('installation', {}).get('status') == 'ready_for_guarded_setup'
            and readiness.get('cloud', {}).get('endpoint_present') is False
            and readiness.get('cloud', {}).get('endpoint_status') == 'not_in_catalog',
            'swift_endpoint_repair_missing_catalog_absence')

    repairs = state.setdefault('endpoint_region_repairs', {})
    key = endpoint.id
    intent = repairs.get(key)
    expected_intent = {'phase': 'intent', 'service_id': service.id,
                       'interface': endpoint.interface, 'region_id': region.id,
                       'url_sha256': hashlib.sha256(API_URL.encode()).hexdigest()}
    if intent is None:
        repairs[key] = expected_intent
        save(state)
    else:
        require(all(intent.get(field) == value for field, value in expected_intent.items()),
                'swift_endpoint_repair_intent_drifted')

    identity.update_endpoint(endpoint, region_id=region.id)
    repaired = identity.get_endpoint(endpoint.id)
    validate_swift_endpoint(repaired, region.id, service.id)
    repairs[key] = dict(expected_intent, phase='verified')
    save(state)


def regionless_endpoint_matches(endpoint, service):
    return (endpoint.interface in {'public', 'internal', 'admin'}
            and endpoint.url == API_URL and endpoint.is_enabled is True
            and endpoint.service_id == service.id and endpoint.region_id is None)


def complete_interrupted_endpoint_repair(endpoint, region, service, state):
    intent = state.get('endpoint_region_repairs', {}).get(endpoint.id)
    if intent is None or intent.get('phase') != 'intent':
        return
    require(intent.get('service_id') == service.id
            and intent.get('interface') == endpoint.interface
            and intent.get('region_id') == region.id
            and intent.get('url_sha256') == hashlib.sha256(API_URL.encode()).hexdigest(),
            'swift_endpoint_repair_intent_drifted')
    intent['phase'] = 'verified'
    save(state)


def ensure_identity(cloud, values, state):
    identity = cloud.identity
    marker = 'T19 local Swift ' + state['run_id']
    user = identity.find_user('swift', domain_id='default')
    if user is None:
        require(not state.get('user_id'), 'owned_swift_identity_missing')
        state['user_create_intent'] = True
        save(state)
        user = identity.create_user(name='swift', domain_id='default',
                                    description=marker, password=values['SWIFT_PASS'], is_enabled=True)
    require(user.description == marker and user.is_enabled
            and state.get('user_create_intent') is True
            and state.get('user_id', user.id) == user.id, 'swift_identity_not_owned')
    with common.connection(values, 'swift', scoped=False) as candidate:
        require(candidate.session.get_user_id() == user.id, 'swift_service_password_mismatch')
    state['user_id'] = user.id
    save(state)
    project = identity.find_project('service', domain_id='default', ignore_missing=False)
    assignments = list(identity.role_assignments(user_id=user.id, scope_project_id=project.id))
    assigned_role_ids = role_ids_from_assignments(assignments)
    for role_name in ('admin', 'service'):
        role = identity.find_role(role_name, ignore_missing=False)
        if role.id not in assigned_role_ids:
            identity.assign_project_role_to_user(project, user, role)
            assigned_role_ids.add(role.id)
    with common.connection(values, 'swift') as candidate:
        candidate.session.get_token()
    unused = 'poc_swift_unused_' + state['run_id']
    require(identity.find_role(unused) is None, 'reseller_role_name_conflict')
    services = [item for item in identity.services() if item.type == 'object-store' or item.name == 'swift']
    require(len(services) <= 1, 'conflicting_swift_catalog')
    if services:
        service = services[0]
        require(state.get('service_create_intent') is True and service.type == 'object-store'
                and service.name == 'swift' and service.description == marker
                and service.is_enabled and state.get('service_id', service.id) == service.id,
                'swift_catalog_not_owned')
    else:
        require(not state.get('service_id'), 'owned_swift_catalog_missing')
        state['service_create_intent'] = True
        save(state)
        service = identity.create_service(name='swift', type='object-store',
                                          description=marker, is_enabled=True)
    state['service_id'] = service.id
    save(state)
    region = identity.find_region('RegionOne', ignore_missing=False)
    endpoints = list(identity.endpoints(service_id=service.id))
    for endpoint in endpoints:
        require(endpoint.interface in {'public', 'internal', 'admin'}, 'unexpected_swift_endpoint')
    for interface in ('public', 'internal', 'admin'):
        require(sum(item.interface == interface for item in endpoints) <= 1,
                'duplicate_swift_endpoint')
    # Validate every existing endpoint before repairing any region field.
    for endpoint in endpoints:
        if endpoint.region_id is None:
            require(regionless_endpoint_matches(endpoint, service), 'unexpected_swift_endpoint')
        else:
            validate_swift_endpoint(endpoint, region.id, service.id)
    for endpoint in endpoints:
        if endpoint.region_id is None:
            repair_regionless_owned_endpoint(identity, endpoint, region, service, state)
        validate_swift_endpoint(endpoint, region.id, service.id)
        complete_interrupted_endpoint_repair(endpoint, region, service, state)
    for interface in ('public', 'internal', 'admin'):
        matches = [item for item in endpoints if item.interface == interface]
        if not matches:
            identity.create_endpoint(service_id=service.id, interface=interface,
                                     region_id=region.id, url=API_URL, is_enabled=True)
    state['unused_reseller_role'] = unused
    save(state)


def validate_ring(ring, server):
    devices = [item for item in ring.devs if item is not None]
    require(len(devices) == 1 and ring.replica_count == 1 and ring._part_shift == 24,
            'unexpected_ring_topology')
    device = devices[0]
    require(device.get('id') == 0 and device.get('region') == 1 and device.get('zone') == 1
            and device.get('ip') == '127.0.0.1' and device.get('port') == PORTS[server]
            and device.get('replication_ip') == '127.0.0.1'
            and device.get('replication_port') == PORTS[server]
            and device.get('device') == DEVICE, 'unexpected_ring_device')
    require(all(index == 0 for row in ring._replica2part2dev_id for index in row),
            'unexpected_ring_partition_assignment')


def restrict_generated_ring(path):
    """Restrict Swift RingData's explicit 0644 output inside the private dir."""
    require(path.parent == PRIVATE and path.name in {name + '.ring.gz' for name in PORTS},
            'unexpected_private_ring_path')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) in {0o600, 0o644}, 'unsafe_file_metadata')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        current = os.fstat(descriptor)
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
                and stat.S_ISREG(current.st_mode) and current.st_uid == 0 and current.st_gid == 0,
                'ring_changed_during_open')
        if stat.S_IMODE(current.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    protected_file(path)


def configure_rings(state, gid):
    importlib.invalidate_caches()
    from swift.common.ring.builder import RingBuilder
    from swift.common.ring.ring import RingData
    for server, port in PORTS.items():
        # Sources are generated in a root-only directory, then validated and
        # copied through the same hash/intent guard as other task-owned files.
        source = PRIVATE / (server + '.ring.gz')
        builder_path = PRIVATE / (server + '.builder')
        if not source.exists() and not source.is_symlink():
            require(not builder_path.exists(), 'partial_ring_builder_requires_review')
            builder = RingBuilder(8, 1, 1)
            builder.add_dev({'id': 0, 'region': 1, 'zone': 1, 'ip': '127.0.0.1',
                             'port': port, 'replication_ip': '127.0.0.1',
                             'replication_port': port, 'device': DEVICE, 'weight': 100.0, 'meta': ''})
            builder.rebalance(seed=int(state['run_id'], 16))
            builder.validate()
            builder.save(str(builder_path))
            builder.get_ring().save(str(source))
        restrict_generated_ring(source)
        validate_ring(RingData.load(str(source)), server)
        protected_file(builder_path)
        write_owned(CONF / source.name, source.read_bytes(), state, mode=0o640, gid=gid)


def rendered_config(template, values):
    content = string.Template((TEMPLATES / template).read_text()).substitute(values)
    configparser.ConfigParser(interpolation=None, strict=True).read_string(content)
    return content


def configure_files(values, state):
    gid = grp.getgrnam('swift').gr_gid
    info = CONF.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            'unsafe_swift_configuration_directory')
    backup = PRIVATE / 'package-config'
    if not state.get('package_config_backed_up'):
        require(not backup.exists(), 'partial_configuration_backup_requires_review')
        shutil.copytree(CONF, backup, symlinks=True)
        state['package_config_backed_up'] = True
        save(state)
    substitutions = {'hash_prefix': values['hash_prefix'], 'hash_suffix': values['hash_suffix'],
                     'service_password': values['SWIFT_PASS'],
                     'unused_reseller_role': state['unused_reseller_role']}
    for name in ('swift', 'proxy-server'):
        write_owned(CONF / (name + '.conf'), rendered_config(name + '.conf.tmpl', substitutions),
                    state, mode=0o640, gid=gid)
    for server, port in PORTS.items():
        content = rendered_config('backend-server.conf.tmpl', {'server': server, 'port': port})
        write_owned(CONF / (server + '-server.conf'), content, state, mode=0o640, gid=gid)
    configure_rings(state, gid)
    for unit in ACTIVE_UNITS:
        folder = Path('/etc/systemd/system') / (unit + '.d')
        safe_directory(folder, 0o755)
        write_owned(folder / 'swift-poc-storage.conf', '[Unit]\nRequires=' + MOUNT_UNIT +
                    '\nAfter=' + MOUNT_UNIT + '\n', state)


def verify_owned_files(state):
    gid = grp.getgrnam('swift').gr_gid
    for name, expected in state['files'].items():
        path = Path(name)
        if path.parent == CONF:
            protected_file(path, 0o640, gid)
        elif path.parent == PRIVATE:
            protected_file(path)
        else:
            protected_file(path, 0o644, 0)
        require(digest_file(path) == expected, 'owned_file_drifted')


def service_health(values):
    import requests
    require(all(observations.systemd_observation(unit)['active'] == 'active' for unit in ACTIVE_UNITS),
            'swift_service_inactive')
    listeners = preflight.listener_inventory(common.run(['ss', '-H', '-ltn'], timeout=15))
    require(all(not item['count'] or item['loopback_only'] for item in listeners.values()),
            'swift_listener_exposed')
    require(all(item['count'] > 0 for item in listeners.values()), 'swift_listener_missing')
    with requests.Session() as local:
        local.trust_env = False
        for port in preflight.PORTS:
            response = local.get('http://127.0.0.1:' + str(port) + '/healthcheck',
                                 timeout=10, allow_redirects=False)
            require(response.status_code == 200 and response.content.strip() == b'OK',
                    'swift_healthcheck_failed')
        anonymous = local.head('http://127.0.0.1:8080/v1/AUTH_' + '0' * 32,
                               timeout=10, allow_redirects=False)
        require(anonymous.status_code in (401, 403), 'anonymous_account_request_not_denied')
    # Fresh token/catalog after endpoint creation; no objects or containers.
    with common.connection(values) as cloud:
        project = cloud.session.get_project_id()
        require(re.fullmatch(r'[0-9a-f]{32}', project) is not None, 'invalid_project_scope')
        endpoint = cloud.session.get_endpoint(service_type='object-store', interface='internal', region_name='RegionOne')
        if endpoint == API_URL:
            endpoint = 'http://127.0.0.1:8080/v1/AUTH_' + project
        require(preflight.expected_account_endpoint(endpoint, project), 'unexpected_authenticated_swift_endpoint')
        response = cloud.session.head(endpoint, timeout=10, allow_redirects=False, raise_exc=False)
        require(response.status_code in (200, 204), 'authenticated_account_head_failed')
    return {'loopback_listeners': True, 'healthchecks': True, 'anonymous_denied': True,
            'authenticated_account_head': True, 'object_round_trip': 'not_tested'}


def start_services(values, state):
    verify_owned_files(state)
    require(mounted_image(), 'storage_mount_missing')
    common.run(['systemctl', 'daemon-reload'], timeout=30)
    for unit in ACTIVE_UNITS:
        mask = Path('/etc/systemd/system') / unit
        if mask.is_symlink() and os.readlink(mask) == '/dev/null':
            common.run(['systemctl', 'unmask', unit], timeout=30)
        common.run(['systemctl', 'enable', '--now', unit], timeout=90)
    for unit in set(ALL_UNITS) - set(ACTIVE_UNITS):
        require(observations.systemd_observation(unit)['load'] == 'masked', 'unexpected_extra_swift_service')
    deadline = time.monotonic() + 90
    while True:
        try:
            return service_health(values)
        except Exception as error:
            if isinstance(error, GuardError) and str(error) == 'swift_listener_exposed':
                common.run(['systemctl', 'stop', *reversed(ACTIVE_UNITS)], timeout=90)
                state['containment'] = 'owned_swift_services_stopped_after_exposed_listener'
                save(state)
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(3)


def check_core(cloud, state, *, baseline=False):
    require(preserve_snapshot() == state['preserved_configuration'], 'core_configuration_changed')
    for unit in ('apache2.service', 'nova-compute.service', 'neutron-server.service',
                 'cinder-volume.service', 'cinder-scheduler.service', 'tgt.service'):
        require(observations.systemd_observation(unit)['active'] == 'active', 'existing_core_service_not_active')
    manifest = protected_json(DATA / 'run.json')
    server_id = manifest['ids']['server']
    server = cloud.compute.get_server(server_id)
    require(server.status == 'ACTIVE' and not server.task_state, 'retained_guest_not_active')
    if baseline:
        state['retained_server_id'] = server_id
        save(state)
    else:
        require(state.get('retained_server_id') == server_id, 'retained_guest_identity_changed')
    # Existing Cinder service/capacity and exact loopback API/iSCSI binds.
    import prepare_cinder_storage as cinder_storage
    import configure_cinder as cinder_setup
    cinder_setup.verify_storage()
    require(cinder_storage.loop_inventory() == str(cinder_storage.IMAGE), 'cinder_loop_attachment_changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--readiness-report', type=Path, required=True,
                        help='Protected ready_for_guarded_setup Swift report')
    parser.add_argument('--repair-umask-directory', action='store_true',
                        help='Review/correct the old installer\'s empty owned pre-mount directory, then resume')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    state = None
    stage = 'host_guards'
    try:
        release = Path('/etc/os-release').read_text()
        require(re.search(r'^ID=ubuntu$', release, re.M)
                and re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                and 'microsoft' in platform.release().lower(), 'outside_existing_wsl_poc')
        safe_directory(DATA)
        descriptor = os.open(observations.LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            protected_file(observations.LOCK)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN; current core, Cinder and existing data preserved')
            base = common.read_secrets(common.SECRET_FILE)
            with common.connection(base) as cloud:
                stage = 'initial_inventory'
                if args.repair_umask_directory:
                    require(STATE.exists(), 'repair_requires_existing_setup_journal')
                state = begin_or_load(args.readiness_report, cloud)
                safe_directory(PRIVATE)
                check_core(cloud, state, baseline=not bool(state.get('retained_server_id')))
                if args.repair_umask_directory:
                    stage = 'owned_directory_resume'
                    progress(stage + ': BEGIN')
                    repair_umask_directory(state)
                    progress(stage + ': PASS')
                values = dict(base, **setup_secrets(state))
                for stage, action in (
                    ('packages', lambda: install_packages(state)),
                    ('filesystem', lambda: prepare_filesystem(state)),
                    ('mount_and_xattrs', lambda: mount_storage(state)),
                    ('keystone_identity_and_catalog', lambda: ensure_identity(cloud, values, state)),
                    ('configuration_and_rings', lambda: configure_files(values, state)),
                    ('services_and_authenticated_api', lambda: start_services(values, state)),
                    ('core_preservation', lambda: check_core(cloud, state)),
                ):
                    state['phase'] = stage
                    save(state)
                    progress(stage + ': BEGIN')
                    result = action()
                    if stage not in state['steps']:
                        state['steps'].append(stage)
                    if stage == 'services_and_authenticated_api':
                        state['health'] = result
                    state.pop('failure', None)
                    save(state)
                    progress(stage + ': PASS')
                state['phase'] = 'service_ready_pending_object_acceptance'
                state['verified_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                save(state)
                progress('PASS (service setup; object round trip pending); report=' + str(STATE))
                return 0
    except Exception as error:
        failure = observations.fixed_api_failure(error)
        failure['stage'] = stage
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            frame = frames[-1]
            failure['origin'] = {'module': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
            # The deepest SDK frame alone is often not actionable. Include a
            # short static call trace, never locals, exception text, or bodies.
            failure['call_trace'] = [
                {'module': Path(item.filename).name, 'function': item.name, 'line': item.lineno}
                for item in frames[-6:]
            ]
        if isinstance(error, GuardError):
            failure['reason'] = str(error)
            if getattr(error, 'context', None):
                failure['context'] = error.context
        if state is not None:
            state['failure'] = failure
            save(state)
        print('STOP: Swift setup incomplete: ' + json.dumps(failure, sort_keys=True)
              + '; preserve owned partial assets and inspect before retry.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
