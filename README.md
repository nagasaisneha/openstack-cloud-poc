# OpenStack Cloud Management POC

## Goal

**Execution status (2026-10-29):** The existing Ubuntu 22.04 / Caracal serves **https://localhost:8443/horizon/**. Local guest SSH/application access, two-tenant browser VM/storage/IP regression, resource-role denials, Octavia distribution/recovery and browser update/delete, monitoring/alerts, and one complete closed demo billing period are verified. The **entire POC is incomplete**: only the current ACER laptop is available, which cannot support independent full-scope staging or tenant GPU assignment. See the [authoritative completion checklist](docs/poc-completion-checklist.md), [T50 evidence/task breakdown](docs/tasks/T50.md) and [browser quickstart](docs/browser-poc-quickstart.md).

The existing backup remains unchanged. WSL restore verification is excluded from active completion/release gates; historical records remain unverified. No restore rehearsals, additional WSL distributions or local OS upgrades. Maintained-release migration records remain historical planning and do not block the existing-Ubuntu delivery.

Deliver a browser-ready, secure, multi-tenant OpenStack self-service platform. Administrators manage projects, users, roles, quotas, service availability, usage, pricing, project billing, and audit history. Tenant users see and manage only their authorized account, projects, and resources. The required end-to-end acceptance runs through the browser and verifies both successful operations and backend authorization denials; an API library, fixture result, or dashboard installation alone does not complete the POC.

The default billable unit is the project. Usage, measured consumption, and calculated charges must remain distinct and source-backed. POC statements are not payment requests or tax-compliant invoices. See the [active requirements](docs/requirements/requirements-specification.md), [browser-ready goal and audit](docs/architecture/browser-ready-cloud-goal.md), and [T29–T41 delivery track](docs/tasks/README.md#browser-ready-platform-delivery-track).

## Environments and boundaries

- **Existing local POC:** Ubuntu 22.04 / WSL2 with Caracal is the current browser-ready delivery target. Retain this instance, identities, data and verified backup. Do not perform OS upgrades or backup/restore rehearsals. Future maintained-release acceptance is a separate task.
- **Staging:** User-selected owned local hardware only; only the current ACER is available. Full staging is hardware-blocked and not deployed. Tribhuvan owns budget approval; Sneha owns release and all other responsibilities. Repeat actual release acceptance on a capable target; local results do not establish staging readiness.
- **Production direction:** OpenStack on organization-owned physical data-center infrastructure. No production topology, capacity, SLA, or readiness claim is established here.
- **Compute path:** Nova is selected; Proxmox is deprecated. AWS-hosted OpenStack and AWS-native tenant provisioning are outside the approved implementation scope.
- **Optional services:** Only explicitly deferred integrations are optional. The active completion checklist, including Octavia, storage and metering, defines required local evidence and separate staging gates; unavailable mandatory capabilities block full completion.

## POC scope and current evidence

Status labels describe the evidence recorded in task documents:

- **Verified locally:** operator or provider evidence passed for the stated local scope; this is not staging or production acceptance.
- **Partial/pending:** implementation or evidence exists, but one or more acceptance criteria remain open.
- **Blocked:** dependent work cannot complete until an upstream task or decision is resolved.
- **Staging-only:** templates, plans, or checks are prepared for a separate staging deployment; no deployment is implied.
- **Deferred/optional:** no implementation is active unless an approved workflow and supported environment require it.

| Capability | Current evidence | Remaining scope |
|---|---|---|
| Identity and browser workflows | Admin and tenant HTTPS logins, project/user/role/quota operations; two-tenant VM/storage/IP browser regression (18 checks each). | Independent staging replay and release security assessment. |
| Images, networks and objects | Private/shared image journeys with matching hashes; network/subnet/port/security-group/router/interface/gateway ownership and foreign-denial tests; private object CRUD/overwrite/metadata/isolation. New test resources removed. | Physical storage durability and target-specific policies on staging. |
| Guest and block-storage access | Pinned SSH, real guest HTTP, UUID-guarded Cinder write/read, browser attachment/snapshot/deletion; original Cinder image/PV and retained resources preserved. | Staging storage/failure acceptance. |
| Octavia and application ingress | Native browser create/update/cascade delete; two real guest backends, automatic unhealthy exclusion/rejoin, foreign denials and quota enforcement. | Actual staging provider and external networking acceptance. |
| Usage and billing | Eight source-backed meters, durable Swift mutation evidence, exact closed-period reconciliation, browser statement/download/isolation checks. One verified demo period totals INR 2.76. | Historical monthly statements remain provisional; unsupported transitions fail completeness. No payment or tax-invoice claim. See [billing policy/evidence](docs/billing-closed-periods.md). |
| Tenant quotas | Nova, Cinder, Octavia, Neutron network/router and Swift container byte/count limits reject excess requests; rejected data and original quotas preserved. | Broader staging policy review; account-wide Swift quota administration is not claimed. |
| Monitoring and audit | 45 local probes, admin dashboard/tenant denial, reversible outage alert/open/resolve and protected audit evidence. | Ninety-day retention aging and off-host delivery/archival remain pending. |
| Security | Local TLS, protected credentials and verified role/resource denials. | Target API TLS, rotation, security findings disposition and exposure review. |
| AI/GPU | Real WSL RTX 2060 CUDA training/inference passed accuracy, utilization, memory and performance criteria. | Actual tenant VM lacks NVIDIA PCI/dxg/nvidia0 or libvirt hostdev. Tenant GPU/H100-MIG acceptance requires suitable GPU compute hardware. |
| Staging preparation | Resource/network/secrets/cost/ownership proposal, executable decision validation and measured laptop inventory. | Only one 6-core/16-GiB ACER is available; independent full-scope staging is hardware-blocked, not deployed. |
| External integrations | Local logs are active; no payment, public DNS or tenant email/SMS/webhook destination is assumed. | Optional external integrations require their own approved destinations. |

## Staging-readiness objectives

Staging requires an actual independent deployment and release acceptance. The user selected owned local hardware only and confirmed that only the current ACER laptop is available. Budget approval belongs to Tribhuvan; Sneha owns release, platform, networking/storage, security, operations and billing acceptance. No purchases or rentals are approved. The [proposal](docs/staging-deployment-proposal.md) and [measured hardware inventory](docs/evidence/local-staging-hardware-20261029.json) explain the capacity, storage/failure-domain and tenant GPU blockers. Additional suitable hardware or an explicit acceptance-scope change is needed; templates and local tests do not pass staging gates.

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
