#!/usr/bin/python3
"""Migrate the existing Cinder image to identity-based loop attachment.

Default: read-only plan. --apply installs the boot helper and starts only the
existing Cinder attachment/volume services. --attach-existing is the installed
systemd helper. No image, PV, VG, filesystem, volume, or snapshot is created,
formatted, detached, restored, or deleted. Swift and other loop users are left
running. Only this backend's LVM filter and protected attachment metadata change.
"""
import argparse
import configparser
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import grp
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile

BASE = Path('/root/openstack-poc-acceptance')
IMAGE = Path('/var/lib/cinder-poc/cinder-backend.img')
STATE = BASE / 'cinder-storage.json'
SETUP = BASE / 'cinder-setup.json'
VG = 'cinder-poc-vg'
SIZE = 8 * 1024 ** 3
HELPER = Path('/usr/local/sbin/cinder-poc-loop-attach')
UNIT = Path('/etc/systemd/system/cinder-poc-loop.service')
FILTER = Path('/etc/cinder/cinder-poc-lvm.conf')
LOCK = Path('/run/lock/openstack-poc-cinder-loop.lock')
MANAGEMENT_LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')
DEVICE = re.compile(r'/dev/loop(?:0|[1-9][0-9]*)\Z')


class GuardError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise GuardError(reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def run(argv, timeout=60):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'command_failed_' + Path(argv[0]).name)
    return result.stdout


def protected(path, mode=None):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            'unsafe_file_' + path.name)
    require(mode is None or stat.S_IMODE(info.st_mode) == mode, 'unexpected_mode_' + path.name)
    return info


def atomic_write(path, content, mode, gid=0):
    if path.exists() or path.is_symlink():
        protected(path)
    descriptor, name = tempfile.mkstemp(prefix='.cinder-loop-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), 0, gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_state():
    protected(STATE, 0o600)
    require(protected(IMAGE, 0o600).st_size == SIZE, 'existing_image_size_changed')
    data = json.loads(STATE.read_text())
    require(data.get('schema') == 1 and data.get('status') == 'prepared'
            and data.get('image') == str(IMAGE) and data.get('volume_group') == VG
            and isinstance(data.get('pv_uuid'), str) and data['pv_uuid']
            and DEVICE.fullmatch(data.get('loop_device', '')),
            'unexpected_existing_cinder_state')
    return data


def inventory():
    data = json.loads(run(['losetup', '--json', '--list', '--output',
                           'NAME,BACK-FILE,OFFSET,SIZELIMIT,RO']))
    require(isinstance(data.get('loopdevices'), list), 'invalid_loop_inventory')
    return data['loopdevices']


def owned_device(devices):
    matches = [item for item in devices if item.get('back-file') == str(IMAGE)]
    require(len(matches) <= 1, 'duplicate_attachments_to_cinder_image')
    if not matches:
        return None
    item = matches[0]
    require(DEVICE.fullmatch(item.get('name', '')) and item.get('offset') == 0
            and item.get('sizelimit') == 0 and item.get('ro') is False,
            'unexpected_cinder_attachment_geometry_or_mode')
    return item['name']


def lvm_filter(device):
    require(isinstance(device, str) and DEVICE.fullmatch(device), 'invalid_loop_device')
    return 'devices {\n    filter = [ "a|^' + device + '$|", "r|.*|" ]\n}\n'


def pv_identity(device):
    # Command-local filters constrain even the read-only scan to this image.
    config = lvm_filter(device).replace('\n', ' ')
    data = json.loads(run(['pvs', '--config', config, '--reportformat', 'json',
                           '--options', 'pv_name,vg_name,pv_uuid', device]))
    rows = data.get('report', [])
    require(len(rows) == 1 and isinstance(rows[0].get('pv'), list)
            and len(rows[0]['pv']) == 1, 'existing_pv_not_found')
    return {key: str(value).strip() for key, value in rows[0]['pv'][0].items()}


def attach_existing():
    state = read_state()
    protected(FILTER)
    require(FILTER.read_text() == lvm_filter(state['loop_device']), 'backend_filter_drift')
    if SETUP.exists():
        protected(SETUP, 0o600)
        setup = json.loads(SETUP.read_text())
        require(setup.get('volume_group') == VG and setup.get('loop_device') == state['loop_device'],
                'existing_setup_metadata_drift')
    else:
        setup = None
    device = owned_device(inventory())
    if device is None:
        device = run(['losetup', '--find', '--show', '--nooverlap', str(IMAGE)]).strip()
        require(DEVICE.fullmatch(device), 'attachment_returned_invalid_device')
    require(owned_device(inventory()) == device, 'image_attachment_identity_unverified')
    pv = pv_identity(device)
    require(pv.get('pv_name') == device and pv.get('vg_name') == VG
            and pv.get('pv_uuid') == state['pv_uuid'], 'existing_pv_or_vg_identity_mismatch')
    # No pvcreate/vgcreate, device detachment, global LVM edits, or image writes.
    atomic_write(FILTER, lvm_filter(device), 0o640, grp.getgrnam('cinder').gr_gid)
    state.update(loop_device=device, attachment_mode='existing_image_identity', updated_at=now())
    atomic_write(STATE, json.dumps(state, sort_keys=True, indent=2) + '\n', 0o600)
    if setup is not None:
        setup.update(loop_device=device, attachment_mode='existing_image_identity', updated_at=now())
        atomic_write(SETUP, json.dumps(setup, sort_keys=True, indent=2) + '\n', 0o600)
    return {'device': device, 'original_pv_uuid_verified': True, 'volume_group': VG}


def host_guard(service=False):
    require(os.geteuid() == 0, 'root_required')
    distribution = os.environ.get('WSL_DISTRO_NAME')
    # systemd services do not necessarily inherit WSL's shell environment.
    require((distribution == 'Ubuntu' or service and distribution is None)
            and 'microsoft' in platform.release().lower()
            and 'VERSION_ID="22.04"' in Path('/etc/os-release').read_text()
            and Path('/proc/1/comm').read_text().strip() == 'systemd',
            'existing_ubuntu_systemd_required')


@contextmanager
def locked(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        info = os.fstat(lock.fileno())
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_attachment_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def migration_plan():
    import prepare_cinder_storage as legacy
    state = read_state()
    source = Path(__file__).resolve().read_text()
    expected_unit = legacy.UNIT_TEXT.replace(str(HELPER) + '\n', str(HELPER) + ' --attach-existing\n')
    for path, allowed in ((HELPER, (legacy.HELPER_TEXT, source)),
                          (UNIT, (legacy.UNIT_TEXT, expected_unit)),
                          (legacy.VOLUME_DROPIN, (legacy.DROPIN_TEXT,)),
                          (FILTER, (lvm_filter(state['loop_device']),))):
        protected(path)
        require(path.read_text() in allowed, 'configuration_drift_' + path.name)
    config_path = Path('/etc/cinder/cinder.conf')
    protected(config_path)
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_path.read_text())
    backends = [name.strip() for name in config.get('DEFAULT', 'enabled_backends').split(',')]
    require(len(backends) == 1 and config.get(backends[0], 'volume_group') == VG
            and config.get(backends[0], 'lvm_conf_file') == str(FILTER),
            'unexpected_cinder_backend_configuration')
    require('ActiveState=inactive' in run(['systemctl', 'show', 'cinder-volume.service',
                                          '--property=ActiveState']), 'cinder_volume_must_be_inactive')
    device = owned_device(inventory())
    return {'schema': 1, 'action': 'attach_existing_cinder_image_by_identity',
            'image': str(IMAGE), 'existing_device': device,
            'original_pv_uuid': state['pv_uuid'], 'volume_group': VG,
            'swift_and_other_devices_unchanged': True,
            'image_format_or_restore': False, 'os_upgrade': False,
            'source_sha256': hashlib.sha256(source.encode()).hexdigest()}, source, expected_unit


def apply():
    plan, source, unit = migration_plan()
    import configure_nova as common
    import validate_guest as guest
    # The authorization is limited to the current POC, with no active volumes.
    with common.connection(common.read_secrets(common.SECRET_FILE)) as cloud:
        guest.load_run(cloud, require_ssh=False)
        require(not list(cloud.block_storage.volumes(all_projects=True))
                and not list(cloud.block_storage.snapshots(all_projects=True)),
                'existing_volume_or_snapshot_requires_review')
    directory = Path(tempfile.mkdtemp(prefix='cinder-loop-migration-', dir=BASE))
    for path in (HELPER, UNIT, FILTER, STATE, SETUP):
        if path.exists():
            (directory / (path.name + '.before')).write_bytes(path.read_bytes())
    report_path = directory / 'report.json'
    report = {'schema': 1, 'created_at': now(), 'plan': plan,
              'phase': 'install_existing_image_helper_intent', 'source_upgrade_performed': False,
              'full_wsl_restore_verified': False}
    def save(phase):
        report.update(phase=phase, updated_at=now())
        atomic_write(report_path, json.dumps(report, sort_keys=True, indent=2) + '\n', 0o600)
    save(report['phase'])
    try:
        atomic_write(HELPER, source, 0o700)
        atomic_write(UNIT, unit, 0o644)
        run(['systemctl', 'daemon-reload'])
        save('start_existing_attachment_intent')
        run(['systemctl', 'start', 'cinder-poc-loop.service'], timeout=90)
        actual = read_state()
        require(owned_device(inventory()) == actual['loop_device'], 'attachment_not_verified')
        report['attachment'] = {'device': actual['loop_device'], 'original_pv_uuid_verified': True}
        save('start_existing_cinder_volume_intent')
        run(['systemctl', 'start', 'cinder-volume.service'], timeout=90)
        require('ActiveState=active' in run(['systemctl', 'show', 'cinder-volume.service',
                                            '--property=ActiveState']), 'cinder_volume_not_active')
        save('existing_cinder_service_active')
    except Exception as error:
        report.update(failure_at_phase=report['phase'], error_type=type(error).__name__)
        if isinstance(error, GuardError):
            report['guard_reason'] = str(error)
        save('failed')
        raise
    print(json.dumps({'phase': report['phase'], 'report': str(report_path),
                      'attachment': report['attachment']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--attach-existing', action='store_true')
    args = parser.parse_args()
    host_guard(service=args.attach_existing)
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    if args.attach_existing:
        with locked(LOCK):
            print(json.dumps(attach_existing()))
    elif args.apply:
        # The installed helper takes its own attachment lock as a child.
        with locked(MANAGEMENT_LOCK):
            apply()
    else:
        print(json.dumps(migration_plan()[0], sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        reason = str(error) if isinstance(error, GuardError) else type(error).__name__
        print('STOP: Cinder image attachment: ' + reason + '; inspect the migration report; no automatic replay')
        raise SystemExit(2)
