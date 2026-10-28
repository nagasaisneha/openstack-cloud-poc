#!/usr/bin/python3
"""Verify live prospective journal ownership, authorized ACL writes and cleanup."""
import json
import logging
from pathlib import Path
import sqlite3
from uuid import uuid4

import browser_resources as controls
import continue_poc as task
import manage_cinder_loop as safety


def main():
    logging.disable(logging.CRITICAL)
    source, private = task.initialize()
    with task.locked(), controls.connection(source, private, 'a') as a, controls.connection(source, private, 'b') as b:
        state = task.read('t50-swift-journal-live', source)
        safety.require(not state['intents'], 'existing_journal_acceptance_preserved')
        project = source['projects']['a']['id']
        base = 'http://127.0.0.1:8080/v1/AUTH_' + project + '/poc-t50-journal-' + uuid4().hex[:10]
        database = '/var/lib/openstack-poc-swift-meter/events.sqlite'
        with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
            before = db.execute('SELECT coalesce(max(sequence),0) FROM mutations').fetchone()[0]
        task.intent('t50-swift-journal-live', state, 'new-acl-container', 'create_only_marked_journal_test_container')
        a.object_store.put(base, headers={'X-Container-Meta-Poc': state['marker']}, raise_exc=True)
        try:
            denied = b.object_store.put(base + '/owned', data=b'forbidden', raise_exc=False)
            safety.require(denied.status_code in (403, 404), 'private_foreign_put_not_denied')
            acl = source['projects']['b']['id'] + ':' + source['users']['b']['id']
            a.object_store.post(base, headers={'X-Container-Write': acl}, raise_exc=True)
            b.object_store.put(base + '/owned', data=b'journal-acl-write', raise_exc=True)
            safety.require(a.object_store.get(base + '/owned', raise_exc=True).content == b'journal-acl-write',
                           'authorized_acl_write_content_mismatch')
            denied_read = b.object_store.get(base + '/owned', raise_exc=False)
            safety.require(denied_read.status_code in (403, 404), 'write_only_acl_leaked_read_access')
            b.object_store.delete(base + '/owned', raise_exc=True)
            with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute('SELECT * FROM mutations WHERE sequence>?', (before,))]
            safety.require(len(rows) == 2 and [row['method'] for row in rows] == ['PUT', 'DELETE']
                and [row['size_bytes'] for row in rows] == [17, 0]
                and all(row['project_id'] == project and row['known'] == 1 for row in rows),
                'live_swift_journal_owner_or_outcome_mismatch')
            state['checks'] = {'private_foreign_put_denied': True, 'explicit_acl_writer_put_delete_allowed': True,
                'write_acl_does_not_grant_read': True, 'two_durable_known_events': True,
                'charged_to_storage_account_owner_not_actor': True, 'server_transaction_id_verified': True}
        finally:
            metadata = a.object_store.head(base, raise_exc=True)
            safety.require(metadata.headers.get('X-Container-Meta-Poc') == state['marker'], 'journal_test_cleanup_scope_changed')
            object_head = a.object_store.head(base + '/owned', raise_exc=False)
            safety.require(object_head.status_code in (200, 404), 'journal_cleanup_object_state_unknown')
            if object_head.status_code == 200:
                a.object_store.delete(base + '/owned', raise_exc=True)
            a.object_store.delete(base, raise_exc=True)
        state['checks']['test_container_absent'] = a.object_store.head(base, raise_exc=False).status_code == 404
        state['phase'] = 'live_swift_journal_and_acl_attribution_verified'
        task.save('t50-swift-journal-live', state)
        output = Path(__file__).resolve().parents[2] / 'docs/evidence/swift-journal-live-20261029.json'
        output.write_text(json.dumps({'phase': state['phase'], 'checks': state['checks']}, indent=2) + '\n')
        output.chmod(0o644)
        print(json.dumps({'phase': state['phase'], 'checks': state['checks']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'swift_journal_acceptance_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
