#!/usr/bin/env python3
"""Read-only OSV check of exact public PyPI versions in repository hash locks.

Sends package names/versions only, no credentials or private inventory. Unknown
responses fail closed. A clean published-advisory query is not a security audit.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ('requirements-test.lock', 'deployment/swift-maintained/requirements.lock',
            'deployment/swift-maintained/epoxy-requirements.lock',
            'deployment/kolla/requirements.lock')


def packages(content):
    found = set()
    for line in content.splitlines():
        if not line or line.startswith(('#', ' ', '\t')):
            continue
        match = re.match(r'^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:[ ;\\]|$)', line)
        if not match:
            raise ValueError('unsupported_lock_entry')
        name, version = match.groups()
        found.add((re.sub(r'[-_.]+', '-', name).lower(), version))
    if not found:
        raise ValueError('empty_lock')
    return found


def findings_for(values, response):
    rows = response.get('results')
    if not isinstance(rows, list) or len(rows) != len(values):
        raise ValueError('incomplete_advisory_response')
    findings = []
    for (name, version), row in zip(values, rows):
        if not isinstance(row, dict) or row.get('error') or row.get('next_page_token'):
            raise ValueError('advisory_query_incomplete')
        vulnerabilities = row.get('vulns', [])
        if not isinstance(vulnerabilities, list):
            raise ValueError('invalid_advisory_response')
        for item in vulnerabilities:
            identifier = item.get('id') if isinstance(item, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch('[A-Za-z0-9_.:-]{3,100}', identifier):
                raise ValueError('invalid_advisory_identity')
            findings.append({'package': name, 'version': version, 'advisory_id': identifier,
                             'url': 'https://osv.dev/vulnerability/' + identifier})
    return findings


def dispositions_for(findings, document, today):
    if document.get('schema') != 1 or not isinstance(document.get('dispositions'), list):
        raise ValueError('invalid_advisory_dispositions')
    reviewed = {}
    for row in document['dispositions']:
        if (row.get('reason') != 'upstream_fixed_backport' or not row.get('evidence')
                or not row.get('primary_source', '').startswith('https://security.openstack.org/ossa/')
                or not row.get('advisory_ids')):
            raise ValueError('invalid_backport_evidence')
        if not date.fromisoformat(row['reviewed_at']) <= today <= date.fromisoformat(row['expires_at']):
            raise ValueError('advisory_disposition_expired_or_future')
        for identifier in row['advisory_ids']:
            key = (row['package'], row['version'], identifier)
            if key in reviewed:
                raise ValueError('duplicate_advisory_disposition')
            reviewed[key] = row
    accepted, unresolved = [], []
    for item in findings:
        row = reviewed.get((item['package'], item['version'], item['advisory_id']))
        if row:
            accepted.append({**item, 'disposition': row})
        else:
            unresolved.append(item)
    return accepted, unresolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', action='append', type=Path)
    args = parser.parse_args()
    report = {'schema': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'public_package_metadata_only': True, 'query_complete': False,
              'full_security_validation_passed': False, 'locks': {}}
    try:
        values = set()
        for path in args.lock or [ROOT / name for name in DEFAULTS]:
            content = path.read_text()
            values |= packages(content)
            report['locks'][str(path.relative_to(ROOT))] = hashlib.sha256(content.encode()).hexdigest()
        values = sorted(values)
        payload = json.dumps({'queries': [{'package': {'name': name, 'ecosystem': 'PyPI'}, 'version': version}
                                          for name, version in values]}).encode()
        request = Request('https://api.osv.dev/v1/querybatch', data=payload,
                          headers={'Content-Type': 'application/json', 'User-Agent': 'OpenStack-POC-public-lock-audit'})
        with urlopen(request, timeout=45) as response:
            data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('advisory_response_too_large')
        findings = findings_for(values, json.loads(data))
        reviewed, unresolved = dispositions_for(findings, json.loads((ROOT / 'deployment/constraints/advisory-dispositions.json').read_text()), date.today())
        report.update(query_complete=True, package_version_count=len(values), findings=findings,
                      reviewed_matches=reviewed, unresolved_findings=unresolved,
                      status=('published_matches_found' if unresolved else
                              'published_matches_reviewed' if findings else 'no_published_matches'))
    except Exception as error:
        report.update(status='unavailable', error_type=type(error).__name__)
    fd, name = tempfile.mkstemp(prefix='poc-dependency-advisories-', suffix='.json')
    import os
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, indent=2, sort_keys=True); stream.write('\n')
    print(json.dumps({'status': report['status'], 'query_complete': report['query_complete'],
                      'finding_count': len(report.get('findings', [])), 'report': name,
                      'unresolved_finding_count': len(report.get('unresolved_findings', [])),
                      'full_security_validation_passed': False}, sort_keys=True))
    return 0 if report['query_complete'] and not report['unresolved_findings'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
