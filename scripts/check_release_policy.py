#!/usr/bin/env python3
"""Validate a proposed target or final evidence against the release policy.

This command is offline and performs no installation, endpoint contact or
host change. Evidence validation checks its supplied fields, not authenticity.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openstack_cloud_poc.release_policy import final_acceptance_blockers, target_blockers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--target', type=Path)
    group.add_argument('--final-evidence', type=Path)
    args = parser.parse_args()
    try:
        policy = json.loads((ROOT / 'deployment/release-policy.json').read_text())
        value = json.loads((args.target or args.final_evidence).read_text())
        if not isinstance(value, dict):
            raise ValueError('object required')
        blockers = (target_blockers(policy, value, today=date.today()) if args.target else
                    final_acceptance_blockers(policy, value, today=date.today()))
        print(json.dumps({'status': 'blocked' if blockers else 'fields_validated',
                          'blockers': blockers, 'live_acceptance_attested': False}, sort_keys=True))
        return 2 if blockers else 0
    except (OSError, ValueError, TypeError, KeyError):
        print('{"status":"unavailable","reason":"invalid_input_or_policy"}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
