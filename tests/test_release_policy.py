from datetime import date
import json
from pathlib import Path
import unittest

from openstack_cloud_poc.release_policy import final_acceptance_blockers, target_blockers

POLICY = json.loads((Path(__file__).resolve().parents[1] / 'deployment/release-policy.json').read_text())
TODAY = date.fromisoformat(POLICY['reviewed_on'])


class ReleasePolicyTests(unittest.TestCase):
    def target(self):
        return {'environment': 'local_poc', 'openstack_release': '2026.2',
                'host_os': {'id': 'ubuntu', 'version': '24.04'}, 'is_wsl': False,
                'memory_mib': 32768, 'kolla_major': 23, 'ansible_core_minor': '2.20',
                'preserve_caracal_source': True, 'target_change_authorized': True}

    def evidence(self):
        return {'environment': 'local_poc', 'openstack_release': '2026.2',
                'provenance': 'real', 'customer_workflow': {str(i): 'PASS' for i in range(1, 15)}}

    def test_caracal_final_result_is_rejected_even_if_every_step_says_pass(self):
        evidence = self.evidence()
        evidence['openstack_release'] = '2024.1'
        self.assertIn('final_acceptance_requires_selected_maintained_release',
                      final_acceptance_blockers(POLICY, evidence, today=TODAY))

    def test_both_final_environments_require_real_complete_workflow(self):
        for environment in ('local_poc', 'staging'):
            evidence = self.evidence()
            evidence['environment'] = environment
            self.assertEqual(final_acceptance_blockers(POLICY, evidence, today=TODAY), ())
            evidence['customer_workflow']['9'] = 'PENDING'
            self.assertIn('mandatory_customer_step_not_passed', final_acceptance_blockers(POLICY, evidence, today=TODAY))
            evidence['provenance'] = 'simulated'
            self.assertIn('real_provider_evidence_required', final_acceptance_blockers(POLICY, evidence, today=TODAY))

    def test_missing_failover_step_never_passes(self):
        evidence = self.evidence()
        del evidence['customer_workflow']['8']
        self.assertIn('all_fourteen_customer_steps_required', final_acceptance_blockers(POLICY, evidence, today=TODAY))

    def test_final_evidence_rejects_stale_maintenance_status(self):
        self.assertIn('maintenance_evidence_stale', final_acceptance_blockers(
            POLICY, self.evidence(), today=date(2026, 11, 10)))

    def test_supported_plan_fields_pass_without_implying_deployment(self):
        self.assertEqual(target_blockers(POLICY, self.target(), today=TODAY), ())

    def test_existing_wsl_target_fails_os_memory_and_release_guards(self):
        target = self.target()
        target.update(openstack_release='2024.1', host_os={'id': 'ubuntu', 'version': '22.04'},
                      is_wsl=True, memory_mib=7867)
        self.assertTrue({'unsupported_host_os', 'full_scope_capacity_not_established',
                         'release_does_not_match_selected_maintained_target'} <= set(target_blockers(POLICY, target, today=TODAY)))

    def test_stale_maintenance_review_must_be_refreshed(self):
        self.assertIn('maintenance_evidence_stale', target_blockers(POLICY, self.target(), today=date(2026, 11, 10)))

    def test_preservation_authorization_and_tool_versions_are_required(self):
        target = self.target()
        target.update(preserve_caracal_source=False, target_change_authorized=False, kolla_major=22)
        self.assertTrue({'legacy_preservation_not_confirmed', 'target_change_not_authorized',
                         'deployment_tool_release_mismatch'} <= set(target_blockers(POLICY, target, today=TODAY)))
