#!/usr/bin/env python3
"""Verify the proposed Swift source signature and wheel's Python module bytes.

Public downloads/new /tmp files only. Does not install or deploy Swift.
A code-byte comparison is not a full wheel build/provenance attestation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile

VERSION = '2.37.4'
TRUSTED = ('30566C450E41D7C91E442DFB231F942F608DDEFF', '7F0F63951535B4D12D3FA55DECE58D3984D688D7')


def fetch(url):
    with urlopen(url, timeout=30) as response:
        value = response.read(16 * 1024 * 1024 + 1)
    if len(value) > 16 * 1024 * 1024:
        raise RuntimeError('artifact_size_limit')
    return value


def run(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('signature_verification_unavailable')
    return result.stdout


def module_map(source, wheel, version=VERSION):
    with tarfile.open(fileobj=io.BytesIO(source), mode='r:gz') as archive:
        original = {member.name.split('/', 1)[1]: archive.extractfile(member).read()
                    for member in archive.getmembers() if member.isfile()
                    and member.name.startswith('swift-' + version + '/swift/') and member.name.endswith('.py')}
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        packaged = {name: archive.read(name) for name in archive.namelist()
                    if name.startswith('swift/') and name.endswith('.py')}
    if not original or original != packaged:
        raise RuntimeError('wheel_python_modules_differ_from_signed_source')
    return len(original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('2.35.5', '2.37.4'), default=VERSION)
    version = parser.parse_args().version
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='poc-swift-source-verification-'))
    home = root / 'gpg-home'; home.mkdir(mode=0o700)
    keyring = root / 'release-keys.gpg'
    report = {'schema': 1, 'version': version, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'installation_performed': False, 'full_wheel_build_attestation_verified': False, 'verified': False}
    try:
        for fingerprint in TRUSTED:
            key = root / (fingerprint + '.asc')
            text = fetch('https://releases.openstack.org/_static/0x' + fingerprint.lower() + '.txt').decode()
            armored = text[text.index('-----BEGIN PGP PUBLIC KEY BLOCK-----'):]
            key.write_text(armored)
            listing = run(['gpg', '--batch', '--no-autostart', '--homedir', str(home), '--with-colons', '--import-options', 'show-only', '--import', str(key)])
            actual = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
            if actual != fingerprint:
                raise RuntimeError('unexpected_published_release_key')
            run(['gpg', '--batch', '--no-autostart', '--homedir', str(home), '--no-default-keyring', '--keyring', str(keyring), '--import', str(key)])
        name = 'swift-' + version + '.tar.gz'
        source = fetch('https://tarballs.openstack.org/swift/' + name)
        (root / name).write_bytes(source)
        signature = root / (name + '.asc')
        signature.write_bytes(fetch('https://tarballs.openstack.org/swift/' + name + '.asc'))
        status = run(['gpgv', '--status-fd=1', '--homedir', str(home), '--keyring', str(keyring), str(signature), str(root / name)])
        if any(value in status for value in ('REVKEYSIG', 'EXPKEYSIG', 'BADSIG', 'EXPSIG')):
            raise RuntimeError('release_signature_not_currently_acceptable')
        signers = [line.split()[-1] for line in status.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
        if len(signers) != 1 or signers[0] not in TRUSTED:
            raise RuntimeError('unrecognized_release_signer')
        metadata = json.loads(fetch('https://pypi.org/pypi/swift/' + version + '/json'))
        wheel_record = next(item for item in metadata['urls'] if item['filename'].endswith('.whl'))
        wheel = fetch(wheel_record['url'])
        wheel_hash = hashlib.sha256(wheel).hexdigest()
        if wheel_hash != wheel_record['digests']['sha256']:
            raise RuntimeError('wheel_digest_mismatch')
        count = module_map(source, wheel, version)
        report.update(verified=True, signing_primary_fingerprint=signers[0],
                      source_sha256=hashlib.sha256(source).hexdigest(), wheel_sha256=wheel_hash,
                      wheel_filename=wheel_record['filename'], python_module_bytes_match=True,
                      python_module_count=count)
    except Exception as error:
        report['error_type'] = type(error).__name__
    path = root / 'report.json'; path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'PASS' if report['verified'] else 'unavailable', 'report': str(path),
                      'scope': 'source_signature_and_wheel_python_modules', 'installation_performed': False}))
    return 0 if report['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
