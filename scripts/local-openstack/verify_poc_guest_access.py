#!/usr/bin/python3
"""Verify pinned key SSH and a dedicated real guest app through Neutron FIPs.

Only the two marked sample guests are configured. Their existing root disks,
keys and volumes are preserved. The small application has its own directory,
marker and PID guard; it does not replace any pre-existing guest service.
"""
import argparse
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety

NAME = 'connectivity'


def known_hosts(label):
    path = task.DIRECTORY / ('known_hosts_' + label)
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    safety.protected(path, 0o600)
    return path


def ssh_command(source, state, label, *, pin_only=False):
    expected = source['resources'][label]['server']['id']
    safety.require(state['access'][label]['guest_id'] == expected, 'ssh_guest_scope_changed')
    key = task.tenants.DIRECTORY / ('id_ed25519_' + label)
    safety.protected(key, 0o600)
    return ['ssh', '-F', '/dev/null', '-i', str(key), '-p', str(state['access'][label]['ssh_port']),
            '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'IdentitiesOnly=yes', '-o', 'ProxyCommand=none', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
            '-o', 'StrictHostKeyChecking=' + ('yes' if pin_only else 'accept-new'),
            '-o', 'UserKnownHostsFile=' + str(known_hosts(label)), '-o', 'GlobalKnownHostsFile=/dev/null',
            'cirros@127.0.0.1', 'sh', '-s']


def ssh(source, state, label, script, *, pin_only=False):
    result = subprocess.run(ssh_command(source, state, label, pin_only=pin_only), input=script,
                            text=True, capture_output=True, timeout=25)
    safety.require(result.returncode == 0, 'guest_ssh_failed_' + label + '_exit_' + str(result.returncode))
    return result.stdout


def application_script(expected):
    encoded = base64.b64encode((json.dumps(expected, sort_keys=True) + '\n').encode()).decode()
    marker = shlex.quote(expected['run'])
    return '''set -eu
directory=/home/cirros/poc-t49-app
if [ -e "$directory" ]; then
    [ -d "$directory" ] && [ ! -L "$directory" ]
    [ "$(cat "$directory/owner")" = MARKER ]
else
    mkdir -m 700 "$directory"
    printf '%s\\n' MARKER > "$directory/owner"
fi
printf '%s' ENCODED | base64 -d > "$directory/index.html"
cat > "$directory/start.sh" <<'POC_APP_SCRIPT'
#!/bin/sh
set -eu
directory=/home/cirros/poc-t49-app
if [ -f "$directory/httpd.pid" ]; then
    pid=$(cat "$directory/httpd.pid")
    case "$pid" in *[!0-9]*|'') exit 2;; esac
    if kill -0 "$pid" 2>/dev/null; then
        tr '\\000' ' ' < "/proc/$pid/cmdline" | grep -F 'httpd -f -p 8080 -h /home/cirros/poc-t49-app' >/dev/null
        exit 0
    fi
fi
nohup busybox httpd -f -p 8080 -h "$directory" > "$directory/httpd.log" 2>&1 < /dev/null &
printf '%s\\n' "$!" > "$directory/httpd.pid"
POC_APP_SCRIPT
chmod 700 "$directory/start.sh"
"$directory/start.sh"
printf 'POC_APP_STARTED\\n'
'''.replace('MARKER', marker).replace('ENCODED', shlex.quote(encoded))


def verify(source, private, state, configure):
    safety.require(state.get('access') and set(state['access']) == {'a', 'b'}, 'guest_forward_configuration_missing')
    checks = {}
    client = build_opener(ProxyHandler({}))
    for label in ('a', 'b'):
        with task.browser.connection(source, private, label) as cloud:
            vm, network, port = task.validate_tenant(cloud, source, label)
            safety.require(vm.status == 'ACTIVE' and port.id == state['access'][label]['port_id'], 'guest_not_ready')
        deadline = time.monotonic() + 60
        while True:
            try:
                raw = ssh(source, state, label, 'set -eu\nwget -q -T 8 -O - http://169.254.169.254/openstack/latest/meta_data.json\n')
                identity = json.loads(raw)
                safety.require(identity['uuid'] == vm.id, 'ssh_guest_identity_mismatch')
                break
            except Exception:
                safety.require(time.monotonic() < deadline, 'guest_ssh_not_ready_' + label)
                time.sleep(2)
        expected = {'tenant': label, 'backend': 'primary-' + label, 'run': state['marker'], 'server_id': vm.id}
        if configure:
            ssh(source, state, label, application_script(expected), pin_only=True)
        url = 'http://127.0.0.1:' + str(state['access'][label]['http_port']) + '/'
        deadline = time.monotonic() + 20
        while True:
            try:
                with client.open(url, timeout=5) as response:
                    actual = json.loads(response.read(4096))
                    safety.require(response.status == 200 and actual == expected, 'guest_application_identity_mismatch')
                break
            except Exception:
                safety.require(time.monotonic() < deadline, 'guest_app_not_ready_' + label)
                time.sleep(1)
        checks[label] = {'pinned_key_ssh': True, 'metadata_uuid_verified': True,
                         'floating_ip_path': True, 'localhost_http': 200,
                         'application_sha256': hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()}
        print(json.dumps({'phase': 'guest_ssh_and_app_verified', 'tenant': label, **checks[label]}), flush=True)
    state['checks']['linux_guest_access'] = checks
    state['phase'] = 'linux_routed_ssh_and_application_verified'
    task.save(NAME, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-apps', action='store_true')
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked():
        verify(source, private, task.read(NAME, source), args.configure_apps)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'guest_access_stopped', 'reason': reason}), flush=True)
        raise SystemExit(2)
