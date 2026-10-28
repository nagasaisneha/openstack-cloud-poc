#!/usr/bin/python3
"""Validate a staging decision record and emit preparation steps; never deploys."""
import argparse
from decimal import Decimal, InvalidOperation
import ipaddress
import json
from pathlib import Path

ROLE_COUNTS = {'controller': 3, 'compute': 2, 'storage': 3}
OWNERS = ('release', 'budget_approval', 'platform', 'network_storage', 'security', 'operations', 'billing_acceptance')


def proposal(record):
    missing, invalid = [], []
    target = record.get('target_kind')
    if target not in ('organization_baremetal', 'rented_baremetal', 'existing_openstack', 'existing_local_hardware_only'):
        missing.append('infrastructure_selection')
    if not record.get('region_and_data_location'):
        missing.append('region_and_data_location')
    for role in OWNERS:
        if not record.get('owners', {}).get(role):
            missing.append('owner:' + role)
    budget = record.get('budget', {})
    zero_owned_use = (target in ('organization_baremetal', 'existing_local_hardware_only')
        and budget.get('zero_cost_use_authorized') is True
        and str(budget.get('monthly_cap')) == '0' and str(budget.get('setup_cap')) == '0')
    if not budget.get('currency') and not zero_owned_use:
        missing.append('budget_currency')
    for field in ('monthly_cap', 'setup_cap'):
        if budget.get(field) is None:
            missing.append('budget:' + field)
        else:
            try:
                value = Decimal(str(budget[field]))
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
            except (ValueError, InvalidOperation):
                invalid.append('invalid_budget:' + field)
    if budget.get('spending_approved') is not True and not zero_owned_use:
        missing.append('explicit_spending_or_zero_cost_approval')
    if target == 'existing_local_hardware_only':
        invalid.append('current_16_GiB_WSL_source_cannot_host_supported_separate_full_scope_staging')
    nodes = record.get('nodes', [])
    if target in ('organization_baremetal', 'rented_baremetal'):
        for role, count in ROLE_COUNTS.items():
            if sum(role in node.get('roles', []) for node in nodes) < count:
                missing.append('host_inventory:' + role)
    networks = []
    for field in ('management_cidr', 'overlay_cidr', 'storage_cidr', 'provider_cidr'):
        if not record.get(field):
            missing.append(field)
        else:
            try:
                network = ipaddress.ip_network(record[field], strict=True)
                if any(network.overlaps(other) for other in networks):
                    invalid.append('overlapping_network:' + field)
                networks.append(network)
            except ValueError:
                invalid.append('invalid_network:' + field)
    for node in nodes:
        if node.get('is_wsl') is not False:
            invalid.append('non_wsl_host_not_established:' + node.get('name', 'unnamed'))
        try:
            address = ipaddress.ip_address(node['management_address'])
            documentation = any(address in ipaddress.ip_network(cidr) for cidr in
                                ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'))
            if address.is_loopback or address.is_unspecified or documentation:
                raise ValueError
        except (KeyError, ValueError):
            invalid.append('actual_host_address_required:' + node.get('name', 'unnamed'))
    for field in ('internal_api_vip', 'external_api_vip', 'floating_ip_pool', 'endpoint_dns',
                  'secret_store_reference', 'certificate_issuer', 'approved_change_id'):
        if not record.get(field):
            missing.append(field)
    return {'schema': 1, 'decisions_complete': not missing and not invalid,
        'missing_decisions_or_target_allocations': sorted(missing), 'invalid_inputs': sorted(invalid),
        'deployment_verified': False, 'staging_ready': False,
        'proposed_baseline': {'controllers': 3, 'computes': 2, 'storage_hosts': 3,
            'physical_cores': 80, 'host_memory_gib': 416, 'independent_deployment_monitor_gib': 16},
        'next_preparation': [
            'materialize protected inventory from approved actual addresses',
            'discover actual OS/KVM/IOMMU/NIC/disk ownership without writes',
            'verify signed release, scan images/SBOMs and pin selected registry digests',
            'prepare protected secrets/rings/TLS for selected DNS and devices',
            'run repository preflight and pinned Kolla prechecks',
            'deploy approved target and run real dedicated-project release acceptance'],
        'external_commands_executed': [], 'wsl_restore_required': False, 'local_os_upgrade_required': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = proposal(json.loads(args.request.read_text()))
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
    raise SystemExit(0 if result['decisions_complete'] else 2)
