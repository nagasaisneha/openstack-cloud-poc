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

The next milestone is **admin and tenant browser login, user/project management, self-service resources, VM access, usage and billing**, tracked in [T48](tasks/T48.md). Backup and restore rehearsals are stopped. Keep full restore verification unverified without using it as a blocker for these features. Protected credentials, runtime records and the unchanged backup remain outside Git.

## Every mandatory capability remains in scope

The service-specific backlog below retains future maintained-release acceptance where stated. Those migration/staging checks are separate from the current Caracal browser workflow and cannot block it. Current delivery evidence is recorded in T48.

| ID / tasks | Capability | Status | Evidence boundary and next acceptance |
|---|---|---|---|
| F01 · T11/T33/T40 | Keystone users, projects, roles, isolation | In progress | Source two-tenant API checks passed historically. Verify live admin/customer login, least privilege and backend denials in the existing Ubuntu; no upgrade dependency. |
| F02 · T32/T34 | Horizon/self-service dashboard | In progress | Horizon TLS, trusted Windows HTTPS and admin/two-tenant Chromium login verified. Full self-service/resource acceptance continues on Caracal. |
| F03 · T33/T34 | Administrator/customer resource workflows | In progress | Two project/user creations through Horizon verified; bounded quotas and billing roles configured. Resource, quota-edit and billing acceptance continue. |
| F04 · T11/T16 | Nova provision/lifecycle/KVM | In progress | Retained VM ACTIVE/KVM/SSH verified after live service repair. Verify tenant browser create/access without replacing existing resources. |
| F05 · T11 | Glance image management | In progress | Preserve existing image/payloads; verify project-visible browser image selection and launch on Caracal. |
| F06 · T11/T15 | Neutron private networking/security groups | In progress | Retained guest addressing/MTU/ping verified. Tenant browser networking and backend isolation acceptance remain. |
| F07 · T42 | Floating IP/application ingress | Not implemented | Source has no external network/router/FIP. Need reviewed laptop client path and real VM/VIP HTTP response. No public-internet publication implied. |
| F08 · T43 | Octavia customer lifecycle | Not implemented | No catalog service/package deployment. Matching maintained OVN provider, roles/quotas and customer LB/listener/pool/member/monitor CRUD required. |
| F09 · T44 | Two real VM traffic distribution | Not implemented | Two owned app VMs, unique responses, fresh connection distribution and recorded errors required. |
| F10 · T44 | Automatic unhealthy exclusion/rejoin | Not implemented | Real app outage/recovery with monitor-driven transitions, untouched member admin state and measured traffic/convergence required. |
| F11 · T18/T35 | Cinder volumes/snapshots/storage | In progress | Existing Cinder image/PV preserved and volume service repaired. Verify browser creation/quotas and usage. Full-backend restore remains unverified and is not a development gate. |
| F12 · T19/T36 | Object lifecycle/isolation | In progress | Swift is healthy with its original image. Verify browser object workflow, project isolation and usage; restore rehearsal is stopped. |
| F13 · T20/T37 | Compute/storage/network metering | In progress | Scoped one-minute collector and immutable history deployed. Nova allocation and current Cinder/Swift observations verified; active-only/network meters and storage-hour rating remain unavailable. |
| F14 · T21/T31/T38/T39 | Configurable demo rates/billing | In progress | Protected ledger and Horizon panels deployed; logins and scope denials verified. Browser rate-entry/statement/download acceptance continues. Incomplete coverage is provisional. |
| F15 · T33/T35/T36/T43 | Tenant quotas and limits | In progress | Full positive/over-quota and cross-tenant/admin denial workflow across compute/network/storage/LB remains unverified. |
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
