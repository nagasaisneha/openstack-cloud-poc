#!/usr/bin/python3
"""Read-only SQL metadata inventory for the two recorded Task 11 projects.

No cloud API calls, credential/configuration output, object/file writes or
deletions. Avoids Neutron's lazy default-group creation on list requests.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import uuid

import configure_nova as common
import launch_test_vm as launch
import validate_guest as guest


def checked_uuid(value):
    if not isinstance(value, str):
        raise RuntimeError('inventory project/resource ID must be a UUID')
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise RuntimeError('inventory project/resource ID must be a UUID') from None
    if value not in (parsed.hex, str(parsed)):
        raise RuntimeError('inventory project/resource ID must be canonical')
    return value


def network_resources(project_ids):
    projects = {checked_uuid(value) for value in project_ids}
    if not projects:
        raise RuntimeError('explicit project IDs required for inventory')
    selected = ','.join("'" + value + "'" for value in sorted(projects))
    statements = ['SET TRANSACTION READ ONLY', 'START TRANSACTION WITH CONSISTENT SNAPSHOT']
    for kind, table in (('network', 'networks'), ('subnet', 'subnets'), ('port', 'ports'),
                        ('router', 'routers'), ('floating_ip', 'floatingips')):
        statements.append("SELECT JSON_OBJECT('kind','" + kind + "','id',id,'project_id',project_id) "
                          'FROM neutron.' + table + ' WHERE project_id IN (' + selected + ') ORDER BY id')
    statements.append("""SELECT JSON_OBJECT('kind','security_group','id',g.id,'project_id',g.project_id,
        'default_name',g.name='default','default_description',a.description='Default security group',
        'is_project_default',EXISTS(SELECT 1 FROM neutron.default_security_group d
            WHERE d.project_id=g.project_id AND d.security_group_id=g.id),
        'bound_port_count',(SELECT COUNT(*) FROM neutron.securitygroupportbindings b WHERE b.security_group_id=g.id),
        'external_reference_count',(SELECT COUNT(*) FROM neutron.securitygrouprules r
            WHERE r.remote_group_id=g.id AND r.security_group_id<>g.id),
        'sharing_policy_count',(SELECT COUNT(*) FROM neutron.securitygrouprbacs b WHERE b.object_id=g.id),
        'rule_count',(SELECT COUNT(*) FROM neutron.securitygrouprules r WHERE r.security_group_id=g.id),
        'created_at',a.created_at,'updated_at',a.updated_at)
        FROM neutron.securitygroups g LEFT JOIN neutron.standardattributes a ON a.id=g.standard_attr_id
        WHERE g.project_id IN (""" + selected + ') ORDER BY g.id')
    statements.append('COMMIT')
    output = common.sql(';\n'.join(statements) + ';\n')
    rows = []
    allowed = {'network', 'subnet', 'port', 'router', 'floating_ip', 'security_group'}
    for line in output.splitlines():
        row = json.loads(line)
        if (not isinstance(row, dict) or row.get('kind') not in allowed
                or row.get('project_id') not in projects):
            raise RuntimeError('unexpected SQL inventory result; preserving resources')
        checked_uuid(row['id'])
        # Print only fixed-schema metadata; never echo an unexpected SQL field.
        fields = {'kind', 'id', 'project_id'}
        if row['kind'] == 'security_group':
            fields |= {'default_name', 'default_description', 'is_project_default', 'bound_port_count',
                       'external_reference_count', 'sharing_policy_count', 'rule_count', 'created_at', 'updated_at'}
        if set(row) != fields:
            raise RuntimeError('unexpected SQL inventory fields; refusing to expose contents')
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='exact recorded Task 11 run marker')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal; no cleanup executed.', file=sys.stderr)
        return 1
    try:
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M) or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise RuntimeError('inventory scoped to existing Ubuntu 22.04 WSL')
        info = launch.DATA_DIR.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('protected acceptance directory ownership/mode differs')
        manifests = []
        for filename in ('run.json', 'application.json'):
            path = launch.DATA_DIR / filename
            guest.protected_file(path, 0o600)
            manifests.append(json.loads(path.read_text()))
        original, state = manifests
        if args.run != original['run_id'] or args.run != state['run_id'] or not re.fullmatch('[0-9a-f]{32}', args.run):
            raise RuntimeError('exact protected run marker required')
        import cleanup_application_dependencies as cleanup
        record = state['results']['dependency_cleanup']
        cleanup.validate_plan(record['plan'], original, state)
        records = original['results']['tenant_api']['tenants']
        projects = {label: checked_uuid(records[label]['project_id']) for label in ('a', 'b')}
        print('Task 11 remaining inventory: BEGIN (SQL READ ONLY; no cloud API requests)', flush=True)
        print('Recorded completed cleanup indices: ' + json.dumps(record['done']), flush=True)
        rows = network_resources(projects.values())
        targets = {item['id'] for item in record['plan']}
        for label, project_id in projects.items():
            selected = [dict(row, recorded_plan_id=row['id'] in targets) for row in rows if row['project_id'] == project_id]
            print('Tenant ' + label + ': ' + json.dumps({'project_id': project_id, 'network_resources': selected}, sort_keys=True), flush=True)
        print('Task 11 remaining inventory: COMPLETE (metadata only; no cleanup executed)', flush=True)
        return 0
    except Exception as exc:
        # No raw database exception/configuration/credential output.
        print('STOP: read-only inventory failed (' + type(exc).__name__ + '); no cleanup executed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
