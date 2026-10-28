# Authoritative POC completion checklist

Updated 2026-10-28 (IST). This is the single execution/status checklist. The [capability traceability matrix](requirements/capability-traceability.md) retains requirement mappings, historical evidence and detailed acceptance criteria. Task files record implementation history; they cannot override this checklist's final acceptance boundaries.

**Final verdict: local browser workflows in progress; no staging deployment.** The active delivery target is the existing Ubuntu 22.04 WSL2 / Caracal POC. [T48](tasks/T48.md) records the user's current authorization: preserve the verified backup, stop backup/restore rehearsals, resolve live service health and complete browser workflows. No OS upgrade or additional WSL distribution. WSL image restore verification is removed from active completion scope by the current user instruction; historical results remain in T48/T49.

Status vocabulary: **Completed and verified**, **In progress**, **Blocked**, **Not implemented**, **Requires staging infrastructure**. A completed component row names its exact scope; it never completes the entire POC. Offline tests, signed metadata and synthetic application data are not live cloud acceptance.

## Historical recovery and baseline evidence (not active release gates)

| ID | Status | Verified result / next required action |
|---|---|---|
| R01 | Completed and verified | Operator source API inventory: Keystone authenticated; one ACTIVE VM/image; Cinder and Swift responding. Octavia, external networks, routers and FIPs absent. [Evidence](evidence/recovery-progress-2026-10-26.json). This is source inventory, not a fresh full functional replay. |
| R02 | Completed and verified | Fresh **actual eight-database** backup/isolated import, including Cinder, plus three OVS/OVN restores; owned children stopped, live databases untouched. Protected operator report: `extended-database-recovery-uglbhz4c/report.json`. Source-version logical recovery only. |
| R03 | Completed and verified | Fresh **actual application job journal** backup/restore into independent private paths: `application-recovery-19hcppgg`. Committed WAL, checksum, integrity and schema checks implemented. No deployed billing-ledger recovery is claimed. |
| R04 | Completed and verified | Swift 2.33 → Epoxy 2.35.5 → Gazpacho 2.37.4 isolated **real backend-engine** object read/write and account/container checks, reverse object read, and restored file/xattr integrity. Signed source verified; all 175/176 respective wheel Python modules match signed source; maintained Gatekeeper header filters pass. Not a live Swift cutover or Keystone/network test. |
| R05 | Completed and verified | RabbitMQ/Erlang bridge package availability: documented key/signatures, 36 downloaded package hashes; three APT dependency simulations pass with removals forbidden; extracted Erlang 24/25 runtimes and crypto work. No broker was started, upgraded, reset or migrated. |
| R06 | Completed and verified | Cinder's original image/PV is on `/dev/loop3`; Swift remains on `/dev/loop0`. All 27 required services are active. The exact retained VM is ACTIVE with pinned-key SSH, MTU/ping, CPU and KVM verification. Protected live report: `return-to-service-20261027.json`. No resource replacement or backup/restore rehearsal. See [T48](tasks/T48.md). |
| R07 | Completed and verified | Export creation and independent SHA256 match were observed: 43,135,270,912 bytes. The verified Windows VHDX is preserved unchanged. Restore verification was not established; it is excluded from active scope. Original continuation failures remain historical records and are not reset or replayed. |
| R08 | Not implemented | Further upgrade compatibility/migration rehearsals are outside the current work. Historical results and [migration matrix](architecture/service-upgrade-matrix.md) remain evidence only; they do not gate browser development. |
| R09 | Not implemented | OS/OpenStack upgrade execution is outside the current authorized scope. The existing Ubuntu/Caracal is the delivery environment; no extra distribution or staging deployment. |
| R10 | In progress | Patched hash-locked SDK/Swift dependencies: 737 tests pass with two opt-in skips on each clean Python 3.10/3.12; both Swift dependency checks pass. Published advisory scan has zero unresolved findings and two narrowly dispositioned Swift backport aliases. Windows syntax/read-only/unconfirmed-export refusal pass. CI jobs implemented. Remote CI and complete release artifact acceptance are not yet evidenced. |

The core browser demonstration and two-tenant VM/storage/IP regression are
verified in [T48](tasks/T48.md) and [T49](tasks/T49.md). T49 now also verifies a
real OVN load balancer, traffic distribution and health-monitor recovery,
Octavia project isolation/quota rejection, and the local operations dashboard
and alert lifecycle. Historical measurement gaps still make generated billing
statements provisional. Remaining local work includes wider image/object and
network/role matrices, month-complete usage coverage, and observed audit
retention aging. Backup and restore rehearsals are stopped. WSL restore verification is excluded from active scope. Protected credentials/runtime
records and the unchanged backup stay outside Git. Staging readiness remains an
overall objective; staging deployment and AI/GPU acceptance are explicitly
pending.

## Active acceptance scope

[T50](tasks/T50.md) defines the current work, dependencies and evidence. The [staging proposal](staging-deployment-proposal.md) records the concrete resource and decision requirements. WSL restore verification is excluded.

The service-specific backlog below retains future maintained-release acceptance where stated. Those migration/staging checks are separate from the current Caracal browser workflow and cannot block it. Current delivery evidence is recorded in T48.

| ID / tasks | Capability | Status | Evidence boundary and next acceptance |
|---|---|---|---|
| F01 · T11/T33/T40 | Keystone users, projects, roles, isolation | Verified local subset | Fresh admin/two-tenant login, browser project/user creation and billing denials passed in T48. T49 tenant B receives 403 for tenant A's Octavia LB, 404 for tenant A's subnet, and only sees its own empty LB list. Provider discovery is intentionally visible and returns only `ovn`. Broader image/object mutation and role matrices remain open. |
| F02 · T32/T34 | Horizon/self-service dashboard | Verified local subset | Trusted Windows HTTPS, Chromium tenant/admin logins, resource creation, consoles, scoped usage/billing and the admin operations page passed. The operations page returns 200 to admin and 403 to tenant A. Full capability matrix remains open. |
| F03 · T33/T34 | Administrator/customer resource workflows | Verified local subset | Browser project/user creation, admin profile edit and quota 2→1→2, tenant resource creation and provisional statements/downloads passed. T49 created the Octavia graph through Horizon and verified one-LB-per-project quota enforcement. |
| F04 · T11/T16 | Nova provision/lifecycle/KVM | Verified local subset | Retained VM ACTIVE/KVM/pinned SSH/MTU passed. T49 automated regression `t49-regression-01` passed 18 VM/storage/IP checks per tenant, including native create/stop/start/console/delete, provider timestamps and independent Nova/libvirt absence. Original/sample resources preserved. |
| F05 · T11 | Glance image management | In progress | Browser selected and launched a hash-verified public CirrOS copy; original image preserved. Private/shared image mutation cases remain open. |
| F06 · T11/T15 | Neutron private networking/security groups | In progress | Browser private networks/DHCP subnets and backend foreign-network denial passed. Broader security-group/lifecycle cases remain open. |
| F07 · T42 | Floating IP/application ingress | Verified local VM and VIP paths | T49 adds isolated logical provider bridge, per-tenant routers/FIPs and source-restricted rules. Native Windows pinned SSH, foreign-key rejection and distinct app HTTP 200 passed for both tenants (2026-10-27 21:18:38 UTC). The tenant A Octavia VIP FIP was allocated and associated through Horizon; 60 fresh HTTP connections reached both real backends. Only localhost forwards; no physical NIC/Windows route/public exposure. |
| F08 · T43 | Octavia customer lifecycle | Verified local subset | In the existing Ubuntu, the native Horizon wizard created an OVN load balancer, TCP/8080 listener, `SOURCE_IP_PORT` pool, two tenant-owned members and TCP monitor. Provider API independently confirmed the full graph active/online. Tenant B cannot read tenant A's LB. Full browser delete/update journeys and staging provider acceptance remain open. |
| F09 · T44 | Two real VM traffic distribution | Verified local subset | The tenant A VIP returned exact primary/secondary VM identities over 60 fresh connections (29/31, zero errors). Both apps are actual owned guests; the VIP is reached through a local-only forward. |
| F10 · T44 | Automatic unhealthy exclusion/rejoin | Verified local subset | With the member admin flag left enabled, the marked secondary app was stopped over pinned SSH; OVN reported member `ERROR` (the provider's unhealthy state), and 30/30 requests reached the primary with zero errors. Restarting the same app returned the member to `ONLINE`; 60/60 post-recovery requests reached both backends (30/30), zero errors. |
| F11 · T18/T35 | Cinder volumes/snapshots/storage | Verified local subset | T49 verifies browser create/attach, UUID-guarded guest 4 KiB SHA-256 write/read, browser detach, ordinary snapshot create/delete and volume delete on new disposable data. Scoped loopback node removed/global defaults unchanged; temporary 3 GiB quota restored to 2 GiB. No restore rehearsal is part of current acceptance. |
| F12 · T19/T36 | Object lifecycle/isolation | Verified local subset | Two private browser containers and 40-byte object round trips, foreign-read denial and usage counts passed. Broader mutation/delete/durability scope remains open; rehearsals stopped. |
| F13 · T20/T37 | Compute/storage/network metering | Verified observed-window subset; completeness pending | T49 read-only provider-event/libvirt meter supplies seven metric types with immutable lineage, active-stop exclusion and reset gaps. Independent reconciliation matched 3,780 active seconds, 8 stopped seconds, two storage lifetimes and raw CPU/interface deltas. Historical counters, object byte-hours and larger-volume resize history remain unavailable. |
| F14 · T21/T31/T38/T39 | Configurable demo rates/billing | Verified provisional demonstration | T49 new measured demo rates and browser statements: 20 A / 13 B lines across seven metrics, independent Decimal checks, own CSV/JSON 200 and foreign 404. Old immutable cards preserved. Incomplete history/open period keep status provisional and total null; no invoice/payment claim. |
| F15 · T33/T35/T36/T43 | Tenant quotas and limits | Verified local subset | Admin quota edit/restoration, tenant launch validation, actual Nova 403/Cinder 413 faults, and T49 Octavia quota enforcement passed. Tenant B's default load-balancer limit is 1: one marked probe LB was accepted, the second was rejected with HTTP 403 and a quota response, then all probe LBs were deleted. Broader network/object limits remain open. |
| F16 · T26/T41 | Monitoring, dashboards, logs, alerts | Verified local subset; retention aging pending | T49 local timer polls all 27 baseline service units, 11 additional POC service/timer units, dashboard/tenant/VIP HTTP routes and three project meter-freshness probes (45 probes total); latest snapshot had zero unhealthy probes/active alerts. Chromium admin page showed all probes with HTTP 200; tenant A gets 403. A reversible tenant A route outage opened an alert after two failed samples; restart resolved it after two successful samples. Both transitions were found in the mode-0600 JSON audit. Ninety daily rotations are configured, not yet observed over 90 days; off-host archival/external delivery remain staging items. |
| F17 · T25/T32/T40 | TLS, secrets, isolation/security | In progress | Source isolation/protected secrets/local Horizon TLS exist. Complete API TLS, browser/role tests, listeners/firewall, rotation, scans and findings disposition remain open. |
| F18 · T27/T47 | Operational service recovery | Verified local subset | T49 verified app failure exclusion/rejoin and alert recovery. WSL restore verification has been removed from active completion scope; historical recovery evidence is retained separately. |
| F19 · T22/T41/T46 | Repeatable deployment/upgrade/rollback | In progress | Staging templates/locks/read-only checks and guarded local recovery tools exist. Actual maintained same-instance upgrade/rollback and idempotent staging deploy unverified. |
| F20 · T28/T40 | Automated real end-to-end regression | Verified local VM/storage/IP/operations subset | T49 fresh-run two-tenant browser runner passed 18 recorded checks per tenant with provider reconciliation and cleanup. Separate real-browser checks verified the admin operations view (200) and tenant denial (403); API-backed Octavia acceptance verified distribution, recovery, tenant denials and quota cleanup. Broader image/object/network journeys, LB browser update/delete coverage and independent staging regression remain pending. |
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
