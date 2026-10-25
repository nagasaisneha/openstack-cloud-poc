"""Offline tests for the fixture-only network policy model."""

import socket
import unittest
from unittest.mock import patch

from openstack_cloud_poc.adapter import FixtureManagementAdapter
from openstack_cloud_poc.identity import AuthenticationResult, Identity
from openstack_cloud_poc.network_policy import (
    Direction,
    NetworkPolicyError,
    NetworkRecord,
    NetworkTopologyFixture,
    PolicyDecision,
    Protocol,
    RouterInterfaceRecord,
    RouterRecord,
    SecurityGroupRecord,
    SecurityGroupRule,
    SubnetRecord,
    evaluate_security_group_flow,
    security_group_rule_from_mapping,
    validate_topology,
)


SOURCE = "fixture:T15 synthetic test data"
PROJECT = "fixture:project-alpha"
OTHER_PROJECT = "fixture:project-beta"


def rule(
    rule_id="fixture:rule.allow-ssh",
    *,
    direction=Direction.INGRESS,
    protocol=Protocol.TCP,
    port_min=22,
    port_max=22,
    peer_cidr="192.0.2.0/24",
    project_id=PROJECT,
):
    return SecurityGroupRule(
        rule_id=rule_id,
        project_id=project_id,
        direction=direction,
        protocol=protocol,
        port_min=port_min,
        port_max=port_max,
        peer_cidr=peer_cidr,
        source=SOURCE,
        status="simulated",
    )


def topology(
    *,
    project_id=PROJECT,
    networks=None,
    subnets=None,
    routers=None,
    router_interfaces=None,
    security_groups=None,
):
    return NetworkTopologyFixture(
        project_id=project_id,
        networks=(
            NetworkRecord("fixture:network.private", project_id, SOURCE, "simulated"),
        ) if networks is None else networks,
        subnets=(
            SubnetRecord(
                "fixture:subnet.private",
                project_id,
                "fixture:network.private",
                "192.0.2.0/24",
                SOURCE,
                "simulated",
            ),
        ) if subnets is None else subnets,
        routers=(
            RouterRecord("fixture:router.demo", project_id, SOURCE, "simulated"),
        ) if routers is None else routers,
        router_interfaces=(
            RouterInterfaceRecord(
                "fixture:interface.demo",
                project_id,
                "fixture:router.demo",
                "fixture:subnet.private",
                SOURCE,
                "simulated",
            ),
        ) if router_interfaces is None else router_interfaces,
        security_groups=(
            SecurityGroupRecord("fixture:sg.demo", project_id, (rule(project_id=project_id),), SOURCE, "simulated"),
        ) if security_groups is None else security_groups,
        source=SOURCE,
        status="simulated",
    )


def evaluate(
    *,
    topology_fixture=None,
    identity="fixture:alice",
    project_id=PROJECT,
    security_group_id="fixture:sg.demo",
    direction=Direction.INGRESS,
    protocol=Protocol.TCP,
    peer_ip="192.0.2.14",
    port=22,
):
    adapter = FixtureManagementAdapter()
    authentication = adapter.authenticate(identity)
    return evaluate_security_group_flow(
        topology_fixture or topology(),
        adapter=adapter,
        authentication=authentication,
        project_id=project_id,
        security_group_id=security_group_id,
        direction=direction,
        protocol=protocol,
        peer_ip=peer_ip,
        port=port,
    )


def mapping(**overrides):
    values = {
        "rule_id": "fixture:rule.mapped",
        "project_id": PROJECT,
        "direction": "ingress",
        "protocol": "tcp",
        "port_min": 22,
        "port_max": 22,
        "peer_cidr": "192.0.2.0/24",
        "source": SOURCE,
        "status": "simulated",
    }
    values.update(overrides)
    return values


class NetworkPolicyTests(unittest.TestCase):
    def assert_error_code(self, expected_code, callable_, *args, **kwargs):
        with self.assertRaises(NetworkPolicyError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected_code)
        self.assertNotIn("sensitive", str(raised.exception).lower())

    def test_valid_topology_and_same_project_references(self):
        fixture = topology()
        validate_topology(fixture)
        self.assertEqual(fixture.subnets[0].network_id, fixture.networks[0].network_id)
        self.assertEqual(fixture.router_interfaces[0].subnet_id, fixture.subnets[0].subnet_id)
        self.assertFalse(fixture.networks[0].is_public)

    def test_partial_topology_is_allowed_when_references_resolve(self):
        fixture = topology(routers=(), router_interfaces=(), security_groups=())
        validate_topology(fixture)

    def test_missing_network_reference_is_rejected(self):
        subnet = SubnetRecord("fixture:subnet.orphan", PROJECT, "fixture:network.missing", "192.0.2.0/24", SOURCE, "simulated")
        self.assert_error_code("missing_reference", topology, subnets=(subnet,))

    def test_missing_router_and_subnet_references_are_rejected(self):
        interface = RouterInterfaceRecord(
            "fixture:interface.orphan", PROJECT, "fixture:router.missing", "fixture:subnet.missing", SOURCE, "simulated"
        )
        self.assert_error_code("missing_reference", topology, router_interfaces=(interface,))

    def test_missing_router_reference_with_existing_subnet_is_rejected(self):
        interface = RouterInterfaceRecord(
            "fixture:interface.orphan", PROJECT, "fixture:router.missing", "fixture:subnet.private", SOURCE, "simulated"
        )
        self.assert_error_code("missing_reference", topology, router_interfaces=(interface,))

    def test_missing_subnet_reference_with_existing_router_is_rejected(self):
        interface = RouterInterfaceRecord(
            "fixture:interface.orphan", PROJECT, "fixture:router.demo", "fixture:subnet.missing", SOURCE, "simulated"
        )
        self.assert_error_code("missing_reference", topology, router_interfaces=(interface,))

    def test_missing_selected_security_group_is_rejected(self):
        self.assert_error_code("missing_reference", evaluate, security_group_id="fixture:sg.missing")

    def test_duplicate_ids_are_rejected(self):
        duplicate = NetworkRecord("fixture:network.private", PROJECT, SOURCE, "simulated")
        self.assert_error_code("duplicate_id", topology, networks=(duplicate, duplicate))

    def test_duplicate_rule_ids_are_rejected(self):
        duplicate_rules = (rule("fixture:rule.duplicate"), rule("fixture:rule.duplicate"))
        group = SecurityGroupRecord("fixture:sg.demo", PROJECT, duplicate_rules, SOURCE, "simulated")
        self.assert_error_code("duplicate_id", topology, security_groups=(group,))

    def test_cross_project_references_are_rejected(self):
        other_network = NetworkRecord("fixture:network.other", OTHER_PROJECT, SOURCE, "simulated")
        self.assert_error_code("inconsistent_reference", topology, networks=(other_network,))

    def test_group_rule_cross_project_reference_is_rejected(self):
        group = SecurityGroupRecord("fixture:sg.demo", PROJECT, (rule(project_id=OTHER_PROJECT),), SOURCE, "simulated")
        self.assert_error_code("inconsistent_reference", topology, security_groups=(group,))

    def test_records_require_simulated_status_and_fixture_source(self):
        self.assert_error_code(
            "invalid_record",
            NetworkRecord,
            "fixture:network.bad",
            PROJECT,
            "unverified source",
            "simulated",
        )
        self.assert_error_code(
            "invalid_record",
            NetworkRecord,
            "fixture:network.bad",
            PROJECT,
            SOURCE,
            "available",
        )

    def test_record_types_and_required_identifiers_are_checked(self):
        self.assert_error_code("invalid_record", NetworkRecord, " ", PROJECT, SOURCE, "simulated")
        self.assert_error_code("invalid_record", NetworkRecord, "fixture:network.bad", PROJECT, SOURCE, "simulated", 1)

    def test_direct_rule_requires_enum_instances(self):
        self.assertIsInstance(rule(), SecurityGroupRule)
        self.assert_error_code(
            "invalid_input",
            rule,
            direction="ingress",
        )
        self.assert_error_code(
            "invalid_input",
            rule,
            protocol="tcp",
        )

    def test_mapping_converts_exact_supported_enum_strings(self):
        parsed = security_group_rule_from_mapping(mapping(direction="egress", protocol="udp"))
        self.assertIs(parsed.direction, Direction.EGRESS)
        self.assertIs(parsed.protocol, Protocol.UDP)

    def test_mapping_rejects_unsupported_direction_and_protocol(self):
        self.assert_error_code("invalid_direction", security_group_rule_from_mapping, mapping(direction="inbound"))
        for protocol in ("icmp", "any", "sctp"):
            with self.subTest(protocol=protocol):
                self.assert_error_code("unsupported_protocol", security_group_rule_from_mapping, mapping(protocol=protocol))

    def test_mapping_rejects_wrong_enum_field_types(self):
        self.assert_error_code("invalid_input", security_group_rule_from_mapping, mapping(direction=Direction.INGRESS))
        self.assert_error_code("invalid_input", security_group_rule_from_mapping, mapping(protocol=Protocol.TCP))

    def test_mapping_rejects_unknown_or_missing_fields(self):
        self.assert_error_code(
            "unsupported_rule_form",
            security_group_rule_from_mapping,
            mapping(remote_group_id="fixture:sg.other"),
        )
        incomplete = mapping()
        del incomplete["project_id"]
        self.assert_error_code("invalid_input", security_group_rule_from_mapping, incomplete)

    def test_mapping_general_field_types_and_provenance_are_validated(self):
        self.assert_error_code("invalid_input", security_group_rule_from_mapping, mapping(rule_id=7))
        self.assert_error_code("invalid_input", security_group_rule_from_mapping, mapping(project_id=None))
        self.assert_error_code("invalid_cidr", security_group_rule_from_mapping, mapping(peer_cidr=7))
        self.assert_error_code("invalid_record", security_group_rule_from_mapping, mapping(source="unverified"))
        self.assert_error_code("invalid_record", security_group_rule_from_mapping, mapping(status="available"))

    def test_invalid_cidr_is_rejected(self):
        self.assert_error_code(
            "invalid_cidr",
            SubnetRecord,
            "fixture:subnet.bad",
            PROJECT,
            "fixture:network.private",
            "192.0.2.1/24",
            SOURCE,
            "simulated",
        )
        self.assert_error_code("invalid_cidr", rule, peer_cidr="not-a-cidr")

    def test_malformed_flow_ip_is_rejected(self):
        self.assert_error_code("invalid_cidr", evaluate, peer_ip="invalid-address")
        self.assert_error_code("invalid_cidr", evaluate, peer_ip="fe80::1%eth0")

    def test_rule_port_types_and_ranges_are_rejected(self):
        for port_value in (True, "22", 0, -1, 65536):
            with self.subTest(port_value=port_value):
                self.assert_error_code("invalid_port", rule, port_min=port_value)
        self.assert_error_code("invalid_rule", rule, port_min=23, port_max=22)

    def test_mapping_port_type_errors_use_stable_codes(self):
        self.assert_error_code("invalid_port", security_group_rule_from_mapping, mapping(port_min=True))
        self.assert_error_code("invalid_rule", security_group_rule_from_mapping, mapping(port_min=23, port_max=22))

    def test_evaluator_rejects_invalid_direction_and_protocol_types(self):
        self.assert_error_code("invalid_input", evaluate, direction="ingress")
        self.assert_error_code("invalid_input", evaluate, protocol="tcp")

    def test_evaluator_rejects_invalid_port_inputs_as_invalid_rule(self):
        for port_value in (True, "22", 0, -1, 65536):
            with self.subTest(port_value=port_value):
                self.assert_error_code("invalid_rule", evaluate, port=port_value)

    def test_ingress_tcp_rule_matches(self):
        decision = evaluate()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, "allow_rule_match")
        self.assertEqual(decision.matched_rule_id, "fixture:rule.allow-ssh")

    def test_egress_udp_rule_matches(self):
        egress_rule = rule(
            "fixture:rule.allow-dns",
            direction=Direction.EGRESS,
            protocol=Protocol.UDP,
            port_min=53,
            port_max=53,
            peer_cidr="198.51.100.0/24",
        )
        fixture = topology(
            security_groups=(SecurityGroupRecord("fixture:sg.demo", PROJECT, (egress_rule,), SOURCE, "simulated"),)
        )
        decision = evaluate(
            topology_fixture=fixture,
            direction=Direction.EGRESS,
            protocol=Protocol.UDP,
            peer_ip="198.51.100.53",
            port=53,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, "allow_rule_match")

    def test_port_range_boundaries_are_inclusive(self):
        port_rule = rule(port_min=20, port_max=24)
        fixture = topology(
            security_groups=(SecurityGroupRecord("fixture:sg.demo", PROJECT, (port_rule,), SOURCE, "simulated"),)
        )
        for port_value in (20, 24):
            with self.subTest(port=port_value):
                self.assertTrue(evaluate(topology_fixture=fixture, port=port_value).allowed)

    def test_cidr_match_non_match_and_different_family(self):
        self.assertTrue(evaluate(peer_ip="192.0.2.255").allowed)
        for address in ("192.0.3.1", "2001:db8::1"):
            with self.subTest(address=address):
                self.assertEqual(evaluate(peer_ip=address).code, "default_deny")

    def test_wrong_direction_protocol_port_or_peer_is_simulated_default_deny(self):
        cases = (
            {"direction": Direction.EGRESS},
            {"protocol": Protocol.UDP},
            {"port": 23},
            {"peer_ip": "198.51.100.1"},
        )
        for inputs in cases:
            with self.subTest(inputs=inputs):
                decision = evaluate(**inputs)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.code, "default_deny")

    def test_multiple_matching_rules_choose_lexicographically_first(self):
        rules = (
            rule("fixture:rule.z-last"),
            rule("fixture:rule.a-first"),
        )
        fixture = topology(
            security_groups=(SecurityGroupRecord("fixture:sg.demo", PROJECT, rules, SOURCE, "simulated"),)
        )
        result = evaluate(topology_fixture=fixture)
        self.assertEqual(result.matched_rule_id, "fixture:rule.a-first")

    def test_unknown_identity_and_insufficient_role_are_denied(self):
        unknown = evaluate(identity="sensitive-unknown-identity")
        self.assertEqual(unknown.code, "authorization_denied")
        self.assertFalse(unknown.allowed)

        no_role = AuthenticationResult(
            authenticated=True,
            identity=Identity("fixture:test-no-viewer", PROJECT, frozenset({"compute.operator"})),
            status="simulated",
        )
        adapter = FixtureManagementAdapter()
        result = evaluate_security_group_flow(
            topology(),
            adapter=adapter,
            authentication=no_role,
            project_id=PROJECT,
            security_group_id="fixture:sg.demo",
            direction=Direction.INGRESS,
            protocol=Protocol.TCP,
            peer_ip="192.0.2.4",
            port=22,
        )
        self.assertEqual(result.code, "authorization_denied")

    def test_cross_project_authorization_denial_has_generic_provenance(self):
        result = evaluate(project_id=OTHER_PROJECT)
        self.assertEqual(result.code, "authorization_denied")
        self.assertEqual(result.provenance, ("fixture:T15 network-policy evaluator",))

    def test_authorized_identity_with_mismatched_topology_is_denied_generically(self):
        adapter = FixtureManagementAdapter()
        mallory = adapter.authenticate("fixture:mallory")
        result = evaluate_security_group_flow(
            topology(),
            adapter=adapter,
            authentication=mallory,
            project_id=OTHER_PROJECT,
            security_group_id="sensitive-group-name",
            direction=Direction.INGRESS,
            protocol=Protocol.TCP,
            peer_ip="192.0.2.4",
            port=22,
        )
        self.assertEqual(result.code, "project_scope_denied")
        self.assertEqual(result.provenance, ("fixture:T15 network-policy evaluator",))
        self.assertNotIn("sensitive-group-name", repr(result))

    def test_policy_results_have_simulated_status_and_declared_provenance(self):
        decisions = (
            evaluate(),
            evaluate(peer_ip="198.51.100.1"),
            evaluate(identity="sensitive-unknown-identity"),
        )
        for result in decisions:
            with self.subTest(code=result.code):
                self.assertEqual(result.status, "simulated")
                self.assertIn("fixture:T15 network-policy evaluator", result.provenance)
        for result in decisions[:2]:
            self.assertIn(SOURCE, result.provenance)
        self.assertEqual(decisions[2].provenance, ("fixture:T15 network-policy evaluator",))

    def test_authorization_denial_does_not_reveal_topology_provenance(self):
        private_topology_source = "fixture:private-topology-source-sentinel"
        fixture = NetworkTopologyFixture(
            PROJECT,
            topology().networks,
            topology().subnets,
            topology().routers,
            topology().router_interfaces,
            topology().security_groups,
            private_topology_source,
            "simulated",
        )
        denied = evaluate(topology_fixture=fixture, identity="sensitive-unknown-identity")
        self.assertNotIn(private_topology_source, denied.provenance)
        self.assertNotIn("sensitive-unknown-identity", repr(denied))

    def test_evaluator_uses_no_network_or_socket_calls(self):
        adapter = FixtureManagementAdapter()
        authentication = adapter.authenticate("fixture:alice")
        with patch.object(socket, "create_connection", side_effect=AssertionError("socket call")) as connect:
            with patch.object(socket, "socket", side_effect=AssertionError("socket call")) as socket_ctor:
                result = evaluate_security_group_flow(
                    topology(),
                    adapter=adapter,
                    authentication=authentication,
                    project_id=PROJECT,
                    security_group_id="fixture:sg.demo",
                    direction=Direction.INGRESS,
                    protocol=Protocol.TCP,
                    peer_ip="192.0.2.4",
                    port=22,
                )
        connect.assert_not_called()
        socket_ctor.assert_not_called()
        self.assertEqual(result.status, "simulated")

    def test_policy_decision_rejects_non_simulated_or_inconsistent_values(self):
        self.assert_error_code(
            "invalid_record",
            PolicyDecision,
            True,
            "allow_rule_match",
            "available",
            ("fixture:test",),
            "fixture:rule.one",
        )
        self.assert_error_code(
            "invalid_record",
            PolicyDecision,
            True,
            "default_deny",
            "simulated",
            ("fixture:test",),
            None,
        )


if __name__ == "__main__":
    unittest.main()
