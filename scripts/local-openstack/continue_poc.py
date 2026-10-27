#!/usr/bin/python3
"""Protected state and scope checks for the existing-Ubuntu T49 continuation."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re

import browser_resources as browser
import configure_nova as common
import manage_cinder_loop as safety
import prepare_browser_tenants as tenants

DIRECTORY = tenants.DIRECTORY / 'continuation-20261028'
MARK = 't49-existing-ubuntu'


def initialize():
    safety.host_guard()
    os.umask(0o077)
    source, private = tenants.load()
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    info = DIRECTORY.lstat()
    safety.require(info.st_uid == 0 and info.st_mode & 0o777 == 0o700 and not DIRECTORY.is_symlink(),
                   'unsafe_continuation_directory')
    return source, private


def read(name, source):
    path = DIRECTORY / (name + '.json')
    if not path.exists():
        return {'schema': 1, 'marker': source['marker'] + '/' + MARK, 'created_at': safety.now(),
                'intents': {}, 'resources': {}, 'checks': {}}
    safety.protected(path, 0o600)
    result = json.loads(path.read_text())
    safety.require(result['schema'] == 1 and result['marker'] == source['marker'] + '/' + MARK,
                   'continuation_scope_changed')
    return result


def save(name, state):
    state['updated_at'] = safety.now()
    safety.atomic_write(DIRECTORY / (name + '.json'), json.dumps(state, indent=2, sort_keys=True) + '\n', 0o600)


def intent(name, state, key, operation):
    safety.require(key not in state['intents'], 'uncertain_' + key + '_no_automatic_replay')
    state['intents'][key] = {'operation': operation, 'at': safety.now()}
    save(name, state)


def validate_tenant(cloud, source, label):
    project = source['projects'][label]['id']
    safety.require(cloud.session.get_project_id() == project, 'unexpected_tenant_token_scope')
    saved = source['resources'][label]
    vm = cloud.compute.get_server(saved['server']['id'])
    net = cloud.network.get_network(saved['network']['id'])
    safety.require(vm.project_id == net.project_id == project and vm.description == source['marker']
                   and vm.name == saved['server']['name'] and net.name == saved['network']['name']
                   and not net.is_shared and not net.is_router_external,
                   'marked_tenant_workload_changed')
    ports = [p for p in cloud.network.ports(device_id=vm.id) if p.network_id == net.id]
    safety.require(len(ports) == 1 and ports[0].project_id == project and ports[0].is_port_security_enabled,
                   'unexpected_owned_guest_port')
    return vm, net, ports[0]


@contextmanager
def locked():
    with safety.locked(safety.MANAGEMENT_LOCK):
        yield
