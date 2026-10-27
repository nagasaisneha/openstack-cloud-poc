#!/usr/bin/python3
"""Prepare a dedicated loop-backed LVM VG for the local Cinder POC.

This is scoped to the existing Ubuntu 22.04 WSL host. It creates one new
8-GiB sparse backing file, uses only the verified-free /dev/loop0, and never
changes the host-wide LVM filter or scans/writes another block device.
"""

import fcntl
import grp
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys

import configure_nova as common


IMAGE = Path('/var/lib/cinder-poc/cinder-backend.img')
IMAGE_DIR = IMAGE.parent
LOOP = Path('/dev/loop0')
VG = 'cinder-poc-vg'
SIZE = 8 * 1024 ** 3
LVM_CONFIG = Path('/etc/cinder/cinder-poc-lvm.conf')
HELPER = Path('/usr/local/sbin/cinder-poc-loop-attach')
UNIT = Path('/etc/systemd/system/cinder-poc-loop.service')
VOLUME_DROPIN = Path('/etc/systemd/system/cinder-volume.service.d/cinder-poc-loop.conf')
STATE = Path('/root/openstack-poc-acceptance/cinder-storage.json')
LOCK = Path('/root/.openstack-poc-nova-prerequisites.lock')

HELPER_TEXT = '''#!/bin/sh
set -eu
image=/var/lib/cinder-poc/cinder-backend.img
device=/dev/loop0
[ -f "$image" ] && [ ! -L "$image" ] || exit 1
current=$(losetup --noheadings --output BACK-FILE "$device" 2>/dev/null || true)
if [ "$current" = "$image" ]; then
    exit 0
fi
[ -z "$current" ] || exit 1
exec losetup "$device" "$image"
'''

UNIT_TEXT = '''[Unit]
Description=Attach the dedicated OpenStack Cinder POC loop device
After=local-fs.target
Before=cinder-volume.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/cinder-poc-loop-attach

[Install]
WantedBy=multi-user.target
'''

DROPIN_TEXT = '''[Unit]
Requires=cinder-poc-loop.service
After=cinder-poc-loop.service
'''

LVM_TEXT = '''devices {
    filter = [ "a|^/dev/loop0$|", "r|.*|" ]
}
'''


def progress(message):
    print(f'POC Cinder storage: {message}', flush=True)


def run(command, timeout=60):
    return common.run(command, timeout=timeout)


def atomic_create(path, content, mode, gid=0):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f'refusing symlink at task file path: {path}')
    if path.exists():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != mode or info.st_gid != gid
                or path.read_text() != content):
            raise RuntimeError(f'existing file differs from the expected task file: {path}')
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = __import__('tempfile').mkstemp(prefix='.cinder-poc-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), 0, gid)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def loop_inventory(device=LOOP):
    expected_device = str(device)
    if not re.fullmatch(r'/dev/loop(?:0|[1-9][0-9]*)', expected_device):
        raise RuntimeError('invalid dedicated loop device')
    raw = run(['losetup', '--json', '--list', '--output', 'NAME,BACK-FILE', expected_device])
    body = json.loads(raw)
    devices = body.get('loopdevices', [])
    if not isinstance(devices, list) or len(devices) > 1:
        raise RuntimeError('unexpected loop device inventory')
    if not devices:
        return None
    device = devices[0]
    name = device.get('name')
    backing = device.get('back-file')
    if name != expected_device:
        raise RuntimeError('unexpected loop device identity')
    if backing is None or backing == '':
        return None
    if not isinstance(backing, str):
        raise RuntimeError('unexpected loop backing-file field')
    return backing


def pv_inventory(device=LOOP):
    if not re.fullmatch(r'/dev/loop(?:0|[1-9][0-9]*)', str(device)):
        raise RuntimeError('invalid dedicated loop device')
    result = subprocess.run(
        ['pvs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
         '--options', 'pv_name,vg_name,pv_uuid', str(device)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    body = parse_pv_report(result.stdout, result.returncode)
    reports = body.get('report', [])
    if len(reports) != 1 or not isinstance(reports[0].get('pv'), list):
        raise RuntimeError('unexpected LVM PV report schema')
    rows = reports[0]['pv']
    if len(rows) > 1:
        raise RuntimeError('multiple PVs reported for the dedicated loop device')
    return rows[0] if rows else None


def parse_pv_report(raw, returncode):
    body = json.loads(raw)
    reports = body.get('report', [])
    if len(reports) != 1 or not isinstance(reports[0].get('pv'), list):
        raise RuntimeError('unexpected LVM PV report schema')
    rows = reports[0]['pv']
    if returncode == 0 or (returncode == 5 and not rows):
        return body
    raise RuntimeError('LVM PV query failed or returned an inconsistent status')


def validate_loop_attachment(backing):
    if backing not in (None, str(IMAGE)):
        raise RuntimeError('/dev/loop0 is attached to a different backing file; preserving it')


def vg_inventory():
    raw = run(['vgs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
               '--options', 'vg_name,pv_count,vg_size,vg_free'])
    body = json.loads(raw)
    reports = body.get('report', [])
    if len(reports) != 1 or not isinstance(reports[0].get('vg'), list):
        raise RuntimeError('unexpected LVM VG report schema')
    return reports[0]['vg']


def prepare_image():
    if not IMAGE_DIR.exists():
        if shutil.disk_usage('/var/lib').free < 2 * 1024 ** 3:
            raise RuntimeError('less than 2 GiB free on the image filesystem')
        IMAGE_DIR.mkdir(mode=0o700)
    directory = IMAGE_DIR.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
            or stat.S_IMODE(directory.st_mode) != 0o700):
        raise RuntimeError('Cinder POC image directory must be root-owned mode 0700')
    if IMAGE.is_symlink():
        raise RuntimeError('refusing symlink at Cinder POC image path')
    if IMAGE.exists():
        info = IMAGE.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != SIZE):
            raise RuntimeError('existing Cinder POC image has unexpected type, owner, mode, or size')
    else:
        descriptor = os.open(IMAGE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.ftruncate(descriptor, SIZE)
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    progress(f'backing image ready: {IMAGE} (8 GiB virtual size)')


def prepare_loop_and_lvm():
    if not LOOP.exists() or not stat.S_ISBLK(LOOP.stat().st_mode):
        raise RuntimeError('/dev/loop0 is not an available block device')
    backing = loop_inventory()
    validate_loop_attachment(backing)
    if backing is None:
        run(['losetup', str(LOOP), str(IMAGE)])
    if loop_inventory() != str(IMAGE):
        raise RuntimeError('loop device did not attach to the dedicated image')
    size = run(['blockdev', '--getsize64', str(LOOP)]).strip()
    if size != str(SIZE):
        raise RuntimeError('loop device reports an unexpected size')

    pv = pv_inventory()
    groups = vg_inventory()
    matching_groups = [row for row in groups if row.get('vg_name', '').strip() == VG]
    if pv:
        if (pv.get('pv_name', '').strip() != str(LOOP)
                or pv.get('vg_name', '').strip() != VG):
            raise RuntimeError('/dev/loop0 already contains an unrelated or incomplete PV; preserving it')
        if len(matching_groups) != 1:
            raise RuntimeError('dedicated PV does not have exactly one expected Cinder VG')
        if matching_groups[0].get('pv_count', '').strip() != '1':
            raise RuntimeError('Cinder POC VG includes an unexpected additional PV')
    else:
        if matching_groups:
            raise RuntimeError('Cinder VG exists without its expected PV; preserving it')
        signatures = run(['wipefs', '--noheadings', '--output', 'TYPE', str(LOOP)]).strip()
        if signatures:
            raise RuntimeError('/dev/loop0 contains an unrecognized signature; preserving it')
        run(['pvcreate', str(LOOP)])
        run(['vgcreate', VG, str(LOOP)])
        pv = pv_inventory()
        groups = vg_inventory()
        matching_groups = [row for row in groups if row.get('vg_name', '').strip() == VG]
        if (not pv or len(matching_groups) != 1
                or matching_groups[0].get('pv_count', '').strip() != '1'):
            raise RuntimeError('new Cinder POC PV/VG did not verify')
    progress(f'dedicated LVM backend verified: {VG} on {LOOP}')
    return pv, matching_groups[0]


def verify_tgt():
    if listen_addresses('3260') != ['127.0.0.1:3260']:
        raise RuntimeError('tgt must have exactly one listener on 127.0.0.1:3260')


def listen_addresses(port):
    output = run(['ss', '-H', '-ltn', 'sport', '=', ':' + str(port)])
    return [line.split()[3] for line in output.splitlines() if len(line.split()) >= 4]


def loopback_api_config(text):
    listeners = re.findall(r'^\s*Listen\s+(\S+)', text, re.M)
    vhosts = re.findall(r'^\s*<VirtualHost\s+([^>]+)>', text, re.M)
    return listeners == ['127.0.0.1:8776'] and vhosts == ['127.0.0.1:8776']


def write_state(pv, vg):
    state = {
        'schema': 1,
        'status': 'prepared',
        'image': str(IMAGE),
        'image_virtual_size_bytes': SIZE,
        'loop_device': str(LOOP),
        'pv_uuid': pv.get('pv_uuid', '').strip(),
        'volume_group': VG,
        'vg_size_bytes': vg.get('vg_size', '').strip(),
        'vg_free_bytes': vg.get('vg_free', '').strip(),
        'global_lvm_filter_changed': False,
        'existing_pv_or_vg_overwritten': False,
    }
    if STATE.exists() or STATE.is_symlink():
        info = STATE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError('existing Cinder storage state is not a protected regular file')
        existing = json.loads(STATE.read_text())
        immutable = ('schema', 'status', 'image', 'image_virtual_size_bytes',
                     'loop_device', 'pv_uuid', 'volume_group',
                     'global_lvm_filter_changed', 'existing_pv_or_vg_overwritten')
        if any(existing.get(key) != state[key] for key in immutable):
            raise RuntimeError('existing Cinder storage state differs; preserving it')
        return
    atomic_create(STATE, json.dumps(state, indent=2, sort_keys=True) + '\n', 0o600)


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    stage = 'host_preflight'
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this setup is scoped to the existing Ubuntu 22.04 WSL host')
        lock_fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if lock_info.st_uid != 0 or stat.S_IMODE(lock_info.st_mode) != 0o600:
            os.close(lock_fd)
            raise RuntimeError('task lock must be root-owned mode 0600')
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            progress('BEGIN')
            for unit in ('cinder-scheduler.service', 'cinder-volume.service'):
                output = run(['systemctl', 'show', unit, '--property=LoadState'])
                if output.strip() != 'LoadState=masked':
                    raise RuntimeError(f'{unit} must remain runtime-masked until Cinder config is applied')
            config = Path('/etc/apache2/conf-available/cinder-wsgi.conf')
            info = config.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise RuntimeError('Cinder WSGI config must be a regular root-owned file')
            text = config.read_text()
            if not loopback_api_config(text):
                raise RuntimeError('Cinder API listener is not verified as loopback-only')
            if listen_addresses('8776') != ['127.0.0.1:8776']:
                raise RuntimeError('active Cinder API socket is not bound only to 127.0.0.1:8776')
            verify_tgt()
            if shutil.disk_usage('/').free < 2 * 1024 ** 3:
                raise RuntimeError('less than 2 GiB free on the Cinder backing filesystem')
            stage = 'backing_image'
            prepare_image()
            stage = 'loop_device_and_lvm'
            pv, vg = prepare_loop_and_lvm()
            stage = 'Cinder_lvm_filter_and_systemd_units'
            atomic_create(LVM_CONFIG, LVM_TEXT, 0o640, grp.getgrnam('cinder').gr_gid)
            atomic_create(HELPER, HELPER_TEXT, 0o700)
            atomic_create(UNIT, UNIT_TEXT, 0o644)
            atomic_create(VOLUME_DROPIN, DROPIN_TEXT, 0o644)
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'enable', 'cinder-poc-loop.service'])
            run(['systemctl', 'start', 'cinder-poc-loop.service'])
            stage = 'protected_state'
            write_state(pv, vg)
            progress(f'PASS; state={STATE}; Cinder services remain masked')
        return 0
    except Exception as error:
        detail = ' '.join(str(error).split())
        detail = re.sub(r'[^A-Za-z0-9 _./:=+-]', '?', detail)[:240]
        print(f'STOP: Cinder storage setup incomplete at {stage} ({type(error).__name__}): {detail}; inspect host state before retry.',
              file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
