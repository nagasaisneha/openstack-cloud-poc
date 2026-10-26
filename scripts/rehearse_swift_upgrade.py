#!/usr/bin/env python3
"""Rehearse Swift backend format/recovery in a NEW /tmp directory only.

Runs the real installed engines under explicitly selected interpreters, without
network listeners, service changes, live config/data or Keystone credentials.
This is component evidence, not integrated Swift/cloud upgrade acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

PAYLOADS = (b'isolated-swift-recovery-source-object\n', b'isolated-swift-recovery-target-object\n')


def check_worker_root(root):
    if (not root.is_absolute() or root.resolve() != root or root.parent.parent != Path('/tmp')
            or not root.parent.name.startswith('poc-swift-format-recovery-')
            or root.name not in ('source-copy', 'restored-copy')):
        raise RuntimeError('unsafe_component_workspace')
    info = root.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError('unsafe_component_workspace')


def worker(root, operation):
    check_worker_root(root)
    if operation not in ('write_source', 'write_target', 'read_source', 'read_target'):
        raise RuntimeError('unknown_component_action')
    from swift import __version__
    from swift.common import utils
    from swift.common.swob import Request
    from swift.account.server import AccountController
    from swift.container.server import ContainerController
    from swift.obj.server import ObjectController

    class QuietLogger:
        txn_id = None
        thread_locals = (None, None)
        def __init__(self):
            self.logger = self
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    # These synthetic salts apply only to this process and its temporary files.
    utils.HASH_PATH_PREFIX = b'isolated-component-test-prefix'
    utils.HASH_PATH_SUFFIX = b'isolated-component-test-suffix'
    (root / 'devices/poc1').mkdir(parents=True, exist_ok=True)
    conf = {'devices': str(root / 'devices'), 'mount_check': 'false',
            'log_requests': 'false', 'replication_server': 'false'}
    controllers = (AccountController(conf, logger=QuietLogger()),
                   ContainerController(conf, logger=QuietLogger()),
                   ObjectController(conf, logger=QuietLogger()))
    security = 'not_asserted_for_preserved_source'
    if __version__ in ('2.35.5', '2.37.4'):
        from swift.common.middleware.gatekeeper import GatekeeperMiddleware
        observed = {}
        def downstream(env, start_response):
            observed.update(Request(env).headers)
            start_response('204 No Content', [])
            return [b'']
        gate = GatekeeperMiddleware(downstream, {})
        gate.logger = QuietLogger()
        forbidden = ('X-Container-Host', 'X-Container-Device', 'X-Container-Partition',
                     'X-Account-Host', 'X-Delete-At-Host', 'X-Backend-Injected')
        headers = {name: '192.0.2.1:6200' for name in forbidden}
        headers['X-Object-Meta-Preserved'] = 'ordinary-tenant-metadata'
        checked = Request.blank('/v1/AUTH_component/container/object', headers=headers).get_response(gate)
        if (checked.status_int != 204 or any(name in observed for name in forbidden)
                or observed.get('X-Object-Meta-Preserved') != 'ordinary-tenant-metadata'):
            raise RuntimeError('ssrf_control_header_filter_failed')
        security = 'PASS'
    paths = ('/poc1/1/AUTH_component', '/poc1/1/AUTH_component/container',
             '/poc1/1/AUTH_component/container/object')
    def request(controller, path, method, body=b''):
        value = Request.blank(path, method=method, body=body,
                              headers={'X-Timestamp': utils.Timestamp(time.time()).internal,
                                       'Content-Type': 'application/octet-stream'}).get_response(controller)
        if value.status_int >= 400:
            raise RuntimeError('backend_request_failed')
        return value
    if operation == 'write_source':
        request(controllers[0], paths[0], 'PUT')
        request(controllers[1], paths[1], 'PUT')
        request(controllers[2], paths[2], 'PUT', PAYLOADS[0])
        payload = PAYLOADS[0]
    elif operation == 'write_target':
        request(controllers[2], paths[2], 'PUT', PAYLOADS[1])
        payload = PAYLOADS[1]
    else:
        payload = PAYLOADS[0] if operation == 'read_source' else PAYLOADS[1]
    request(controllers[0], paths[0], 'HEAD')
    request(controllers[1], paths[1], 'HEAD')
    response = request(controllers[2], paths[2], 'GET')
    if response.body != payload:
        raise RuntimeError('object_contents_mismatch')
    return {'swift_version': __version__, 'python': '.'.join(map(str, sys.version_info[:3])),
            'ssrf_control_header_filter': security,
            'account_head': 'PASS', 'container_head': 'PASS', 'object_get_sha256': hashlib.sha256(response.body).hexdigest()}


def tree_digest(root):
    result = hashlib.sha256()
    files = xattrs = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('unexpected_symlink')
        if not path.is_file():
            continue
        files += 1
        result.update(str(path.relative_to(root)).encode() + b'\x00')
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                result.update(chunk)
        for name in sorted(os.listxattr(path)):
            result.update(name.encode() + b'\x00' + os.getxattr(path, name))
            xattrs += 1
    return {'sha256': result.hexdigest(), 'files': files, 'extended_attributes': xattrs}


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '_worker':
        try:
            print(json.dumps(worker(Path(sys.argv[2]), sys.argv[3]), sort_keys=True))
            return 0
        except Exception as error:
            print(json.dumps({'status': 'unavailable', 'error_type': type(error).__name__}))
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-python', type=Path, required=True)
    parser.add_argument('--target-python', type=Path, required=True)
    parser.add_argument('--source-version', default='2.33.0')
    parser.add_argument('--target-version', default='2.37.4')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='poc-swift-format-recovery-', dir='/tmp'))
    report = {'schema': 1, 'scope': 'isolated_real_swift_backend_component',
              'network_path_verified': False, 'keystone_integration_verified': False,
              'full_live_swift_recovery_verified': False, 'live_services_changed': False,
              'stages': {}, 'verified': False}
    def invoke(interpreter, directory, action, expected):
        result = subprocess.run([str(interpreter), '-B', str(Path(__file__).resolve()), '_worker', str(directory), action],
                                env={**os.environ, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('isolated_worker_failed')
        value = json.loads(result.stdout)
        if value['swift_version'] != expected:
            raise RuntimeError('unexpected_runtime_version')
        return value
    try:
        source = root / 'source-copy'
        report['stages']['old_write'] = invoke(args.source_python, source, 'write_source', args.source_version)
        report['stages']['new_read_old'] = invoke(args.target_python, source, 'read_source', args.target_version)
        report['stages']['new_write'] = invoke(args.target_python, source, 'write_target', args.target_version)
        report['stages']['old_read_new'] = invoke(args.source_python, source, 'read_target', args.source_version)
        before = tree_digest(source)
        if before['extended_attributes'] == 0:
            raise RuntimeError('object_xattrs_missing')
        restored = root / 'restored-copy'
        shutil.copytree(source, restored, copy_function=shutil.copy2)
        if tree_digest(restored) != before:
            raise RuntimeError('restore_file_or_xattr_mismatch')
        report['stages']['new_read_restored'] = invoke(args.target_python, restored, 'read_target', args.target_version)
        if tree_digest(source) != before:
            raise RuntimeError('source_changed_during_restore')
        report.update(verified=True, restored_artifact=before, all_owned_child_processes_stopped=True)
    except Exception as error:
        report.update(error_type=type(error).__name__, all_owned_child_processes_stopped=True)
    path = root / 'report.json'
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'PASS' if report['verified'] else 'unavailable', 'scope': report['scope'],
                      'report': str(path), 'live_services_changed': False,
                      'full_live_swift_recovery_verified': False}, sort_keys=True))
    return 0 if report['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
