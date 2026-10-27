#!/usr/bin/python3
"""Show local POC login names; print passwords only with explicit operator opt-in.

Run locally as root. Do not redirect --show-passwords output into logs/reports.
Never displays collector credentials, session tokens or private SSH keys.
"""
import argparse
import json

import configure_nova as common
import manage_cinder_loop as safety
import prepare_browser_tenants as tenants


def access(private, admin_password=None, show_passwords=False):
    entries = [{'username': 'admin', 'project': 'admin', 'domain': 'Default'}]
    if show_passwords:
        entries[0]['password'] = admin_password
    for label in ('a', 'b'):
        original = private['tenants'][label]
        item = {'username': original['username'], 'project': original['project_name'], 'domain': 'Default'}
        if show_passwords:
            item['password'] = original['password']
        entries.append(item)
    return {'url': 'https://localhost:8443/horizon/', 'accounts': entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-passwords', action='store_true',
                        help='Deliberately display only administrator and demo tenant login passwords locally')
    args = parser.parse_args()
    safety.host_guard()
    ignored, private = tenants.load()
    admin = common.read_secrets(common.SECRET_FILE)['ADMIN_PASS'] if args.show_passwords else None
    print(json.dumps(access(private, admin, args.show_passwords), indent=2))


if __name__ == '__main__':
    main()
