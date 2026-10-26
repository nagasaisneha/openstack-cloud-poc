# Browser-ready POC and staging execution plan

**Updated:** 2026-10-25
**Purpose:** Deliver the expanded browser-ready, multi-tenant POC goal and prepare a separate, reproducible staging release without changing or replacing the existing WSL installation.

## Active goal and current delivery path

The user-defined platform goal supersedes the prior API-focused definition of done. Task 11 remains valid core infrastructure/recovery evidence, but it does not establish Horizon, browser login, complete identity administration, real metering, billing statements, billing roles, or portal audit history. The [goal audit and target diagrams](browser-ready-cloud-goal.md) record the current requirement map and service evidence.

The active dependency path is [T29–T41](../tasks/README.md#browser-ready-platform-delivery-track):

| Phase | Tasks | Exit evidence | Current boundary |
|---|---|---|---|
| Audit and design | T29–T30 | Requirement map, Horizon feature fit, supported service catalogue, target diagrams | Complete as repository work; no deployment claim |
| Offline billing core | T31 | Decimal/effective-dated rates, project-only access, immutable/idempotent statements, focused and regression tests | Implemented with synthetic usage only |
| Browser foundation | T32 | Secure Horizon login URL; existing Apache/OpenStack/VM preservation checks | Horizon absent; Apache active on 80, no HTTPS/443 or noVNC/6080; install not run |
| Tenant administration/resources | T33–T36 | Two projects/users/quotas; compute/network/image access; Cinder volume/snapshot and Swift object workflows | T18/T19 local storage functional acceptance passed; browser policy/quota workflows and staging backends remain open |
| Usage and bills | T37–T38 | Real meters reconciled to controlled workloads; explicit prices and project statements | One-off Nova allocation-hour collection and provisional billing source tested; no complete meter feed or controlled bill |
| Browser billing/audit and acceptance | T39–T40 | Role-limited downloadable statements, audit events, two-tenant end-to-end browser demo | Backend CSV/audit/principal adapter exist; Horizon request, views, download route and full workflow remain open |
| Staging and operations | T41 | Separate deployable environment, HTTPS, secrets, monitoring, backup/restore, operator sign-off | Target/owners unselected; Kolla package is a template only |

The current read-only local inventory is in the [goal audit](browser-ready-cloud-goal.md). Core services were active at observation time; tenant inventory, quotas, VM state, and authenticated API health were not checked. Preserve the active Apache service and retained demo VM while planning any browser deployment.

## Historical core baseline (2026-10-25)

- Repository: branch `main`, `HEAD` `d07edefb0044797d09de42cfc83f22d88c9a48b0`; it matched `origin/main` and had a clean worktree at start.
- Core POC test baseline: `python3 -m unittest discover -s tests -v` — **496 tests discovered, 494 passed, 2 opt-in isolated-daemon tests skipped** (2026-10-25, Python 3.10.12).
- Existing local operator acceptance: Nova/Neutron application lifecycle and guest/network/isolation; scoped cleanup; isolated restore; restart readiness; service cycle; WSL restart. Evidence and limitations are in [Task 11 final acceptance](task11-final-local-acceptance.md).
- Current command context could not authenticate an OpenStack CLI token. This is not evidence that the previously accepted cloud is stopped; it means this invocation cannot currently perform fresh provider-backed checks. No endpoint mutation was attempted.
- Current WSL reports 7.7 GiB RAM total and about 4.0 GiB available. Docker Desktop integration is not available to this WSL command context, and direct WSL GPU access returned `GPU access blocked by the operating system`. Kolla Ansible's [quick start](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart.html) lists minimums of 8 GiB RAM, two network interfaces, and 40 GiB disk; this host is below the RAM threshold. Capacity and runtime must be rechecked immediately before any additional local service or model deployment.
- The stale publication claims in T27, the task crosswalk, Task 11 closeout, and the recovery runbook were corrected in this execution branch. Subsequent acceptance updates should preserve the actual pushed SHA.

## Historical dependency-ordered work (superseded by T29–T41)

| Order | Work | Dependencies and acceptance evidence | Environment boundary |
|---|---|---|---|
| 0 | Close repository baseline and record current acceptance | Clean `main`, commit/remote match, full core suite, corrected push records, this execution plan | Locally complete at baseline; new plan updates still require a task commit |
| 1 | Re-establish protected read-only cloud/service access and capacity | Use the existing protected cloud configuration; verify Keystone scope, catalog, service states, existing Cinder/object/metering endpoints, free memory/disk and backup freshness; never print secrets | Requires a credential-bearing but protected local shell context; current CLI token probe failed |
| 2 | T22 and staging automation foundation | Versioned Kolla Ansible inventory/config templates, secret-free environment schema, fail-closed preflight, CI and deployment runbook; hash-locked dependencies resolved and installed in a temporary Python 3.12 environment; inventory and playbook syntax passed | Package/syntax verified locally; live host preflight, target plan and deployment require an approved staging target and supported dedicated hosts |
| 3 | T18 Cinder | Confirm supported service/backend and owned test project; create, attach, read/write, detach, snapshot and delete only task-owned volume; verify no pre-existing resource changed | Requires a real endpoint and safe supported backend; do not provision LVM against an unreviewed disk |
| 4 | T19 object storage | Confirm Swift or another approved OpenStack object service; project-scoped container/object put/get/hash/delete and cleanup evidence | Requires a real service, storage path and explicit ownership; no external bucket assumed |
| 5 | T20 real metering, then T21 configurable demonstration bill | Collect provider-sourced project/resource intervals with explicit units/freshness/provenance; feed owner-configurable Decimal rate cards, currency and effective dates; output a reproducible non-invoice estimate with missing-data behavior | Requires real metering source/API and an approved example rate policy; no financial charge/payment integration |
| 6 | T25 formal security validation | Threat model, supported policy checks, positive and denied operations, project boundary tests, secret/log scanning, listener/TLS review, findings and dispositions | Code checks can run locally; cloud and network assertions require protected cloud access; penetration testing requires separate explicit scope |
| 7 | T26 monitoring, logging and alerts | Redacted structured events, health/readiness and bounded metrics for real workflow state, source-labeled dashboards/alerts, testable thresholds and runbook | Application instrumentation and local tests are local; real source/dashboard validation requires a deployment and data source |
| 8 | T24 sample AI workload | Select a small licensed model/runtime, record checksum/license, constrain memory/VRAM/time, benchmark and clean up only task-owned artifacts | RTX 2060 host/container visibility is historical evidence; WSL Docker integration currently fails and no model run is claimed. Nova passthrough/MIG is outside this hardware's capability |
| 9 | T27 extended recovery and T28 complete demonstration | Back up representative database, object, volume, application and guest state; restore into isolation; then run auth → tenant → network → image → VM → volume → object → metering/bill → health/security → cleanup; archive sanitized evidence | Isolated local restore is proven for existing scope; new service restores need actual configured services. Full-cloud/offsite/production DR remains distinct |
| 10 | Gate 0–6 decision and readiness evidence | Gate 0 scope/owners/target; Gate 1 design approval; Gate 2 foundation; Gate 3 integrations/security; Gate 4 release candidate; Gate 5 rollout authorization; Gate 6 operational handover | Gate evidence can be prepared locally; provisioning, acceptance sign-off, rollout, and operational ownership require the selected external staging environment and named approvers |

Tasks do not become complete from templates, fixtures, unit tests, design documents, or failed prerequisite probes. Each service task must retain live provider evidence and safe cleanup confirmation. Where a capability cannot be proven on this laptop, record it as blocked/unavailable and provide a separately testable staging procedure.

## Staging technical baseline

The current [OpenStack release status](https://releases.openstack.org/) identifies **2026.2 Hibiscus** as maintained. The current [Kolla Ansible 2026.2 support matrix](https://docs.openstack.org/kolla-ansible/2026.2/user/support-matrix.html) lists Ubuntu 24.04 Noble, Rocky Linux 10, and Debian 13 as deployment host choices. The [Kolla Ansible 2026.2 release notes](https://docs.openstack.org/releasenotes/kolla-ansible/2026.2.html) require Ansible Core 2.20 or 2.21. Use Ubuntu 24.04 LTS as the initial staging design candidate, subject to operator support, security lifecycle, hardware, and storage review. The Python dependencies are pinned and hash-locked in `deployment/kolla/requirements.lock`; container-image digests, source provenance, and the operator-approved component baseline still require Gate 1 review.

The existing Ubuntu 22.04 WSL2 / Caracal POC is a preserved development environment. Do not attempt an in-place upgrade to satisfy the staging baseline. The staging proposal is a separate Kolla Ansible deployment on dedicated supported hosts with separate networks, disks, credentials, inventories, and state. Decide the final control, compute, and storage topology at Gate 0/1; do not pretend a laptop or a single-node inventory demonstrates HA, production capacity, tenant isolation at scale, or failure-domain recovery.

## Gate state

- **Gate 0:** Not passed. Workload, customer population, staging target, owners, budget, security classification and service scope are not approved in repository evidence.
- **Gate 1:** Technical candidate and initial deployment approach documented; not approved. Environment-specific architecture, threat model, capacity, network, storage and operator sign-off are still required.
- **Gate 2:** Not started; requires an approved staging foundation.
- **Gate 3:** Not started; requires integration and security tests against staging-owned resources.
- **Gate 4:** Not started; requires a release candidate, end-to-end evidence, upgrade/rollback/restore exercises and named acceptance.
- **Gate 5:** Not authorized; requires explicit production change approval and rollout authority.
- **Gate 6:** Not started; requires operational ownership, SLO/alert review, incident process and scheduled lifecycle reviews.

## Change log

- 2026-10-25: Captured repository and full offline-suite baseline; reconciled stale Git publication statements; recorded current CLI authentication and WSL resource limitations; selected maintained OpenStack 2026.2 / Kolla Ansible as a staging design candidate, not a deployment authorization. Added pinned/hash-locked Kolla dependencies and passed inventory/playbook syntax checks in a temporary Python 3.12 environment.
