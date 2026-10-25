"""Offline fixture-only network topology references and policy evaluation.

This module does not implement Neutron or test routing, security-group
enforcement, packet delivery, or guest connectivity. ``source`` values that
begin with ``fixture:`` are schema labels only; they do not authenticate a
record or establish a real cloud observation.
"""

from dataclasses import dataclass
from enum import Enum
import ipaddress
from typing import Mapping

from .adapter import FixtureManagementAdapter
from .identity import AuthenticationResult, AuthorizationResult


SIMULATED = "simulated"
_EVALUATOR_SOURCE = "fixture:T15 network-policy evaluator"


class Direction(str, Enum):
    INGRESS = "ingress"
    EGRESS = "egress"


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


_ERROR_MESSAGES = {
    "invalid_input": "The network-policy input is invalid.",
    "invalid_record": "The fixture record is invalid.",
    "invalid_cidr": "A CIDR or IP address is invalid.",
    "invalid_direction": "The rule direction is not supported.",
    "unsupported_protocol": "The rule protocol is not supported.",
    "invalid_port": "A rule port is invalid.",
    "invalid_rule": "The security-group rule is invalid.",
    "unsupported_rule_form": "The security-group rule form is not supported.",
    "duplicate_id": "The topology contains a duplicate record identifier.",
    "missing_reference": "A referenced fixture record does not exist.",
    "inconsistent_reference": "A fixture reference crosses project scope.",
}


class NetworkPolicyError(ValueError):
    """Safe validation error with a stable code and fixed message."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MESSAGES:
            code = "invalid_input"
        self.code = code
        self.message = _ERROR_MESSAGES[code]
        super().__init__(self.message)


def _fail(code: str) -> None:
    raise NetworkPolicyError(code)


def _require_text(value: object, *, code: str = "invalid_record") -> None:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        _fail(code)


def _validate_common(
    *,
    identifier: object,
    project_id: object,
    source: object,
    status: object,
) -> None:
    _require_text(identifier)
    _require_text(project_id)
    if not isinstance(source, str) or not source.strip() or not source.startswith("fixture:"):
        _fail("invalid_record")
    if status != SIMULATED or type(status) is not str:
        _fail("invalid_record")


def _parse_network(cidr: object) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    if not isinstance(cidr, str):
        _fail("invalid_cidr")
    try:
        return ipaddress.ip_network(cidr, strict=True)
    except (TypeError, ValueError):
        _fail("invalid_cidr")


def _parse_ip(address: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(address, str):
        _fail("invalid_cidr")
    try:
        parsed = ipaddress.ip_address(address)
    except (TypeError, ValueError):
        _fail("invalid_cidr")
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.scope_id is not None:
        _fail("invalid_cidr")
    return parsed


def _validate_rule_ports(port_min: object, port_max: object) -> None:
    if type(port_min) is not int or type(port_max) is not int:
        _fail("invalid_port")
    if not (1 <= port_min <= 65535 and 1 <= port_max <= 65535):
        _fail("invalid_port")
    if port_min > port_max:
        _fail("invalid_rule")


@dataclass(frozen=True)
class NetworkRecord:
    network_id: str
    project_id: str
    source: str
    status: str
    is_public: bool = False

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.network_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        if type(self.is_public) is not bool:
            _fail("invalid_record")


@dataclass(frozen=True)
class SubnetRecord:
    subnet_id: str
    project_id: str
    network_id: str
    cidr: str
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.subnet_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        _require_text(self.network_id)
        _parse_network(self.cidr)


@dataclass(frozen=True)
class RouterRecord:
    router_id: str
    project_id: str
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.router_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )


@dataclass(frozen=True)
class RouterInterfaceRecord:
    interface_id: str
    project_id: str
    router_id: str
    subnet_id: str
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.interface_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        _require_text(self.router_id)
        _require_text(self.subnet_id)


@dataclass(frozen=True)
class SecurityGroupRule:
    rule_id: str
    project_id: str
    direction: Direction
    protocol: Protocol
    port_min: int
    port_max: int
    peer_cidr: str
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.rule_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        if not isinstance(self.direction, Direction):
            _fail("invalid_input")
        if not isinstance(self.protocol, Protocol):
            _fail("invalid_input")
        _validate_rule_ports(self.port_min, self.port_max)
        _parse_network(self.peer_cidr)


@dataclass(frozen=True)
class SecurityGroupRecord:
    security_group_id: str
    project_id: str
    rules: tuple[SecurityGroupRule, ...]
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.security_group_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        if type(self.rules) is not tuple or any(
            not isinstance(rule, SecurityGroupRule) for rule in self.rules
        ):
            _fail("invalid_record")


@dataclass(frozen=True)
class NetworkTopologyFixture:
    project_id: str
    networks: tuple[NetworkRecord, ...]
    subnets: tuple[SubnetRecord, ...]
    routers: tuple[RouterRecord, ...]
    router_interfaces: tuple[RouterInterfaceRecord, ...]
    security_groups: tuple[SecurityGroupRecord, ...]
    source: str
    status: str

    def __post_init__(self) -> None:
        _validate_common(
            identifier=self.project_id,
            project_id=self.project_id,
            source=self.source,
            status=self.status,
        )
        validate_topology(self)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str
    status: str
    provenance: tuple[str, ...]
    matched_rule_id: str | None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool or type(self.status) is not str or self.status != SIMULATED:
            _fail("invalid_record")
        if self.code not in {
            "allow_rule_match",
            "default_deny",
            "authorization_denied",
            "project_scope_denied",
        }:
            _fail("invalid_record")
        if type(self.provenance) is not tuple or any(
            not isinstance(source, str) or not source.startswith("fixture:")
            for source in self.provenance
        ):
            _fail("invalid_record")
        if self.provenance != tuple(sorted(set(self.provenance))):
            _fail("invalid_record")
        if self.code == "allow_rule_match":
            if not self.allowed or not isinstance(self.matched_rule_id, str):
                _fail("invalid_record")
        elif self.allowed or self.matched_rule_id is not None:
            _fail("invalid_record")


def validate_topology(topology: NetworkTopologyFixture) -> None:
    """Validate fixture types, unique IDs, project scope, and references.

    This checks declared fixture references only; it does not validate routes.
    """
    if not isinstance(topology, NetworkTopologyFixture):
        _fail("invalid_input")
    _validate_common(
        identifier=topology.project_id,
        project_id=topology.project_id,
        source=topology.source,
        status=topology.status,
    )
    groups = (
        (topology.networks, NetworkRecord, "network_id"),
        (topology.subnets, SubnetRecord, "subnet_id"),
        (topology.routers, RouterRecord, "router_id"),
        (topology.router_interfaces, RouterInterfaceRecord, "interface_id"),
        (topology.security_groups, SecurityGroupRecord, "security_group_id"),
    )
    for records, expected_type, identifier_field in groups:
        if type(records) is not tuple:
            _fail("invalid_input")
        if any(not isinstance(record, expected_type) for record in records):
            _fail("invalid_record")
        identifiers = [getattr(record, identifier_field) for record in records]
        if len(identifiers) != len(set(identifiers)):
            _fail("duplicate_id")
        for record in records:
            if record.project_id != topology.project_id:
                _fail("inconsistent_reference")
            _validate_common(
                identifier=getattr(record, identifier_field),
                project_id=record.project_id,
                source=record.source,
                status=record.status,
            )

    networks = {record.network_id: record for record in topology.networks}
    subnets = {record.subnet_id: record for record in topology.subnets}
    routers = {record.router_id: record for record in topology.routers}
    for subnet in topology.subnets:
        _parse_network(subnet.cidr)
        network = networks.get(subnet.network_id)
        if network is None:
            _fail("missing_reference")
        if network.project_id != subnet.project_id:
            _fail("inconsistent_reference")
    for interface in topology.router_interfaces:
        router = routers.get(interface.router_id)
        subnet = subnets.get(interface.subnet_id)
        if router is None or subnet is None:
            _fail("missing_reference")
        if router.project_id != interface.project_id or subnet.project_id != interface.project_id:
            _fail("inconsistent_reference")
    rule_ids: set[str] = set()
    for group in topology.security_groups:
        if type(group.rules) is not tuple:
            _fail("invalid_record")
        for rule in group.rules:
            if not isinstance(rule, SecurityGroupRule):
                _fail("invalid_record")
            if rule.rule_id in rule_ids:
                _fail("duplicate_id")
            rule_ids.add(rule.rule_id)
            if rule.project_id != group.project_id:
                _fail("inconsistent_reference")
            _validate_common(
                identifier=rule.rule_id,
                project_id=rule.project_id,
                source=rule.source,
                status=rule.status,
            )
            if not isinstance(rule.direction, Direction):
                _fail("invalid_input")
            if not isinstance(rule.protocol, Protocol):
                _fail("invalid_input")
            _validate_rule_ports(rule.port_min, rule.port_max)
            _parse_network(rule.peer_cidr)


_RULE_MAPPING_KEYS = frozenset(
    {
        "rule_id",
        "project_id",
        "direction",
        "protocol",
        "port_min",
        "port_max",
        "peer_cidr",
        "source",
        "status",
    }
)


def security_group_rule_from_mapping(record: Mapping[str, object]) -> SecurityGroupRule:
    """Build a rule from an exact mapping schema and serialized enum strings."""
    if not isinstance(record, Mapping):
        _fail("invalid_input")
    if set(record) - _RULE_MAPPING_KEYS:
        _fail("unsupported_rule_form")
    if set(record) != _RULE_MAPPING_KEYS:
        _fail("invalid_input")

    for field_name in ("rule_id", "project_id"):
        if type(record[field_name]) is not str:
            _fail("invalid_input")
    for field_name in ("source", "status"):
        if type(record[field_name]) is not str:
            _fail("invalid_input")
    if not record["source"].strip() or not record["source"].startswith("fixture:"):
        _fail("invalid_record")
    if record["status"] != SIMULATED:
        _fail("invalid_record")
    if type(record["peer_cidr"]) is not str:
        _fail("invalid_cidr")

    direction_value = record["direction"]
    if type(direction_value) is not str:
        _fail("invalid_input")
    try:
        direction = Direction(direction_value)
    except ValueError:
        _fail("invalid_direction")

    protocol_value = record["protocol"]
    if type(protocol_value) is not str:
        _fail("invalid_input")
    try:
        protocol = Protocol(protocol_value)
    except ValueError:
        _fail("unsupported_protocol")

    return SecurityGroupRule(
        rule_id=record["rule_id"],
        project_id=record["project_id"],
        direction=direction,
        protocol=protocol,
        port_min=record["port_min"],
        port_max=record["port_max"],
        peer_cidr=record["peer_cidr"],
        source=record["source"],
        status=record["status"],
    )


def evaluate_security_group_flow(
    topology: NetworkTopologyFixture,
    *,
    adapter: FixtureManagementAdapter,
    authentication: AuthenticationResult,
    project_id: str,
    security_group_id: str,
    direction: Direction,
    protocol: Protocol,
    peer_ip: str,
    port: int,
) -> PolicyDecision:
    """Evaluate one simulated flow after existing project authorization."""
    if not isinstance(topology, NetworkTopologyFixture):
        _fail("invalid_input")
    if type(adapter) is not FixtureManagementAdapter:
        _fail("invalid_input")
    if not isinstance(authentication, AuthenticationResult):
        _fail("invalid_input")
    _require_text(project_id, code="invalid_input")
    _require_text(security_group_id, code="invalid_input")
    if not isinstance(direction, Direction):
        _fail("invalid_input")
    if not isinstance(protocol, Protocol):
        _fail("invalid_input")
    peer_address = _parse_ip(peer_ip)
    if type(port) is not int or not 1 <= port <= 65535:
        _fail("invalid_rule")

    authorization = adapter.authorize(authentication, project_id, "catalog.read")
    if not isinstance(authorization, AuthorizationResult):
        _fail("invalid_input")
    if not authorization.allowed:
        return _decision(False, "authorization_denied", (_EVALUATOR_SOURCE,))
    if topology.project_id != project_id:
        return _decision(False, "project_scope_denied", (_EVALUATOR_SOURCE,))

    validate_topology(topology)
    security_group = next(
        (group for group in topology.security_groups if group.security_group_id == security_group_id),
        None,
    )
    if security_group is None:
        _fail("missing_reference")

    matching = []
    for rule in security_group.rules:
        peer_network = _parse_network(rule.peer_cidr)
        if (
            rule.direction is direction
            and rule.protocol is protocol
            and rule.port_min <= port <= rule.port_max
            and peer_address.version == peer_network.version
            and peer_address in peer_network
        ):
            matching.append(rule)

    provenance = {
        _EVALUATOR_SOURCE,
        topology.source,
        security_group.source,
        *(rule.source for rule in security_group.rules),
    }
    if not matching:
        return _decision(False, "default_deny", provenance)
    matched_rule = min(matching, key=lambda item: item.rule_id)
    return _decision(True, "allow_rule_match", provenance, matched_rule.rule_id)


def _decision(
    allowed: bool,
    code: str,
    provenance: tuple[str, ...] | set[str],
    matched_rule_id: str | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        allowed=allowed,
        code=code,
        status=SIMULATED,
        provenance=tuple(sorted(set(provenance))),
        matched_rule_id=matched_rule_id,
    )
