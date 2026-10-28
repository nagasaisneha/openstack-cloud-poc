#!/usr/bin/python3
"""Plan/install bounded local monitoring without external alert delivery."""
import argparse
import grp
import json
from pathlib import Path
import pwd
import os

import configure_nova as common
import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety

DIRECTORY = Path('/var/lib/openstack-poc-operations')
BASELINE_UNITS = [
    'glance-api.service', 'neutron-server.service', 'neutron-ovn-metadata-agent.service',
    'nova-api.service', 'nova-conductor.service', 'nova-scheduler.service', 'nova-compute.service',
    'swift-account.service', 'swift-container.service', 'swift-object.service',
    'swift-container-updater.service', 'swift-object-updater.service', 'swift-proxy.service',
    'apache2.service', 'cinder-scheduler.service', 'cinder-volume.service', 'openvswitch-switch.service',
    'ovn-central.service', 'ovn-controller.service', 'ovn-controller-vtep.service', 'ovn-northd.service',
    'ovn-ovsdb-server-nb.service', 'ovn-ovsdb-server-sb.service', 'mariadb.service',
    'rabbitmq-server.service', 'libvirtd.service', 'tgt.service',
]
UNITS = BASELINE_UNITS + [
    'poc-provider-meter.service', 'poc-usage-collector.timer', 'poc-ingress-bridge.service',
    'poc-guest-a-ssh.service', 'poc-guest-a-http.service', 'poc-guest-b-ssh.service', 'poc-guest-b-http.service',
    'octavia-api.service', 'octavia-driver-agent.service', 'poc-operations.timer',
]


def apply(source):
    ingress = task.read('connectivity', source)
    lb = task.read('octavia-acceptance', source)
    http = {'dashboard': {'url': 'https://localhost:8443/horizon/auth/login/'}}
    for label in ('a', 'b'):
        http['tenant-' + label] = {'url': 'http://127.0.0.1:' + str(ingress['access'][label]['http_port']) + '/',
                                  'backend_ids': [source['resources'][label]['server']['id']], 'tenant': label}
    units = list(UNITS)
    if lb.get('checks', {}).get('vip_route'):
        backend = task.read('octavia-backends', source)
        http['vip'] = {'url': 'http://127.0.0.1:8083/', 'backend_ids': [backend[k]['id'] for k in ('primary', 'secondary')], 'tenant': 'a'}
        units.append('poc-vip-http.service')
    config = {'schema': 1, 'units': units, 'http': http,
              'project_ids': [source['retained_project_id']] + [p['id'] for p in source['projects'].values()]}
    account = pwd.getpwnam('horizon')
    directory = Path('/etc/openstack-poc-operations')
    directory.mkdir(mode=0o750, exist_ok=True)
    safety.require(directory.lstat().st_uid == 0 and not directory.is_symlink(), 'unowned_operations_configuration_directory')
    os.chown(directory, 0, account.pw_gid)
    os.chmod(directory, 0o750)
    safety.atomic_write(directory / 'config.json', json.dumps(config, indent=2) + '\n', 0o640, account.pw_gid)
    if not DIRECTORY.exists():
        DIRECTORY.mkdir(mode=0o700)
        os.chown(DIRECTORY, account.pw_uid, account.pw_gid)
    info = DIRECTORY.lstat()
    safety.require(not DIRECTORY.is_symlink() and info.st_uid == account.pw_uid and info.st_mode & 0o777 == 0o700,
                   'unsafe_operations_state_directory')
    files = install.package_files()
    files['poc_operations_agent.py'] = (Path(__file__).parent / 'poc_operations_agent.py').read_bytes()
    release = Path('/opt/openstack-poc/operations-releases') / install.release_hash(files)
    for relative, content in files.items():
        target = release / relative
        if target.exists():
            safety.require(target.read_bytes() == content, 'immutable_operations_release_changed')
        else:
            install.write_root(target, content.decode())
    service = ('[Unit]\nDescription=Local POC health and alert observations\nAfter=apache2.service poc-provider-meter.service\n\n'
        '[Service]\nType=oneshot\nUser=horizon\nGroup=horizon\nUMask=0077\n'
        'ExecStart=/usr/bin/python3 -B ' + str(release / 'poc_operations_agent.py') + '\n'
        'TimeoutStartSec=40\nNoNewPrivileges=true\nPrivateTmp=true\nPrivateDevices=true\n'
        'ProtectSystem=strict\nProtectHome=true\nReadWritePaths=' + str(DIRECTORY) + '\n'
        'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nMemoryMax=96M\nTasksMax=8\n')
    timer = ('[Unit]\nDescription=Observe local POC health every 15 seconds\n\n'
        '[Timer]\nOnBootSec=15\nOnUnitInactiveSec=15\nAccuracySec=1\n\n[Install]\nWantedBy=timers.target\n')
    install.write_root(Path('/etc/systemd/system/poc-operations.service'), service)
    install.write_root(Path('/etc/systemd/system/poc-operations.timer'), timer)
    safety.run(['systemctl', 'daemon-reload'])
    safety.run(['systemctl', 'enable', '--now', 'poc-operations.timer'])
    safety.run(['systemctl', 'start', 'poc-operations.service'], timeout=45)
    status = json.loads((DIRECTORY / 'status.json').read_text())
    state = task.read('operations-install', source)
    state.update(phase='local_operations_monitor_installed', release=str(release), probe_count=len(status['probes']),
                 audit_retention='90 daily rotations', external_delivery=False, billing_evidence_expiry=False)
    task.save('operations-install', state)
    print(json.dumps({'phase': state['phase'], 'probe_count': state['probe_count'], 'active_alerts': status['active_alerts'],
                      'external_delivery': False, 'audit_daily_rotations': 90}), flush=True)


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--apply', action='store_true')
        args = parser.parse_args()
        source, private = task.initialize()
        if args.apply:
            with task.locked():
                apply(source)
        else:
            print(json.dumps({'phase': 'operations_plan', 'interval_seconds': 15, 'failure_success_threshold': 2,
                              'audit_daily_rotations': 90, 'memory_cap_mib': 96, 'external_delivery': False}))
    except Exception as error:
        print(json.dumps({'phase': 'operations_install_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__}), flush=True)
        raise SystemExit(2)
