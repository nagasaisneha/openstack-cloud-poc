#!/usr/bin/python3
"""Run the owned Cinder volume, attachment, guest-I/O and snapshot acceptance.

The run uses only a newly created, marked one-GiB Cinder volume and the
existing Task 11-owned CirrOS guest. It writes test data only to the newly
attached volume. On failure, owned resources are retained for inspection;
on success, the test resources are deleted in dependency order.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import stat
import sys
import time
import uuid

import configure_nova as common
import cinder_loopback_chap as chap
import inspect_cinder_volume_acceptance as diagnostic
import launch_test_vm as launch
import validate_guest as guest


DATA_DIR = launch.DATA_DIR
STATE = DATA_DIR / 'cinder-volume-acceptance.json'
SETUP_STATE = DATA_DIR / 'cinder-setup.json'
MARKER_KEY = 'task18_run_id'
VOLUME_SIZE_GB = 1
DEVICE_PATTERN = re.compile(r'vd[b-z]')


def progress(message):
    print(f'POC Cinder volume: {message}', flush=True)


def save(state):
    launch.atomic_state(STATE, state)
    os.chmod(STATE, 0o600)


def wait_volume(cloud, volume_id, statuses, timeout=180):
    from openstack import exceptions as os_exc

    deadline = time.monotonic() + timeout
    last_status = 'not_found'
    while time.monotonic() < deadline:
        try:
            volume = cloud.block_storage.get_volume(volume_id)
        except os_exc.ResourceNotFound:
            if 'deleted' in statuses:
                return None
            raise
        if volume is None:
            if 'deleted' in statuses:
                return None
        else:
            last_status = volume.status
            if volume.status in statuses:
                return volume
            if isinstance(volume.status, str) and volume.status.startswith('error'):
                raise RuntimeError(f'owned volume entered {volume.status}: {volume_id}')
        time.sleep(2)
    raise RuntimeError(f'volume did not reach expected state: {volume_id}; last_status={last_status}')


def wait_snapshot(cloud, snapshot_id, statuses, timeout=180):
    from openstack import exceptions as os_exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            snapshot = cloud.block_storage.get_snapshot(snapshot_id)
        except os_exc.ResourceNotFound:
            if 'deleted' in statuses:
                return None
            raise
        if snapshot is None:
            if 'deleted' in statuses:
                return None
        elif snapshot.status in statuses:
            return snapshot
        elif snapshot.status == 'error':
            raise RuntimeError(f'owned snapshot entered error state: {snapshot_id}')
        time.sleep(2)
    raise RuntimeError(f'snapshot did not reach expected state: {snapshot_id}')


def assert_volume_owned(volume, state):
    if (volume.project_id != state['project_id']
            or (volume.metadata or {}).get(MARKER_KEY) != state['run_id']
            or volume.description != state['marker']):
        raise RuntimeError('volume ownership markers do not match; preserving resource')


def parse_guest_devices(output):
    match = re.findall(r'^TASK18_DEVICES_BEGIN\r?\n(.*?)^TASK18_DEVICES_END\r?$',
                       output, re.M | re.S)
    if len(match) != 1:
        raise RuntimeError('guest block-device observation was incomplete')
    devices = [line.strip() for line in match[0].splitlines() if line.strip()]
    if any(not re.fullmatch(r'vd[a-z]', item) for item in devices):
        raise RuntimeError('guest reported an unexpected virtio block-device name')
    return set(devices)


def guest_devices(cloud, state):
    script = '''set -eu
echo TASK18_DEVICES_BEGIN
for path in /sys/block/vd*; do
    [ -e "$path" ] && basename "$path"
done | sort
echo TASK18_DEVICES_END
'''
    output = common.run(guest.ssh_command(state), input_text=script, timeout=30)
    return parse_guest_devices(output)


def guest_volume_guard(device, volume_id):
    if not DEVICE_PATTERN.fullmatch(device):
        raise RuntimeError('refusing guest I/O to an unexpected device name')
    if str(uuid.UUID(volume_id)) != volume_id:
        raise RuntimeError('refusing guest I/O without a canonical volume UUID')
    return f'''set -eu
dev=/dev/{device}
test -b "$dev"
test "$(cat /sys/block/{device}/size)" -eq {VOLUME_SIZE_GB * 1024**3 // 512}
serial_file=/sys/block/{device}/serial
test -r "$serial_file" || serial_file=/sys/block/{device}/device/serial
serial=$(cat "$serial_file")
case "$serial" in
    {volume_id}|{volume_id[:20]}) ;;
    *) echo 'STOP: guest device serial does not match owned Cinder volume' >&2; exit 3 ;;
esac
'''


def verify_guest_volume_identity(state, device, volume_id):
    guard = guest_volume_guard(device, volume_id)
    output = common.run(guest.ssh_command(state), input_text=guard + '''
printf 'TASK18_VOLUME_IDENTITY_OK\\n'
''', timeout=30)
    if not re.search(r'^TASK18_VOLUME_IDENTITY_OK\r?$', output, re.M):
        raise RuntimeError('guest device identity validation was incomplete')


def guest_block_io(state, device, volume_id, write):
    # Validate on both sides of the privilege boundary before any device I/O.
    verify_guest_volume_identity(state, device, volume_id)
    command = list(guest.ssh_command(state))
    if command[-2:] != ['sh', '-s']:
        raise RuntimeError('unexpected SSH command shape; refusing privileged guest I/O')
    command[-2:] = ['sudo', '-n', 'sh', '-s']
    script = guest_volume_guard(device, volume_id) + '''
umask 077
scratch=$(mktemp -d /tmp/task18-volume.XXXXXX)
trap 'rm -f "$scratch/pattern" "$scratch/readback"; rmdir "$scratch"' 0
'''
    if write:
        script += '''
dd if=/dev/urandom of="$scratch/pattern" bs=4096 count=1 2>/dev/null
dd if="$scratch/pattern" of="$dev" bs=4096 count=1 2>/dev/null
sync
dd if="$dev" of="$scratch/readback" bs=4096 count=1 2>/dev/null
cmp "$scratch/pattern" "$scratch/readback"
'''
    else:
        script += '''
dd if="$dev" of="$scratch/readback" bs=4096 count=1 2>/dev/null
'''
    script += '''
printf 'TASK18_DATA_DIGEST_BEGIN\\n'
md5sum "$scratch/readback" | cut -d ' ' -f 1
printf 'TASK18_DATA_DIGEST_END\\n'
'''
    output = common.run(command, input_text=script, timeout=30)
    matches = re.findall(r'^TASK18_DATA_DIGEST_BEGIN\r?\n([0-9a-f]{32})\r?\nTASK18_DATA_DIGEST_END\r?$',
                         output, re.M)
    if len(matches) != 1:
        raise RuntimeError('guest block-device write/read verification returned no digest')
    return matches[0]


def guest_write_read(cloud, state, device, volume_id):
    return guest_block_io(state, device, volume_id, write=True)


def guest_verify_read(cloud, state, device, expected_digest, volume_id):
    actual = guest_block_io(state, device, volume_id, write=False)
    if actual != expected_digest:
        raise RuntimeError('snapshot clone data does not match the written volume data')


def attach_and_test(cloud, state, run_state, server, volume_id, expected_devices,
                    digest=None):
    volume = cloud.block_storage.get_volume(volume_id)
    assert_volume_owned(volume, run_state)
    if volume.status != 'available':
        raise RuntimeError('owned test volume is not available before attachment')
    attachments = list(cloud.compute.volume_attachments(server))
    matching = [item for item in attachments if item.volume_id == volume_id]
    if matching:
        if len(matching) != 1:
            raise RuntimeError('multiple attachments exist for the owned test volume')
        attachment = matching[0]
    else:
        if run_state.get('loopback_chap_md5') is True:
            chap.prepare_node(volume, run_state, save)
            progress('task-owned loopback node prepared for interoperable MD5 CHAP: PASS')
        consume_resume_retry(run_state)
        run_state['phase'] = 'attach_intent'
        save(run_state)
        attachment = cloud.compute.create_volume_attachment(
            server, volume, device='/dev/vdb')
        run_state['attachment_id'] = attachment.id
        run_state['phase'] = 'attach_submitted'
        save(run_state)
    volume = wait_volume(cloud, volume_id, {'in-use'})
    assert_volume_owned(volume, run_state)
    deadline = time.monotonic() + 60
    new_devices = set()
    while time.monotonic() < deadline:
        new_devices = guest_devices(cloud, state) - expected_devices
        if len(new_devices) == 1:
            break
        time.sleep(2)
    if len(new_devices) != 1:
        raise RuntimeError('guest did not observe exactly one new virtio disk')
    device = next(iter(new_devices))
    if digest is None:
        digest = guest_write_read(cloud, state, device, volume_id)
    else:
        guest_verify_read(cloud, state, device, digest, volume_id)
    run_state['phase'] = 'attached_io_verified'
    run_state['device'] = device
    run_state['data_md5'] = digest
    save(run_state)
    cloud.compute.delete_volume_attachment(server, volume, ignore_missing=False)
    volume = wait_volume(cloud, volume_id, {'available'})
    deadline = time.monotonic() + 30
    remaining = {device}
    while time.monotonic() < deadline:
        remaining = guest_devices(cloud, state) - expected_devices
        if not remaining:
            break
        time.sleep(2)
    if remaining:
        raise RuntimeError('guest still sees the detached test disk; preserving owned resources')
    if run_state.get('loopback_chap_md5') is True:
        chap.verify_node_removed(volume, run_state, save)
    run_state['phase'] = 'detached'
    save(run_state)
    return set(expected_devices), digest


def validate_resume_manifest(state, volume_id, reviewed_md5_request=None):
    if str(uuid.UUID(volume_id)) != volume_id:
        raise RuntimeError('resume requires the canonical retained volume UUID')
    run_id = state.get('run_id', '')
    if (state.get('schema') != 1 or state.get('task') != 'T18-Cinder-volume-acceptance'
            or not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id)
            or state.get('marker') != 'Task 18 Cinder volume test ' + run_id
            or state.get('volume_id') != volume_id
            or state.get('created_assets') != ['volume']
            or state.get('phase') not in {'volume_available', 'attach_intent', 'attach_submitted',
                                        'retained_volume_revalidated'}
            or 'snapshot_id' in state or 'clone_id' in state):
        raise RuntimeError('retained run is not eligible for one reviewed volume-attachment retry')
    retries = state.get('attach_retries', 0)
    if reviewed_md5_request is None:
        if retries != 0 or state.get('reviewed_md5_retry'):
            raise RuntimeError('a further attachment requires a distinct reviewed failure; preserve the retry history')
        return
    if (not re.fullmatch(r'req-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', reviewed_md5_request)
            or retries != 1):
        raise RuntimeError('reviewed MD5 retry requires exactly one prior consumed retry and its request UUID')
    pending = state.get('reviewed_md5_retry')
    if pending is not None:
        if (not isinstance(pending, dict) or pending.get('request_id') != reviewed_md5_request
                or pending.get('phase') != 'prepared' or pending.get('previous_attach_retries') != 1
                or pending.get('previous_algorithm') != 'SHA1' or pending.get('algorithm') != 'MD5'):
            raise RuntimeError('the reviewed MD5 retry has already been submitted or does not match this request')
    else:
        record = state.get('loopback_chap_nodes', {}).get(volume_id, {})
        if (state.get('loopback_chap_sha1') is not True or record.get('algorithm') != 'SHA1'
                or record.get('phase') != 'prepared'):
            raise RuntimeError('MD5 review is limited to the recorded, unsuccessful SHA1 compatibility attempt')


def validate_reviewed_md5_request(cloud, state, request_id):
    validate_resume_manifest(state, state['volume_id'], request_id)
    actions = [item for item in cloud.compute.server_actions(state['server_id'])
               if item.action == 'attach_volume']
    actions.sort(key=lambda item: item.start_time or '', reverse=True)
    if not actions or actions[0].request_id != request_id:
        raise RuntimeError('reviewed request is not the latest Nova volume attachment action')
    detail = cloud.compute.get_server_action(request_id, state['server_id'], ignore_missing=False)
    events = [event for event in detail.events or [] if event.event == 'compute_attach_volume']
    if (len(events) != 1 or events[0].result != 'Error'
            or 'VolumeDeviceNotFound' not in str(events[0].traceback or '')):
        raise RuntimeError('latest reviewed request does not prove the expected failed device-connection attempt')
    pending = state.get('reviewed_md5_retry')
    baseline = (pending.get('prior_global_config_sha256') if isinstance(pending, dict)
                else state['loopback_chap_nodes'][state['volume_id']].get('global_config_sha256'))
    if not baseline or hashlib.sha256(chap.ISCSID_CONF.read_bytes()).hexdigest() != baseline:
        raise RuntimeError('global initiator configuration changed after the reviewed SHA1 attempt')
    return {'request_id': request_id, 'previous_attach_retries': 1,
            'previous_algorithm': 'SHA1', 'algorithm': 'MD5', 'phase': 'prepared',
            'prior_global_config_sha256': baseline,
            'reason': 'Open-iSCSI SHA1 wire ID 6 differs from TGT SHA1 wire ID 7; MD5 wire ID 5 agrees'}


def consume_resume_retry(state):
    if not state.pop('resume_pending', False):
        return
    review = state.get('reviewed_md5_retry')
    if review is not None:
        if review.get('phase') != 'prepared' or state.get('attach_retries') != 1:
            raise RuntimeError('reviewed MD5 retry was already consumed; preserving the run')
        state['attach_retries'] = 2
        review['phase'] = 'submitted'
        review['submitted_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    else:
        if state.get('attach_retries', 0) != 0:
            raise RuntimeError('unreviewed repeat attachment refused')
        state['attach_retries'] = 1


def validate_retained_volume(cloud, state, task_state, devices):
    if (state['project_id'] != cloud.session.get_project_id()
            or state['server_id'] != task_state['ids']['server']
            or set(state['baseline_devices']) != devices):
        raise RuntimeError('retained run project, guest or baseline devices changed')
    volume = cloud.block_storage.get_volume(state['volume_id'])
    assert_volume_owned(volume, state)
    if volume.status != 'available' or volume.size != VOLUME_SIZE_GB or volume.attachments:
        raise RuntimeError('retained volume must be available, one GiB, and detached before retry')
    if list(cloud.compute.volume_attachments(state['server_id'])):
        raise RuntimeError('guest has a volume attachment; inspect it before retry')
    if any(item.volume_id == volume.id
           for item in cloud.block_storage.attachments(volume_id=volume.id)):
        raise RuntimeError('Cinder still has an attachment for the retained volume')
    if any(item.volume_id == volume.id
           for item in cloud.block_storage.snapshots(volume_id=volume.id)):
        raise RuntimeError('retained volume has snapshots; preserve them and review the run')
    return volume


def validate_state_file(resume_volume_id=None, reviewed_md5_request=None):
    info = DATA_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('acceptance directory must be root-owned mode 0700')
    existing = None
    if STATE.exists() or STATE.is_symlink():
        if resume_volume_id is None:
            raise RuntimeError('prior volume acceptance state exists; inspect it before a new run')
        guest.protected_file(STATE, 0o600)
        existing = json.loads(STATE.read_text())
        validate_resume_manifest(existing, resume_volume_id, reviewed_md5_request)
    elif resume_volume_id is not None:
        raise RuntimeError('resume requires the retained protected acceptance manifest')
    if SETUP_STATE.is_symlink() or not SETUP_STATE.is_file():
        raise RuntimeError('Cinder setup readiness record is missing or unsafe')
    setup = json.loads(SETUP_STATE.read_text())
    if setup.get('phase') != 'ready_for_scoped_volume_test':
        raise RuntimeError('Cinder setup record does not show readiness for a scoped volume test')
    return existing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-volume', metavar='UUID',
                        help='after diagnosing/fixing attachment failure, reuse this exact retained volume for one reviewed retry')
    compatibility = parser.add_mutually_exclusive_group()
    compatibility.add_argument('--loopback-chap-sha1', action='store_true',
                               help='retained for clarity: this option is rejected because SHA1 protocol IDs differ')
    compatibility.add_argument('--loopback-chap-md5', action='store_true',
                               help='explicit local compatibility exception: MD5 CHAP only on task-owned loopback nodes; global defaults are read only')
    parser.add_argument('--retry-md5-after', metavar='REQUEST_ID',
                        help='authorize one MD5 retry after this latest reviewed failed SHA1 request; preserves previous retry history')
    args = parser.parse_args(argv)
    if args.retry_md5_after and (not args.resume_volume or not args.loopback_chap_md5):
        parser.error('--retry-md5-after requires --resume-volume and explicit --loopback-chap-md5')
    secrets = {}
    run_state = None
    stage = 'preflight'
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('this acceptance is scoped to the existing Ubuntu 22.04 WSL POC')
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                             os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            existing = validate_state_file(args.resume_volume, args.retry_md5_after)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                task_state, _ = guest.load_run(cloud, require_ssh=True)
                server = cloud.compute.get_server(task_state['ids']['server'])
                launch.assert_owned(server, task_state,
                                    'Task 11 local POC run ' + task_state['run_id'], server=True)
                guest.validate_attachment(cloud, task_state)
                # Require the retained guest to be reachable before allocating storage.
                devices = guest_devices(cloud, task_state)
                if devices != {'vda'}:
                    raise RuntimeError('acceptance guest must have only its baseline virtio boot disk')
                chap.compatibility_preflight(allow_sha1=args.loopback_chap_sha1,
                                             allow_md5=args.loopback_chap_md5)
                if existing is not None:
                    stage = 'retained_volume_preflight'
                    volume = validate_retained_volume(cloud, existing, task_state, devices)
                    run_state = existing
                    if args.retry_md5_after:
                        run_state['reviewed_md5_retry'] = validate_reviewed_md5_request(
                            cloud, run_state, args.retry_md5_after)
                    run_state['previous_attach_phase'] = run_state['phase']
                    run_state['resume_pending'] = True
                    run_state['phase'] = 'retained_volume_revalidated'
                    save(run_state)
                    progress('retained owned volume revalidated; no replacement volume created')
                else:
                    run_state = {
                        'schema': 1, 'task': 'T18-Cinder-volume-acceptance',
                        'run_id': uuid.uuid4().hex,
                        'project_id': cloud.session.get_project_id(),
                        'server_id': server.id, 'phase': 'baseline_verified',
                        'baseline_devices': sorted(devices), 'created_assets': [],
                    }
                    run_state['marker'] = 'Task 18 Cinder volume test ' + run_state['run_id']
                    save(run_state)
                run_state['loopback_chap_sha1'] = args.loopback_chap_sha1
                run_state['loopback_chap_md5'] = args.loopback_chap_md5
                run_state['loopback_chap_algorithm'] = 'MD5' if args.loopback_chap_md5 else None
                save(run_state)
                progress('guest ownership, active state, and SSH baseline: PASS')

                if existing is None:
                    stage = 'volume_create'
                    run_state['phase'] = 'volume_create_intent'
                    save(run_state)
                    volume = cloud.block_storage.create_volume(
                        size=VOLUME_SIZE_GB,
                        name='task18-' + run_state['run_id'][:12],
                        description=run_state['marker'],
                        metadata={MARKER_KEY: run_state['run_id']})
                    run_state['volume_id'] = volume.id
                    run_state['created_assets'].append('volume')
                    run_state['phase'] = 'volume_create_submitted'
                    save(run_state)
                    volume = wait_volume(cloud, volume.id, {'available'})
                    assert_volume_owned(volume, run_state)
                    run_state['phase'] = 'volume_available'
                    save(run_state)
                    progress('owned volume create/available: PASS')

                stage = 'volume_attach_guest_io'
                devices, digest = attach_and_test(
                    cloud, task_state, run_state, server, volume.id, devices)
                run_state['volume_data_md5'] = digest
                progress('Nova attachment and guest block write/read: PASS')

                stage = 'snapshot_and_clone'
                run_state['phase'] = 'snapshot_create_intent'
                save(run_state)
                snapshot = cloud.block_storage.create_snapshot(
                    volume_id=volume.id, force=False,
                    name='task18-snap-' + run_state['run_id'][:12],
                    description=run_state['marker'],
                    metadata={MARKER_KEY: run_state['run_id']})
                run_state['snapshot_id'] = snapshot.id
                run_state['created_assets'].append('snapshot')
                run_state['phase'] = 'snapshot_create_submitted'
                save(run_state)
                snapshot = wait_snapshot(cloud, snapshot.id, {'available'})
                if (snapshot.project_id != run_state['project_id']
                        or snapshot.description != run_state['marker']
                        or (snapshot.metadata or {}).get(MARKER_KEY) != run_state['run_id']):
                    raise RuntimeError('snapshot ownership markers do not match')
                run_state['phase'] = 'snapshot_available'
                save(run_state)
                run_state['phase'] = 'snapshot_clone_create_intent'
                save(run_state)
                clone = cloud.block_storage.create_volume(
                    size=VOLUME_SIZE_GB,
                    name='task18-clone-' + run_state['run_id'][:12],
                    description=run_state['marker'], snapshot_id=snapshot.id,
                    metadata={MARKER_KEY: run_state['run_id']})
                run_state['clone_id'] = clone.id
                run_state['created_assets'].append('snapshot_clone')
                save(run_state)
                clone = wait_volume(cloud, clone.id, {'available'})
                assert_volume_owned(clone, run_state)
                devices, _ = attach_and_test(
                    cloud, task_state, run_state, server, clone.id, devices, digest)
                run_state['phase'] = 'snapshot_data_verified'
                save(run_state)
                progress('snapshot creation, clone, attachment, and persisted guest data: PASS')

                stage = 'owned_resource_cleanup'
                # Confirm both volumes are detached and still carry this run's markers.
                volume = cloud.block_storage.get_volume(run_state['volume_id'])
                clone = cloud.block_storage.get_volume(run_state['clone_id'])
                assert_volume_owned(volume, run_state)
                assert_volume_owned(clone, run_state)
                if volume.status != 'available' or clone.status != 'available':
                    raise RuntimeError('owned volumes are not detached; preserving all resources')
                cloud.block_storage.delete_volume(clone, ignore_missing=False)
                wait_volume(cloud, clone.id, {'deleted'})
                cloud.block_storage.delete_snapshot(snapshot, ignore_missing=False)
                wait_snapshot(cloud, snapshot.id, {'deleted'})
                cloud.block_storage.delete_volume(volume, ignore_missing=False)
                wait_volume(cloud, volume.id, {'deleted'})
                run_state['created_assets'] = []
                run_state['phase'] = 'test_resources_deleted'
                save(run_state)
                guest.validate_attachment(cloud, task_state)
                if guest_devices(cloud, task_state) != set(run_state['baseline_devices']):
                    raise RuntimeError('retained guest block-device inventory differs from its baseline')
                run_state['phase'] = 'passed_and_cleaned'
                run_state['completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                save(run_state)
                progress(f'PASS; create/attach/read-write/detach/snapshot/restore/cleanup verified; report={STATE}')
        return 0
    except Exception as error:
        details = common.redact(f'{type(error).__name__}: {error}', secrets)
        details = ' '.join(details.split())
        details = re.sub(r'[^A-Za-z0-9 _./:=+-]', '?', details)[:240]
        print(f'STOP: Cinder volume acceptance incomplete at {stage}: {details}; task-owned resources are preserved for inspection.',
              file=sys.stderr, flush=True)
        if run_state is not None and run_state.get('volume_id') and secrets:
            try:
                with common.connection(secrets) as cloud:
                    report = diagnostic.inspect(cloud, run_state)
                    report['attach_failure'] = diagnostic.attach_failure_observations(cloud, run_state)
                path = DATA_DIR / ('cinder-volume-failure-' + uuid.uuid4().hex + '.json')
                launch.atomic_state(path, report)
                os.chmod(path, 0o600)
                progress('failure diagnostic: ' + json.dumps(diagnostic.failure_summary(report), sort_keys=True))
                progress('protected failure report=' + str(path))
            except Exception as diagnostic_error:
                progress('failure diagnostic unavailable (' + type(diagnostic_error).__name__ + '); original error retained')
        return 1


if __name__ == '__main__':
    sys.exit(main())
