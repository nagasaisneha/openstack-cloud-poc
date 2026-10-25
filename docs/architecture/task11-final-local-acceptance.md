# Task 11 — final local POC acceptance

**Updated:** 2026-10-25

## Result

The approved local core POC sequence passed: real application/API lifecycle and cleanup; guest network/isolation checks; isolated recovery rehearsal; read-only restart readiness; controlled service cycle; and a guarded Ubuntu WSL distribution restart with recovery of required services and the retained VM. This is local operator evidence, not staging or production acceptance.

The restart report is `/root/openstack-poc-acceptance/wsl-restart.json`. The operator reported `POC WSL restart: PASS`; Codex independently validated its protected acceptance fields and rechecked the pinned recovery manifest and artifact hashes. It records `phase: verified`, `wsl_restart_verified: true`, and `kernel_restart_observed: true` for the retained demo `82382430-4b64-4386-a55b-8468e04da011`. The guest recovered through Nova/libvirt with required service state, addressing, KVM, metadata/network, MTU and CPU checks.

Supporting protected evidence:

- Isolated restore and recovered guest serial boot: `/root/openstack-poc-acceptance/restore-rehearsal-5zlr6r17/report.json`.
- Full read-only restart readiness: `/root/openstack-poc-acceptance/restart-readiness-7103dd5dc08d424bb7d1b0ae03d27f4b.json`.
- Controlled service cycle, retained without replay: `/root/openstack-poc-acceptance/service-cycle.json`.
- Root WSL review of saved recovery/cycle evidence and fresh cloud/guest readiness: `/root/openstack-poc-acceptance/wsl-restart-review-6fdb77f29a084b6ea7d0252fc0fffcb9.json`.
- Fresh retained-demo recovery point: `/root/openstack-poc-acceptance/recovery-82382430-4b64-4386-a55b-8468e04da011-wsl-auiv00ve`.

The historical T15–T17 predeployment blockers have been marked as initial audit observations and superseded by real local network/lifecycle acceptance. T27's scoped recovery and restart work passed. The broader backlog still identifies capabilities outside this POC's verified core path.

## Verification and boundaries

The full offline suite at the WSL restart manager checkpoint discovered **496 tests: 494 passed and 2 opt-in daemon tests were skipped**. Focused counts were 28 restart-readiness, 16 service-cycle, 8 WSL-review and 19 WSL-manager tests. Two additional private synthetic SQL/OVSDB restore round trips passed. Subsequent changes were documentation-only; `git diff --check` passed.

Full-cloud distributed rollback, offsite/bare-metal recovery, Cinder and Swift backends, integrated metering/monitoring, AI/GPU workloads and optional external services remain untested, unavailable or deferred according to their task records. Historical assets, the original demo and recovery points remain preserved.

Staging is unselected and requires separate evidence. Nothing here establishes production readiness or acceptance.

## Repository status

The recovery/restart implementation, tests and runbook are in `523e3ffc14f75bee24faa5cf7dec97b179a62241`. The additional sanitized POC implementation and task records were committed as `d07edefb0044797d09de42cfc83f22d88c9a48b0` and pushed to `origin/main`. Protected recovery artifacts and credentials remain outside Git. The published repository does not complete the conditional storage, metering, pricing, automation, AI workload, formal security, monitoring, or extended demonstration criteria listed in the task backlog.
