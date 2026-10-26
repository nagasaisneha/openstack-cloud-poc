"""Offline compatibility guard for proposed maintained-release targets.

Passing this guard validates submitted plan fields only. It does not attest
actual hardware, deploy services, or count as live POC/staging acceptance.
"""

from datetime import date
from typing import Mapping


def target_blockers(policy: Mapping, target: Mapping, *, today: date) -> tuple[str, ...]:
    blockers = []
    try:
        reviewed = date.fromisoformat(policy["reviewed_on"])
        age = (today - reviewed).days
        if age < 0 or age > policy["maintenance_review_max_age_days"]:
            blockers.append("maintenance_evidence_stale")
        selected = policy["selected_release"]
        candidate = policy["candidates"][selected]
        if candidate["upstream_status_at_review"] != "maintained":
            blockers.append("selected_release_not_maintained")
        if target.get("environment") not in policy["required_environments"]:
            blockers.append("unknown_environment")
        if target.get("openstack_release") != selected:
            blockers.append("release_does_not_match_selected_maintained_target")
        if target.get("host_os") != candidate["selected_host_os"]:
            blockers.append("unsupported_host_os")
        if target.get("is_wsl") is not False:
            blockers.append("supported_non_wsl_target_not_established")
        memory = target.get("memory_mib")
        if type(memory) is not int or memory < policy["minimum_full_scope_memory_mib"]:
            blockers.append("full_scope_capacity_not_established")
        if target.get("kolla_major") != candidate["kolla_major"]:
            blockers.append("deployment_tool_release_mismatch")
        if target.get("ansible_core_minor") not in candidate["ansible_core_minors"]:
            blockers.append("ansible_release_mismatch")
        if target.get("preserve_caracal_source") is not True:
            blockers.append("legacy_preservation_not_confirmed")
        if target.get("target_change_authorized") is not True:
            blockers.append("target_change_not_authorized")
    except (KeyError, TypeError, ValueError):
        blockers.append("invalid_policy_or_target")
    return tuple(blockers)


def final_acceptance_blockers(policy: Mapping, evidence: Mapping, *, today: date) -> tuple[str, ...]:
    """Reject wrong-release, simulated, or incomplete final customer evidence.

    Report authenticity and artifact checks are the runner/operator's job.
    This guard ensures a Caracal or fixture report cannot pass the release gate.
    """
    blockers = []
    try:
        age = (today - date.fromisoformat(policy["reviewed_on"])).days
        if age < 0 or age > policy["maintenance_review_max_age_days"]:
            blockers.append("maintenance_evidence_stale")
        candidate = policy["candidates"][policy["selected_release"]]
        if candidate["upstream_status_at_review"] != "maintained":
            blockers.append("selected_release_not_maintained")
    except (KeyError, TypeError, ValueError):
        blockers.append("invalid_release_policy")
    if evidence.get("openstack_release") != policy.get("selected_release"):
        blockers.append("final_acceptance_requires_selected_maintained_release")
    if evidence.get("environment") not in policy.get("required_environments", ()):
        blockers.append("unknown_environment")
    if evidence.get("provenance") != "real":
        blockers.append("real_provider_evidence_required")
    checks = evidence.get("customer_workflow")
    expected = {str(number) for number in range(1, 15)}
    if not isinstance(checks, dict) or set(checks) != expected:
        blockers.append("all_fourteen_customer_steps_required")
    elif any(value != "PASS" for value in checks.values()):
        blockers.append("mandatory_customer_step_not_passed")
    return tuple(blockers)
