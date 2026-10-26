import importlib.util
from datetime import date
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


audit = module('audit_dependency_advisories')
constraints = module('render_security_constraints')


class DependencyAdvisoryTests(unittest.TestCase):
    def test_backport_disposition_is_exact_and_expires(self):
        document = json.loads((ROOT / 'deployment/constraints/advisory-dispositions.json').read_text())
        findings = [{'package': 'swift', 'version': version, 'advisory_id': identifier}
                    for version, identifier in (('2.35.5', 'GHSA-h8rc-w822-q8vq'),
                                                ('2.35.4', 'GHSA-h8rc-w822-q8vq'),
                                                ('2.35.5', 'GHSA-unrelated'))]
        reviewed, unresolved = audit.dispositions_for(findings, document, date(2026, 10, 27))
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(len(unresolved), 2)
        with self.assertRaises(ValueError):
            audit.dispositions_for(findings, document, date(2026, 11, 27))

    def test_incomplete_or_paginated_advisories_never_report_clean(self):
        for response in ({}, {'results': []}, {'results': [{'error': 'unavailable'}]},
                         {'results': [{'next_page_token': 'more'}]}):
            with self.assertRaises(ValueError):
                audit.findings_for([('package', '1.0')], response)

    def test_matches_keep_exact_package_and_version(self):
        values = audit.findings_for([('package', '1.0')], {'results': [{'vulns': [{'id': 'GHSA-example'}]}]})
        self.assertEqual(values[0]['version'], '1.0')
        self.assertEqual(values[0]['advisory_id'], 'GHSA-example')
        self.assertEqual(audit.findings_for([('package', '1.0')], {'results': [{}]}), [])

    def test_lock_rejects_unpinned_dependencies(self):
        with self.assertRaises(ValueError):
            audit.packages('requests>=2\n')
        self.assertEqual(audit.packages('PyJWT==2.15.1 \\\n    --hash=sha256:example\n'), {('pyjwt', '2.15.1')})

    def test_overlay_replaces_all_marker_variants_without_changing_other_pins(self):
        base = 'requests===2.0; python_version>="3"\nrequests===1.0; python_version<"3"\nother===1.0\n'
        rendered = constraints.render(base, 'requests==2.34.2\n')
        self.assertEqual(rendered.count('requests=='), 1)
        self.assertIn('other===1.0', rendered)
        for overlay in ('absent==1.0\n', 'requests>=2\n', 'requests==2\nRequests==3\n'):
            with self.assertRaises(ValueError):
                constraints.render(base, overlay)
