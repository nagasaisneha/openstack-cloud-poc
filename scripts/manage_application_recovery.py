#!/usr/bin/env python3
"""Back up or rehearse application SQLite recovery into NEW private paths only.

Parent directories must be owned by the invoking user with mode 0700; database
and bundle files require 0600. No cloud/service/WSL calls or in-place restore.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openstack_cloud_poc.application_recovery import (
    create_backup, verify_backup, rehearse_restore, private_directory,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    backup = actions.add_parser('backup')
    backup.add_argument('--jobs', type=Path)
    backup.add_argument('--billing', type=Path)
    backup.add_argument('--destination', type=Path, required=True)
    verify = actions.add_parser('verify')
    verify.add_argument('--bundle', type=Path, required=True)
    restore = actions.add_parser('rehearse')
    restore.add_argument('--bundle', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    checkpoint = actions.add_parser('checkpoint', help='Back up and rehearse into new generated private paths')
    checkpoint.add_argument('--jobs', type=Path)
    checkpoint.add_argument('--billing', type=Path)
    checkpoint.add_argument('--parent', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action in ('backup', 'checkpoint'):
            destination = args.destination if args.action == 'backup' else Path(tempfile.mkdtemp(
                prefix='application-recovery-', dir=private_directory(args.parent)))
            result = create_backup({role: getattr(args, role) for role in ('jobs', 'billing')
                                    if getattr(args, role)}, destination if args.action == 'backup' else destination / 'bundle')
            if args.action == 'checkpoint':
                result = rehearse_restore(destination / 'bundle', destination / 'restored')
        elif args.action == 'verify':
            result = verify_backup(args.bundle)
        else:
            result = rehearse_restore(args.bundle, args.destination)
        print(json.dumps({'status': 'PASS', 'scope': 'supplied_application_sqlite_databases_only',
                          'components': sorted(result['components']),
                          'recovery_directory': str(destination) if args.action == 'checkpoint' else None,
                          'cloud_or_wsl_recovery_verified': False}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'unavailable', 'error_type': type(error).__name__,
                          'live_database_overwritten': False,
                          'cloud_or_wsl_recovery_verified': False}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
