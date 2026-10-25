# OpenStack Cloud Management POC

## Goal
Continue the original local OpenStack management POC in the Windows/WSL2 development environment. Use its requirements, architecture, implementation, and acceptance evidence as inputs to the separate staging release plan. Local POC success does not establish staging or production readiness.

## Environment
Ubuntu on WSL2 is the local development and POC environment. The staging environment has not been selected or approved. The long-term production direction remains OpenStack on organization-owned data-center infrastructure.

## Architecture and scope
The local POC uses OpenStack management workflows and Nova as the selected tenant compute path; Proxmox is deprecated. The [latest operational evidence](docs/architecture/task11-operations-and-closeout.md) records operator-verified real Keystone/Glance/Nova/Neutron integration, member-scoped application lifecycle with durable SQLite jobs, guest networking and bounded isolation checks, temporary cleanup and local hardening. The separate fixture path remains simulated. The [staging release plan](docs/staging-release-plan.md) maps POC evidence to capabilities that must be revalidated in staging. AWS-hosted OpenStack and AWS-native EC2 provisioning are outside the approved implementation scope. The deferred [Option A technical reference design](docs/architecture/option-a-technical-reference-design.md) and [Gate 1 feasibility package](docs/architecture/option-a-gate1-feasibility-package.md) are retained for traceability only.

The [restore/restart sequence](docs/architecture/task11-recovery-and-restart.md) passed, including isolated restore, readiness, service cycle and WSL distribution restart. Codex independently validated the protected restart report and recovery hashes. Core local functional acceptance passed. The focused implementation/tests/runbook commit is `523e3ffc14f75bee24faa5cf7dec97b179a62241`; the remaining task-crosswalk and related implementation changes are recorded in subsequent local commits. GitHub publication is pending. Deferred capabilities and staging/production acceptance remain separate.

## Staging and production delivery
[`docs/staging-release-plan.md`](docs/staging-release-plan.md) defines how the local POC informs a distinct staging validation, followed by controlled production rollout. It links the original requirements, architecture, implementation task IDs, and acceptance evidence, and identifies where local results are insufficient.

## Implementation rules
- Use OpenStack-native services where appropriate.
- Implement one task at a time.
- Test and document every task.
- Commit and push every verified task to GitHub.
- Preserve existing work and avoid destructive changes.
- Do not treat the local WSL2 environment or fixture results as production evidence.
- Do not add AWS-specific APIs, SDKs, services, or infrastructure provisioning to the local POC; AWS-hosted OpenStack and AWS-native tenant provisioning are outside current implementation scope.
- Use staging as the production release gate; production changes require explicit scoped approval.

## Existing development configuration (historical T04 deliverable)

- [`config/poc.example.yaml`](config/poc.example.yaml) is a development-only template for the separate deterministic fixture adapter with real integration disabled. Copy it to ignored `config/local.yaml` for local-only settings; keep credentials and endpoint details out of tracked files. The real application uses protected operator configuration described in the operational record; this sample file does not initiate connections.
- [`.gitignore`](.gitignore) excludes local environment files, `config/local.yaml`, logs, Python bytecode caches and `state/`. Real manifests, journals, recovery material and diagnostics remain in protected host directories and must not be committed.
