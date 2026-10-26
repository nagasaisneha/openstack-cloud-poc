# Maintained-release impact and migration decision

Reviewed 2026-10-26 against the urgent release requirement. This decision supersedes older statements allowing Caracal as the final local POC. The [capability matrix](../requirements/capability-traceability.md) records functional gaps; [release-policy.json](../../deployment/release-policy.json) records the selected release and compatibility guard.

## Immediate disposition

No package installation, upgrade, or service reconfiguration was initiated during this audit. A host process check found the unattended-upgrades and PackageKit daemons, no apt/dpkg transaction process, and `dpkg --audit` returned no incomplete package state. Daemon presence alone does not prove an active transaction; neither daemon was stopped. Recheck transaction/lock state immediately before any later package change.

Preserve the running Ubuntu 22.04 WSL2 / Caracal source, retained VM, all configuration, databases, Cinder backend image/VG, Swift filesystem/rings/secrets, billing ledger and every recovery point. Existing functionality remains useful migration-source evidence. It cannot satisfy final maintained-release acceptance.

Safe work continues: source/test development, documentation, dependency resolution in isolated tooling environments, read-only observations, target planning, evidence-gate checks and acceptance-runner development. Caracal-specific installers remain legacy preservation tools. Do not add Caracal Octavia packages or repoint the host's package repositories to another Ubuntu series.

## Release comparison and selection

| Item | 2026.1 Gazpacho | 2026.2 Hibiscus |
|---|---|---|
| Upstream status at review | Maintained; SLURP series | Maintained; non-SLURP series |
| Deployment tool series | Kolla Ansible 22 | Kolla Ansible 23 |
| Supported Ansible Core | 2.19–2.20 | 2.20–2.21 |
| Selected supported host OS | Ubuntu 24.04 | Ubuntu 24.04 |
| Existing Ubuntu 22.04 accepted by these Kolla host matrices | No | No |
| Octavia route | Matching Octavia + OVN provider, independently validated | Matching Octavia + OVN provider, independently validated |
| Existing repository tooling | New separate lock would be required | Existing exact 23.0.0 / Core 2.20.10 hash lock already resolves and was previously syntax-checked |
| Decision | Alternative if a documented driver/support constraint rules out Hibiscus; change policy/lock together | **Selected technical target for both final local POC and staging**; target and image compatibility still require validation |

The [upstream release table](https://releases.openstack.org/) lists both as maintained and Caracal as unmaintained. Review status again before deployment and final acceptance; scheduled lifecycle dates are estimates. The [Gazpacho host matrix](https://docs.openstack.org/kolla-ansible/2026.1/user/support-matrix.html) and [Hibiscus host matrix](https://docs.openstack.org/kolla-ansible/2026.2/user/support-matrix) support Ubuntu 24.04, Debian 13 and Rocky Linux 10. Tool ranges come from [Gazpacho](https://docs.openstack.org/releasenotes/kolla-ansible/2026.1.html) and [Hibiscus](https://docs.openstack.org/releasenotes/kolla-ansible/2026.2.html) release notes. This choice establishes a design target, not a deployment result or authorization for an OS change.

## Observed source compatibility

| Component | Current package observation | Maintained target impact |
|---|---|---|
| OS/Python | Ubuntu 22.04; distro Python 3.10 | Keep source untouched; deploy matching containers on supported Ubuntu 24.04 target. Tooling Python 3.12 is already isolated under `/tmp`, not a host upgrade. |
| Nova | 29.2.0, Jammy Caracal archive | Revalidate API microversions, policies, concurrency, libvirt/QEMU and guest behavior. Old service-unit/WSGI templates are not target templates. |
| Neutron/OVN | Neutron 24.1.0; OVN 24.03.6 | Recreate reviewed network design; validate provider mappings, gateway scheduling, Geneve MTU, routing, floating IP and Octavia health flows on target. |
| Cinder | 24.2.0, loop LVM + local TGT | Keep source VG/image; select target backend and transport/security. Never attach source VG writable in two deployments. Export/import representative data through supported operations after quiesce/hash verification. |
| Swift | 2.33.0, single-device local filesystem | Keep source rings/hash secrets/data. New staging rings and failure domains are separate. Copying rings is not a topology migration or durability test. |
| Horizon | 24.0.1, loopback Apache TLS | Port browser policy/custom panels into matching target Horizon image. Repeat login, roles, CSRF, TLS and view/API consistency; do not copy package paths or old WSGI wiring. |
| Databases/broker | MariaDB 10.6.23; RabbitMQ 3.9.27 | New matching target dependency images and backups; do not feed Caracal SQL to new schemas or share source databases/broker queues. |
| Octavia | Not installed; available source-host candidates are Octavia 14.0.0 / OVN provider 6.0.0 from Caracal | Do not install these for the final POC. Use matching Hibiscus images/driver and verify API/provider health monitors before workload tests. |
| Metering/billing | Real Nova existence-based allocation samples; provisional code source; no storage/network meter | Preserve ledger/history; configure least-privilege target collectors. Require controlled-workload and unit reconciliation before any complete demo bill. |
| Deployment | Isolated Kolla 23.0.0 / Core 2.20.10 inspected; no deployment | Existing source host is outside host matrix. Exact service image digests, registry availability and plugin/package inclusion are not yet verified. |

The local apt inventory exposes Caracal candidates for Nova and Octavia, not a verified Gazpacho/Hibiscus package path for this Jammy installation. No claim is made that manually installing source packages or newer Python would produce a supported deployment.

Nova's [2026.1 notes](https://docs.openstack.org/releasenotes/nova/2026.1.html) document native-threading defaults and removal of old WSGI script entry points. They permit a SLURP upgrade from 2025.1, not a direct Caracal jump. [2025.1 notes](https://docs.openstack.org/releasenotes/nova/2025.1.html) describe the Caracal-to-Epoxy hop. Those Nova paths do not validate every service or convert a package installation into Kolla.

## Migration approach

Prefer a **fresh, isolated Hibiscus target with controlled resource/data migration**. A separate target can be a separately approved local machine or supported Linux VM with adequate resources; it is not automatically another WSL distribution. No Ubuntu upgrade, new WSL distribution, external deployment, or infrastructure purchase is authorized by this design.

1. Inventory source identities, quotas, images/checksums, network intent, volumes/snapshots, objects, application state, billing/audit history, configuration and backup hashes into protected artifacts. Record writable resources and consistency requirements. Read-only inventory requires the protected Ubuntu operator context.
2. Produce and rehearse a fresh source recovery point. Existing core restore evidence remains useful; extend it to representative Cinder and Swift contents and application/billing state. Stop only task-owned workloads where a consistency test needs it.
3. Select/authorize the target. Require supported OS/runtime, independent networks/disks/credentials, reserved addressing, enough physical memory/storage, maintenance ownership, reviewed image digests and capacity tests. Do not run target bootstrap against the source WSL host.
4. Deploy the maintained target from reviewed templates. Run prerequisites, Kolla prechecks, API/service smoke tests and provider-policy checks before transferring customer data.
5. Recreate test tenants/roles/quotas and networks using supported APIs; retain mappings between source and target IDs. UUID preservation is not assumed. Image import and volume/object transfer must retain checksums and prove recovered application contents.
6. Replay all mandatory customer tests on the maintained target, including two real application VMs, Octavia traffic distribution, automatic backend exclusion/rejoin, scoped storage, real meter coverage and demo statements. Repeat core regressions; previous Caracal PASS records do not pass these gates.
7. Compare source/target inventory and data checksums. Test target backup/restore, rollback and startup/recovery. Record target version, image digests, test outputs and cleanup of only owned test assets.
8. Cut over only under an explicit reviewed change. Preserve source and every original backup until target acceptance and retention decisions are approved. On failure, return access to the intact source and reconcile divergent writes; never downgrade migrated database schemas.

An in-place multi-hop alternative would need a service-by-service supported route, OS transition, broker/database compatibility, isolated rehearsals and rollback at each hop. At least Nova requires Caracal → Epoxy → Gazpacho, then a supported Gazpacho → Hibiscus step if Hibiscus is chosen. No direct jump or live upgrade is prepared here. This alternative does not meet the current prohibition on an unapproved Ubuntu/WSL change.

## Capacity and target blocker

The current WSL allocation is about 7.7 GiB total RAM with about 1.8 GiB available in this audit. This says nothing about unused physical Windows RAM. It is below Kolla's 8-GiB minimum and the project's conservative 32-GiB planning guard for the full mandatory service set plus two application VMs. The 32-GiB guard is an engineering estimate, not an upstream minimum or measured benchmark. Re-size from service RSS, guest budgets, metering/log retention and observed load on the actual target.

No supported replacement local target is selected or authorized. This blocks maintained-release deployment and real acceptance; it does not block source development or staging preparation. Do not declare two-backend Octavia impossible from the present free-memory snapshot. OVN avoids Amphora VM overhead, but service, guest and health-monitor capacity still need tests.

## Revised dependencies

T45 (this impact assessment/policy/traceability) → T46 (source recovery inventory, supported local target, maintained deployment and migration acceptance) → replay T11/T18/T19/T27 on maintained target → T32–T36 browser/admin/tenant/storage workflows → T42 ingress → T43 Octavia → T44 distribution/failure/recovery → T37–T39 complete metering/pricing/billing/audit → T25/T26/T24 as applicable → T28/T40 all fourteen customer steps → independent staging Gates 0–6.

Security, observability, test-runner development, CI and IaC proceed in parallel where they do not depend on a live target. Feature code should use SDK/service boundaries and environment configuration; preserve legacy host installers for source recovery rather than generalizing their pinned packages into the maintained deployment.

## Final acceptance rule

Both `local_poc` and `staging` must have real provider evidence on the selected, still-maintained release. All fourteen customer steps must pass, including automatic backend exclusion and rejoin. The offline [release guard](../../scripts/check_release_policy.py) rejects a wrong-release, simulated, missing-step or failed-step report; it validates report fields and does not attest authenticity or replace execution. No maintained deployment, migration, physical HA, load-balancer health exercise or staging gate has passed in this change.
