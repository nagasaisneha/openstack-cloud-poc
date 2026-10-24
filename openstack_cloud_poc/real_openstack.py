"""Explicit SDK-backed management path, separate from the fixture executor.

Single-process, bounded asynchronous execution with a durable SQLite journal.
Interrupted/uncertain mutations remain unverified and are never auto-replayed.
The caller owns credentials/SDK sessions; neither tokens nor passwords are stored.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import BoundedSemaphore, RLock
import time
from uuid import UUID, uuid4

from .api_contract import LifecycleRequest, Operation
from .catalog import Catalog, CatalogCategory, CatalogRecord, VerificationStatus


SOURCE = 'openstack:Keystone/Glance/Nova/Neutron SDK'
MUTATIONS = {'compute.create', 'compute.start', 'compute.stop', 'compute.delete'}


class RealProviderError(RuntimeError):
    """Allowlisted category only; discard all raw SDK error text."""
    def __init__(self, code):
        if code not in {'authorization_denied', 'invalid_request', 'resource_conflict',
                        'provider_error', 'capacity_exhausted', 'closed', 'unverified'}:
            code = 'provider_error'
        self.code = code
        super().__init__('Real OpenStack operation: ' + code)


class _ObservedServerFailure(RealProviderError):
    """Nova explicitly reported ERROR, rather than an uncertain API outcome."""


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def provider_uuid(value):
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise RealProviderError('invalid_request') from None
    return value


class RealOpenStackAdapter:
    """Use only an explicitly injected SDK session and expected actor/project.

    Authentication is reissued before each management authorization, including
    worker execution. Caller-supplied identity claims are not trusted.
    """
    def __init__(self, cloud, project_id, user_id):
        self.cloud = cloud
        self.project_id = provider_uuid(project_id)
        self.user_id = provider_uuid(user_id)
        self._auth_lock = RLock()

    def authorize(self, project_id, operation):
        if project_id != self.project_id or operation not in MUTATIONS | {'catalog.read', 'compute.inspect', 'job.read'}:
            raise RealProviderError('authorization_denied')
        try:
            with self._auth_lock:
                self.cloud.session.auth.invalidate()
                access = self.cloud.session.auth.get_access(self.cloud.session)
                roles = set(access.role_names)
                required = {'reader', 'member'} if operation in {'catalog.read', 'compute.inspect', 'job.read'} else {'member'}
                if (access.project_id != project_id or access.user_id != self.user_id
                        or not access.project_scoped or access.system_scoped
                        or not roles.intersection(required)
                        or not roles.issubset({'reader', 'member'})):
                    raise RealProviderError('authorization_denied')
        except Exception:
            raise RealProviderError('authorization_denied') from None

    def read_catalog(self, project_id):
        self.authorize(project_id, 'catalog.read')
        records = []
        evidence = 'Authenticated project-scoped API observation at ' + timestamp() + '; not a placement/quota guarantee'
        try:
            for flavor in self.cloud.compute.flavors():
                records.append(CatalogRecord(CatalogCategory.FLAVOR, flavor.id, SOURCE,
                    VerificationStatus.UNVERIFIED,
                    {'name': flavor.name, 'vcpus': flavor.vcpus, 'memory_mib': flavor.ram,
                     'root_disk_gib': flavor.disk}, evidence))
            for image in self.cloud.image.images():
                records.append(CatalogRecord(CatalogCategory.IMAGE, image.id, SOURCE,
                    VerificationStatus.AVAILABLE if image.status == 'active' else VerificationStatus.UNVERIFIED,
                    {'name': image.name or '', 'provider_status': image.status or ''}, evidence))
            for network in self.cloud.network.networks():
                records.append(CatalogRecord(CatalogCategory.NETWORK, network.id, SOURCE,
                    VerificationStatus.AVAILABLE if network.status == 'ACTIVE' else VerificationStatus.UNVERIFIED,
                    {'name': network.name or '', 'shared': bool(network.is_shared)}, evidence))
            return Catalog(tuple(records))
        except Exception:
            raise RealProviderError('provider_error') from None

    def server(self, project_id, instance_id):
        provider_uuid(instance_id)
        self.authorize(project_id, 'compute.inspect')
        try:
            server = self.cloud.compute.get_server(instance_id)
            if server.project_id != project_id:
                raise RealProviderError('authorization_denied')
            return server
        except RealProviderError:
            raise
        except Exception:
            raise RealProviderError('provider_error') from None

    def inspect(self, project_id, instance_id):
        server = self.server(project_id, instance_id)
        return {'instance_id': server.id, 'project_id': project_id, 'state': server.status,
                'task_state': server.task_state, 'power_state': server.power_state,
                'evidence_mode': 'real', 'source': SOURCE, 'checked_at': timestamp()}


class RealLifecycleManagementService:
    """One worker, at most two accepted operations, explicit task ownership.

    No HTTP endpoint is implied. Inject a member-scoped adapter and protected
    journal path. A crashed worker is retained as unverified on reopening;
    identical resubmissions return that job without a second provider request.
    """
    def __init__(self, adapter, journal, run_id, *, create_options=None, timeout=180):
        if not re.fullmatch('[0-9a-f]{32}', run_id) or not 0 < timeout <= 300:
            raise RealProviderError('invalid_request')
        self.adapter, self.run_id, self.timeout = adapter, run_id, timeout
        self.options = dict(create_options or {})
        if set(self.options) - {'key_name', 'security_groups'}:
            raise RealProviderError('invalid_request')
        self._lock = RLock()
        self._capacity = BoundedSemaphore(2)
        self._closed = False
        self._futures = {}
        path = Path(journal)
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise RealProviderError('invalid_request')
        # Restrict journal and lifetime lock before SQLite opens any files.
        self._file = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise RealProviderError('invalid_request')
            finally:
                os.close(fd)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, project_id TEXT, actor_id TEXT, run_id TEXT,
                operation TEXT, request TEXT, idem_hash TEXT, fingerprint TEXT,
                state TEXT, instance_id TEXT, failure_code TEXT,
                created_at TEXT, updated_at TEXT,
                UNIQUE(project_id, actor_id, operation, idem_hash))''')
            self._db.execute("UPDATE jobs SET state='unverified', failure_code='interrupted', updated_at=? WHERE state IN ('pending','running')", (timestamp(),))
            self._db.commit()
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='real-openstack')
        except Exception:
            os.close(self._file)
            raise

    def _row(self, job_id):
        row = self._db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
        if row is None or row['run_id'] != self.run_id:
            raise RealProviderError('invalid_request')
        return dict(row)

    def _update(self, job_id, **fields):
        with self._lock:
            fields['updated_at'] = timestamp()
            self._db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in fields) + ' WHERE job_id=?',
                             (*fields.values(), job_id))
            self._db.commit()

    def get_job(self, job_id, project_id):
        self.adapter.authorize(project_id, 'job.read')
        with self._lock:
            row = self._row(job_id)
            if row['project_id'] != project_id or row['actor_id'] != self.adapter.user_id:
                raise RealProviderError('authorization_denied')
            return {key: row[key] for key in ('job_id', 'project_id', 'operation', 'state',
                'instance_id', 'failure_code', 'created_at', 'updated_at')} | {'evidence_mode': 'real', 'source': SOURCE}

    def submit(self, payload, idempotency_key):
        try:
            request = LifecycleRequest.from_mapping(payload)
            if request.operation.value not in MUTATIONS or not isinstance(idempotency_key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', idempotency_key):
                raise ValueError
            for field, value in request.to_wire().items():
                if field != 'operation':
                    provider_uuid(value)
        except Exception:
            raise RealProviderError('invalid_request') from None
        self.adapter.authorize(request.project_id, request.operation.value)
        encoded = json.dumps(request.to_wire(), sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        idem = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self._lock:
            if self._closed:
                raise RealProviderError('closed')
            previous = self._db.execute('SELECT * FROM jobs WHERE project_id=? AND actor_id=? AND operation=? AND idem_hash=?',
                (request.project_id, self.adapter.user_id, request.operation.value, idem)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint or previous['run_id'] != self.run_id:
                    raise RealProviderError('resource_conflict')
                return self._accepted(previous['job_id'], request)
            if request.instance_id and self._db.execute("SELECT 1 FROM jobs WHERE instance_id=? AND state IN ('pending','running','unverified')", (request.instance_id,)).fetchone():
                raise RealProviderError('resource_conflict')
            if not self._capacity.acquire(blocking=False):
                raise RealProviderError('capacity_exhausted')
            job_id = 'openstack-job-' + uuid4().hex
            now = timestamp()
            try:
                self._db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (job_id, request.project_id, self.adapter.user_id, self.run_id, request.operation.value,
                     encoded, idem, fingerprint, 'pending', request.instance_id, None, now, now))
                self._db.commit()
                self._futures[job_id] = self._pool.submit(self._execute, job_id)
            except Exception:
                self._db.rollback()
                self._update(job_id, state='failed', failure_code='execution_rejected')
                self._capacity.release()
                raise RealProviderError('provider_error') from None
            return self._accepted(job_id, request)

    @staticmethod
    def _accepted(job_id, request):
        return {'status': 'accepted', 'job_id': job_id, 'project_id': request.project_id,
                'operation': request.operation.value, 'evidence_mode': 'real', 'source': SOURCE}

    def _owned_mutation_server(self, request):
        server = self.adapter.server(request.project_id, request.instance_id)
        with self._lock:
            created = self._db.execute("SELECT job_id FROM jobs WHERE instance_id=? AND project_id=? AND actor_id=? AND run_id=? AND operation='compute.create'", (server.id, request.project_id, self.adapter.user_id, self.run_id)).fetchall()
        if (len(created) != 1 or (server.metadata or {}).get('poc_run_id') != self.run_id
                or server.metadata.get('poc_job_id') != created[0]['job_id']):
            raise RealProviderError('authorization_denied')
        return server

    def _execute(self, job_id):
        uncertain = False
        try:
            with self._lock:
                row = self._row(job_id)
            request = LifecycleRequest.from_mapping(json.loads(row['request']))
            self.adapter.authorize(request.project_id, request.operation.value)
            self._update(job_id, state='running')
            cloud = self.adapter.cloud
            if request.operation is Operation.COMPUTE_CREATE:
                image = cloud.image.get_image(request.image_id)
                network = cloud.network.get_network(request.network_id)
                cloud.compute.get_flavor(request.flavor_id)
                if image.status != 'active' or network.project_id != request.project_id or network.is_shared or network.is_router_external:
                    raise RealProviderError('resource_conflict')
                # This first execution submits at most one create. Any lost
                # response leaves an unverified job; resubmission never retries.
                uncertain = True
                server = cloud.compute.create_server(name=job_id, image_id=request.image_id,
                    flavor_id=request.flavor_id, networks=[{'uuid': request.network_id}],
                    metadata={'poc_run_id': self.run_id, 'poc_job_id': job_id},
                    config_drive=False, **self.options)
                provider_uuid(server.id)
                self._update(job_id, instance_id=server.id)
                instance_id = server.id
                target, power = 'ACTIVE', 1
            else:
                server = self._owned_mutation_server(request)
                instance_id = server.id
                if server.task_state is not None:
                    raise RealProviderError('resource_conflict')
                if request.operation is Operation.COMPUTE_DELETE:
                    uncertain = True
                    cloud.compute.delete_server(instance_id, ignore_missing=False)
                    target, power = 'DELETED', None
                else:
                    target, power = ('ACTIVE', 1) if request.operation is Operation.COMPUTE_START else ('SHUTOFF', 4)
                    source = 'SHUTOFF' if target == 'ACTIVE' else 'ACTIVE'
                    if server.status not in {source, target}:
                        raise RealProviderError('resource_conflict')
                    if server.status != target:
                        uncertain = True
                        action = cloud.compute.start_server if target == 'ACTIVE' else cloud.compute.stop_server
                        action(instance_id)
            self._observe(job_id, request.project_id, instance_id, target, power)
            self._update(job_id, state='succeeded', failure_code=None)
        except Exception as exc:
            code = exc.code if isinstance(exc, RealProviderError) else 'provider_error'
            self._update(job_id, state='unverified' if uncertain and not isinstance(exc, _ObservedServerFailure) else 'failed', failure_code=code)
        finally:
            self._capacity.release()

    def _observe(self, job_id, project_id, instance_id, target, power):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                server = self.adapter.cloud.compute.get_server(instance_id)
            except Exception as exc:
                if target == 'DELETED' and getattr(exc, 'status_code', None) == 404:
                    return
                raise
            with self._lock:
                origins = self._db.execute("SELECT job_id FROM jobs WHERE operation='compute.create' AND instance_id=? AND project_id=? AND actor_id=? AND run_id=?", (instance_id, project_id, self.adapter.user_id, self.run_id)).fetchall()
            if (server.project_id != project_id or (server.metadata or {}).get('poc_run_id') != self.run_id
                    or len(origins) != 1 or server.metadata.get('poc_job_id') != origins[0]['job_id']):
                raise RealProviderError('authorization_denied')
            if server.status == 'ERROR':
                raise _ObservedServerFailure('provider_error')
            if server.status == target and server.task_state is None and server.power_state == power:
                return
            time.sleep(2)
        raise RealProviderError('unverified')

    def close(self):
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)
        self._db.close()
        os.close(self._file)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
