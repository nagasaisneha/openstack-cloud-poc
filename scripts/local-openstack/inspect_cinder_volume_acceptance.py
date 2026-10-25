#!/usr/bin/python3
"""Read-only inspection of an incomplete Task 18 volume acceptance run."""

import argparse
import configparser
import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys

from openstack import exceptions as os_exc

import configure_nova as common
import cinder_loopback_chap as chap
import launch_test_vm as launch


DATA_DIR = launch.DATA_DIR
STATE = DATA_DIR / 'cinder-volume-acceptance.json'
SAFE_STATES = {'creating', 'available', 'attaching', 'in-use', 'detaching',
               'error', 'deleting', 'deleted', 'maintenance', 'attached',
               'detached', 'reserved', 'error_attaching', 'error_detaching'}

# Raw logs/tracebacks can contain iSCSI credentials or connection dictionaries.
# Emit only fixed categories, validated exception names and exit codes.
FAILURE_PATTERNS = {
    'iscsi_transport_unavailable': r'(?:iscsi_tcp|scsi_transport_iscsi|iSCSI transport|module tcp).{0,180}(?:not found|No such|not present|failed|error)|Could not (?:insert|load) module',
    'initiator_name_unavailable': r'(?:initiatorname\.iscsi|initiator name).{0,100}(?:No such|not found|missing|not configured)|Could not find.*initiator',
    'iscsi_login_failed': r'iscsiadm.{0,180}(?:Could not login|login failed)|Could not log(?:in| in) to|iSCSI login.*(?:failed|error)|Failed to login iSCSI target',
    'iscsi_authentication_failed': r'(?:iSCSI|CHAP|login).{0,100}(?:authentication failure|authentication failed|authorization failure)|initiator failed authorization',
    'iscsi_login_timeout': r'(?:login|connect).{0,100}(?:timed out|timeout)|iSCSI connection timed out',
    'scsi_lun_missing_after_login': r'LUN .{0,150}not found on sysfs after logging in',
    'scsi_scan_failed': r'Exception scanning|Unable to find host for|could not find.*HCTL',
    'iscsi_no_records': r'No records found',
    'iscsi_session_unavailable': r'No active sessions|No session found|iSCSI session.*not found',
    'volume_device_not_found': r'VolumeDeviceNotFound|volume device (?:was )?not found|Could not find.*volume device',
    'service_auth_configuration': r'MissingRequiredOptions|MissingAuthPlugin|AuthTypeNotFound|Missing value auth-url|auth_url.{0,80}(?:required|missing)|auth plugin.{0,80}(?:required|missing)',
    'service_token_rejected': r'InvalidServiceToken|ServiceTokenRoleRequired|service token.{0,100}(?:invalid|missing|role|require)',
    'authentication_or_policy_denied': r'\bUnauthorized\b|\bForbidden\b|AuthorizationFailure|PolicyNotAuthorized|HTTP 40[13]',
    'catalog_endpoint_unavailable': r'EndpointNotFound|CatalogException|Could not find.*endpoint',
    'privilege_or_permission_failure': r'Permission denied|PermissionError|Unauthorized command|FailedToDropPrivileges|PrivsepTimeout',
    'target_export_failure': r'tgtadm.{0,120}(?:error|failed)|Target.{0,80}(?:not found|does not exist)|ISCSITargetCreateFailed',
    'backend_connection_failure': r'Connection refused|ConnectionError|ConnectFailure|Connection timed out',
    'guest_hotplug_failure': r'libvirtError|Failed to attach disk|Unable to attach device',
}


def classify_failure(text):
    """Return diagnostic metadata without copying arbitrary message text."""
    categories = [name for name, pattern in FAILURE_PATTERNS.items()
                  if re.search(pattern, text, re.I)]
    names = re.findall(
        r'\b(?:[A-Za-z_]\w*\.)*(?:[A-Z]\w*(?:Error|Exception)|VolumeDeviceNotFound|Unauthorized|Forbidden|MissingRequiredOptions|EndpointNotFound)\b',
        text)
    # Bound all output even when private diagnostic text is unexpectedly large.
    return {'categories': categories,
            'exception_types': sorted(set(names))[:20],
            'process_exit_codes': sorted(set(int(value) for value in re.findall(
                r'\bexit code[: ]+\s*(\d{1,3})\b', text, re.I)))[:10],
            'login_exit_codes': sorted(set(int(value) for value in re.findall(
                r'Failed to login iSCSI target[^\n]{0,500}?\(exit code (\d{1,3})\)', text)))[:10],
            'iscsi_commands': iscsi_command_failures(text),
            'iscsiadm_mentioned': 'iscsiadm' in text,
            'modprobe_mentioned': 'modprobe' in text}


def iscsi_command_failures(text):
    """Extract command operations and exit codes, never targets or auth values."""
    results = []
    for match in re.finditer(r'\bCommand: ([^\n]{1,8192})\n', text):
        try:
            args = shlex.split(match[1])
        except ValueError:
            continue
        positions = [i for i, value in enumerate(args) if Path(value).name == 'iscsiadm']
        if not positions:
            continue
        args = args[positions[0] + 1:]
        mode = next((args[i + 1] for i, value in enumerate(args[:-1]) if value in {'-m', '--mode'}), None)
        if mode not in {'node', 'session', 'discovery', 'discoverydb', 'iface'}:
            mode = 'unknown'
        operation = next((value[2:] for value in args if value in {'--login', '--logout', '--rescan'}), None)
        if operation is None:
            operation = next((args[i + 1] for i, value in enumerate(args[:-1]) if value in {'-o', '--op'}), 'query')
        if operation not in {'login', 'logout', 'rescan', 'new', 'delete', 'update', 'show', 'query'}:
            operation = 'unknown'
        following = text[match.end():match.end() + 600]
        code = re.search(r'\bExit code:\s*(\d{1,3})\b', following)
        result = {'mode': mode, 'operation': operation,
                  'exit_code': int(code[1]) if code else None}
        if result not in results:
            results.append(result)
    return results[:12]


def traceback_locations(text):
    allowed = {'iscsi.py', 'linuxscsi.py', 'executor.py', 'processutils.py',
               'block_device.py', 'driver.py', 'manager.py'}
    locations = []
    for match in re.finditer(r'File "([^"\n]+)", line (\d{1,7}), in ([A-Za-z_]\w{0,79})', text):
        name = Path(match[1]).name
        if name in allowed:
            item = {'file': name, 'line': int(match[2]), 'function': match[3]}
            if item not in locations:
                locations.append(item)
    return locations[-12:]


def log_failure_observations(state, request_id=None):
    observations = {}
    for source in ('nova/nova-compute.log', 'cinder/cinder-volume.log'):
        path = Path('/var/log') / source
        try:
            with path.open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 2 * 1024 * 1024))
                lines = stream.read().decode(errors='replace').splitlines()
        except (OSError, PermissionError):
            observations[source] = {'readable': False}
            continue
        # Request context includes the earlier per-login warnings that may not
        # carry a volume UUID; avoid diagnosing rollback cleanup as the cause.
        request_match = bool(request_id and any(request_id in line for line in lines))
        indices = [i for i, line in enumerate(lines)
                   if (request_id in line if request_match else state['volume_id'] in line)]
        if not indices and not request_match:
            indices = [i for i, line in enumerate(lines)
                       if state['server_id'] in line and (' ERROR ' in line or 'attach' in line.lower())]
        selected = set()
        if request_match:
            selected.update(indices)
            for index in indices:
                for following in range(index + 1, min(len(lines), index + 100)):
                    if re.match(r'^\d{4}-\d{2}-\d{2} ', lines[following]):
                        break
                    selected.add(following)
        else:
            for index in indices[-12:]:
                selected.update(range(max(0, index - 25), min(len(lines), index + 100)))
        text = '\n'.join(lines[index] for index in sorted(selected))
        observations[source] = {'readable': True, 'matching_rows': len(indices),
                                'request_correlated': request_match,
                                'traceback_locations': traceback_locations(text),
                                **classify_failure(text)}
    return observations


def initiator_journal(started_at):
    since = '-15min'
    if isinstance(started_at, str):
        try:
            stamp = datetime.datetime.fromisoformat(started_at.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
            since = (stamp - datetime.timedelta(seconds=5)).astimezone(
                datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        except ValueError:
            pass
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'iscsid.service', '--since', since,
             '-n', '160', '-o', 'cat', '--no-pager'],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {'readable': False}
    return {'readable': result.returncode == 0,
            'correlation': 'attach_time_window',
            **classify_failure(result.stdout)}


def login_rejection_reasons(text):
    """Extract only protocol status bytes and fixed daemon rejection reasons."""
    patterns = {
        'target_not_found': r'login rejected:[^\n]{0,160}target not found',
        'incompatible_protocol_version': r'login rejected:[^\n]{0,160}incompatible version',
        'initiator_authentication_failed': r'login rejected:[^\n]{0,160}failed authentication',
        'initiator_authorization_failed': r'login rejected:[^\n]{0,160}failed authorization',
        'initiator_protocol_error': r'login rejected:\s*initiator error',
        'target_protocol_error': r'login rejected:\s*target error',
        'target_rdma_tcp_mismatch': r'Target [^\n]{0,220}is RDMA[^\n]{0,160}is TCP',
        'target_not_ready': r'target[^\n]{0,80}(?:not ready|offline)',
        'authentication_negotiation_rejected': r'(?:AuthMethod|auth\. method)[^\n]{0,80}(?:unknown|invalid|not supported|not allowed)|Unknown auth\. method',
        'login_negotiation_failed': r'Login error \(Login status|login negotiation failed|could not negotiate',
    }
    reasons = [reason for reason, pattern in patterns.items() if re.search(pattern, text, re.I)]
    codes = sorted(set(re.findall(
        r'login rejected:[^\n]{0,200}\(([0-9a-fA-F]{2})/([0-9a-fA-F]{2})\)', text, re.I)))
    return {'reasons': reasons,
            'protocol_statuses': [{'class': value[0].lower(), 'detail': value[1].lower()}
                                  for value in codes],
            'login_rejection_rows': sum('login rejected:' in line.lower() for line in text.splitlines())}


def target_login_reasons():
    """Read both daemons by unit and syslog identifier, including older rows.

    iSCSI daemon logging can be attributed to its syslog identifier rather
    than its systemd unit. Avoid a date filter here so an attribution or
    timestamp difference cannot silently hide the original rejection.
    """
    result = {}
    try:
        output = subprocess.run(
            ['journalctl', '--no-pager', '-o', 'json', '-n', '400',
             '_SYSTEMD_UNIT=iscsid.service', '+', '_SYSTEMD_UNIT=tgt.service',
             '+', 'SYSLOG_IDENTIFIER=iscsid', '+', 'SYSLOG_IDENTIFIER=tgtd'],
            capture_output=True, text=True, timeout=20)
        messages = []
        for line in output.stdout.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict) and isinstance(row.get('MESSAGE'), str):
                messages.append(row['MESSAGE'])
        result['daemon_journal'] = {
            'readable': output.returncode == 0, 'rows': len(messages),
            **login_rejection_reasons('\n'.join(messages))}
    except (OSError, subprocess.TimeoutExpired):
        result['daemon_journal'] = {'readable': False}
    try:
        with Path('/var/log/syslog').open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4 * 1024 * 1024))
            lines = stream.read().decode(errors='replace').splitlines()
        messages = [line for line in lines if re.search(r'\b(?:iscsid|tgtd)(?:\[\d+\])?:', line)]
        result['daemon_syslog'] = {'readable': True, 'rows': len(messages),
                                   **login_rejection_reasons('\n'.join(messages))}
    except OSError:
        result['daemon_syslog'] = {'readable': False}
    try:
        output = subprocess.run(['tgtadm', '--lld', 'iscsi', '--mode', 'sys', '--op', 'show'],
                                capture_output=True, text=True, timeout=10)
        states = re.findall(r'(?im)^\s*State:\s*(ready|offline|shutdown)\s*$', output.stdout)
        result['target_system'] = {'query_succeeded': output.returncode == 0,
                                   'state': states[0].lower() if len(states) == 1 else 'unknown'}
    except (OSError, subprocess.TimeoutExpired):
        result['target_system'] = {'query_succeeded': False}
    return result


def iscsi_path_observations(state):
    """Only query node/session inventories and the target's current exports."""
    result = {
        'device_by_path_directory_present': Path('/dev/disk/by-path').is_dir(),
        'scsi_device_count': len(list(Path('/sys/class/scsi_device').glob('*'))),
    }
    for mode in ('node', 'session'):
        try:
            status = subprocess.run(['iscsiadm', '-m', mode], capture_output=True,
                                    text=True, timeout=10)
            matching = [line for line in status.stdout.splitlines()
                        if state['volume_id'].lower() in line.lower()]
            result[mode] = {
                'exit_code': status.returncode,
                'volume_match_count': len(matching),
                'matched_portals_loopback_only': bool(matching) and all(
                    '127.0.0.1:3260' in line for line in matching)}
        except (OSError, subprocess.TimeoutExpired):
            result[mode] = {'query_failed': True}
    try:
        targets = subprocess.run(
            ['tgtadm', '--lld', 'iscsi', '--mode', 'target', '--op', 'show'],
            capture_output=True, text=True, timeout=10)
        blocks = re.split(r'(?m)(?=^Target \d+:)', targets.stdout)
        matching = [block for block in blocks
                    if state['volume_id'].lower() in block.split('\n', 1)[0].lower()]
        result['target_export'] = {
            'query_succeeded': targets.returncode == 0,
            'volume_target_count': len(matching),
            'volume_backing_store_listed': any(
                '/dev/cinder-poc-vg/volume-' + state['volume_id'] in block for block in matching)}
    except (OSError, subprocess.TimeoutExpired):
        result['target_export'] = {'query_failed': True}
    return result


def chap_compatibility_observations(state):
    result = {'opted_in': state.get('loopback_chap_sha1') is True or state.get('loopback_chap_md5') is True,
              'nodes': {}}
    try:
        text = chap.regular_text(chap.ISCSID_CONF)
        result['global_algorithms'] = list(chap.chap_algorithms(text))
        digest = hashlib.sha256(chap.ISCSID_CONF.read_bytes()).hexdigest()
    except (OSError, ValueError, RuntimeError):
        result['global_readable'] = False
        digest = None
    for volume_id in (state.get('volume_id'), state.get('clone_id')):
        if not isinstance(volume_id, str) or not re.fullmatch(r'[0-9a-f-]{36}', volume_id):
            continue
        record = state.get('loopback_chap_nodes', {}).get(volume_id)
        if not isinstance(record, dict):
            continue
        item = {
            'prepared_algorithm': record.get('algorithm') if record.get('algorithm') in {'SHA1', 'MD5'} else 'unknown',
            'global_hash_matches_preparation': bool(digest and digest == record.get('global_config_sha256')),
        }
        try:
            query = chap.node_command(chap.PREFIX + 'volume-' + volume_id, accepted=(0, 21))
            item['node_present'] = query.returncode == 0
            if query.returncode == 0:
                item['current_node_algorithms'] = list(chap.chap_algorithms(query.stdout, default=()))
                item['query_warning'] = bool(query.stderr.strip())
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            item['query_failed'] = True
        result['nodes'][volume_id] = item
    return result


def attach_failure_observations(cloud, state):
    result = {}
    request_id = None
    started_at = None
    try:
        actions = [item for item in cloud.compute.server_actions(state['server_id'])
                   if item.action == 'attach_volume']
        actions.sort(key=lambda item: item.start_time or '', reverse=True)
        if actions:
            detail = cloud.compute.get_server_action(actions[0].request_id,
                                                     state['server_id'], ignore_missing=False)
            request_id = detail.request_id
            started_at = actions[0].start_time
            events = []
            for event in detail.events or []:
                name = event.event
                events.append({
                    'event': name if isinstance(name, str)
                    and re.fullmatch(r'[a-zA-Z0-9_]{1,80}', name) else 'unknown',
                    'result': event.result if event.result in {'Success', 'Error', 'Failed'} else 'unknown',
                    'traceback_locations': traceback_locations(str(event.traceback or '')),
                    **classify_failure(str(event.traceback or '') + '\n' + str(getattr(event, 'details', '') or ''))})
            result['latest_nova_attach'] = {'request_id': detail.request_id, 'events': events}
        else:
            result['latest_nova_attach'] = {'found': False}
    except Exception as error:
        result['latest_nova_attach'] = {'error_type': type(error).__name__}
    result['logs'] = log_failure_observations(state, request_id=request_id)
    result['initiator_journal'] = initiator_journal(started_at)
    result['iscsi_path'] = iscsi_path_observations(state)
    result['chap_compatibility'] = chap_compatibility_observations(state)
    result['initiator_prerequisites'] = {
        'iscsiadm_installed': shutil.which('iscsiadm') is not None,
        'kernel_components': {name: (Path('/sys/module') / name).exists()
                              for name in ('iscsi_tcp', 'libiscsi', 'scsi_transport_iscsi')},
        'tcp_transport_present': Path('/sys/class/iscsi_transport/tcp').exists(),
    }
    try:
        configured = bool(re.search(r'^InitiatorName=\S+',
                                   Path('/etc/iscsi/initiatorname.iscsi').read_text(), re.M))
    except OSError:
        configured = False
    result['initiator_prerequisites']['initiator_name_configured'] = configured
    for unit in ('iscsid.service', 'iscsid.socket', 'open-iscsi.service',
                 'systemd-udevd.service', 'tgt.service'):
        status = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'],
                                capture_output=True, text=True, timeout=10)
        value = status.stdout.strip()
        result['initiator_prerequisites'][unit] = value if status.returncode == 0 and value in {
            'active', 'inactive', 'failed', 'activating', 'deactivating'} else 'unavailable'
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read('/etc/nova/nova.conf')
        result['nova_service_auth'] = {
            option + '_configured': bool(parser.get('service_user', option, fallback=''))
            for option in ('auth_url', 'auth_type', 'username', 'password', 'project_name')}
        result['nova_iscsi'] = {
            'default_interface': parser.get('libvirt', 'iscsi_iface', fallback='default') in {'', 'default'},
            'multipath_enabled': parser.getboolean('libvirt', 'volume_use_multipath', fallback=False),
        }
    except (configparser.Error, ValueError):
        result['nova_service_auth'] = {'parseable': False}
    return result


def failure_summary(report):
    """Compact, credential-free output for a failed acceptance attempt."""
    failure = report.get('attach_failure', {})
    latest = failure.get('latest_nova_attach', {})
    log = failure.get('logs', {}).get('nova/nova-compute.log', {})
    event_categories = {category for event in latest.get('events', [])
                        for category in event.get('categories', [])}
    return {
        'phase': report.get('phase'),
        'volume_status': report.get('volume', {}).get('status'),
        'nova_attachment_count': len(report.get('nova_attachments', [])),
        'cinder_attachment_count': len(report.get('cinder_attachments', [])),
        'request_id': latest.get('request_id'),
        'categories': sorted(event_categories | set(log.get('categories', []))),
        'login_exit_codes': log.get('login_exit_codes', []),
        'chap_compatibility': failure.get('chap_compatibility', {}),
    }


def safe_state(value):
    return value if isinstance(value, str) and value in SAFE_STATES else 'unknown'


def protected_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('acceptance manifest must be a root-owned regular file with mode 0600')
    return json.loads(path.read_text())


def inspect(cloud, state):
    volume = cloud.block_storage.get_volume(state['volume_id'])
    if (volume.project_id != state['project_id']
            or (volume.metadata or {}).get('task18_run_id') != state['run_id']
            or volume.description != state['marker']):
        raise RuntimeError('volume ownership markers do not match; no details emitted')
    server = cloud.compute.get_server(state['server_id'])
    nova_records = [item for item in cloud.compute.volume_attachments(server)
                    if item.volume_id == volume.id]
    cinder_records = [item for item in cloud.block_storage.attachments(volume_id=volume.id)
                      if item.volume_id == volume.id]
    return {
        'phase': state.get('phase', 'unknown'),
        'volume': {'id': volume.id, 'status': safe_state(volume.status),
                   'owned': True, 'project_match': True},
        'server': {'id': server.id, 'status': server.status if server.status in
                   {'ACTIVE', 'SHUTOFF', 'ERROR', 'BUILD', 'PAUSED'} else 'unknown',
                   'task_pending': server.task_state is not None},
        'nova_attachments': [
            {'id': item.id,
             'device': item.device if isinstance(item.device, str)
             and re.fullmatch(r'/dev/vd[a-z]', item.device) else 'unknown',
             'volume_match': item.volume_id == volume.id}
            for item in nova_records],
        'cinder_attachments': [
            {'id': item.id,
             'status': safe_state(getattr(item, 'status', None)),
             'instance_match': getattr(item, 'instance', None) == server.id,
             'connector_present': bool(getattr(item, 'connector', None))}
            for item in cinder_records],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attach-errors', action='store_true',
                        help='also classify attach errors and inspect initiator prerequisites; raw logs are not emitted')
    parser.add_argument('--login-reason', action='store_true',
                        help='emit a compact, read-only target/login rejection report from both daemon logging paths')
    args = parser.parse_args(argv)
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
            raise RuntimeError('inspection is scoped to the existing Ubuntu 22.04 WSL POC')
        fd = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                     os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = protected_json(STATE)
            if state.get('schema') != 1 or state.get('task') != 'T18-Cinder-volume-acceptance':
                raise RuntimeError('manifest is not a recognized Task 18 acceptance run')
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                result = inspect(cloud, state)
                if args.login_reason:
                    result = {'volume': result['volume'], 'server': result['server'],
                              'login_rejection': target_login_reasons()}
                elif args.attach_errors:
                    result['attach_failure'] = attach_failure_observations(cloud, state)
            print('POC Cinder volume diagnostic: ' + json.dumps(result, sort_keys=True), flush=True)
        return 0
    except os_exc.ResourceNotFound:
        print('POC Cinder volume diagnostic: owned volume or guest was not found; preserve manifest and inspect.',
              file=sys.stderr, flush=True)
        return 1
    except Exception as error:
        print('POC Cinder volume diagnostic: STOP (' + type(error).__name__ + '); no changes performed.',
              file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
