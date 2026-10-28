#!/usr/bin/python3
"""Unprivileged, read-only provider/event and libvirt meter; private Unix API.

Database access is restricted to enrolled-project, secret-free SQL views.
Libvirt is opened read-only. Immutable snapshots preserve input provenance;
socket peers and requested projects are independently authorized. No cloud
resource, guest, disk or provider configuration can be mutated by this agent.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import sqlite3
import stat
import struct
import threading
import time
import xml.etree.ElementTree as ET

from openstack_cloud_poc.provider_usage import compile_usage, timestamp
from openstack_cloud_poc.usage_history import batch_wire

CONFIG = Path('/etc/openstack-poc-meter/config.json')
STATE = Path('/var/lib/openstack-poc-meter')
SOCKET = Path('/run/openstack-poc-meter/read.sock')
TABLES = ('instances', 'actions', 'events', 'volumes', 'snapshots')


def serialize(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    raise TypeError('unsupported_provider_field')


class Store:
    def __init__(self, directory):
        self.path = Path(directory) / 'evidence.sqlite'
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('unsafe_meter_state_directory')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('unsafe_meter_database')
        finally:
            os.close(fd)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS meter_project_time ON snapshots(project_id, observed_at);
                CREATE TRIGGER IF NOT EXISTS meter_no_update BEFORE UPDATE ON snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS meter_no_delete BEFORE DELETE ON snapshots
                    BEGIN SELECT RAISE(ABORT, 'immutable'); END;
            ''')

    @contextmanager
    def connect(self):
        result = sqlite3.connect(self.path, timeout=5)
        try:
            result.execute('PRAGMA synchronous=FULL')
            with result:
                yield result
        finally:
            result.close()

    def append(self, payload):
        text = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=serialize)
        key = hashlib.sha256(text.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)',
                       (key, payload['project_id'], timestamp(payload['observed_at']).isoformat(), text))
        return key

    def report(self, project_id, start, end):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM snapshots WHERE project_id=? ORDER BY observed_at DESC LIMIT 1',
                             (project_id,)).fetchone()
            if row is None:
                raise ValueError('no_meter_collection')
            latest = json.loads(row[0])
            # One predecessor is needed to detect a boundary instead of
            # prorating or pretending the first cumulative value is a delta.
            previous = db.execute('SELECT observed_at FROM snapshots WHERE project_id=? AND observed_at<? '
                                  'ORDER BY observed_at DESC LIMIT 1', (project_id, start.isoformat())).fetchone()
            floor = previous[0] if previous else start.isoformat()
            ceiling = min(end, timestamp(latest['observed_at'])).isoformat()
            rows = db.execute('SELECT payload FROM snapshots WHERE project_id=? AND observed_at>=? '
                              'AND observed_at<=? ORDER BY observed_at LIMIT 100001',
                              (project_id, floor, ceiling))
            observations = 0
            def stream():
                nonlocal observations
                for index, row in enumerate(rows):
                    if index >= 100000:
                        raise ValueError('meter_history_limit')
                    for counter in json.loads(row[0]).get('counters', []):
                        observations += 1
                        yield counter
            # Keep a month's history bounded in memory. The producer serializes
            # collection, timestamps counters in order, and seals each snapshot
            # after the last counter, so SQL source order is counter order.
            batch = compile_usage(project_id, start, end, latest, stream(), counters_are_ordered=True)
        return {'project_id': project_id, 'batch': batch_wire(batch), 'measurement_policy': {
            'compute_active_boundary': 'provider_success_event_finish',
            'cpu': 'domain_process_seconds_including_hypervisor',
            'network': 'combined_interface_bytes_not_internet_egress',
            'volume': 'provisioned_one_gib_lifetime', 'snapshot': 'provisioned_gib_lifetime'},
            'latest_source_timestamp': latest['observed_at'],
            'provider_snapshot_at': latest.get('source_snapshot_at', latest['observed_at']),
            'counter_observation_count': observations,
            'source_clock_checked': latest.get('source_clock_checked', False)}


def provider_snapshot(config):
    import pymysql
    result = {}
    connection = pymysql.connect(host='127.0.0.1', user='poc_meter_reader', password=config['database_password'],
        database='poc_meter_sources', connect_timeout=5, read_timeout=5, write_timeout=5,
        cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    try:
        with connection.cursor() as db:
            db.execute("SET time_zone='+00:00'")
            db.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            db.execute('SET SESSION TRANSACTION READ ONLY')
            connection.begin()
            for project in config['project_ids']:
                snapshot = {'project_id': project}
                for table in TABLES:
                    db.execute('SELECT * FROM `' + table + '` WHERE project_id=%s LIMIT 10001', (project,))
                    rows = db.fetchall()
                    if len(rows) > 10000:
                        raise ValueError('provider_view_limit')
                    snapshot[table] = rows
                result[project] = snapshot
            db.execute('SELECT UTC_TIMESTAMP(6) AS clock')
            at = db.fetchone()['clock'].replace(tzinfo=timezone.utc)
            local = datetime.now(timezone.utc)
            if abs((local - at).total_seconds()) > 2:
                raise ValueError('provider_clock_skew')
            for snapshot in result.values():
                snapshot.update(observed_at=at.isoformat(), source_snapshot_at=at.isoformat(), source_clock_checked=True)
        connection.rollback()
    finally:
        connection.close()
    return result


def hypervisor_counters(snapshots):
    import libvirt
    libvirt.registerErrorHandler(lambda context, error: None, None)
    expected = {item['uuid']: project for project, snapshot in snapshots.items() for item in snapshot['instances']
                if not item.get('deleted')}
    host_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    connection = libvirt.openReadOnly('qemu:///system')
    try:
        for domain in connection.listAllDomains():
            resource = domain.UUIDString()
            if resource not in expected or not domain.isActive():
                continue
            project = expected[resource]
            tree = ET.fromstring(domain.XMLDesc(0))
            owner = tree.find('./metadata/{*}instance/{*}owner/{*}project')
            if owner is None or owner.get('uuid') != project:
                raise ValueError('hypervisor_provider_project_mismatch')
            cpu = domain.info()[4]
            total, identities = 0, []
            for interface in tree.findall('./devices/interface'):
                target = interface.find('target')
                if target is None:
                    raise ValueError('missing_domain_interface_target')
                stats = domain.interfaceStats(target.get('dev'))
                if stats[0] < 0 or stats[4] < 0:
                    raise ValueError('unavailable_domain_interface_counter')
                total += stats[0] + stats[4]
                mac = interface.find('mac')
                identities.append((target.get('dev'), mac.get('address') if mac is not None else None))
            if cpu < 0:
                raise ValueError('unavailable_domain_cpu_counter')
            snapshots[project].setdefault('counters', []).append({
                'project_id': project, 'resource_id': resource,
                'generation': host_boot + ':' + str(domain.ID()) + ':' + hashlib.sha256(
                    json.dumps(sorted(identities)).encode()).hexdigest()[:16],
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'cpu_ns': int(cpu), 'network_bytes': int(total)})
    finally:
        connection.close()


class Meter:
    def __init__(self, config, directory):
        self.config = config
        self.store = Store(directory)
        self.lock = threading.RLock()
        self.last_error = None

    def collect(self):
        with self.lock:
            snapshots = provider_snapshot(self.config)
            hypervisor_counters(snapshots)
            for value in snapshots.values():
                value['observed_at'] = datetime.now(timezone.utc).isoformat()
                self.store.append(value)
            self.last_error = None

    def run(self):
        while True:
            try:
                self.collect()
                print(json.dumps({'phase': 'provider_meter_collected', 'projects': len(self.config['project_ids'])}), flush=True)
            except Exception as error:
                self.last_error = type(error).__name__
                print(json.dumps({'phase': 'provider_meter_unavailable', 'error_type': self.last_error}), flush=True)
            time.sleep(30)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        meter = self.server.meter
        try:
            ignored_pid, uid, ignored_gid = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, meter.config['horizon_uid']):
                raise ValueError('untrusted_meter_peer')
            request = json.loads(self.rfile.readline(4097))
            project = request['project_id']
            if project not in meter.config['project_ids']:
                raise ValueError('unenrolled_project')
            if request.get('collect'):
                if uid != 0:
                    raise ValueError('force_collection_requires_operator')
                meter.collect()
            start, end = timestamp(request['start']), timestamp(request['end'])
            if not 0 < (end - start).total_seconds() <= 32 * 86400:
                raise ValueError('invalid_meter_period')
            result = meter.store.report(project, start, end)
            self.wfile.write(json.dumps(result).encode() + b'\n')
        except Exception:
            self.wfile.write(b'{"error":"meter_unavailable"}\n')


def main():
    os.umask(0o077)
    info = CONFIG.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        raise ValueError('unsafe_meter_config')
    config = json.loads(CONFIG.read_text())
    meter = Meter(config, STATE)
    if SOCKET.exists():
        if not stat.S_ISSOCK(SOCKET.lstat().st_mode) or SOCKET.lstat().st_uid != os.geteuid():
            raise ValueError('unsafe_existing_meter_socket')
        SOCKET.unlink()
    with socketserver.UnixStreamServer(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.meter = meter
        worker = threading.Thread(target=meter.run, daemon=True)
        worker.start()
        server.serve_forever(poll_interval=0.5)


if __name__ == '__main__':
    main()
