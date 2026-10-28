#!/usr/bin/python3
"""Enable prospective Swift byte mutation evidence; never edits object data."""
import grp
import json
import logging
import os
from pathlib import Path
import pwd
import sys

import continue_poc as task
import install_poc_browser as install
import manage_cinder_loop as safety
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openstack_cloud_poc.swift_usage_journal import MutationJournal


def main():
    source, private = task.initialize()
    logging.disable(logging.CRITICAL)
    with task.locked():
        directory = Path('/var/lib/openstack-poc-swift-meter')
        owner, group = pwd.getpwnam('swift'), grp.getgrnam('poc-meter')
        if not directory.exists():
            directory.mkdir(mode=0o750)
            os.chown(directory, owner.pw_uid, group.gr_gid)
            os.chmod(directory, 0o750)
        info = directory.lstat()
        if (not directory.is_symlink() and info.st_uid == owner.pw_uid and info.st_gid == group.gr_gid
                and info.st_mode & 0o777 == 0o700 and not any(directory.iterdir())):
            os.chmod(directory, 0o750)  # Empty directory from an interrupted prepare under umask 077.
            info = directory.lstat()
        safety.require(not directory.is_symlink() and info.st_uid == owner.pw_uid
                       and info.st_gid == group.gr_gid and info.st_mode & 0o777 == 0o750,
                       'unsafe_swift_meter_directory')
        database = directory / 'events.sqlite'
        if database.exists():
            info = database.lstat()
            safety.require(not database.is_symlink() and info.st_uid == owner.pw_uid
                           and info.st_gid == group.gr_gid and info.st_mode & 0o777 == 0o640,
                           'unsafe_existing_swift_meter_database')
        MutationJournal(database)
        os.chown(database, owner.pw_uid, group.gr_gid)
        os.chmod(database, 0o640)
        projects = sorted([source['retained_project_id']] + [value['id'] for value in source['projects'].values()])
        config = Path('/etc/swift/poc-meter-projects.json')
        safety.atomic_write(config, json.dumps({'schema': 1, 'project_ids': projects}) + '\n', 0o640, owner.pw_gid)
        install.apply()
        state = task.read('t50-swift-journal', source)
        state.update(phase='prospective_mutation_journal_installed', project_count=len(projects),
            boundary_policy='successful_proxy_response_completion', historical_backfill=False,
            poison_marker_present=database.with_suffix('.failed').exists())
        task.save('t50-swift-journal', state)
        print(json.dumps({'phase': state['phase'], 'historical_backfill': False,
                          'poison_marker_present': state['poison_marker_present']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'phase': 'swift_journal_install_stopped',
            'reason': str(error) if isinstance(error, safety.GuardError) else type(error).__name__}), flush=True)
        raise SystemExit(2)
