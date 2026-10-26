# OpenStack Cloud Management POC

## Goal

**Current release requirement (2026-10-26):** Both the final local POC and staging must run a currently maintained upstream OpenStack release. The selected technical target is **2026.2 Hibiscus / Kolla Ansible 23 / Ubuntu 24.04**. Preserve the existing Caracal installation and backups as the migration source; its acceptance results do not pass final release gates. Octavia, traffic across two real application VMs, and automatic backend exclusion/recovery are mandatory. See the [authoritative capability matrix](docs/requirements/capability-traceability.md) and [release impact/migration decision](docs/architecture/maintained-release-migration.md).

Deliver a browser-ready, secure, multi-tenant OpenStack self-service platform. Administrators manage projects, users, roles, quotas, service availability, usage, pricing, project billing, and audit history. Tenant users see and manage only their authorized account, projects, and resources. The required end-to-end acceptance runs through the browser and verifies both successful operations and backend authorization denials; an API library, fixture result, or dashboard installation alone does not complete the POC.

The default billable unit is the project. Usage, measured consumption, and calculated charges must remain distinct and source-backed. POC statements are not payment requests or tax-compliant invoices. See the [active requirements](docs/requirements/requirements-specification.md), [browser-ready goal and audit](docs/architecture/browser-ready-cloud-goal.md), and [T29–T41 delivery track](docs/tasks/README.md#browser-ready-platform-delivery-track).

## Environments and boundaries

- **Preserved source POC:** Ubuntu 22.04 / WSL2 with Caracal. Preserve the retained VM, shared services, backups and Apache/API routes. A separate supported maintained-release local target and migration validation are required before final POC acceptance; no Ubuntu upgrade or new WSL distribution is authorized.
- **Staging:** A separate environment that has not yet been selected or approved. Repeat service, security, customer/admin workflow, monitoring, recovery, and release acceptance there; do not reuse local results as staging proof.
- **Production direction:** OpenStack on organization-owned physical data-center infrastructure. No production topology, capacity, SLA, or readiness claim is established here.
- **Compute path:** Nova is selected; Proxmox is deprecated. AWS-hosted OpenStack and AWS-native tenant provisioning are outside the approved implementation scope.
- **Optional services:** Only explicitly deferred integrations are optional. The mandatory capability matrix, including Octavia, storage and metering, must pass on the maintained target; unavailable mandatory services block completion.

## POC scope and current evidence

Status labels describe the evidence recorded in task documents:

- **Verified locally:** operator or provider evidence passed for the stated local scope; this is not staging or production acceptance.
- **Partial/pending:** implementation or evidence exists, but one or more acceptance criteria remain open.
- **Blocked:** dependent work cannot complete until an upstream task or decision is resolved.
- **Staging-only:** templates, plans, or checks are prepared for a separate staging deployment; no deployment is implied.
- **Deferred/optional:** no implementation is active unless an approved workflow and supported environment require it.

| Capability | Current evidence | Status |
|---|---|---|
| Core identity, compute, images, and networking | Task 11 records real local Keystone, Glance, Placement, Nova, and Neutron lifecycle, guest networking/isolation, scoped cleanup, recovery, and WSL restart evidence. The real SDK application path has a durable local job journal; the separate fixture path is simulated. | **Verified locally**, within the recorded scope. Fresh staging provider and policy evidence is required. |
| Browser and Horizon | The later T32 record (2026-10-26) reports Horizon 24.0.1 installed. A dedicated TLS vhost serves `https://localhost:8443/horizon/` on loopback, with the global HTTP dashboard route disabled and existing API routes preserved. The local CA-validated endpoint returned a redirect. No custom management HTTP service or billing download route is deployed. | **Partial.** Windows browser CA trust and Keystone sign-in are not verified; browser/API consistency and full acceptance remain open. See [T32](docs/tasks/T32.md). |
| Customer and administrator workflows | The target flows include browser login; project/user/role/quota administration; project-scoped resource, usage, and bill views; and admin pricing and audit review. | **Blocked/pending.** Admin project/role/quota workflow [T33](docs/tasks/T33.md) and tenant resource/access workflow [T34](docs/tasks/T34.md) depend on completing browser trust and login. The two-tenant browser acceptance [T40](docs/tasks/T40.md) is blocked by T32–T39. |
| Cinder block storage | T18 records local create, attach, guest read/write, detach, snapshot, restore, and cleanup acceptance on the POC backend, including its loopback CHAP limitation. | **Verified locally for functional scope.** Browser policy/quota integration, billing meter, independent recovery, and staging backend validation remain open in [T35](docs/tasks/T35.md). |
| Object storage | T19 records two-tenant local Swift put/get/hash/delete acceptance and cross-project denial. | **Verified locally for functional scope.** Browser integration, usage attribution, backup/restore, and staging durability remain open in [T36](docs/tasks/T36.md). |
| Octavia and application ingress | Octavia, two real backend VMs, automatic health exclusion/rejoin, and controlled floating-IP ingress are mandatory. No LB service or traffic/health acceptance has passed. | **Not implemented.** T42–T44 follow the maintained-target dependency T46. Public internet publication, WAF and DDoS services remain outside the current deployment authorization. |
| Usage metering | T20 observed three project-attributed Nova allocation-hour samples. This is allocation time, not active-only compute use, guest utilization, network traffic, Cinder usage, or Swift usage. No complete Ceilometer/Gnocchi meter pipeline is verified. | **Partial.** T37 still needs a least-privilege collector, complete source coverage, pagination/freshness checks, and controlled workload reconciliation. Statements from the current Nova source remain provisional. |
| Pricing and billing | T31 implements an offline Decimal-based billing domain with project scope and immutable statement history using synthetic inputs. T38/T39 add a provisional Nova source, role-checked CSV export, and billing-action audit records. | **Partial.** No complete real-usage bill, Horizon billing route/panel, or end-to-end billing workflow is accepted. Payment collection and tax-compliant invoicing are separate and out of scope. |
| Security and audit | Offline authorization/configuration checks, local TLS configuration, and Task 11 isolation evidence exist. The billing ledger records a limited set of billing actions. | **Partial.** Browser sessions, complete live policy/security review, full login/provisioning/identity audit, audit retention/restore, and independent staging assessment remain open. See the [security validation report](docs/security-validation-report.md). |
| Monitoring and operations | Host monitoring daemons have been observed, but POC/OpenStack metrics, application logs, dashboards, and alerts have not been verified. Core local recovery and WSL restart evidence exists. | **Partial.** Source-backed dashboards/alerts, operational thresholds/runbooks, and storage/staging restore evidence remain pending. |
| Automation and staging package | Kolla dependencies/templates and read-only preflight are checked locally. Foundation templates leave extended services disabled; final scope requires the reviewed storage/Octavia/metering/monitoring overlay and release gate. | **Deployment preparation only; deployment blocked.** Supported maintained local/staging targets, owners and approvals remain unresolved; no maintained deployment has occurred. See [T45](docs/tasks/T45.md), [T46](docs/tasks/T46.md), [T22](docs/tasks/T22.md) and [T41](docs/tasks/T41.md). |
| External integrations | No tenant email/SMS/webhook notification, payment collection, public DNS, or offsite-storage integration is active. Requirements allow local logs or a development mail catcher for email until an SMTP provider is approved. | **Deferred/optional.** External credentials and destinations are not assumed. |
| GPU/AI workload | RTX 2060 evidence does not establish Nova guest passthrough, H100 or MIG support. T24 still requires a bounded real sample inference result where feasible; no such result exists. | **AI acceptance pending; H100/MIG blocked on this laptop.** Verify a licensed model/runtime and CPU feasibility before claiming a hardware limitation. |

## Staging-readiness objectives

Staging is a separate release gate, not an extension of the WSL host. The staging target, product scope, service owners, budget, and approvers remain unselected. Gate 0 has not passed; Gate 1 has a documented candidate design but is not approved; Gates 2–4 have not started; production rollout Gate 5 is not authorized; and operational handover Gate 6 has not started. The current staging candidate is Kolla Ansible / OpenStack 2026.2 on dedicated supported hosts, subject to Gate 0/1 review; it is not a deployment decision.

Before staging acceptance, the project must:

- Approve the target, operator, workload, customer roles, data classification, supported service catalog, capacity, cost limits, and reliability/recovery objectives.
- Deploy separately with reviewed, pinned automation; externalized and rotatable secrets; HTTPS; least-privilege Keystone roles; private service endpoints; and reviewed network policy.
- Independently validate admin and tenant browser journeys, quotas, Nova/Glance/Neutron lifecycle, selected Cinder and object-storage paths, cross-project denial, and truthful failure/status reporting.
- Verify real meter sources, units, project/resource attribution, freshness, completeness, reconciliation, pricing policy, provisional-data handling, and role-scoped statements.
- Validate redacted audit events, monitoring and actionable alerts, capacity, backup/restore, upgrade/rollback, incident procedures, and named operator ownership.
- Repeat end-to-end acceptance against staging-owned resources, archive sanitized provider evidence, and obtain explicit release-owner sign-off.

The [staging release plan](docs/staging-release-plan.md) defines Gates 0–6 and the POC-to-staging evidence crosswalk. The [POC completion and staging execution plan](docs/architecture/poc-completion-and-staging-execution-plan.md) records the dependency path and current blockers.

## Project documentation

- [Requirements specification](docs/requirements/requirements-specification.md) — product workflows, service scope, constraints, and acceptance criteria.
- [Browser-ready goal and audit](docs/architecture/browser-ready-cloud-goal.md) — current feature evidence, Horizon fit, target architecture, and local/staging boundary.
- [OpenStack service mapping](docs/architecture/openstack-service-mapping.md) — intended service choices and current limits, including conditional Octavia and storage.
- [Task backlog](docs/tasks/README.md) — original POC history and active T29–T41 implementation and acceptance status.
- [Staging release plan](docs/staging-release-plan.md) — release gates, rollback, ownership, and evidence crosswalk.
- [Security validation report](docs/security-validation-report.md) — security findings and evidence limits.
- [Core recovery and restart record](docs/architecture/task11-recovery-and-restart.md) — local recovery and restart evidence.
- [T32 Horizon deployment](docs/tasks/T32.md), [T35 Cinder](docs/tasks/T35.md), [T36 Swift](docs/tasks/T36.md), [T37 metering](docs/tasks/T37.md), [T38 billing](docs/tasks/T38.md), [T39 billing and audit backend](docs/tasks/T39.md), and [T41 staging](docs/tasks/T41.md) — current implementation records.

## Implementation rules

- Use OpenStack-native services where appropriate and implement one scoped task at a time.
- Test and document each task with actual evidence, limitations, and cleanup results.
- Keep verified, partial, pending, blocked, simulated, deferred, and staging-only behavior distinct.
- Enforce authorization in backend service paths; UI visibility alone is not access control.
- Keep project-level usage and billing separate from individual user identities; never store or commit credentials.
- Mark simulated, estimated, and provisional data clearly. Do not describe POC charge summaries as payment collection or tax-compliant invoices.
- Preserve existing work and avoid destructive changes. Live cloud operations, package/service changes, and resource cleanup must stay within their approved scope.
- Do not add AWS-specific APIs, SDKs, services, or infrastructure provisioning to the local POC.
- Use staging as the production release gate. Production changes require explicit scoped approval and release ownership.
- Commit verified task work locally in focused commits. Push only when explicitly requested; never stage unrelated files.

## Existing development configuration (historical T04 deliverable)

- [`config/poc.example.yaml`](config/poc.example.yaml) is a development-only template for the separate deterministic fixture adapter with real integration disabled. Copy it to ignored `config/local.yaml` for local-only settings; keep credentials and endpoint details out of tracked files. This sample file does not initiate connections.
- [`.gitignore`](.gitignore) excludes local environment files, `config/local.yaml`, logs, Python bytecode caches, and `state/`. Real manifests, journals, recovery material, and diagnostics remain in protected host directories and must not be committed.
