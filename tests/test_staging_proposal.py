import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('staging_proposal', Path(__file__).resolve().parents[1] / 'scripts/prepare_staging_proposal.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class StagingProposalTests(unittest.TestCase):
    def test_resolved_local_owners_and_zero_spend_do_not_erase_actual_hardware_blocker(self):
        path = Path(__file__).resolve().parents[1] / 'deployment/staging-local-hardware-decision.json'
        result = module.proposal(json.loads(path.read_text()))
        self.assertFalse(result['decisions_complete'])
        self.assertFalse(result['staging_ready'])
        self.assertNotIn('infrastructure_selection', result['missing_decisions_or_target_allocations'])
        self.assertFalse(any(item.startswith('owner:') for item in result['missing_decisions_or_target_allocations']))
        self.assertNotIn('budget_currency', result['missing_decisions_or_target_allocations'])
        self.assertIn('current_16_GiB_WSL_source_cannot_host_supported_separate_full_scope_staging', result['invalid_inputs'])
    def test_unknown_target_owners_and_money_never_authorize_or_claim_deployment(self):
        result = module.proposal({})
        self.assertFalse(result['decisions_complete'])
        self.assertFalse(result['staging_ready'])
        self.assertEqual(result['external_commands_executed'], [])
        self.assertIn('infrastructure_selection', result['missing_decisions_or_target_allocations'])
        self.assertIn('owner:release', result['missing_decisions_or_target_allocations'])

    def test_documentation_addresses_wsl_overlap_and_invalid_money_fail_closed(self):
        result = module.proposal({'nodes': [{'name': 'example', 'management_address': '192.0.2.1', 'is_wsl': True}],
            'budget': {'monthly_cap': '-1', 'setup_cap': 'NaN'},
            'management_cidr': '10.0.0.0/24', 'overlay_cidr': '10.0.0.0/25'})
        self.assertIn('actual_host_address_required:example', result['invalid_inputs'])
        self.assertIn('non_wsl_host_not_established:example', result['invalid_inputs'])
        self.assertIn('overlapping_network:overlay_cidr', result['invalid_inputs'])
        self.assertIn('invalid_budget:monthly_cap', result['invalid_inputs'])
