# Authoritative POC completion checklist

Updated 2026-10-27 (IST). This is the single execution/status checklist. The [capability traceability matrix](requirements/capability-traceability.md) retains requirement mappings, historical evidence and detailed acceptance criteria. Task files record implementation history; they cannot override this checklist's final acceptance boundaries.

**Final verdict: local browser workflows in progress; no staging deployment.** The active delivery target is the existing Ubuntu 22.04 WSL2 / Caracal POC. [T48](tasks/T48.md) records the user's current authorization: preserve the verified backup, stop backup/restore rehearsals, resolve live service health and complete browser workflows. No OS upgrade or additional WSL distribution. Full-image restore verification remains **unverified**, independently of development progress.

Status vocabulary: **Completed and verified**, **In progress**, **Blocked**, **Not implemented**, **Requires staging infrastructure**. A completed component row names its exact scope; it never completes the entire POC. Offline tests, signed metadata and synthetic application data are not live cloud acceptance.

## Current implementation milestone: complete the existing browser-ready POC

| ID | Status | Verified result / next required action |
|---|---|---|
| R01 | Completed and verified | Operator source API inventory: Keystone authenticated; one ACTIVE VM/image; Cinder and Swift responding. Octavia, external networks, routers and FIPs absent. [Evidence](evidence/recovery-progress-2026-10-26.json). This is source inventory, not a fresh full functional replay. |
| R02 | Completed and verified | Fresh **actual eight-database** backup/isolated import, including Cinder, plus three OVS/OVN restores; owned children stopped, live databases untouched. Protected operator report: `extended-database-recovery-uglbhz4c/report.json`. Source-version logical recovery only. |
| R03 | Completed and verified | Fresh **actual application job journal** backup/restore into independent private paths: `application-recovery-19hcppgg`. Committed WAL, checksum, integrity and schema checks implemented. No deployed billing-ledger recovery is claimed. |
| R04 | Completed and verified | Swift 2.33 → Epoxy 2.35.5 → Gazpacho 2.37.4 isolated **real backend-engine** object read/write and account/container checks, reverse object read, and restored file/xattr integrity. Signed source verified; all 175/176 respective wheel Python modules match signed source; maintained Gatekeeper header filters pass. Not a live Swift cutover or Keystone/network test. |
| R05 | Completed and verified | RabbitMQ/Erlang bridge package availability: documented key/signatures, 36 downloaded package hashes; three APT dependency simulations pass with removals forbidden; extracted Erlang 24/25 runtimes and crypto work. No broker was started, upgraded, reset or migrated. |
| R06 | Completed and verified | Cinder's original image/PV is on `/dev/loop3`; Swift remains on `/dev/loop0`. All 27 required services are active. The exact retained VM is ACTIVE with pinned-key SSH, MTU/ping, CPU and KVM verification. Protected live report: `return-to-service-20261027.json`. No resource replacement or backup/restore rehearsal. See [T48](tasks/T48.md). |
| R07 | Completed and verified | Export creation and independent SHA256 match were observed: 43,135,270,912 bytes. The verified Windows VHDX is preserved unchanged. **Full-image restore remains unverified; further rehearsals are stopped by user instruction.** Original continuation failures remain historical records and are not reset or replayed. |
| R08 | Not implemented | Further upgrade compatibility/migration rehearsals are outside the current work. Historical results and [migration matrix](architecture/service-upgrade-matrix.md) remain evidence only; they do not gate browser development. |
| R09 | Not implemented | OS/OpenStack upgrade execution is outside the current authorized scope. The existing Ubuntu/Caracal is the delivery environment; no extra distribution or staging deployment. |
| R10 | In progress | Patched hash-locked SDK/Swift dependencies: 737 tests pass with two opt-in skips on each clean Python 3.10/3.12; both Swift dependency checks pass. Published advisory scan has zero unresolved findings and two narrowly dispositioned Swift backport aliases. Windows syntax/read-only/unconfirmed-export refusal pass. CI jobs implemented. Remote CI and complete release artifact acceptance are not yet evidenced. |

The core browser demonstration is verified in [T48](tasks/T48.md). The active
continuation [T49](tasks/T49.md) prioritizes guest/client access, full browser
lifecycle, isolation/quotas, complete measurements/billing verification,
Octavia/failure recovery and monitoring/regression. Backup and restore
rehearsals are stopped. Full restore remains unverified and is not a development
gate. Protected credentials/runtime records and the unchanged backup stay
outside Git. Staging readiness remains an overall objective; staging deployment
and AI/GPU acceptance are explicitly pending.

## Every mandatory capability remains in scope

The service-specific backlog below retains future maintained-release acceptance where stated. Those migration/staging checks are separate from the current Caracal browser workflow and cannot block it. Current delivery evidence is recorded in T48.

| ID / tasks | Capability | Status | Evidence boundary and next acceptance |
|---|---|---|---|
| F01 · T11/T33/T40 | Keystone users, projects, roles, isolation | Verified local subset | Fresh admin/two-tenant login, browser project/user creation and backend foreign-resource/billing denials passed in T48. Broader role/mutation cases remain open. |
| F02 · T32/T34 | Horizon/self-service dashboard | Verified local subset | Trusted Windows HTTPS, Chromium logins, resource creation, consoles and scoped usage/billing passed. Full capability matrix remains open. |
| F03 · T33/T34 | Administrator/customer resource workflows | Verified local subset | Browser project/user creation, admin profile edit and quota 2→1→2, tenant resource creation and provisional statements/downloads passed. |
| F04 · T11/T16 | Nova provision/lifecycle/KVM | Verified local subset | Retained VM ACTIVE/KVM/pinned SSH/MTU passed. T49 additionally verifies an A-owned disposable VM's browser create/stop/start/console/delete, Nova statuses/event timestamps and independent Nova/libvirt absence. Repeatable two-tenant regression remains in progress. |
| F05 · T11 | Glance image management | In progress | Browser selected and launched a hash-verified public CirrOS copy; original image preserved. Private/shared image mutation cases remain open. |
| F06 · T11/T15 | Neutron private networking/security groups | In progress | Browser private networks/DHCP subnets and backend foreign-network denial passed. Broader security-group/lifecycle cases remain open. |
| F07 · T42 | Floating IP/application ingress | Verified local VM path; VIP pending | T49 adds isolated logical provider bridge, per-tenant routers/FIPs and source-restricted rules. Native Windows pinned SSH, foreign-key rejection and distinct app HTTP 200 passed for both tenants (2026-10-27 21:18:38 UTC). Only localhost forwards; no physical NIC/Windows route/public exposure. VIP acceptance awaits Octavia. |
| F08 · T43 | Octavia customer lifecycle | Not implemented | No catalog service/package deployment. Matching maintained OVN provider, roles/quotas and customer LB/listener/pool/member/monitor CRUD required. |
| F09 · T44 | Two real VM traffic distribution | Not implemented | Two owned app VMs, unique responses, fresh connection distribution and recorded errors required. |
| F10 · T44 | Automatic unhealthy exclusion/rejoin | Not implemented | Real app outage/recovery with monitor-driven transitions, untouched member admin state and measured traffic/convergence required. |
| F11 · T18/T35 | Cinder volumes/snapshots/storage | Verified local subset | T49 verifies browser create/attach, UUID-guarded guest 4 KiB SHA-256 write/read, browser detach, ordinary snapshot create/delete and volume delete on new disposable data. Scoped loopback node removed/global defaults unchanged; temporary 3 GiB quota restored to 2 GiB. No restore rehearsal. Full restore remains unverified and not a gate. |
| F12 · T19/T36 | Object lifecycle/isolation | Verified local subset | Two private browser containers and 40-byte object round trips, foreign-read denial and usage counts passed. Broader mutation/delete/durability scope remains open; rehearsals stopped. |
| F13 · T20/T37 | Compute/storage/network metering | In progress | Scoped one-minute collector and immutable history deployed. Nova allocation and current Cinder/Swift observations verified; active-only/network meters and storage-hour rating remain unavailable. |
| F14 · T21/T31/T38/T39 | Configurable demo rates/billing | Verified provisional demonstration | Browser rate entry, statements, itemized lines, Decimal calculations, tenant CSV/JSON and foreign-statement denial passed. Complete consumption coverage remains unavailable; no complete total/invoice claimed. |
| F15 · T33/T35/T36/T43 | Tenant quotas and limits | Verified local subset | Admin quota edit/restoration, tenant launch validation and actual Nova 403/Cinder 413 quota faults passed with zero probe resources. Broader network/object/LB limits remain open. |
| F16 · T26/T41 | Monitoring, dashboards, logs, alerts | In progress | Host PCP metrics exist. POC/service/billing/LB dashboards, alert triggers/delivery/recovery and retention are unverified. |
| F17 · T25/T32/T40 | TLS, secrets, isolation/security | In progress | Source isolation/protected secrets/local Horizon TLS exist. Complete API TLS, browser/role tests, listeners/firewall, rotation, scans and findings disposition remain open. |
| F18 · T27/T47 | Backup/restore/service recovery | In progress | Historical core recovery plus fresh R02/R03 passed. Whole-WSL/representative storage/billing recovery and measured RPO/RTO remain open. |
| F19 · T22/T41/T46 | Repeatable deployment/upgrade/rollback | In progress | Staging templates/locks/read-only checks and guarded local recovery tools exist. Actual maintained same-instance upgrade/rollback and idempotent staging deploy unverified. |
| F20 · T28/T40 | Automated real end-to-end regression | Not implemented | Individual historical component runners exist. All fourteen customer steps must pass on maintained local POC, then independently staging; no fixture substitution. |
| F21 · T24 | Bounded sample AI workload | Not implemented | Licensed/pinned model, real bounded inference and measured resources still required. CPU feasibility is not ruled out by laptop GPU limitations. |
| F22 · T23 | H100/MIG hardware acceptance | Blocked | Existing RTX 2060 cannot demonstrate H100 MIG. Any physical GPU/isolation acceptance requires separately approved suitable hardware; no hardware claim is made. |

## Staging release gates

| Gate | Status | Remaining evidence |
|---|---|---|
| 0: scope/target/owners | Blocked | Human target, workload, budget, service owners and approvers not selected/approved. Maintained candidate design exists. |
| 1: architecture/design | In progress | Kolla/Hibiscus/Noble candidate and topology prepared; actual networks, storage, sizing, images/provenance and owner review pending. |
| 2: foundation | Requires staging infrastructure | Real supported hosts, networking, disks, secrets/TLS and restore capacity. |
| 3: integrations/security | Requires staging infrastructure | Live multi-node provider, identity/storage/LB/metering/security validation. |
| 4: release candidate | Requires staging infrastructure | All real customer steps, failure recovery, performance, upgrade/rollback and security acceptance. |
| 5: controlled production rollout | Blocked | Release-owner/change approval; no external rollout authorized. |
| 6: operational handover | Requires staging infrastructure | Validated runbooks, named on-call/ownership, alerts, recovery and scheduled access/restore reviews. |

No gate is passed because a template, unit test or laptop result exists. Staging deployment status: **not deployed**. The [release plan](staging-release-plan.md) retains the authoritative gate definitions.
