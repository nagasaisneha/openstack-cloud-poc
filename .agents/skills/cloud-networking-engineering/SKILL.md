---
name: cloud-networking-engineering
description: Design or troubleshoot Neutron tenant networks, security groups, routing, addressing, and the physical network path.
---

# Cloud networking engineering

Reason across both virtual and physical layers. Network problems cross service, host, switch, and tenant boundaries; identify the failing layer before changing policy.

## Workflow

1. Map project networks, subnets/CIDRs, ports, routers, address pools, DNS/DHCP, floating IPs, security groups, provider networks, overlays, gateways, and physical interfaces.
2. Trace an example packet hop-by-hop with source/destination, direction, protocol, port, route, NAT, and policy decision. Check MTU, VLAN/VXLAN, ARP/ND, DHCP, DNS, and asymmetric routing when relevant.
3. Apply private-by-default and least exposure. Prefer narrow source CIDRs and ports; distinguish control-plane reachability from guest traffic.
4. Assess blast radius, overlapping CIDRs, shared-network impact, quota, rollback, and existing consumers before proposed changes.
5. Verify with approved evidence at each layer. A simulated rule match does not prove Neutron enforcement or packet delivery; WSL or host reachability does not prove guest reachability.

Do not create or alter network resources, open public ports, or scan endpoints without explicit approval naming resources, rules, test scope, and cleanup. Use release-specific Neutron docs and observed topology.
