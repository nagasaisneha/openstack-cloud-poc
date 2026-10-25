#!/usr/bin/python3
"""Scoped CHAP compatibility for task-owned volumes on the local TGT POC.

Open-iSCSI 2.1.5 and TGT 1.0.80 assign different wire IDs to SHA1. MD5 (5)
is their shared CHAP algorithm. With explicit opt-in, prepare MD5 on each
task-owned node at 127.0.0.1:3260. Global defaults are read only.
This is a laptop compatibility exception, not a staging authentication policy.
"""

import configparser
import hashlib
from pathlib import Path
import re
import stat
import subprocess
import uuid

import prepare_cinder_storage as storage


ISCSID_CONF = Path('/etc/iscsi/iscsid.conf')
CINDER_CONF = Path('/etc/cinder/cinder.conf')
NOVA_CONF = Path('/etc/nova/nova.conf')
PORTAL = '127.0.0.1:3260'
PREFIX = 'iqn.2010-10.org.openstack:'
ALGORITHMS = {'MD5', 'SHA1', 'SHA256', 'SHA3-256'}
# Compare protocol IDs as well as names. TGT's SHA1=7 collides with the
# initiator's SHA256=7; offering a shared name alone is not interoperable.
INITIATOR_CHAP_IDS = {'MD5': 5, 'SHA1': 6, 'SHA256': 7, 'SHA3-256': 8}
TGT_CHAP_IDS = {'MD5': 5, 'SHA1': 7}
INTEROPERABLE_ALGORITHMS = {
    name for name, value in INITIATOR_CHAP_IDS.items() if TGT_CHAP_IDS.get(name) == value}


def regular_text(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o002 or info.st_size > 1024 * 1024:
        raise RuntimeError('unsafe or oversized configuration file: ' + path.name)
    return path.read_text()


def chap_algorithms(text, default=('MD5',)):
    values = re.findall(r'^\s*node\.session\.auth\.chap_algs\s*=\s*([^\n#]+)', text, re.M)
    if len(values) > 1:
        raise RuntimeError('ambiguous active CHAP algorithm settings; preserve and review')
    parsed = tuple(item.strip() for item in values[0].split(',')) if values else tuple(default)
    if not parsed or any(item not in ALGORITHMS for item in parsed):
        raise RuntimeError('unexpected CHAP algorithm setting; preserve and review')
    return parsed


def validate_backend(cinder_text, nova_text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(cinder_text)
    if ([item.strip() for item in parser.get('DEFAULT', 'enabled_backends', fallback='').split(',')]
            != ['lvm']):
        raise RuntimeError('CHAP exception is scoped to the single local POC LVM backend')
    expected = {
        'volume_driver': 'cinder.volume.drivers.lvm.LVMVolumeDriver',
        'volume_group': 'cinder-poc-vg', 'volume_backend_name': 'CINDER_POC_LOOP',
        'target_helper': 'tgtadm', 'target_protocol': 'iscsi',
        'target_ip_address': '127.0.0.1', 'target_port': '3260',
    }
    if any(parser.get('lvm', option, fallback='') != value for option, value in expected.items()):
        raise RuntimeError('backend differs from the reviewed loopback LVM/TGT POC')
    if (parser.get('lvm', 'target_prefix', fallback=PREFIX) != PREFIX
            or parser.get('DEFAULT', 'volume_name_template', fallback='volume-%s') != 'volume-%s'):
        raise RuntimeError('unexpected target/volume naming; preserving all node records')
    nova = configparser.ConfigParser(interpolation=None)
    nova.read_string(nova_text)
    if nova.get('libvirt', 'iscsi_iface', fallback='default') not in {'', 'default'}:
        raise RuntimeError('Nova uses a nondefault iSCSI interface; review before preparing a node')


def compatibility_preflight(allow_sha1=False, allow_md5=False):
    if allow_sha1:
        raise RuntimeError('SHA1 is not interoperable: Open-iSCSI uses wire ID 6 and TGT uses 7; reviewed loopback MD5 opt-in required')
    global_text = regular_text(ISCSID_CONF)
    configured = chap_algorithms(global_text)
    if not allow_md5:
        if not set(configured) & INTEROPERABLE_ALGORITHMS:
            raise RuntimeError('initiator and TGT have no common CHAP wire algorithm; reviewed loopback MD5 opt-in required')
        return
    version = subprocess.run(['dpkg-query', '-W', '-f=${Package} ${Version}\n', 'tgt', 'open-iscsi'],
                             capture_output=True, text=True, timeout=10)
    versions = dict(line.split(maxsplit=1) for line in version.stdout.splitlines() if len(line.split()) == 2)
    if (version.returncode
            or not re.fullmatch(r'(?:\d+:)?1\.0\.80(?:[-+~][A-Za-z0-9.+~:-]+)?', versions.get('tgt', ''))
            or not re.fullmatch(r'(?:\d+:)?2\.1\.5(?:[-+~][A-Za-z0-9.+~:-]+)?', versions.get('open-iscsi', ''))):
        raise RuntimeError('compatibility exception is limited to inspected TGT 1.0.80 and Open-iSCSI 2.1.5 packages')
    crypto = subprocess.run(['openssl', 'dgst', '-md5'], input='',
                            capture_output=True, text=True, timeout=10)
    if crypto.returncode:
        raise RuntimeError('MD5 is unavailable under the active crypto policy; preserve that policy and use a compatible backend')
    validate_backend(regular_text(CINDER_CONF), regular_text(NOVA_CONF))
    if storage.listen_addresses('3260') != [PORTAL]:
        raise RuntimeError('TGT must have exactly the loopback listener before preparing a node')


def node_command(iqn, extra=(), accepted=(0,)):
    result = subprocess.run(
        ['iscsiadm', '-m', 'node', '-T', iqn, '-p', PORTAL, '--interface', 'default', *extra],
        capture_output=True, text=True, timeout=20)
    if result.returncode not in accepted:
        # Query output can contain CHAP credentials. Never include it in errors.
        raise RuntimeError('scoped iSCSI node operation failed; exit=' + str(result.returncode))
    return result


def assert_no_session(iqn):
    result = subprocess.run(['iscsiadm', '-m', 'session'], capture_output=True,
                            text=True, timeout=10)
    if result.returncode not in (0, 21):
        raise RuntimeError('cannot verify the absence of a session for the task-owned target')
    if any(iqn in line.split() for line in result.stdout.splitlines()):
        raise RuntimeError('task-owned target already has a host session; preserve and inspect')


def assert_owned(volume, state):
    if (str(uuid.UUID(volume.id)) != volume.id
            or volume.id not in {state.get('volume_id'), state.get('clone_id')}
            or volume.project_id != state['project_id']
            or (volume.metadata or {}).get('task18_run_id') != state['run_id']
            or volume.description != state['marker']
            or volume.status != 'available' or volume.attachments):
        raise RuntimeError('CHAP compatibility requires the exact owned, available, detached test volume')


def prepare_node(volume, state, save):
    assert_owned(volume, state)
    if state.get('loopback_chap_md5') is not True or state.get('loopback_chap_algorithm') != 'MD5':
        raise RuntimeError('explicit MD5 opt-in is required before preparing a task-owned node')
    compatibility_preflight(allow_md5=True)
    iqn = PREFIX + 'volume-' + volume.id
    assert_no_session(iqn)
    global_digest = hashlib.sha256(ISCSID_CONF.read_bytes()).hexdigest()
    records = state.setdefault('loopback_chap_nodes', {})
    prior = records.get(volume.id)
    query = node_command(iqn, accepted=(0, 21))
    if query.returncode == 0 and (
            not isinstance(prior, dict) or prior.get('iqn') != iqn
            or prior.get('portal') != PORTAL or prior.get('algorithm') not in {'SHA1', 'MD5'}
            or prior.get('phase') not in {'create_intent', 'created', 'prepared'}):
        raise RuntimeError('existing iSCSI node lacks this run ownership record; preserving it')
    if isinstance(prior, dict):
        state.setdefault('chap_preparation_history', []).append({'volume_id': volume.id, **prior})
    record = {'iqn': iqn, 'portal': PORTAL, 'algorithm': 'MD5',
              'phase': 'create_intent', 'global_config_sha256': global_digest}
    records[volume.id] = record
    save(state)
    if query.returncode == 21:
        node_command(iqn, ('--op', 'new'))
    record['phase'] = 'created'
    save(state)
    node_command(iqn, ('--op', 'update', '-n', 'node.session.auth.chap_algs', '-v', 'MD5'))
    query = node_command(iqn)
    if query.stderr.strip():
        # os-brick treats any node-query stderr as a missing record and may
        # recreate it from global defaults, undoing this scoped preparation.
        raise RuntimeError('scoped node query emits warnings; inspect before submitting attachment')
    if chap_algorithms(query.stdout, default=()) != ('MD5',):
        raise RuntimeError('task-owned node did not retain the reviewed MD5 setting')
    if hashlib.sha256(ISCSID_CONF.read_bytes()).hexdigest() != global_digest:
        raise RuntimeError('global iSCSI configuration changed during scoped node preparation')
    record['phase'] = 'prepared'
    save(state)


def verify_node_removed(volume, state, save):
    assert_owned(volume, state)
    record = state.get('loopback_chap_nodes', {}).get(volume.id)
    if not record or record.get('phase') != 'prepared':
        raise RuntimeError('missing prepared-node ownership record after detachment')
    iqn = PREFIX + 'volume-' + volume.id
    assert_no_session(iqn)
    if node_command(iqn, accepted=(0, 21)).returncode != 21:
        raise RuntimeError('task-owned node remains after detachment; preserve it for scoped cleanup')
    if hashlib.sha256(ISCSID_CONF.read_bytes()).hexdigest() != record['global_config_sha256']:
        raise RuntimeError('global iSCSI configuration differs from the pre-test baseline')
    record['phase'] = 'removed_after_detach'
    save(state)
