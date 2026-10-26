#!/usr/bin/env python3
"""Verify public Team RabbitMQ repositories in /tmp; no APT/host changes.

Authenticates the documented signing-key fingerprint and signed Jammy package
indexes. Identifies all Erlang intermediate packages and RabbitMQ bridge builds.
Metadata/artifact verification is not a broker data migration acceptance test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.request import Request, urlopen

FINGERPRINT = '0A9AF2115F4687BD29803A206B73A36E6026DFCA'
ERLANG = ('erlang-base', 'erlang-asn1', 'erlang-crypto', 'erlang-eldap', 'erlang-ftp',
          'erlang-inets', 'erlang-mnesia', 'erlang-os-mon', 'erlang-parsetools',
          'erlang-public-key', 'erlang-runtime-tools', 'erlang-snmp', 'erlang-ssl',
          'erlang-syntax-tools', 'erlang-tftp', 'erlang-tools', 'erlang-xmerl')


def fetch(url, limit=64 * 1024 * 1024):
    with urlopen(Request(url, headers={'User-Agent': 'OpenStack-POC-readonly-upgrade-audit'}), timeout=30) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError('artifact_size_limit')
    return content


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('verification_command_failed')
    return result.stdout


def verify_signed(path, keyring, home, artifact=None):
    arguments = ['gpgv', '--status-fd=1', '--homedir', str(home), '--keyring', str(keyring), str(path)]
    if artifact is not None:
        arguments.append(str(artifact))
    status = command(arguments)
    signers = [line.split()[-1] for line in status.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    if (signers != [FINGERPRINT]
            or any(value in status for value in ('REVKEYSIG', 'EXPKEYSIG', 'BADSIG', 'EXPSIG'))):
        raise RuntimeError('release_signature_not_currently_acceptable')


def records(content):
    for paragraph in content.split('\n\n'):
        fields, last = {}, None
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')) and last:
                fields[last] += ' ' + line.strip()
            elif ': ' in line:
                last, value = line.split(': ', 1)
                fields[last] = value
        if 'Package' in fields:
            yield fields


def index_identity(release):
    fields = {}
    for line in release.splitlines():
        if line.startswith(('Origin:', 'Suite:', 'Codename:', 'Date:')):
            key, value = line.split(':', 1)
            fields[key] = value.strip()
    if fields.get('Origin') != 'RabbitMQ' or fields.get('Codename') != 'jammy':
        raise RuntimeError('unexpected_signed_repository_identity')
    return fields


def index_digest(release):
    in_sha = False
    for line in release.splitlines():
        if line == 'SHA256:':
            in_sha = True
            continue
        if in_sha and line and not line[0].isspace():
            break
        parts = line.split()
        if in_sha and len(parts) == 3 and parts[2] == 'main/binary-amd64/Packages.gz':
            return parts[0], int(parts[1])
    raise RuntimeError('signed_package_index_missing')


def inspect_repository(repo, directory, keyring, home):
    base = 'https://deb1.rabbitmq.com/' + repo + '/ubuntu/jammy/'
    directory.mkdir(mode=0o700)
    release = fetch(base + 'dists/jammy/InRelease')
    signed = directory / 'InRelease'
    signed.write_bytes(release)
    verify_signed(signed, keyring, home)
    identity = index_identity(release.decode())
    expected, size = index_digest(release.decode())
    content = fetch(base + 'dists/jammy/main/binary-amd64/Packages.gz')
    if len(content) != size or hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError('package_index_digest_mismatch')
    (directory / 'Packages.gz').write_bytes(content)
    selected = []
    for row in records(gzip.decompress(content).decode()):
        name, version = row['Package'], row.get('Version', '')
        if ((name in ERLANG and re.match(r'1:(24\.3\.4\.17|25\.3\.2\.[0-9]+)-', version))
                or (name == 'rabbitmq-server' and version in ('3.10.25-1', '3.11.28-1'))):
            selected.append({key: row.get(key) for key in ('Package', 'Version', 'Architecture', 'Depends', 'Filename', 'SHA256', 'Size')})
    return {'status': 'metadata_verified', 'identity': identity, 'signature_verified': True,
            'packages_sha256': expected, 'repository_base': base, 'selected_packages': selected}


def download_intermediates(report, root):
    packages = []
    for name, item in report['repositories'].items():
        if item['status'] != 'metadata_verified':
            raise RuntimeError('unverified_repository')
        for row in item['selected_packages']:
            if row['Version'] in ('1:24.3.4.17-1', '1:25.3.2.21-1', '3.10.25-1', '3.11.28-1'):
                packages.append((item['repository_base'], row))
    for version in ('1:24.3.4.17-1', '1:25.3.2.21-1'):
        if {row['Package'] for base, row in packages if row['Version'] == version} != set(ERLANG):
            raise RuntimeError('incomplete_erlang_intermediate')
    if {row['Version'] for base, row in packages if row['Package'] == 'rabbitmq-server'} != {'3.10.25-1', '3.11.28-1'}:
        raise RuntimeError('incomplete_broker_intermediate')
    directory = root / 'verified-debs'; directory.mkdir(mode=0o700)
    def download(item):
        base, row = item
        relative = PurePosixPath(row['Filename'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'pool':
            raise RuntimeError('invalid_signed_artifact_path')
        content = fetch(base + str(relative))
        if len(content) != int(row['Size']) or hashlib.sha256(content).hexdigest() != row['SHA256']:
            raise RuntimeError('signed_artifact_digest_mismatch')
        path = directory / relative.name
        with path.open('xb') as stream:
            stream.write(content)
        actual = command(['dpkg-deb', '-f', str(path), 'Version']).strip()
        if actual != row['Version']:
            raise RuntimeError('artifact_control_identity_mismatch')
        return {**row, 'local_path': str(path), 'artifact_digest_verified': True}
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(download, packages))


def simulate_dependencies(report, root):
    selected = report['downloaded_packages']
    scenarios = (
        ('erlang24_rabbitmq3_9', '1:24.3.4.17-1', root / report['rabbitmq_3_9_29']['filename']),
        ('erlang24_rabbitmq3_10', '1:24.3.4.17-1', next(Path(r['local_path']) for r in selected if r['Version'] == '3.10.25-1')),
        ('erlang25_rabbitmq3_11', '1:25.3.2.21-1', next(Path(r['local_path']) for r in selected if r['Version'] == '3.11.28-1')),
    )
    results = {}
    for label, version, broker in scenarios:
        paths = [row['local_path'] for row in selected if row['Version'] == version] + [str(broker)]
        result = subprocess.run(['apt-get', '--simulate', '--no-remove', '-o', 'Debug::NoLocking=1',
                                 '-o', 'Dir::Cache=' + str(root / 'apt-cache'), 'install', *paths],
                                capture_output=True, text=True, timeout=60)
        (root / (label + '.simulation.log')).write_text(result.stdout + result.stderr)
        results[label] = {'exit_code': result.returncode, 'simulation_only': True,
                          'no_remove_required': True,
                          'packages_planned': sum(line.startswith('Inst ') for line in result.stdout.splitlines())}
    return results


def rehearse_erlang_runtimes(report, root):
    """Extract verified packages and run private, non-distributed Erlang VMs.

    dpkg-deb extraction never runs maintainer scripts. No broker is started.
    """
    results = {}
    for family, version in (('24', '1:24.3.4.17-1'), ('25', '1:25.3.2.21-1')):
        directory = root / ('runtime-' + family)
        directory.mkdir(mode=0o700)
        for row in report['downloaded_packages']:
            if row['Version'] == version:
                command(['dpkg-deb', '--extract', row['local_path'], str(directory)])
        runtime = directory / 'usr/lib/erlang'
        bindir = next(runtime.glob('erts-*/bin'))
        env = {**os.environ, 'ROOTDIR': str(runtime), 'BINDIR': str(bindir), 'EMU': 'beam',
               'PROGNAME': 'erl', 'ERL_CRASH_DUMP': str(directory / 'private-error.dump')}
        # erlexec reads ROOTDIR itself; adding another -root argument is invalid.
        result = subprocess.run([str(bindir / 'erlexec'), '+S', '1:1', '+A', '1', '-noshell', '-eval',
            'io:format("~s~n",[erlang:system_info(otp_release)]), application:ensure_all_started(crypto), '
            'io:format("~p~n",[crypto:hash(sha256, <<"isolated-runtime-check">>)]), halt().'],
            env=env, cwd=directory, capture_output=True, text=True, timeout=30)
        results[family] = {'status': 'PASS' if result.returncode == 0 and result.stdout.startswith(family + '\n') else 'unavailable',
                           'package_version': version, 'broker_started': False, 'distributed_node_started': False,
                           'crypto_hash_checked': result.returncode == 0}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-intermediates', action='store_true')
    parser.add_argument('--simulate-dependencies', action='store_true')
    parser.add_argument('--rehearse-runtimes', action='store_true')
    args = parser.parse_args()
    if (args.simulate_dependencies or args.rehearse_runtimes) and not args.download_intermediates:
        parser.error('simulation/runtime checks require --download-intermediates')
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='poc-rabbitmq-upgrade-audit-'))
    home = root / 'gpg-home'; home.mkdir(mode=0o700)
    report = {'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'installation_performed': False, 'apt_configuration_changed': False,
              'broker_data_migration_verified': False, 'repositories': {}}
    try:
        key = root / 'release-key.asc'
        key.write_bytes(fetch('https://keys.openpgp.org/vks/v1/by-fingerprint/' + FINGERPRINT))
        listing = command(['gpg', '--batch', '--no-autostart', '--homedir', str(home), '--with-colons', '--import-options', 'show-only', '--import', str(key)])
        fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        if fingerprint != FINGERPRINT:
            raise RuntimeError('unexpected_signing_key')
        keyring = root / 'release-key.gpg'
        command(['gpg', '--batch', '--no-autostart', '--homedir', str(home), '--no-default-keyring', '--keyring', str(keyring), '--import', str(key)])
        report['signing_key_fingerprint'] = fingerprint
        for repo in ('rabbitmq-erlang', 'rabbitmq-server'):
            try:
                report['repositories'][repo] = inspect_repository(repo, root / repo, keyring, home)
            except Exception as error:
                report['repositories'][repo] = {'status': 'unavailable', 'error_type': type(error).__name__}
        # 3.9.29 predates the new APT repo: verify its detached official signature.
        name = 'rabbitmq-server_3.9.29-1_all.deb'
        base = 'https://github.com/rabbitmq/rabbitmq-server/releases/download/v3.9.29/'
        artifact = root / name; signature = root / (name + '.asc')
        artifact.write_bytes(fetch(base + name))
        signature.write_bytes(fetch(base + name + '.asc'))
        verify_signed(signature, keyring, home, artifact)
        report['rabbitmq_3_9_29'] = {'signature_verified': True, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                     'filename': name, 'control': command(['dpkg-deb', '-f', str(artifact), 'Package', 'Version', 'Depends'])}
        if args.download_intermediates:
            report['downloaded_packages'] = download_intermediates(report, root)
        if args.simulate_dependencies:
            report['dependency_simulations'] = simulate_dependencies(report, root)
        if args.rehearse_runtimes:
            report['runtime_rehearsals'] = rehearse_erlang_runtimes(report, root)
    except Exception as error:
        report['error_type'] = type(error).__name__
    path = root / 'report.json'; path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'scope': 'public_artifact_metadata_only', 'report': str(path),
                      'repositories': {name: {'status': item['status'], 'selected_packages': len(item.get('selected_packages', []))}
                                       for name, item in report['repositories'].items()},
                      'rabbitmq_3_9_29_verified': report.get('rabbitmq_3_9_29', {}).get('signature_verified', False),
                      'verified_download_count': len(report.get('downloaded_packages', [])),
                      'dependency_simulations': report.get('dependency_simulations'),
                      'runtime_rehearsals': report.get('runtime_rehearsals'),
                      'installation_performed': False}, sort_keys=True))
    passed = ('error_type' not in report
              and set(report['repositories']) == {'rabbitmq-erlang', 'rabbitmq-server'}
              and all(r['status'] == 'metadata_verified' for r in report['repositories'].values())
              and all(r['exit_code'] == 0 for r in report.get('dependency_simulations', {}).values())
              and all(r['status'] == 'PASS' for r in report.get('runtime_rehearsals', {}).values()))
    return 0 if passed else 2


if __name__ == '__main__':
    raise SystemExit(main())
