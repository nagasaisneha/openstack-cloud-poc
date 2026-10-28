#!/usr/bin/python3
"""Repeat the two-tenant native browser lifecycle on fresh disposable data.

Read-only plan by default. --apply requires a fresh run ID. Failures preserve
intentions and resources for inspection; an interrupted run is never replayed.
Existing retained/sample resources and backup/restore workflows are excluded.
"""
import argparse
import json
import logging
import re
from uuid import uuid4

import browser_lifecycle as life
import browser_poc as browser
import browser_resources as controls
import configure_nova as common
import continue_poc as task
import manage_cinder_loop as safety


def run(run_id):
    source, private = task.initialize()
    with task.locked():
        names = {label: 'lifecycle-' + label + '-' + run_id for label in ('a', 'b')}
        safety.require(all(not (task.DIRECTORY / (name + '.json')).exists() for name in names.values()),
                       'existing_regression_run_preserved_no_replay')
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = browser.browser_launch(pw)
            try:
                for label, name in names.items():
                    state = task.read(name, source)
                    state['prefix'] = 'poc-t49-' + label + '-' + uuid4().hex[:10]
                    task.save(name, state)
                    context = browser.local_context(br)
                    admin_context = browser.local_context(br)
                    try:
                        page = context.new_page()
                        browser.login(page, private['tenants'][label]['username'], private['tenants'][label]['password'])
                        admin = admin_context.new_page()
                        browser.login(admin, 'admin', common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'])
                        with controls.connection(source, private, label) as cloud:
                            life.create_vm(page, cloud, source, state, label, name)
                            life.vm_transition(page, cloud, source, state, label, name, 'stop')
                            life.vm_transition(page, cloud, source, state, label, name, 'start')
                            life.create_volume(page, cloud, source, state, label, name)
                            life.attach_volume(page, cloud, source, state, label, name)
                            life.access_and_io(page, cloud, source, state, label, name)
                            life.reserve_snapshot_capacity(admin, source, state, label, name)
                            life.detach_and_snapshot(page, cloud, source, state, label, name)
                            life.cleanup(page, cloud, source, state, label, name)
                            life.restore_snapshot_capacity(admin, source, state, label, name)
                        print(json.dumps({'phase': 'tenant_browser_regression_verified', 'label': label,
                                          'run_id': run_id, 'checks': list(state['checks'])}), flush=True)
                    finally:
                        context.close()
                        admin_context.close()
            finally:
                br.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    safety.require(re.fullmatch('[a-z0-9][a-z0-9-]{0,31}', args.run_id), 'unsafe_regression_run_id')
    logging.disable(logging.CRITICAL)
    if args.apply:
        run(args.run_id)
    else:
        print(json.dumps({'phase': 'browser_regression_plan', 'tenants': ['a', 'b'],
                          'run_id': args.run_id, 'resources': 'fresh marked VM, volume, snapshot and floating IP per tenant',
                          'quota': 'temporary 2 to 3 GiB for snapshot, restored after cleanup',
                          'source_resource_mutations': False, 'backup_or_restore_rehearsal': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'browser_regression_stopped', 'reason': str(error) if isinstance(error, safety.GuardError)
                          else type(error).__name__, 'automatic_replay': False}), flush=True)
        raise SystemExit(2)
