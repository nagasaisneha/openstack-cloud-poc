# OpenStack Cloud Management POC

## Goal

**Execution status (2026-10-27):** The **existing Ubuntu 22.04 / Caracal** serves **https://localhost:8443/horizon/**. Cinder's loop ownership is repaired; the retained VM and 27 baseline services are healthy. Real admin/two-tenant logins, project/user creation, private networks, volumes, objects, tiny VMs, HTTPS consoles, scoped usage, demo rates and provisional statement downloads are verified in [T48](docs/tasks/T48.md). Start with the [browser quickstart](docs/browser-poc-quickstart.md). The verified Windows export remains unchanged. Backup/restore rehearsals and OS upgrades are stopped; full WSL restore remains **unverified** and is not a development gate.

**Future release planning (2026-10-26; not the current delivery gate):** The maintained-release migration and separate staging plans remain historical design work. No OS/OpenStack upgrade or additional WSL distribution is authorized. They must not block the current browser-ready implementation on Caracal. The prior proposal and its separate acceptance limits remain in the [migration plan](docs/architecture/maintained-release-migration.md) and [capability matrix](docs/requirements/capability-traceability.md).

Deliver a browser-ready, secure, multi-tenant OpenStack self-service platform. Administrators manage projects, users, roles, quotas, service availability, usage, pricing, project billing, and audit history. Tenant users see and manage only their authorized account, projects, and resources. The required end-to-end acceptance runs through the browser and verifies both successful operations and backend authorization denials; an API library, fixture result, or dashboard installation alone does not complete the POC.

The default billable unit is the project. Usage, measured consumption, and calculated charges must remain distinct and source-backed. POC statements are not payment requests or tax-compliant invoices. See the [active requirements](docs/requirements/requirements-specification.md), [browser-ready goal and audit](docs/architecture/browser-ready-cloud-goal.md), and [T29–T41 delivery track](docs/tasks/README.md#browser-ready-platform-delivery-track).

## Environments and boundaries

- **Existing local POC:** Ubuntu 22.04 / WSL2 with Caracal is the current browser-ready delivery target. Retain this instance, identities, data and verified backup. Do not perform OS upgrades or backup/restore rehearsals. Future maintained-release acceptance is a separate task.
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
| Browser and Horizon | Horizon 24.0.1 has trusted Windows HTTPS, admin/two-tenant Chromium logins and deployed Usage & Billing panels/downloads. Windows TLS validation and loopback forwarding passed; Windows browser UI automation is separate. | **Verified locally for the T48 journeys.** Broader capability acceptance remains tracked separately. |
| Customer and administrator workflows | Two projects/users and each tenant's network, subnet, imported public key, volume, private container/object and tiny VM were created through Horizon. Both interactive HTTPS/WSS consoles connected; provider and billing cross-project denials passed. | **Verified locally for those journeys.** See [T48](docs/tasks/T48.md); broader lifecycle, LB and quota cases remain in [T40](docs/tasks/T40.md). |
| Cinder block storage | Original image/PV preserved on an identity-selected loop. New browser-created 1 GiB volumes became available. Reader writes are denied after activating registered member/reader policies. Historical T18 covers attach/read-write/snapshot scope. | **Verified locally for repair, creation and tested policy boundaries.** Browser attachment/snapshot lifecycle and storage-hour billing are not newly accepted. Full restore remains unverified. |
| Object storage | Both tenants created private containers and uploaded/downloaded 40-byte objects through Horizon. Provider cross-project access was denied and current usage was collected. | **Verified locally for those workflows.** Broader lifecycle/durability acceptance remains in [T36](docs/tasks/T36.md); restore rehearsals are stopped. |
| Octavia and application ingress | Octavia, two real backend VMs, automatic health exclusion/rejoin, and controlled floating-IP ingress are mandatory. No LB service or traffic/health acceptance has passed. | **Not implemented.** T42–T44 follow the maintained-target dependency T46. Public internet publication, WAF and DDoS services remain outside the current deployment authorization. |
| Usage metering | A scoped reader collects Nova allocation and current Cinder/Swift inventory every minute into immutable history. Tenant refresh and provider attribution passed; cumulative snapshots are never summed. | **Partial source coverage.** Active-only compute intervals, network traffic and storage-hour rating remain unavailable. No complete Ceilometer/Gnocchi pipeline is claimed. |
| Pricing and billing | Real browser-entered immutable demo rates produce three priced allocation lines per project. Provisional statements, tenant CSV/JSON downloads, foreign-statement denial and admin billing audit are deployed and verified. | **Verified local demonstration; incomplete billing coverage.** Current statements have no complete total. Payment collection and tax-compliant invoicing are out of scope. |
| Security and audit | Offline authorization/configuration checks, local TLS configuration, and Task 11 isolation evidence exist. The billing ledger records a limited set of billing actions. | **Partial.** Browser sessions, complete live policy/security review, full login/provisioning/identity audit, audit retention/restore, and independent staging assessment remain open. See the [security validation report](docs/security-validation-report.md). |
| Monitoring and operations | Host monitoring daemons have been observed, but POC/OpenStack metrics, application logs, dashboards, and alerts have not been verified. Core local recovery and WSL restart evidence exists. | **Partial.** Source-backed dashboards/alerts, operational thresholds/runbooks, and storage/staging restore evidence remain pending. |
| Automation and staging package | Local guarded installers and Kolla staging preparation exist. Foundation templates leave extended services disabled. | **Separate staging preparation.** Staging target/owner approvals remain unresolved. Local upgrades and extra distributions are prohibited for this delivery and do not gate T48. No staging deployment occurred. |
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
