#!/usr/bin/env python3
"""Inspect official UCA metadata without configuring APT or installing software.

Downloads public metadata into a new /tmp directory, verifies InRelease with
the installed UCA keyring, verifies the Packages.gz digest, and reports selected
package versions/dependencies. Repository contents do not certify an upgrade.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

BASE = 'https://ubuntu-cloud.archive.canonical.com/ubuntu/dists/'
SUITES = ('jammy-updates/gazpacho', 'jammy-updates/epoxy', 'noble-updates/epoxy',
          'noble-updates/flamingo', 'noble-updates/gazpacho', 'resolute-updates/hibiscus')
PACKAGES = {'nova-common', 'python3-nova', 'neutron-common', 'python3-neutron',
            'keystone', 'python3-keystone', 'glance-common', 'placement-api',
            'cinder-common', 'swift', 'python3-swift', 'swift-proxy', 'openstack-dashboard', 'octavia-api',
            'python3-ovn-octavia-provider', 'ovn-common', 'openvswitch-common'}
KEYRING = Path('/usr/share/keyrings/ubuntu-cloud-keyring.gpg')


def package_digest(release):
    in_sha256 = False
    for line in release.splitlines():
        if line == 'SHA256:':
            in_sha256 = True
            continue
        if in_sha256 and line and not line[0].isspace():
            break
        fields = line.split()
        if in_sha256 and len(fields) == 3 and fields[2] == 'main/binary-amd64/Packages.gz':
            return fields[0], int(fields[1])
    raise ValueError('signed metadata has no amd64 package index')


def release_identity(release):
    fields = {}
    for line in release.splitlines():
        if line.startswith(('Origin:', 'Suite:', 'Codename:', 'Date:', 'Description:')):
            key, value = line.split(':', 1)
            fields[key] = value.strip()
    return fields


def selected_packages(text):
    result = {}
    for paragraph in text.split('\n\n'):
        record = {}
        key = None
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')) and key:
                record[key] += ' ' + line.strip()
            elif ': ' in line:
                key, value = line.split(': ', 1)
                record[key] = value
        name = record.get('Package')
        if name in PACKAGES:
            result[name] = {key: record.get(key) for key in ('Version', 'Architecture', 'Depends', 'Pre-Depends', 'Filename', 'SHA256', 'Size')}
    return result


def download(url):
    with urlopen(url, timeout=20) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('metadata size limit exceeded')
    return data


def inspect(suite, directory):
    result = {'suite': suite, 'package_installation': False}
    try:
        directory.mkdir(mode=0o700)
        signed = download(BASE + suite + '/InRelease')
        release_path = directory / 'InRelease'
        release_path.write_bytes(signed)
        home = directory / 'gpg-home'
        home.mkdir(mode=0o700)
        check = subprocess.run(['gpgv', '--homedir', str(home), '--keyring', str(KEYRING), str(release_path)],
                               capture_output=True, text=True, timeout=20)
        result['signature_verified_with_installed_uca_keyring'] = check.returncode == 0
        if check.returncode:
            result['status'] = 'signature_unverified'
            return result
        release = signed.decode()
        identity = release_identity(release)
        result['signed_release_identity'] = identity
        if identity.get('Codename') != suite:
            result['status'] = 'release_identity_mismatch'
            return result
        expected, size = package_digest(release)
        compressed = download(BASE + suite + '/main/binary-amd64/Packages.gz')
        if len(compressed) != size or hashlib.sha256(compressed).hexdigest() != expected:
            raise ValueError('package index digest or size mismatch')
        (directory / 'Packages.gz').write_bytes(compressed)
        packages = selected_packages(gzip.decompress(compressed).decode())
        result.update(status='metadata_verified', packages_digest_verified=True,
                      selected_packages=packages, selected_packages_absent=sorted(PACKAGES - packages.keys()),
                      package_index_size_bytes=size)
    except HTTPError as error:
        result.update(status='http_unavailable', http_status=error.code)
    except (OSError, URLError, ValueError, subprocess.SubprocessError):
        result.update(status='inspection_unavailable')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', action='append', choices=SUITES,
                        help='default: inspect all candidate suites')
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix='poc-uca-upgrade-audit-'))
    report = {'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'apt_configuration_changed': False, 'installation_authorized': False,
              'output_directory': str(directory), 'repositories': []}
    for suite in args.suite or SUITES:
        item = inspect(suite, directory / suite.replace('/', '-'))
        report['repositories'].append(item)
        print(json.dumps({'suite': suite, 'status': item['status'],
                          'versions': {name: value['Version'] for name, value in item.get('selected_packages', {}).items()}}, sort_keys=True), flush=True)
    path = directory / 'report.json'
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)
    print('Public repository metadata report=' + str(path), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
