"""Durable successful Swift object mutations for prospective byte-hour billing.

The charging boundary is successful proxy-response completion, never a guessed
backend timestamp. Missing sizes, COPY/versioning, failed persistence and
conflicting duplicate IDs prevent complete billing. No historical repair is
inferred from today's account totals.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


class MutationJournal:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('unsafe_swift_journal_path')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    object_key TEXT NOT NULL, method TEXT NOT NULL, at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS completions (
                    request_id TEXT PRIMARY KEY, status INTEGER NOT NULL,
                    event_id TEXT, known INTEGER NOT NULL);
                CREATE TRIGGER IF NOT EXISTS request_no_update BEFORE UPDATE ON requests
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS request_no_delete BEFORE DELETE ON requests
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS completion_no_update BEFORE UPDATE ON completions
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS completion_no_delete BEFORE DELETE ON completions
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TABLE IF NOT EXISTS mutations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL,
                    object_key TEXT NOT NULL, method TEXT NOT NULL,
                    size_bytes INTEGER, at TEXT NOT NULL,
                    request_started_at TEXT NOT NULL, known INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS swift_project_time ON mutations(project_id,at);
                CREATE TRIGGER IF NOT EXISTS swift_no_update BEFORE UPDATE ON mutations
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER IF NOT EXISTS swift_no_delete BEFORE DELETE ON mutations
                    BEGIN SELECT RAISE(ABORT,'immutable'); END;
            ''')

    def begin(self, project_id, object_key, method, at):
        request_id = uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (request_id, project_id, object_key, method, at))
        return request_id

    def finish(self, request_id, status, event_id, known):
        with self.connect() as db:
            db.execute('INSERT INTO completions VALUES (?,?,?,?)', (request_id, status, event_id, int(known)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def append(self, *, event_id, project_id, object_key, method, size_bytes, request_started_at, known):
        if not re.fullmatch('[0-9a-f]{32}', project_id) or not re.fullmatch('[0-9a-f]{64}', object_key):
            raise ValueError('invalid_swift_event_identity')
        if size_bytes is not None and (type(size_bytes) is not int or size_bytes < 0):
            raise ValueError('invalid_swift_object_size')
        values = (project_id, object_key, method, size_bytes, request_started_at, int(known))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT project_id,object_key,method,size_bytes,request_started_at,known '
                                  'FROM mutations WHERE event_id=?', (event_id,)).fetchone()
            if previous:
                if previous != values:
                    raise ValueError('conflicting_swift_event_id')
                return
            at = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO mutations(event_id,project_id,object_key,method,size_bytes,at,request_started_at,known) '
                       'VALUES (?,?,?,?,?,?,?,?)', (event_id, project_id, object_key, method, size_bytes, at,
                                                 request_started_at, int(known)))


class SwiftMutationJournal:
    def __init__(self, app, journal, project_ids):
        self.app, self.journal, self.project_ids = app, journal, frozenset(project_ids)

    def __call__(self, environ, start_response):
        method, path = environ.get('REQUEST_METHOD'), environ.get('PATH_INFO', '')
        parts = path.split('/', 4)
        account = parts[2] if len(parts) > 2 else ''
        project = account[5:] if account.startswith('AUTH_') else ''
        # Charge the storage account owner, including ACL/TempURL-authorized
        # writes by another actor. Authorization is decided by the inner Swift
        # app; only its successful outcome can create a usage mutation.
        track = (project in self.project_ids and re.fullmatch('[0-9a-f]{32}', project) is not None
                 and len(parts) == 5 and parts[3] and parts[4]
                 and method in ('PUT', 'DELETE', 'COPY', 'POST'))
        if not track:
            return self.app(environ, start_response)
        started = datetime.now(timezone.utc).isoformat()
        object_key = hashlib.sha256(path.encode()).hexdigest()
        # A missing completion is durable evidence of uncertain source coverage.
        # Failure here stops the request before it can mutate provider data.
        request_id = self.journal.begin(project, object_key, method, started)
        captured = {}

        def response(status, headers, exc_info=None):
            captured.update(status=int(status.split()[0]), headers={k.lower(): v for k, v in headers})
            return start_response(status, headers, exc_info)

        try:
            iterator = self.app(environ, response)
        except Exception:
            self.journal.finish(request_id, 0, None, False)
            raise

        def body():
            completed = False
            try:
                yield from iterator
                completed = True
            finally:
                if hasattr(iterator, 'close'):
                    iterator.close()
                status = captured.get('status', 0)
                successful = 200 <= status < 300 or method == 'DELETE' and status == 404
                transaction = captured.get('headers', {}).get('x-trans-id') or environ.get('swift.trans_id')
                known = completed and status in (400, 401, 403, 404, 409, 411, 412, 413, 422)
                if successful:
                    known = completed and method in ('PUT', 'DELETE', 'POST')
                    raw_size = environ.get('CONTENT_LENGTH')
                    size = int(raw_size) if method == 'PUT' and raw_size and raw_size.isdecimal() else None
                    if method == 'DELETE':
                        size = 0
                    if method == 'PUT' and size is None:
                        known = False
                    known = known and bool(transaction) and not any(
                        name.startswith('x-object-version') for name in captured.get('headers', {}))
                    if (environ.get('HTTP_X_OBJECT_MANIFEST') or 'multipart-manifest' in environ.get('QUERY_STRING', '')
                            or captured.get('headers', {}).get('x-static-large-object', '').lower() == 'true'):
                        known = False
                    try:
                        self.journal.append(event_id=transaction or 'missing-' + uuid4().hex,
                            project_id=project, object_key=object_key,
                            method=method, size_bytes=size, request_started_at=started, known=known)
                    except Exception:
                        # The provider write already completed. Preserve a durable
                        # poison marker and let the source refuse completeness.
                        poison = self.journal.path.with_suffix('.failed')
                        fd = os.open(poison, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
                        try:
                            os.write(fd, (datetime.now(timezone.utc).isoformat() + '\n').encode())
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        raise
                self.journal.finish(request_id, status, transaction if successful else None, known)
        return body()


def wrap(app, path, projects_path):
    projects = json.loads(Path(projects_path).read_text())['project_ids']
    return SwiftMutationJournal(app, MutationJournal(path), projects)
