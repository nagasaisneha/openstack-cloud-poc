#!/usr/bin/python3
"""Manage only the local HeidiSQL viewer; never print secrets by default.

Run as root in the existing Ubuntu WSL distribution. --apply creates the
account and retains a generated password outside the checkout. --verify is
read-only. --show-password is for deliberate local password-manager/clipboard
use only; never redirect its output to reports or automation logs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

import pymysql


USER = 'openstack_viewer'
HOST = '127.0.0.1'
DATABASES = ('cinder', 'glance', 'keystone', 'neutron', 'nova', 'nova_api',
             'nova_cell0', 'octavia', 'placement', 'poc_meter_sources')
PRIVATE = Path('/root/openstack-poc-acceptance/mariadb-viewer')
CREDENTIALS = PRIVATE / 'credentials.json'
GRANT_TABLES = ('global_priv', 'db', 'tables_priv', 'columns_priv', 'procs_priv',
                'proxies_priv', 'roles_mapping')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def private_path(path, mode):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == mode,
            'protected path ownership/type/permissions mismatch')


def save_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def existing_access(cursor):
    """Fingerprint authentication and grants without printing either."""
    state = {}
    for table in GRANT_TABLES:
        cursor.execute(f'SELECT * FROM mysql.`{table}` WHERE User <> %s', (USER,))
        state[table] = sorted((repr(row) for row in cursor.fetchall()))
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def verify_grants(cursor):
    cursor.execute('SELECT Host FROM mysql.global_priv WHERE User=%s', (USER,))
    require(cursor.fetchall() == ((HOST,),), 'unexpected viewer host entry')
    cursor.execute('SELECT Priv FROM mysql.global_priv WHERE User=%s AND Host=%s', (USER, HOST))
    grant = json.loads(cursor.fetchone()[0])
    require(grant.get('access', 0) == 0 and grant.get('plugin') == 'mysql_native_password'
            and not grant.get('auth_or') and not grant.get('default_role'),
            'unexpected viewer global privileges/authentication/role')
    cursor.execute('SELECT * FROM mysql.db WHERE User=%s AND Host=%s', (USER, HOST))
    fields = [item[0] for item in cursor.description]
    rows = [dict(zip(fields, row)) for row in cursor.fetchall()]
    # Database grants treat underscores as wildcards unless explicitly escaped.
    require({row['Db'] for row in rows} == {db.replace('_', '\\_') for db in DATABASES},
            'unexpected viewer database grant scope')
    for row in rows:
        require(all(value == ('Y' if name == 'Select_priv' else 'N')
                    for name, value in row.items() if name.endswith('_priv')),
                'viewer has a privilege other than SELECT')
    for table in GRANT_TABLES[2:]:
        cursor.execute(f'SELECT COUNT(*) FROM mysql.`{table}` WHERE User=%s', (USER,))
        require(cursor.fetchone()[0] == 0, 'unexpected viewer object/proxy/role grant')


def verify_connection(password):
    with pymysql.connect(host=HOST, port=3306, user=USER, password=password,
                         connect_timeout=5, read_timeout=10, write_timeout=10,
                         autocommit=True) as client:
        with client.cursor() as cursor:
            cursor.execute('SELECT CURRENT_USER()')
            require(cursor.fetchone()[0] == USER + '@' + HOST, 'unexpected authenticated identity')
            cursor.execute('SHOW DATABASES')
            visible = {row[0] for row in cursor.fetchall()}
            require(visible == set(DATABASES) | {'information_schema'}, 'unexpected schema visibility')
            for database in DATABASES:
                cursor.execute('SELECT TABLE_NAME FROM information_schema.tables '
                               'WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME LIMIT 1', (database,))
                table = cursor.fetchone()[0].replace('`', '``')
                # Exercise SELECT authorization without returning tenant data.
                cursor.execute(f'SELECT * FROM `{database}`.`{table}` LIMIT 0')
            try:
                cursor.execute('SELECT User FROM mysql.user LIMIT 0')
            except pymysql.MySQLError as error:
                require(error.args[0] in (1044, 1142), 'unexpected system-schema denial')
            else:
                raise RuntimeError('system-schema read was allowed')
            # Even if UPDATE were unexpectedly allowed, WHERE 1=0 changes no rows.
            try:
                cursor.execute('UPDATE keystone.project SET id=id WHERE 1=0')
            except pymysql.MySQLError as error:
                require(error.args[0] in (1044, 1142, 1143), 'unexpected UPDATE denial')
            else:
                raise RuntimeError('UPDATE was allowed')
    try:
        client = pymysql.connect(host=HOST, port=3306, user=USER,
                                 password=secrets.token_urlsafe(32), connect_timeout=5)
    except pymysql.MySQLError as error:
        require(error.args[0] == 1045, 'unexpected wrong-password result')
    else:
        client.close()
        raise RuntimeError('incorrect password was accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--show-password', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
            and 'VERSION_ID="22.04"' in Path('/etc/os-release').read_text(),
            'run as root in the existing Ubuntu 22.04 WSL distribution')
    os.umask(0o077)
    if args.apply:
        PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_path(PRIVATE, 0o700)
    private = None
    if CREDENTIALS.exists():
        private_path(CREDENTIALS, 0o600)
        private = json.loads(CREDENTIALS.read_text())
        require(private['username'] == USER and private['host'] == HOST, 'credential identity mismatch')
    if args.show_password:
        require(private is not None, 'viewer has not been configured')
        print(private['password'])
        return
    with pymysql.connect(unix_socket='/run/mysqld/mysqld.sock', user='root',
                         autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute('SELECT @@bind_address, @@port, @@skip_networking')
            require(cursor.fetchone() == (HOST, 3306, 0), 'expected loopback TCP listener is absent')
            cursor.execute('SHOW DATABASES')
            require(set(DATABASES) <= {row[0] for row in cursor.fetchall()}, 'required schema is missing')
            before = existing_access(cursor)
            cursor.execute('SELECT Host FROM mysql.global_priv WHERE User=%s', (USER,))
            hosts = cursor.fetchall()
            require(not hosts or hosts == ((HOST,),), 'viewer exists with unexpected hosts; preserving it')
            if private is None:
                require(args.apply and not hosts, 'unowned account or missing credentials; preserving account')
                private = {'username': USER, 'host': HOST, 'port': 3306,
                           'password': secrets.token_urlsafe(32), 'existing_access_sha256': before}
                save_new(CREDENTIALS, private)
            require(private['existing_access_sha256'] == before,
                    'existing accounts/grants differ from the retained baseline')
            if args.apply:
                if not hosts:
                    # Submit a hash rather than the plaintext password to SQL/logs.
                    digest = hashlib.sha1(hashlib.sha1(private['password'].encode()).digest()).hexdigest().upper()
                    cursor.execute(f"CREATE USER '{USER}'@'{HOST}' IDENTIFIED BY PASSWORD '*{digest}'")
                else:
                    # Refuse to adopt/reset or broaden a pre-existing account.
                    verify_grants(cursor)
                for database in DATABASES:
                    scope = database.replace('_', '\\_')
                    cursor.execute(f"GRANT SELECT ON `{scope}`.* TO '{USER}'@'{HOST}'")
            verify_grants(cursor)
            verify_connection(private['password'])
            require(existing_access(cursor) == before, 'existing account/grant preservation failed')
            report = {'observed_at': datetime.now(timezone.utc).isoformat(),
                      'username': USER, 'account_host': HOST, 'listener': HOST + ':3306',
                      'databases': DATABASES, 'select_each_database': True,
                      'only_select_grants': True, 'database_name_wildcards_escaped': True,
                      'system_schema_denied': True, 'no_row_update_denied': True,
                      'wrong_password_denied': True, 'existing_authentication_and_grants_unchanged': True,
                      'password_printed': False}
    if args.apply:
        # A sanitized operational receipt remains beside the protected secret.
        output = PRIVATE / ('verification-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
        save_new(output, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # SQL exceptions can contain submitted statements. Never echo them.
        print('database viewer operation failed: ' + type(error).__name__, file=sys.stderr)
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        sys.exit(1)
