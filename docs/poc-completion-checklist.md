# Authoritative POC completion checklist

Updated 2026-10-27 (IST). This is the single execution/status checklist. The [capability traceability matrix](requirements/capability-traceability.md) retains requirement mappings, historical evidence and detailed acceptance criteria. Task files record implementation history; they cannot override this checklist's final acceptance boundaries.

**Final verdict: incomplete local POC; partial staging preparation; no staging deployment.** The existing Ubuntu 22.04 WSL2 cloud remains Caracal. No OS/OpenStack upgrade is authorized or started. The proposed maintained destination is Gazpacho in this same instance, with a Noble OS bridge, SLURP checkpoints and an isolated upstream Swift runtime. No second POC/host/VM is the migration target.

Status vocabulary: **Completed and verified**, **In progress**, **Blocked**, **Not implemented**, **Requires staging infrastructure**. A completed component row names its exact scope; it never completes the entire POC. Offline tests, signed metadata and synthetic application data are not live cloud acceptance.

## Current implementation milestone: recover the existing environment before upgrading

| ID | Status | Verified result / next required action |
|---|---|---|
| R01 | Completed and verified | Operator source API inventory: Keystone authenticated; one ACTIVE VM/image; Cinder and Swift responding. Octavia, external networks, routers and FIPs absent. [Evidence](evidence/recovery-progress-2026-10-26.json). This is source inventory, not a fresh full functional replay. |
| R02 | Completed and verified | Fresh **actual eight-database** backup/isolated import, including Cinder, plus three OVS/OVN restores; owned children stopped, live databases untouched. Protected operator report: `extended-database-recovery-uglbhz4c/report.json`. Source-version logical recovery only. |
| R03 | Completed and verified | Fresh **actual application job journal** backup/restore into independent private paths: `application-recovery-19hcppgg`. Committed WAL, checksum, integrity and schema checks implemented. No deployed billing-ledger recovery is claimed. |
| R04 | Completed and verified | Swift 2.33 → Epoxy 2.35.5 → Gazpacho 2.37.4 isolated **real backend-engine** object read/write and account/container checks, reverse object read, and restored file/xattr integrity. Signed source verified; all 175/176 respective wheel Python modules match signed source; maintained Gatekeeper header filters pass. Not a live Swift cutover or Keystone/network test. |
| R05 | Completed and verified | RabbitMQ/Erlang bridge package availability: documented key/signatures, 36 downloaded package hashes; three APT dependency simulations pass with removals forbidden; extracted Erlang 24/25 runtimes and crypto work. No broker was started, upgraded, reset or migrated. |
| R06 | In progress | Original checkpoint/bundle intact, guest SHUTOFF with no start intent. Approved continuation quiesced 26 services, then export preparation failed on a locked source VHDX. Ubuntu was reopened; 26 of 27 original services are active, Cinder volume remains blocked by the known loop conflict. Corrected helper requires a readable offline source before creating export artifacts; corrected worker shuts down the quiesced utility VM first. Five Windows synthetic-file tests and 18 Linux guards pass. [Failure and correction](evidence/cold-export-lock-failure-2026-10-27.json). |
| R07 | Blocked | **No backup exists:** source hash failed before export was invoked; the sole partial destination is empty and preserved. Corrected approved continuation must export to a new directory, independently verify it and resume the original checkpoint. Inspect external reports before replay. Full-image restore and same-instance OS rollback remain unverified; original reboot is not backup restoration. |
| R08 | In progress | [Service migration matrix](architecture/service-upgrade-matrix.md) maps OS/dependency/cloud checkpoints. Actual service schema hops, broker message-state migration, live Swift authentication and post-OS networking/storage must be rehearsed/accepted. R02 does not pass these migrations. |
| R09 | Blocked | Final upgrade execution approval comes **after** full recovery and remaining compatibility gates. Proposed chain: Jammy/Caracal → Noble/Caracal → Epoxy → Gazpacho; exact Swift and broker handoff in [migration plan](architecture/maintained-release-migration.md). No package/OS upgrade now. |
| R10 | In progress | Patched hash-locked SDK/Swift dependencies: 737 tests pass with two opt-in skips on each clean Python 3.10/3.12; both Swift dependency checks pass. Published advisory scan has zero unresolved findings and two narrowly dispositioned Swift backport aliases. Windows syntax/read-only/unconfirmed-export refusal pass. CI jobs implemented. Remote CI and complete release artifact acceptance are not yet evidenced. |

The next live milestone is **completion of the already approved cold export and existing-checkpoint recovery**, followed by a reviewed upgrade approval package. [Current recovery handoff](operations/existing-ubuntu-cold-recovery.md). The observed reboot does not demonstrate a completed export. Linux and Windows backups contain secrets and stay outside Git. No source backup or billing record is pruned.

## Every mandatory capability remains in scope

Statuses below refer to the **final maintained-release workflow**, with historical Caracal evidence preserved separately.

| ID / tasks | Capability | Status | Evidence boundary and next acceptance |
|---|---|---|---|
| F01 · T11/T33/T40 | Keystone users, projects, roles, isolation | Blocked | Source two-tenant API checks passed historically. Replay real admin/customer login, least privilege and backend denials after R09. |
| F02 · T32/T34 | Horizon/self-service dashboard | In progress | Source TLS dashboard endpoint installed; Windows browser CA trust, admin/customer login and complete workflow unverified. Port/retest maintained Horizon. |
| F03 · T33/T34 | Administrator/customer resource workflows | Not implemented | Complete browser identity/quota/resource/usage/bill workflows and API consistency still absent. |
| F04 · T11/T16 | Nova provision/lifecycle/KVM | Blocked | Real source acceptance and retained ACTIVE VM exist. Preserve IDs/disks/allocations and replay after every migration checkpoint. |
| F05 · T11 | Glance image management | Blocked | Source image verified historically/current API visible. Preserve hashes/payloads and validate maintained upload/select/launch. |
| F06 · T11/T15 | Neutron private networking/security groups | Blocked | Source DHCP/metadata/guest isolation passed. Revalidate addressing, OVS/OVN schemas, MTU, routing and policies after upgrade. |
| F07 · T42 | Floating IP/application ingress | Not implemented | Source has no external network/router/FIP. Need reviewed laptop client path and real VM/VIP HTTP response. No public-internet publication implied. |
| F08 · T43 | Octavia customer lifecycle | Not implemented | No catalog service/package deployment. Matching maintained OVN provider, roles/quotas and customer LB/listener/pool/member/monitor CRUD required. |
| F09 · T44 | Two real VM traffic distribution | Not implemented | Two owned app VMs, unique responses, fresh connection distribution and recorded errors required. |
| F10 · T44 | Automatic unhealthy exclusion/rejoin | Not implemented | Real app outage/recovery with monitor-driven transitions, untouched member admin state and measured traffic/convergence required. |
| F11 · T18/T35 | Cinder volumes/snapshots/storage | Blocked | Real source attach/write/read/detach/snapshot/clone/cleanup passed. Maintained replay, browser/quota policy and full-backend recovery remain open. |
| F12 · T19/T36 | Object lifecycle/isolation | Blocked | Real source two-tenant object integrity/denial/cleanup passed. R04 is component compatibility only. Maintained integrated/browser/quota/backup acceptance still required. |
| F13 · T20/T37 | Compute/storage/network metering | In progress | Three real Nova allocation-hour samples collected historically. Complete storage/network sources, least privilege, freshness, reset/duplicate handling and reconciliation absent. |
| F14 · T21/T31/T38/T39 | Configurable demo rates/billing | In progress | Decimal/ledger/CSV/auth code and synthetic-data recovery tested; no complete real usage bill or deployed customer/admin billing UI. Actual ledger location/deployment unverified. |
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
