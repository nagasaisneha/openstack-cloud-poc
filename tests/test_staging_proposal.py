import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('staging_proposal', Path(__file__).resolve().parents[1] / 'scripts/prepare_staging_proposal.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class StagingProposalTests(unittest.TestCase):
    def test_unknown_target_owners_and_money_never_authorize_or_claim_deployment(self):
        result = module.proposal({})
        self.assertFalse(result['decisions_complete'])
        self.assertFalse(result['staging_ready'])
        self.assertEqual(result['external_commands_executed'], [])
        self.assertIn('infrastructure_selection', result['missing_decisions_or_target_allocations'])
        self.assertIn('owner:release_budget', result['missing_decisions_or_target_allocations'])

    def test_documentation_addresses_wsl_overlap_and_invalid_money_fail_closed(self):
        result = module.proposal({'nodes': [{'name': 'example', 'management_address': '192.0.2.1', 'is_wsl': True}],
            'budget': {'monthly_cap': '-1', 'setup_cap': 'NaN'},
            'management_cidr': '10.0.0.0/24', 'overlay_cidr': '10.0.0.0/25'})
        self.assertIn('actual_host_address_required:example', result['invalid_inputs'])
        self.assertIn('non_wsl_host_not_established:example', result['invalid_inputs'])
        self.assertIn('overlapping_network:overlay_cidr', result['invalid_inputs'])
        self.assertIn('invalid_budget:monthly_cap', result['invalid_inputs'])
