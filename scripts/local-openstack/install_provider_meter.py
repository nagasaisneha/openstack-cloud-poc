#!/usr/bin/python3
"""Plan/install a small read-only source meter in the existing Ubuntu.

Creates a dedicated unprivileged local identity, enrolled-project SQL views and
a SELECT-only database identity. No provider table/data/service is modified.
Secrets stay in a root-owned service-readable file; Horizon reads only the
authorized meter socket. No OS/package upgrade or recovery rehearsal occurs.
"""
import argparse
import grp
import hashlib
import json
import logging
import os
from pathlib import Path
import pwd
import secrets

import configure_nova as common
import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety

CONFIG = Path('/etc/openstack-poc-meter/config.json')
VIEWS = {
    'instances': "SELECT i.uuid,i.project_id,i.created_at,i.launched_at,i.deleted_at,i.deleted,i.vm_state,i.vcpus,i.memory_mb,i.root_gb,i.ephemeral_gb FROM nova.instances i WHERE i.project_id IN ({projects})",
    'actions': "SELECT a.id,a.instance_uuid,a.action,a.request_id,a.start_time,a.finish_time,i.project_id FROM nova.instance_actions a JOIN nova.instances i ON i.uuid=a.instance_uuid WHERE i.project_id IN ({projects})",
    'events': "SELECT e.id,e.action_id,e.event,e.start_time,e.finish_time,e.result,i.project_id FROM nova.instance_actions_events e JOIN nova.instance_actions a ON a.id=e.action_id JOIN nova.instances i ON i.uuid=a.instance_uuid WHERE i.project_id IN ({projects})",
    'volumes': "SELECT v.id,v.project_id,v.size,v.created_at,v.launched_at,v.deleted_at,v.deleted,v.status FROM cinder.volumes v WHERE v.project_id IN ({projects})",
    'snapshots': "SELECT v.id,v.project_id,v.volume_size,v.created_at,v.deleted_at,v.deleted,v.status FROM cinder.snapshots v WHERE v.project_id IN ({projects})",
}


def view_sql(projects):
    import re
    safety.require(projects and all(re.fullmatch('[0-9a-f]{32}', item) for item in projects), 'unsafe_meter_project_list')
    scope = ','.join("'" + item + "'" for item in sorted(projects))
    return {name: template.format(projects=scope) for name, template in VIEWS.items()}


def service_unit(release):
    return ('[Unit]\nDescription=Read-only source-backed POC usage meter\nAfter=mariadb.service libvirtd.service\n'
            'Requires=mariadb.service libvirtd.service\n\n[Service]\nUser=poc-meter\nGroup=horizon\n'
            'SupplementaryGroups=poc-meter\nUMask=0077\nEnvironment=PYTHONNOUSERSITE=1\n'
            'ExecStart=/usr/bin/python3 -B ' + str(release / 'provider_meter_agent.py') + '\n'
            'RuntimeDirectory=openstack-poc-meter\nRuntimeDirectoryMode=0750\n'
            'StateDirectory=openstack-poc-meter\nStateDirectoryMode=0700\n'
            'NoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\n'
            'PrivateDevices=true\nRestrictAddressFamilies=AF_UNIX AF_INET\n'
            'CapabilityBoundingSet=\nMemoryMax=128M\nTasksMax=16\nRestart=on-failure\nRestartSec=3\n\n'
            '[Install]\nWantedBy=multi-user.target\n')


def apply(source):
    name, state = 'meter-install', task.read('meter-install', source)
    projects = sorted([source['retained_project_id']] + [item['id'] for item in source['projects'].values()])
    views = view_sql(projects)
    existing_db = common.sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='poc_meter_sources'")
    existing_user = common.sql("SELECT COUNT(*) FROM mysql.user WHERE User='poc_meter_reader'")
    safety.require((existing_db == existing_user == '0') or CONFIG.exists() and state.get('database_create_intent'),
                   'unowned_meter_database_or_identity_exists')
    try:
        user = pwd.getpwnam('poc-meter')
        safety.require(user.pw_shell == '/usr/sbin/nologin' and user.pw_dir == '/nonexistent', 'unowned_meter_os_user')
    except KeyError:
        safety.run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
                    '--shell', '/usr/sbin/nologin', 'poc-meter'])
        user = pwd.getpwnam('poc-meter')
    group = grp.getgrnam('poc-meter')
    CONFIG.parent.mkdir(mode=0o750, exist_ok=True)
    safety.require(CONFIG.parent.lstat().st_uid == 0 and not CONFIG.parent.is_symlink(), 'unsafe_meter_config_directory')
    os.chown(CONFIG.parent, 0, group.gr_gid)
    os.chmod(CONFIG.parent, 0o750)
    if CONFIG.exists():
        safety.protected(CONFIG, 0o640)
        config = json.loads(CONFIG.read_text())
        safety.require(config['project_ids'] == projects, 'meter_enrollment_changed_requires_review')
    else:
        config = {'schema': 1, 'project_ids': projects, 'horizon_uid': pwd.getpwnam('horizon').pw_uid,
                  'database_password': secrets.token_hex(32)}
        safety.atomic_write(CONFIG, json.dumps(config, sort_keys=True, indent=2) + '\n', 0o640, group.gr_gid)
    safety.require(__import__('re').fullmatch('[0-9a-f]{64}', config['database_password']), 'invalid_meter_database_credential')
    state['database_create_intent'] = True
    task.save(name, state)
    if existing_db == '0':
        common.sql('CREATE DATABASE poc_meter_sources')
    if existing_user == '0':
        common.sql("CREATE USER 'poc_meter_reader'@'127.0.0.1' IDENTIFIED BY '" + config['database_password'] + "'")
    for view, select in views.items():
        common.sql('CREATE OR REPLACE SQL SECURITY DEFINER VIEW poc_meter_sources.' + view + ' AS ' + select)
    common.sql("GRANT SELECT ON poc_meter_sources.* TO 'poc_meter_reader'@'127.0.0.1'")
    import pymysql
    connection = pymysql.connect(host='127.0.0.1', user='poc_meter_reader', password=config['database_password'],
                                  database='poc_meter_sources', connect_timeout=5, read_timeout=5)
    try:
        with connection.cursor() as db:
            db.execute('SELECT COUNT(*) FROM instances')
            state['visible_instance_history_count'] = int(db.fetchone()[0])
            # These are permission checks, not writes: LIMIT 0 still requires
            # the protected base-table privilege, which the meter must lack.
            denied = 0
            for forbidden in ('nova.instances', 'cinder.volumes', 'mysql.user'):
                try:
                    db.execute('SELECT * FROM ' + forbidden + ' LIMIT 0')
                except pymysql.err.OperationalError as error:
                    safety.require(error.args[0] in (1142, 1143), 'unexpected_meter_permission_error')
                    denied += 1
            safety.require(denied == 3, 'meter_base_table_access_not_denied')
            state['base_table_denials_verified'] = denied
            try:
                db.execute('UPDATE instances SET vcpus=vcpus WHERE 1=0')
                raise safety.GuardError('meter_view_write_privilege_not_denied')
            except pymysql.err.OperationalError as error:
                safety.require(error.args[0] in (1142, 1143), 'unexpected_meter_view_permission_error')
                state['view_write_denial_verified'] = True
    finally:
        connection.close()
    files = install.package_files()
    files['provider_meter_agent.py'] = (Path(__file__).parent / 'provider_meter_agent.py').read_bytes()
    digest = install.release_hash(files)
    release = Path('/opt/openstack-poc/meter-releases') / digest
    install.public_code_directory(release)
    for relative, content in files.items():
        target = release / relative
        if target.exists():
            safety.require(target.read_bytes() == content, 'immutable_meter_release_changed')
        else:
            install.write_root(target, content.decode())
    install.write_root(Path('/etc/systemd/system/poc-provider-meter.service'), service_unit(release))
    safety.run(['systemctl', 'daemon-reload'])
    safety.run(['systemctl', 'enable', '--now', 'poc-provider-meter.service'])
    if state.get('release') and state['release'] != str(release):
        safety.run(['systemctl', 'restart', 'poc-provider-meter.service'])
    state.update(phase='read_only_meter_installed_pending_collection', release=str(release),
                 enrolled_project_count=len(projects), os_upgrade=False, backup_or_restore_rehearsal=False)
    task.save(name, state)
    print(json.dumps({'phase': state['phase'], 'report': str(task.DIRECTORY / (name + '.json')),
                      'enrolled_projects': len(projects), 'base_table_denials': state['base_table_denials_verified']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--activate-in-horizon', action='store_true')
    args = parser.parse_args()
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    if args.activate_in_horizon:
        from datetime import datetime, timezone
        from openstack_cloud_poc.provider_usage import read_meter
        from openstack_cloud_poc.poc_metering import month_bounds
        start, end = month_bounds(datetime.now(timezone.utc))
        config_path = Path('/var/lib/openstack-poc-billing/collector.json')
        info = config_path.lstat()
        safety.require(info.st_uid == pwd.getpwnam('horizon').pw_uid and info.st_mode & 0o777 == 0o600,
                       'unsafe_horizon_collector_config')
        config = json.loads(config_path.read_text())
        for project in config['project_ids']:
            report = read_meter(project, start, end)
            safety.require(report.get('source_clock_checked') and report['counter_observation_count'] >= 2,
                           'meter_not_ready_for_horizon')
        config['verified_meter_socket'] = '/run/openstack-poc-meter/read.sock'
        task.tenants.write_collector_config(config)
        print(json.dumps({'phase': 'verified_meter_enabled_for_horizon', 'projects': len(config['project_ids']),
                          'statements_remain_provisional': True}), flush=True)
    elif args.apply:
        with task.locked():
            apply(source)
    else:
        print(json.dumps({'phase': 'read_only_meter_plan', 'source_views': list(VIEWS),
                          'database_privileges': 'SELECT on secret-free enrolled-project views only',
                          'libvirt_connection': 'read-only', 'service_memory_cap_mib': 128,
                          'credentials_exported_to_horizon': False, 'os_upgrade': False}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        reason = str(error) if isinstance(error, safety.GuardError) else type(error).__name__
        print(json.dumps({'phase': 'meter_install_stopped', 'reason': reason}), flush=True)
        raise SystemExit(2)
