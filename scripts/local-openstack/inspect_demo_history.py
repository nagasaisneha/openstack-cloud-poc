#!/usr/bin/python3
"""Read the retained demo state and recent Nova actions without changing it."""
import fcntl
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys

import configure_nova as common
import launch_test_vm as launch
import review_restart_readiness as readiness
import validate_guest as guest


def bounded(value, limit=100):
    if not isinstance(value, str):
        return None
    return value[:limit] if re.fullmatch(r'[A-Za-z0-9_.:+-]{1,100}', value) else None


def report(server, actions, stop_detail=None):
    state = {
        'status': bounded(getattr(server, 'status', None), 32),
        'power_state': getattr(server, 'power_state', None),
        'task_state': bounded(getattr(server, 'task_state', None), 32),
        'updated_at': bounded(getattr(server, 'updated_at', None)),
    }
    if type(state['power_state']) is not int or not 0 <= state['power_state'] <= 7:
        state['power_state'] = None
    rows = []
    for item in actions[:100]:
        if not isinstance(item, dict):
            continue
        action = bounded(item.get('action'), 32)
        started = bounded(item.get('start_time'))
        updated = bounded(item.get('updated_at'))
        if action is not None:
            rows.append({'action': action, 'start_time': started, 'updated_at': updated})
    return {'server': state, 'recent_actions': rows, 'latest_stop_detail': stop_detail}


def safe_action_detail(action, admin_user_id):
    if not isinstance(action, dict):
        return None
    actor = action.get('user_id')
    if actor is None:
        actor_class = 'system_or_unattributed'
    elif actor == admin_user_id:
        actor_class = 'authenticated_admin'
    else:
        actor_class = 'other_user'
    events = []
    for item in action.get('events', [])[:50]:
        if not isinstance(item, dict):
            continue
        event = bounded(item.get('event'), 80)
        result = item.get('result')
        if event is not None:
            events.append({
                'event': event,
                'result': result if result in ('Success', 'Error') else None,
                'start_time': bounded(item.get('start_time')),
                'finish_time': bounded(item.get('finish_time')),
            })
    return {
        'action': bounded(action.get('action'), 32),
        'start_time': bounded(action.get('start_time')),
        'updated_at': bounded(action.get('updated_at')),
        'actor_class': actor_class,
        'events': events,
    }


def main():
    if os.geteuid() != 0:
        print('STOP: run with sudo /usr/bin/python3 -B in the actual Ubuntu terminal.', file=sys.stderr)
        return 1
    try:
        logging.disable(logging.CRITICAL)
        release = Path('/etc/os-release').read_text()
        if (not re.search(r'^ID=ubuntu$', release, re.M)
                or not re.search(r'^VERSION_ID="22\.04"$', release, re.M)
                or 'microsoft' not in platform.release().lower()):
            raise readiness.ReadinessError('existing Ubuntu 22.04 WSL required')
        os.umask(0o077)
        descriptor = os.open('/root/.openstack-poc-nova-prerequisites.lock',
                             os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            secrets = common.read_secrets(common.SECRET_FILE)
            with common.connection(secrets) as cloud:
                state, _ = guest.load_run(cloud, require_ssh=False)
                server = readiness.owned_demo(cloud, state)
                response = cloud.compute.get(
                    '/servers/' + readiness.DEMO_SERVER + '/os-instance-actions?limit=100',
                    raise_exc=True)
                body = response.json()
                actions = body.get('instanceActions', []) if isinstance(body, dict) else []
                stop = next((item for item in actions if isinstance(item, dict)
                             and item.get('action') == 'stop'), None)
                detail = None
                if (stop is not None and isinstance(stop.get('request_id'), str)
                        and re.fullmatch(r'[A-Za-z0-9-]{1,64}', stop['request_id'])):
                    detail_response = cloud.compute.get(
                        '/servers/' + readiness.DEMO_SERVER + '/os-instance-actions/'
                        + stop['request_id'], raise_exc=True, microversion='2.51')
                    detail_body = detail_response.json()
                    action = detail_body.get('instanceAction') if isinstance(detail_body, dict) else None
                    detail = safe_action_detail(action, cloud.session.get_user_id())
                print('POC demo history: ' + json.dumps(report(server, actions, detail), sort_keys=True), flush=True)
        return 0
    except Exception as error:
        details = readiness.safe_error(error)
        print('STOP: demo history unavailable: ' + json.dumps(details, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
