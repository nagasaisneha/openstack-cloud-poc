# Staging deployment proposal

Prepared 2026-10-28 from the existing Kolla inventory, extended service profile,
preflight and locked tooling. Status: no target selected, no deployment, no
spending approved. The existing Ubuntu POC is a local development environment.

## Target and bill of resources

Recommended procurement path: use an organization-controlled isolated lab if
these resources already exist. Otherwise obtain a bare-metal quote with these
requirements. An existing operator-managed OpenStack staging cloud can run
application acceptance, but its operator must supply foundation/HA evidence.

| Role | Count | Minimum proposed resources per host | Purpose |
|---|---:|---|---|
| Controller/network | 3 | 8 physical cores, 32 GiB ECC, 2 x 480 GB mirrored OS SSD, two 10-Gbit/s ports | API VIP, DB/message/OVN quorum, two gateway chassis |
| Compute | 2 | 16 physical cores, 64 GiB ECC, 2 x 960 GB SSD, two 10-Gbit/s ports, hardware KVM/IOMMU | Real cross-host guests, evacuation reserve; no CPU overcommit during acceptance |
| Storage | 3 | 8 cores, 64 GiB ECC, mirrored OS SSD, **separate** 1.92 TB Ceph and 1.92 TB Swift SSDs, two 10-Gbit/s ports | Three physical failure domains; never share a data disk between Swift and Ceph |
| Deployment/monitoring | 1 small independent host/VM | 4 vCPU, 16 GiB, 200 GB SSD | Locked tooling, monitoring/logs, evidence and private inventory |
| GPU option | Equip one compute host or add one | Supported NVIDIA GPU >=12 GiB for this small AI workload, IOMMU/isolated PCI group, reset support, compatible firmware/driver | Nova passthrough and tenant CUDA acceptance; H100/MIG requires an actual supported H100/A100-class MIG device and a separate isolation test |

Aggregate baseline: 3 controllers + 2 computes + 3 storage hosts, 80 physical
cores and 416 GiB host RAM, plus deployment/monitoring. Compute usable reserve:
limit test guest allocation to 12 cores/48 GiB total so either 16-core compute
can carry the bounded test set. Three 1.92-TB Ceph disks provide 5.76 TB raw,
1.92 TB at replica 3, and about 1.34 TB after a 30% recovery reserve; Swift has
the same separate capacity budget. These are sizing calculations, not measured
performance promises. Keep the existing split topology to exercise real host
failure domains. A smaller converged design requires explicit reduced-scope
acceptance and a revised inventory.

## Networking and access

Reserve management/API, Geneve tunnel, storage, and external provider VLANs.
The network owner supplies non-overlapping CIDRs, VLAN IDs, interface names,
two API VIPs, DNS names, NTP, provider gateway and a floating-IP pool of at least
16 addresses. Allow customer HTTPS only over approved VPN/private ingress;
keep SQL, RabbitMQ, OVN and storage private. Verify UDP 6081, actual MTU across
hosts, guest DNS/metadata, FIP and LB VIP from the intended client network.
Use a routed provider VLAN/physnet; a public-cloud VM with ordinary virtual NICs
is not assumed to support nested KVM, promiscuous forwarding, GPU passthrough or
provider address routing.

## Secrets and release preparation

Keep SSH keys, generated Kolla passwords, clouds.yaml, rings/hash secrets and
private inventory in operator-controlled encrypted storage; expose them only
to the deployment account. Issue endpoint certificates for selected DNS names
from the organization CA. Assign secret rotation/revocation and audit retention
owners. Git holds only schema/templates and sanitized evidence. Use the locked
Kolla 23/Ansible toolchain and review release/tag/image signatures, SBOMs and
immutable image digests before deployment. The existing 2026.2 candidate must
pass a fresh support/security review at target selection. Local OS upgrade and
WSL export/import checks are excluded.

## Costs and approval

No purchase or chargeable resource has been created. Exact total requires a
region-specific quote for the selected hardware, private fabric and storage.
The approval worksheet is:

`monthly = 3*controller + 2*compute + 3*storage + deploy/monitor + GPU + backup/archive + VLAN/fabric + IPs + bandwidth + support + tax`.

`first month = monthly + setup + any hardware purchase/installation`.

Existing lab hardware has no assumed acquisition charge; finance must supply
power/rack/support allocation. Rental pricing must include dedicated private
network bandwidth and the separate data disks above. A public reference is
[Hetzner's current dedicated-server price table](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)
and [AX102 configuration](https://www.hetzner.com/dedicated-rootserver/ax102/konfigurator/);
its base two-disk/server/network profile is **not a quote for this design**.
[OVH public GPU instances](https://www.ovhcloud.com/en/public-cloud/prices/)
are a cost comparison only; renting a GPU VM does not establish Nova PCI
passthrough capability. Procurement must obtain the exact target quote before
spending. H100 purchase is outside the original local requirements.

## Responsibilities and execution

| Responsibility | Required assignment |
|---|---|
| Budget, region/data residency, release acceptance | User-selected accountable release/budget owner |
| Hosts, disks, network/IPAM/VLANs and privileged access | Platform operator and network/storage contacts |
| Secrets, CA, access policy and security findings | Security approver |
| Monitoring, external alerts, archive, incident response | Operations/on-call owner |
| User journeys and billing/rate policy signoff | Acceptance and billing owner (may be same person as release owner) |

After approval: materialize the private inventory, verify resource/disk
ownership and reviewed image digests, run read-only host discovery and Kolla
prechecks, deploy the approved full-service profile, then run dedicated A/B
tenant lifecycle/denial tests, cross-host FIP/VIP traffic, monitor-driven
failure/rejoin, closed-window billing, GPU guest CUDA, alert/audit/retention and
controller/compute/storage failure acceptance. Record exact artifact and
provider IDs plus cleanup. Staging remains unready until those live results
and named-owner acceptance exist. WSL restore evidence is not a release gate.
