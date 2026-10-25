# Implementation task backlog

**Current goal update (2026-10-25):** The user expanded the definition of done to a browser-ready, secure, multi-tenant self-service platform with administration, project-level usage/billing, and auditability. The Task 11 core-cloud acceptance remains useful baseline evidence, but it is **not** final platform acceptance. Horizon, web login, complete project administration, real metering, pricing, bill statements, billing visibility roles, and application audit history remain open. See the [goal audit](../architecture/browser-ready-cloud-goal.md). Historical statements that the original API-focused POC was complete are preserved as dated evidence and do not override this new goal.

**Status: Original T01–T28 implementation history plus active T29–T41 browser-platform delivery.** This file preserves original requirement mappings and evidence, and adds the current goal's dependency-ordered tasks. Staging is separate; local results do not pass staging or production gates. AWS provisioning, AWS-hosted OpenStack, and AWS-native tenant provisioning remain outside scope.

This preserves the original 28-task breakdown and adds the active 13-task browser-platform track below. Coverage labels in the older T01–T28 crosswalk describe traceability to the original requirements only. They do not mean the new goal is accepted. The supplied architecture image is preserved at its actual path, [`architecture-diagram.png.jpeg`](../architecture/architecture-diagram.png.jpeg); the current target and its deviations are documented in the [goal audit](../architecture/browser-ready-cloud-goal.md).

## Required workflow for every approved task

1. For this user-authorized goal, repository edits, offline tests, documentation, and local Git commits are in scope. Inspect `git status` and preserve unrelated work. Live cloud access/mutations, system package installation, service reconfiguration/restart, port exposure, and remote pushes remain separately scoped actions.
2. Implement only the task scope. Run its listed tests and record actual results and whether the capability is real, simulated, or unavailable.
3. Update relevant documentation with results, limitations, cleanup, and decisions.
4. Review the complete diff, including staged changes, for secrets and unrelated changes. Do not stage credentials, tokens, generated state, or unrelated existing files.
5. After verification passes, create a focused local commit for reviewed task-owned files and record its full SHA. Push only when the user explicitly requests it. Never include credentials, runtime state, or protected recovery artifacts.
6. Do not proceed to another task until its required record is complete.

### Initial Git state and first-commit procedure

The initial read-only inspection before the POC implementation found branch `main` with no `HEAD` commit, no configured remote, zero tracked files, and existing untracked `README.md` and `docs/`. That state is historical. At the 2026-10-25 closeout, branch `main` had a clean worktree and sanitized commits through `d07edefb0044797d09de42cfc83f22d88c9a48b0` pushed to `origin/main`. Remote credentials are not recorded here. See the [execution plan](../architecture/poc-completion-and-staging-execution-plan.md) for the next task sequence and new verification status.

For each future task, preserve the existing branch and remote, stage only reviewed task-owned paths, scan for secrets and generated state, run the required verification, and record the exact commit and push results. Never rewrite history or infer authorization for external infrastructure from repository publication.

### Cloud and system mutation approval gate

The current user request authorizes implementation of the browser-ready goal and its acceptance workflow, but it does not identify a deployment target, service change window, package/service impact, test quota/cost ceiling, or disposable cloud resource IDs. Before changing the existing host/OpenStack installation, contacting an authenticated endpoint, creating test resources, or deleting anything, record the exact action, affected scope, expected capacity/cost, preservation checks, and cleanup plan, then obtain any missing task-specific approval. Never delete pre-existing resources.

## Browser-ready platform delivery track

The detailed acceptance map and live-state limits are in the [goal audit](../architecture/browser-ready-cloud-goal.md). Individual records define measurable acceptance, dependencies, scope, and blockers.

| Order | Task | Depends on | Acceptance evidence | Status |
|---|---|---|---|---|
| 29 | [Repository and local installation audit](T29.md) | None | Feature-by-feature evidence map; exact repo/runtime observations; unknown service state clearly marked | Complete (read-only; runtime visibility limited) |
| 30 | [Horizon fit, service catalogue, and target architecture](T30.md) | T29 | Native Horizon versus custom billing/audit work mapped; service catalogue and architecture flows documented | Complete (target design; not deployed) |
| 31 | [Billing domain and immutable statement model](T31.md) | T29–T30 | Decimal pricing contract, effective-dated rates, project aggregation, immutable statement snapshots, denied cross-project reads, synthetic provenance | Complete (offline domain; no real usage/UI) |
| 32 | [Horizon deployment and secure browser login](T32.md) | T30 | Verified browser URL; TLS/session/CSRF configuration; admin and tenant logins; existing Apache/OpenStack paths preserved | Blocked for live deployment; loopback TLS templates prepared, Horizon absent and cloud auth unavailable |
| 33 | [Keystone administration, project roles, and quotas](T33.md) | T32 | Admin creates two projects/users, assigns least-privilege roles and quotas; backend accepts intended actions and denies unauthorized ones | Blocked by T32 |
| 34 | [Tenant compute, image, network, and access workflow](T34.md) | T32–T33 | Project-scoped instance/key/image/network/security-group lifecycle, truthful job errors/status, verified console or SSH path | Blocked by T32–T33 |
| 35 | [Cinder volumes and snapshots](T35.md) | T33–T34; supported backend | Create/attach/read-write/detach/snapshot/restore and cleanup verified, or service explicitly unavailable | Blocked; no verified local backend |
| 36 | [Swift object storage](T36.md) | T33; supported service | Project-scoped object put/get/hash/delete and cleanup verified, or service explicitly unavailable | Blocked; no verified local service |
| 37 | [Real usage metering and attribution](T37.md) | T33–T36 as applicable | Source-backed intervals, units, project/resource lineage, freshness/completeness and controlled workload reconciliation | Blocked; no verified meter source |
| 38 | [Rate administration and project billing statements](T38.md) | T31, T37 | Admin configures versioned rates; complete real samples produce reproducible project bills; deletion preserves history; incomplete data stays provisional | Partial; offline domain complete, T37 pending |
| 39 | [Horizon billing and audit experience](T39.md) | T32–T33, T37–T38 | Role-scoped usage/pricing/charges/downloads; admin views all projects; action audit is retained and redacted | Blocked by T32/T37 |
| 40 | [Two-tenant browser acceptance and security verification](T40.md) | T32–T39 | Full requested browser workflow; positive/negative project tests, quotas, usage, bills, admin comparison, cleanup evidence | Blocked by T32–T39 |
| 41 | [Reproducible staging and operations](T41.md) | T30–T40 | Separately deployable configuration, HTTPS, secret management, monitoring, backup/restore, troubleshooting, and staging evidence | Partial; target and approvals unselected |

T29 and T30 are complete as audit/design work. T31's offline billing domain and focused tests are complete using synthetic samples. No real meter or UI integration is claimed; T32 and later live acceptance remain separate.

## Original requirement coverage

The IDs below are the original identifiers in the requirements specification. They remain unchanged; the T-IDs are implementation slices. The requirements' acceptance criteria and safety constraints remain authoritative. The image reference uses the actual supplied path `docs/architecture/architecture-diagram.png.jpeg`; the requirements' intended `architecture-diagram.jpeg` path does not match that actual filename.

Status definitions: **Covered** means the planned tasks address all stated clauses; **Partially covered** means a clause is blocked by an unresolved decision or unavailable evidence; **Deferred** means the requirements explicitly make it optional/out of scope; **Missing** means no task or explicit disposition exists. These statuses do not imply execution is complete.

| Original requirement | Task IDs | Status | Coverage note / reason |
|---|---|---|---|
| POC-001 — Read-only laptop and virtualization audit | T01, T02 | Covered | Includes Windows/WSL context where available, OS, CPU/RAM/disk/GPU/driver, KVM, Docker, Git, listening ports, and resource constraints; missing evidence is labelled unknown. |
| POC-002 — Git/GitHub audit and project baseline | T01, T02 | Covered for repository publication | Branch/history and remote inventory are recorded. Sanitized local commits are pushed to `origin/main`; secret-bearing files and runtime recovery artifacts remain excluded. |
| POC-003 — OpenStack availability and deployment inventory | T02, T07 | Covered | Local package/container/service/config and endpoint-presence checks plus approved read-only API inventory; credential presence may be recorded, values are never printed, and no resources are changed. |
| POC-004 — OpenStack and VM-launch feasibility | T02, T03, T06, T07, T16, T17 | Covered for the local POC | Real local OpenStack integration and Nova/KVM VM launch passed. The initial deployment gate is historical; staging support and remote endpoint feasibility require separate evidence. |
| POC-005 — Keystone project and role validation | T06, T11, T25 | Covered | Project/user/role scope and authorized/unauthorized cases; creating identities requires explicit approval. |
| POC-006 — Glance image workflow | T07, T16 | Covered | Read image metadata; trusted test image use only with approval in a VM test. |
| POC-007 — Neutron network workflow | T07, T15, T17 | Covered | Inspect networks/subnets/routers/security groups/floating IPs, prepare an approved path, then test guest connectivity without disrupting existing networks. |
| POC-008 — Nova instance lifecycle | T16, T17 | Covered | Small instance create/inspect/start/stop/delete, recorded states, authorized cleanup. |
| POC-009 — Horizon and API consistency | T07, T32 | Partially covered | Historical core APIs exist; Horizon is absent and browser/API consistency has not been tested. |
| POC-010 — Flavors and quotas | T08 | Covered | Inspect actual capacity; change only safe, explicitly approved values. |
| POC-011 — Repeatable VM lifecycle workflow | T16, T17 | Covered | Repeat runs and failure reporting are tested in the repeatable workflow. |
| POC-012 — Cinder storage workflow | T18, T35 | Partially covered | No verified local Cinder backend; staging remains disabled until an approved backend passes volume and recovery tests. |
| POC-013 — Network and security acceptance tests | T15, T17, T25 | Covered | Positive/negative connectivity, security groups, SSH, routing and floating IPs where available; exposed ports/auditability in T25. |
| POC-014 — Resource catalog | T08, T10 | Covered | Catalog uses observed images/flavors/networks/storage and labels unavailable items. |
| POC-015 — Management API design and skeleton | T12 | Covered | Contract, auth, schemas, errors, and tests precede implementation. |
| POC-016 — OpenStack SDK integration | T13 | Covered | Scoped read/provision operations; cloud mutations need explicit task-specific approval. |
| POC-017 — Job tracking | T14, T16, Task 11 | Covered for the accepted local application path | The real SDK application records durable job/phase/idempotency state and observed Nova provider results. The separate fixture service remains simulated; this does not claim a general distributed worker platform or staging behavior. |
| POC-018 — Usage data collection | T20, T37 | Partially covered | Fixture samples preserve project, unit and provenance; no real meter is installed or verified. |
| POC-019 — Monitoring and dashboards | T26, T41 | Partially covered | PCP host load/memory metrics and units are verified; retention remains unknown, and no POC/OpenStack metric source, dashboard, alert or application logging was validated. |
| POC-020 — Usage reporting and automation | T21, T22, T31, T38 | Partially covered | Fixture summaries, a configurable offline billing domain and staging automation package exist; no real meter-backed demonstration bill has passed. |
| POC-021 — GPU feasibility assessment | T23 | Covered | Separates RTX host/container checks from Nova passthrough; no MIG assumption. |
| POC-022 — Sample AI workload | T24 | Partially covered | T23 feasibility only; no approved/pinned model or inference result, and guest GPU path remains unverified. See [T24](T24.md). |
| POC-023 — Security validation | T11, T25, T26 | Partially covered | Offline authorization and configuration-hardening tests pass; live TLS, firewall/port, browser session, audit and staging security checks remain pending in [T25](T25.md) and the [security report](../security-validation-report.md). |
| POC-024 — Backup and recovery test | T27 | Covered | Backup plus restore evidence or a specific limitation. |
| POC-025 — End-to-end demonstration | T28, T40 | Partially covered | The prior real Nova/Neutron core acceptance passed; the required browser, storage, metering, billing, monitoring and audit workflow remains open. |

### Remaining requirement crosswalk

| Requirement group | Task IDs | Status | Reason / disposition |
|---|---|---|---|
| Project goal, source-file preservation, small tasks and no production claims (§1–2) | T01–T03, T28 | Covered | Audit and mapping retain the actual image path; production/H100 equivalence is excluded. |
| Layer 1 — Keystone, required Horizon/browser portal, CLI/SDK, honest billing | T06–T14, T21, T28; active T32–T40 | Partially covered | Keystone is the OpenStack identity source, not a full Keycloak equivalent; mobile is deferred. The original portal-optional scope is superseded; browser login, billing UI, and complete audit remain open. |
| Layer 2 — local security controls and TLS | T07, T15, T25, T32 | Partially covered | Offline policy/configuration tests exist; live TLS, listener, firewall and browser-session review remain pending. |
| Layer 2 — Cloudflare/DDoS, WAF, separate gateway, Octavia | T28 | Deferred | Requirements make these optional; no public ingress or external protection is assumed. Reconsider only if a required workflow and supported deployment justify it. |
| Layer 3 — catalog, provisioning, jobs, database/queue restraint, metering, demo price/payment | T08–T14, T20–T22 | Partially covered | PostgreSQL/Redis/Celery remain conditional, not defaults. Local asynchronous lifecycle jobs are integrated with fixture execution, but state/idempotency are process-local and no real provider operation is tracked. Metering and pricing remain subject to their existing source and limitation requirements. |
| Layer 4 — Nova/Glance/Neutron/Cinder and repeatable orchestration | T07–T19, T22 | Partially covered | Real local Nova/Glance/Neutron lifecycle and isolation passed. Cinder remains unavailable; staging must independently validate selected services and topology. |
| Layer 4 — Kubernetes/JupyterHub/Harbor/Ceph, GPU scheduler/MIG | T22–T24, T28 | Deferred | Requirements make these optional and this laptop does not support H100 MIG or production multi-tenant GPU scheduling. |
| Layer 5 — CPU VM, AI workload, block/object storage | T16–T19, T23–T24, T35–T36 | Partially covered | Core CPU VM is verified; no AI inference result or Cinder/Swift round-trip is verified. |
| Layer 5 — H100/MIG profiles, GPU isolation, managed academy stack | T23–T24, T28 | Deferred | H100/MIG and multi-tenant GPU isolation are unsupported; JupyterHub/academy environments are optional follow-on work. |
| Layer 6 — laptop/network record; no physical data center build | T01–T03 | Covered | Laptop/network evidence is recorded; H100 server, future servers, facility and power/cooling construction are explicitly out of scope. |
| Layer 7 — verified monitoring, automation, backup/restore, logging and alerts | T14, T22, T26–T27, T41 | Partially covered | Staging automation and scoped recovery exist; live monitoring/logging/alerts and production incident response remain pending. |
| Layer 8 — payment, DNS, email, offsite storage | T04, T21, T26, T28 | Deferred | External integrations are not active by assumption; no payments or uploads without separate approval; missing credentials must fail safely; local logs/mail catcher or test hostnames remain optional. |
| Cross-cutting requirements 1, 2, 4, 5, 7–10 (§4) | T04, T06, T10–T17, T25–T28 | Covered | Retains the prior planned coverage status for identity, resource lifecycle, safe errors, idempotency, documentation, resource safety, configuration, and security; this row excludes provisioning status and observability below. |
| Cross-cutting requirement 3 — Provisioning status (§4) | T10, T14, T16–T17 | Partially covered | Local fixture create/start/stop/delete jobs are tied to background execution and instance state; real long-running OpenStack operations and durable status remain unverified. |
| Cross-cutting requirement 6 — Observability (§4) | T10, T14, T16–T17, T26 | Partially covered | Simulated jobs record IDs, project/actor, state, timestamps, outcomes, and fixture instance association. Real provider operation traceability and production observability remain pending. |
| Non-functional requirements (§5) | T01–T03, T28 | Covered | WSL2 support, read-only initial audit, preservation, small tasks, reproducible evidence, compatibility, no production claims, and real/simulated distinction. |
| Suggested repository structure (§9) | T01, T04 | Covered | Adapt the suggested structure to existing paths; do not duplicate files or stage unrelated untracked content. |
| Final acceptance criteria (§10) | T01, T11, T16–T28, T32–T40 | Partially covered | Core VM/network/recovery evidence passes; browser, storage, metering, billing, monitoring, audit and full end-to-end gates remain open. |
| Task-record fields and Git/GitHub workflow (§7–8) | All T01–T28 | Partially covered | The backlog requires per-POC records with required fields and commit/push evidence. Repository publication is complete through `d07edef`; extended task execution records and evidence remain pending for T18–T28 as applicable. |
| Requested repository deliverables and architecture-layer classifications (§11) | T01 | Partially covered | T01 includes `local-poc-architecture.md`; the file and individual POC execution records do not yet exist. |

Before implementation, create a Markdown record for every executed T-ID (for example `docs/tasks/T01.md`) containing the required ID/title, objective/scope, mapped original requirement IDs, dependencies, prerequisites, files/resources affected, implementation plan, exact test commands, acceptance criteria, rollback/cleanup, risks/limitations, status (`Planned`, `In Progress`, `Blocked`, `Failed`, or `Complete`), verification evidence, commit SHA, and GitHub push status. Keep original POC IDs unchanged in each record and use the matrix above to trace them to one or more T-IDs. The backlog entries below are the dependency plan, not substitutes for execution records.

## Tasks

### T01 — Baseline audit and planning documentation

- **Prerequisites:** Read the requirements and actual diagram; inspect Git status.
- **Dependencies:** None.
- **Implementation steps:** Record environment evidence, capability limits, architecture mapping, source discrepancies, and ordered task plan. Create the requirements-requested `docs/architecture/local-poc-architecture.md` with per-layer classifications. Preserve existing files.
- **Tests:** Check factual consistency, secret-free content, relative Markdown links, and `git diff --check`; inspect `git status`.
- **Acceptance criteria:** Audit, mapping, feasibility, gap record, local POC architecture, and dependency-ordered backlog exist; tested capabilities are separated from unverified ones; no system/cloud mutation occurred.
- **Rollback/cleanup:** Remove only task-created documentation if rejected; preserve pre-existing files.
- **Git:** If this task is approved for repository integration, follow the required workflow above; record the SHA and push result. Do not commit/push before approval.

### T02 — Read-only capacity, Git, and installed-service discovery

- **Prerequisites:** Approval for read-only discovery; no install or configuration.
- **Dependencies:** T01.
- **Implementation steps:** Record timestamped disk capacity; determine Docker resource allocation or document it unknown; inspect local Git state, installed OpenStack packages/services, listening ports, and configuration-path presence without endpoint calls or secret values.
- **Exact read-only checks:** Run `date --iso-8601=seconds` and `df -h /` and record both output and timestamp. Inspect Docker Desktop **Settings → Resources** manually without applying changes; on WSL2, inspect an existing `%UserProfile%\.wslconfig` read-only if present. Optionally run `docker info --format 'Docker daemon CPUs={{.NCPU}} MemoryBytes={{.MemTotal}}'` only if the local daemon is reachable, and treat permission errors or zero fields as unknown. For local packages/services/processes/ports, use `dpkg-query -W -f='${binary:Package}\t${Version}\n' | rg -i 'openstack|nova|neutron|keystone|glance|cinder|heat|placement|horizon|kolla|microstack|devstack'`, `systemctl list-units --all --type=service --no-legend | rg -i 'openstack|nova|neutron|keystone|glance|cinder|heat|placement|horizon|kolla|microstack|devstack'`, `docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'` only if the local socket is available, `ps -eo comm | rg -i 'nova|neutron|keystone|glance|cinder|heat|openstack'`, and `ss -lntu`. Record unavailable commands/services without attempting installation or endpoint probes. For Git, inspect `git status --short --branch`, `git branch --show-current`, `git remote` (names only), `git ls-files`, `.gitignore` presence, and `git check-ignore -v` for intended task paths; do not print remote URLs. For config presence, use `test -e`/`stat` only on `~/.config/openstack/clouds.yaml`, `~/.config/openstack/secure.yaml`, `/etc/openstack`, `/etc/kolla/globals.yml`, `/etc/nova/nova.conf`, `/etc/neutron/neutron.conf`, `/etc/glance/glance-api.conf`, `/etc/cinder/cinder.conf`, `/etc/keystone/keystone.conf`, and `/etc/heat/heat.conf`. Never `cat` credential files, print environment-variable values, or include token/password fields in output. If service inventory needs cloud API calls, defer those calls to T07.
- **Tests:** Repeat safe version/capability checks; verify no state changes and redact endpoint/user details. Treat push permission as unknown if it cannot be tested safely/read-only. Treat Docker daemon output as unknown when the socket is inaccessible.
- **Acceptance criteria:** Disk/runtime limits, Git baseline, existing-service inventory, local endpoint/config presence (not reachability), and push status (confirmed/unavailable/unknown) are documented without secrets.
- **Rollback/cleanup:** None; read-only. Remove only temporary diagnostic outputs created by this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T03 — Select management-layer-first and fixture-backed route

- **Prerequisites:** T02 local discovery findings and the user's explicit scope decision: OpenStack management POC first, deterministic fixtures first, VM-manager-independent adapter boundary, Proxmox deferred unless explicitly approved.
- **Dependencies:** T02.
- **Implementation steps:** Record the selected management-layer-first route in `architecture-gaps.md`; define a deterministic fixture adapter for early development whose contract does not depend on Nova or Proxmox; preserve POC-008 Nova lifecycle as a later real integration behind the same boundary; defer Proxmox integration unless explicitly approved. State that real OpenStack integration requires a verified endpoint or a separately approved deployment plan, followed by task-specific approval for endpoint contact or mutations. Define the fallback as fixture-backed responses clearly marked simulated, with unsupported/unverified capabilities reported honestly. Keep VM lifecycle, endpoint authentication, and service health outside T03 execution.
- **Tests:** Review wording against the diagram's “Proxmox VE (initial)” / “OpenStack Nova (future)” labels and requirements §3 Layer 4 / POC-008; verify the adapter boundary is manager-independent, dependencies remain acyclic, and no step authorizes endpoint contact or cloud mutation.
- **Acceptance criteria:** The documentation records OpenStack management POC first and deterministic fixtures first; explains that the Proxmox diagram stage is deferred by scope without rewriting it; retains Nova lifecycle as conditional future integration; specifies verified endpoint or separately approved deployment plan as the gate for real OpenStack integration; names fixtures as the fallback and marks them simulated; all cloud operations remain behind explicit task-specific approval.
- **Rollback/cleanup:** Documentation-only task; restore only T03-edited planning text if rejected. No system, endpoint, or cloud changes.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T04 — Repository and configuration scaffold

- **Prerequisites:** T03 route selected; approval for source changes.
- **Dependencies:** T03.
- **Implementation steps:** Create only the approved project's minimal directory structure, ignore rules, and placeholder configuration template. Keep secrets in user-controlled local files; optional external integrations must fail safely when credentials/configuration are absent.
- **Tests:** Validate template parsing and ignore rules; verify no local credentials or generated state are tracked; verify missing optional integration credentials cause a safe disabled/error state and no outbound call.
- **Acceptance criteria:** Clean, minimal scaffold works without embedding secrets or machine-specific state.
- **Rollback/cleanup:** Remove only scaffold files created in this task; preserve existing repository content.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T05 — Approved development/client prerequisites

- **Prerequisites:** T03 route selected. Any future installation requires a documented need, exact tool and version, installation method, and explicit approval before execution.
- **Dependencies:** T04.
- **Implementation steps:** Assess whether client/development tools are needed for the selected fixture-first route. That route does not currently justify installing an OpenStack SDK, OpenStack CLI, Kubernetes tooling, or other new dependencies. T05 covers prerequisite assessment only; Keystone authentication implementation belongs to T06. If a later task establishes a need, document the exact tool, version, and installation method and obtain explicit approval before installing; use a project-local environment where practical. Do not install OpenStack or Kubernetes unless a separately approved task authorizes it.
- **Tests:** For this documentation-only assessment, verify the recorded decision and approval gate. Version/import/smoke checks and provenance/version records apply only after a separately approved installation; verify credentials are absent from the repository without claiming unperformed checks.
- **Acceptance criteria:** The fixture-first prerequisite assessment and its no-new-dependency decision are documented; T05 is distinguished from T06 authentication; any future installation is gated on a documented need, exact tool/version/method, and explicit approval. No tools are claimed as installed or verified by this documentation-only task.
- **Rollback/cleanup:** Use documented uninstall/environment cleanup for only task-installed components; preserve unrelated packages/data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; commit manifests, not local environments.

### T06 — Secure identity configuration and authentication

- **Prerequisites:** T05; explicit approval for any OpenStack authentication endpoint contact and least-privilege test identity, or approved fixture identity.
- **Dependencies:** T05.
- **Implementation steps:** Implemented the fixture equivalent with nonsecret fixture identity references and explicit project/role assignments. `catalog.read` requires `catalog.viewer`; `compute.create` requires `compute.operator`; unlisted operations and cross-project access are denied. All fixture results are labelled `simulated`. Real Keystone authentication and real credentials remain absent; never print tokens or credential-bearing URLs.
- **Tests:** `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` — 8 fixture tests passed for known/unknown identities, project scope, role and operation enforcement, simulated status, safe failure, and no socket connection. These tests use no endpoint, network, cloud credentials, or added dependency; they do not validate Keystone.
- **Acceptance criteria:** Fixture identity and authorization outcomes are reproducible and explicitly simulated; unknown identities, cross-project access, insufficient roles, and unlisted operations fail safely. Real Keystone authentication remains conditional on a separately approved endpoint and least-privilege identity and is not claimed as verified.
- **Rollback/cleanup:** Remove only task-created local credentials/config; revoke temporary credentials only if authorized.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; commit placeholders only.

### T07 — Read-only OpenStack service inventory

- **Prerequisites:** T06 fixture authentication is implemented, but it is not real Keystone authentication. Any endpoint query requires explicit user approval naming that endpoint and the read-only query scope, plus an approved real credential path.
- **Dependencies:** T06.
- **Implementation steps:** Completed the authorized local-only inventory and recorded fresh, timestamped evidence in [`environment-audit.md`](../architecture/environment-audit.md). OpenStack CLI and queried Python clients/package names were absent; checked standard config paths and conventional service ports were absent. The service manager and Docker daemon were inaccessible; no matching process names were observed. QEMU and `virsh` CLIs and KVM kernel modules were present, but `/dev/kvm` was absent, so current usable KVM is unverified. No endpoint or cloud resource was queried. Remote Keystone, Nova, Glance, Neutron, Cinder, Horizon, and metering inventory, image/network metadata, and Horizon/API comparison remain pending separate endpoint/scope approval and real authentication.
- **Tests:** Local read-only checks performed at 2026-10-18 22:21:09 +05:30: `command -v openstack`; Python `importlib.util.find_spec` for `openstack`, `novaclient`, and `keystoneauth1`; filtered `dpkg-query`; filtered `systemctl list-units` (inaccessible: system bus permission denied); filtered `ps -eo comm=`; Docker CLI check and `docker ps -a` (inaccessible: local daemon permission denied); formatted `docker info` (empty server fields discarded as invalid); filtered `ss -lntu`; presence-only Python checks for standard config paths; `qemu-system-x86_64 --version`, `virsh --version`, `/dev/kvm` presence and `lsmod`. No configuration contents, environment-variable values, credentials, tokens, or cloud endpoint URLs were displayed. No API/list test or dashboard/API comparison was run.
- **Acceptance criteria:** The local-only inventory classifies observed local evidence and limits without implying remote availability. Endpoint-dependent service/resource inventory and Horizon/API consistency are **pending/conditional** on an explicitly approved endpoint and read-only scope, usable real authentication, and service availability. This local-only slice does not establish complete OpenStack deployment status.
- **Rollback/cleanup:** None; read-only.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T08 — Capability and resource catalog model

- **Prerequisites:** T04 scaffold, T05 approved development prerequisites, and T02 findings; T07 inventory is optional input when separately approved and available.
- **Dependencies:** T04 and T05; T07 is an optional data source, not a blocker for the local schema/model.
- **Implementation steps:** Added a manager-independent catalog record/provider contract for flavors, images, networks, and storage, with deterministic illustrative fixtures. Every record carries a category, stable identifier, source, status, and attributes. Fixture provenance enforces `simulated`; `available` and `unavailable` require evidence; missing data remains missing and remote uncertainty can be `unverified`. Unit-bearing field names use `vcpus` (count), `*_mib` (MiB), and `*_gib` (GiB), with no conversion. No OpenStack provider, endpoint configuration, cloud observation, flavor/quota change, or real-capacity claim was added. Later real data may be merged only after verified read-only T07 evidence and separate endpoint approval.
- **Tests:** `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` — 18 tests passed, including 10 catalog tests for all categories, deterministic output, required metadata/provenance, status and evidence semantics, explicit units, and missing/partial data; the existing 8 fixture identity tests also passed. Tests use only the standard library and require no endpoint, network, credentials, or new dependency.
- **Acceptance criteria:** Catalog schema and fixture path work offline; each fixture datum has a source/status and is marked simulated. Missing records do not imply unavailability, and evidence is required for available/unavailable states. Real inventory remains unverified until supported by approved T07 evidence. No cloud changes occurred.
- **Rollback/cleanup:** Remove only task-created local test data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T09 — Deterministic fixture adapter

- **Prerequisites:** T04 scaffold, T05 approved development prerequisites, and T08 manager-independent contract.
- **Dependencies:** T04, T05, and T08; no real endpoint or VM manager is required.
- **Implementation steps:** Added `openstack_cloud_poc/adapter.py` with a narrow `ManagementAdapter` contract and `FixtureManagementAdapter`. It composes the existing T06 identity provider and T08 catalog provider, exposing catalog list/get, fixture identity authentication, and fixture authorization. Its request allowlist rejects unsupported operations with safe errors. Missing fixture records are reported as simulated missing data with remote availability left unverified. No provider/VM-manager client, job, provisioning, Nova lifecycle, or Proxmox integration was added.
- **Tests:** Added `tests/test_adapter.py` using standard-library `unittest`. The complete offline suite was run with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: 27 tests passed, covering identity/catalog composition, stable repeated reads, project and role policy, metadata/provenance/status preservation, missing records, unsupported and malformed requests, safe errors, and absence of network calls/cloud-client imports. This is fixture evidence only, not proof of Keystone authentication or cloud service availability.
- **Acceptance criteria:** The identity/catalog management boundary is usable offline from deterministic fixtures, marked simulated, and independent of a VM manager. No fixture outcome is represented as a real cloud resource. Real endpoint behavior, remote missing-service detection, and production identity/catalog verification remain pending and require separately authorized integration work.
- **Rollback/cleanup:** Delete only task-created fixtures/test outputs if rejected.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T10 — Capability status reporting

- **Prerequisites:** T08 and T09.
- **Dependencies:** T09.
- **Implementation steps:** Added `openstack_cloud_poc/capabilities.py`, reusing T08's `VerificationStatus`. `CapabilityStatusRecord` requires capability name and source, carries optional observation time, and requires evidence for `available` and `unavailable`. `format_capabilities` sorts records deterministically and prints `observed_at=unknown` when no timestamp is supplied. Catalog provenance/status is preserved; fixture identity results remain simulated. Missing evidence and error cases remain unverified or simulated as appropriate. The reporter formats only caller-supplied records and performs no discovery; it defines no freshness threshold or TTL.
- **Tests:** Added `tests/test_capabilities.py` using standard-library `unittest`. The complete suite was run with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: 36 tests passed. Coverage includes every status, source/evidence requirements, known/unknown timestamps, stable formatting, catalog and fixture identity status preservation, missing/error evidence, and safe error formatting. Synthetic available/unavailable records test formatting only and do not establish real service state.
- **Acceptance criteria:** The formatter distinguishes supplied available, simulated, evidence-backed unavailable, and unverified records with provenance and observation time or explicit unknown freshness. Missing records/errors do not imply remote unavailability. No API/UI, endpoint discovery, credentials, or real OpenStack provider was added; real capability verification remains conditional on separately authorized evidence.
- **Rollback/cleanup:** Remove only task-created local state.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T11 — Project isolation verification

- **Prerequisites:** T06; two approved test scopes/projects or fixture scopes.
- **Dependencies:** T10.
- **Implementation steps:** Updated `openstack_cloud_poc/adapter.py` so `read_catalog`, `catalog.list`, and `catalog.get` require an authentication result and explicit project ID. The adapter applies the existing `catalog.read` policy before returning catalog data. Denied, unknown-identity, cross-project, and missing-role results are marked `simulated` and contain no catalog records. Raw fixture-provider access is private (`_get_catalog_fixture`); the former public unscoped `get_catalog` and `get_record` methods are absent.
- **Tests:** Added `tests/test_isolation.py` using standard-library `unittest`; updated T09 adapter tests for the scoped read contract. The complete suite was run with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: 43 tests passed. Isolation tests cover an authorized same-project read, cross-project denial, unknown identity, missing role, no unscoped public path, denied results without catalog data, simulated status, and a sensitive sentinel absent from results and standard output/error. The fixture emits no logs; these tests do not establish real Keystone behavior.
- **Acceptance criteria:** Fixture-only project-scope checks are explicitly simulated. The adapter withholds catalog records unless the existing fixture identity and role policy authorizes `catalog.read` for the requested project. Real Keystone authentication and cloud-side project isolation remain unverified and require separately approved endpoint/identity access.
- **Rollback/cleanup:** Remove only task-created identities/projects with explicit approval; otherwise preserve and document them.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; omit identifying data.

### T12 — Provisioning API contract

- **Prerequisites:** T08-T10 and a demonstrated need for a custom API.
- **Dependencies:** T10.
- **Implementation steps:** Added [`provisioning-contract.md`](../api/provisioning-contract.md) and `openstack_cloud_poc/api_contract.py`. The framework-independent contract supports only `compute.create`, requires explicit project, image, flavor, and network references, validates fields locally, and uses safe stable error codes. Lifecycle status (`accepted`, `completed`, `rejected`, `failed`) is separate from evidence mode (`simulated`, `real`). The accepted-response factory requires an allowed authorization result and accepted responses cannot include a resource ID. Completed responses require a resource reference and provider-supplied evidence. This deliverable defines an API boundary; it does not demonstrate a need for a running custom server. POC-016 SDK integration and POC-017 job tracking remain deferred.
- **Tests:** Added `tests/test_api_contract.py` using standard-library `unittest`. `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` passed all 53 tests. Contract tests cover valid/invalid fields, project scope, unsupported operations, safe errors, authorization, lifecycle/evidence separation, and accepted-versus-completed semantics. Completion examples are synthetic and visibly simulated; no endpoint, provider, queue, or resource operation was used.
- **Acceptance criteria:** The documented and locally validated contract distinguishes request acceptance from provider-evidenced completion. `accepted` means validation and authorization passed; it does not mean work was queued or a resource created. No server, HTTP routing, real Keystone authentication, or real cloud-side authorization was implemented or claimed. The prerequisite for a running custom API remains unestablished. Identifier semantics depend on execution outcome: an accepted-only request returns neither a job ID nor a resource ID; a synchronous operation may return a resource ID only when provider evidence supports creation; an asynchronous operation may return a job ID and queued/running status only after an execution mechanism accepts the work. Fixture IDs and outcomes are explicitly simulated.
- **Rollback/cleanup:** Remove only task-created contract files if rejected.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T13 — Synchronous management adapter

- **Prerequisites:** T12 and T03 route. Implement/test the fixture adapter first. Real OpenStack integration additionally requires a verified endpoint or a separately approved deployment plan; endpoint contact needs explicit scope approval. Any real adapter that can create/update/delete resources requires approval naming each operation, allowed resource scope, and cleanup. Keep the management contract independent of the VM manager.
- **Dependencies:** T03, T07, and T12.
- **Implementation steps:** Added `openstack_cloud_poc/management_service.py`, a synchronous, framework-independent fixture service for the T12 `compute.create` contract. It reuses T12 request validation and T06 authorization (`compute.create` project scope and `compute.operator` role), then checks the referenced image, flavor, and network against existing illustrative fixtures. Success means only validated, authorized fixture handling and returns `accepted` with `simulated` evidence and fixture provenance. It does not create, queue, or identify a resource. Validation, authorization, missing fixture data, unavailable service, service failure, and timeout have stable safe error categories; submitted values and exception text are not echoed. The validator's internal fixture lookup occurs only after authorization and is not exposed as an unscoped adapter operation. No SDK, real provider, endpoint, server, queue, job system, Nova lifecycle, or Proxmox integration was added.
- **Tests:** Added `tests/test_management_service.py` using standard-library `unittest`. The complete suite passed **61 tests** with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`. T13 tests cover accepted simulated handling without creation claims, invalid input, unknown/cross-project and missing-role denial, missing fixture data, injected service-unavailable/timeout/provider-error outcomes, safe diagnostics, and simulated status. All checks use local fixtures or injected test doubles; no endpoint, credentials, network, or new dependency is required.
- **Acceptance criteria:** The fixture service returns an explicit simulated result and safe diagnosable errors. `accepted` does not mean a resource was created or queued. No `completed` result or resource ID is produced; completion remains gated on provider-supplied evidence. T07's local observations do not establish remote service status, and POC-016 real SDK integration and POC-008 Nova lifecycle remain deferred pending their endpoint/deployment gates and task-specific approval. T14 provides a standalone simulated job model only; integrated POC-017 tracking remains pending, and this synchronous T13 service does not queue work or return a job or resource identifier.
- **Rollback/cleanup:** Remove only local test state or explicitly approved resources created by this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T14 — Job status and failure reporting

- **Prerequisites:** T13 shows an operation that needs asynchronous status tracking; any remote cancellation action is separately authorized in the task scope.
- **Dependencies:** T13.
- **Implementation steps:** Extended `openstack_cloud_poc/jobs.py` with a thread-safe, in-memory tracker for `compute.create`, `compute.start`, `compute.stop`, and `compute.delete`. Immutable records now retain fixture job/project/actor IDs, operation, state, timestamps, optional fixture instance ID, allowlisted failure code, and simulated provenance. Project-scoped registration and inspection enforce the T1 fixture role policy; denied and cross-project lookups return the same not-found error. Optional idempotency keys are scoped by project and operation and use a canonical request fingerprint; identical duplicates return the existing job, while a payload conflict is rejected. Transitions remain limited to the specified pending/running/succeeded/failed graph and are available only through the module-internal execution path. At T14 completion the tracker was standalone; Task 3 added the bounded local executor, and Task 4 connected jobs to simulated fixture lifecycle execution. No persistence, cancellation, real provider integration, or crash recovery was added.
- **Compatibility:** The old unscoped `create(operation)`, `get(job_id)`, and direct `transition(...)` tracker calls are intentionally replaced by authorized `register(...)`, project-scoped `get(...)`, and the internal execution path. The serialized `failure_category` field remains for wire compatibility; the record exposes `failure_code` with a `failure_category` property alias. T12's create-only request and T13 synchronous fixture validation remain unchanged.
- **Tests:** `tests/test_jobs.py` uses standard-library `unittest` for supported operations, role/project isolation, transition and terminal rules, thread safety, idempotency and payload conflicts, safe identifiers, timestamps, and simulated provenance. All **17 focused job tests** passed with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_jobs -v`; the full **137-test offline suite** passed with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`. Tests use no endpoint, credentials, network, or added dependency.
- **Acceptance criteria:** The process-local tracker records scoped simulated jobs for the four mutation operations, enforces the defined state graph, requires role/project authorization on registration and inspection, returns non-disclosing not-found errors across project boundaries, deduplicates identical keyed submissions, rejects conflicting payloads, and refuses non-fixture instance IDs. State changes are not callable through the management API. The Task 4 follow-on integrates simulated fixture execution, while durable execution, restart recovery, and real provider operations remain unproven; T13 remains the separate synchronous validation path.
- **Rollback/cleanup:** Remove only task-created job records/state; keep required audit records sanitized.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T15 — Neutron network preparation and security-group validation (conditional)

- **Execution record:** [`T15.md`](T15.md) — currently **Blocked** pending verified local Neutron service/network evidence and resolution of the host readiness gates.

- **Prerequisites:** T03 confirms the approved workflow uses OpenStack/Neutron; T07 confirms Neutron. Explicit approval enumerates any network/subnet/router/security-group/floating-IP creation or change, intended rules, and cleanup. Read-only inspection may be scoped separately.
- **Dependencies:** T03 and T07.
- **Implementation steps:** Inspect existing topology and prepare a usable approved test network/security-group path for T16. Prefer existing resources; create/change only resources explicitly approved for this task.
- **Tests:** Verify network/subnet/router associations and security-group rules; verify no public exposure by default. Guest SSH/routing/floating-IP connectivity is tested in T17 after an approved VM exists.
- **Acceptance criteria:** A suitable Neutron path is evidenced for T16 or marked unavailable; WSL reachability is not overstated.
- **Rollback/cleanup:** Remove only resources created for this task; retain pre-existing network/security-group resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T16 — Nova instance lifecycle (conditional)

- **Execution record:** [`T16.md`](T16.md) — currently **Blocked** pending deployment, KVM, Neutron, and approved resource-scope gates.

- **Prerequisites:** T07 confirms Nova at a verified local endpoint or a separately approved local deployment plan; T15 provides an approved usable network; explicit approval enumerates image, flavor, quotas, resource use, and cleanup/deletion scope. Implement Nova behind the manager-independent T09 provider boundary. Proxmox remains deprecated. The approved Task 1–4 contract selects asynchronous create/start/stop/delete with synchronous inspect; no new synchronous-versus-asynchronous decision is pending. Task 4 integrates T14 tracking and execution for fixtures only. Real Nova integration must extend job/resource identifier provenance so a Nova server ID is never accepted or reported as a fixture ID. T16 remains blocked on the real service/network and resource-approval gates.
- **Dependencies:** T14 and T15.
- **Implementation steps:** Implement create/inspect/start/stop/delete for one small approved Linux instance.
- **Tests:** Lifecycle state checks and failure cases; confirm only the task-created instance is deleted. T17 verifies repeat runs and failure reporting.
- **Acceptance criteria:** Lifecycle works against real Nova or is explicitly reported unavailable with a fixture path.
- **Local fixture follow-on (Task 4):** `AsynchronousLifecycleManagementService` connects the `FixtureInstanceProvider` to project-scoped jobs and the bounded executor for simulated create/start/stop/delete, with synchronous inspect. This satisfies only the local fixture lifecycle; it does not satisfy T16's real Nova/Neutron prerequisites or claim provider availability.
- **Task 4 verification:** Added `tests/test_async_lifecycle.py` with 14 integration tests covering lifecycle state changes, failures and partial IDs, synchronous inspect, authorization/isolation, concurrent idempotency, instance mutation conflicts, capacity/submission rejection, worker authorization, network isolation, and T13 compatibility. All 14 integration tests and the full **165-test offline suite** passed with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`. No cloud endpoint or provider was contacted.
- **Rollback/cleanup:** Delete only this task's instance and dependencies; preserve all pre-existing resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; include sanitized evidence.

### T17 — Compute and network end-to-end acceptance

- **Execution record:** [`T17.md`](T17.md) — fixture workflow verified; real Nova/Neutron end-to-end validation remains **Blocked**.

- **Prerequisites:** T15 and T16 pass; user approval covers the specific repeat-run resource operations and cleanup scope.
- **Dependencies:** T16 (which depends on T15).
- **Implementation steps:** The existing lifecycle service is exercised through the fixture adapter for repeatable create, inspect, stop/start, and delete runs. Real Nova/Neutron status and guest connectivity remain unavailable in the inspected local paths.
- **Tests:** Repeat runs, error reporting, positive/negative connectivity where authorized, and cleanup confirmation.
- **Fixture evidence:** `tests/test_poc_workflow.py` verifies two full in-process fixture lifecycle runs. All outcomes remain simulated; deleted fixtures remain tombstones. This does not verify packets, SSH, or real resource cleanup.
- **Acceptance criteria:** A full real workflow is reported as real only if Nova and Neutron operations succeeded; otherwise report simulation/unavailability accurately. Current real-path status: **Blocked/unavailable in inspected local paths**.
- **Rollback/cleanup:** Confirm task-created resources are removed; do not alter unrelated cloud objects.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T18 — Cinder block-storage validation (conditional)

- **Execution record:** [`T18.md`](T18.md) — no Cinder service/backend found in inspected local paths; real volume validation remains **Blocked**.

- **Prerequisites:** T07 confirms compatible Cinder backend; explicit approval enumerates volume/snapshot create, attach, detach, and delete operations and storage use.
- **Dependencies:** T07; T16 only if attachment to the approved test instance is required. Do not block standalone backend checks on Neutron/T17.
- **Implementation steps:** Validate create, attach, detach, snapshot, and delete only for task-created test volume(s).
- **Tests:** Read/write, attachment state, snapshot, and cleanup checks.
- **Acceptance criteria:** Cinder capabilities are reported from observed backend behavior; absent backends are marked unavailable.
- **Rollback/cleanup:** Remove only task-created volumes/snapshots after detach; never touch existing data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T19 — Object storage validation (optional/conditional)

- **Execution record:** [`T19.md`](T19.md) — no Swift/object-storage service found in inspected local paths; object round-trip remains **Blocked**.

- **Prerequisites:** T07 confirms Swift or another locally approved compatible service; explicit approval enumerates test object/container create and delete operations.
- **Dependencies:** T07; independent of Cinder unless the approved use case explicitly needs both.
- **Implementation steps:** Test a small local object put/get/delete; no external/offsite upload.
- **Tests:** Round trip, permissions, cleanup, and backend identification.
- **Acceptance criteria:** Report only verified object-store functionality; local files are not represented as object storage.
- **Rollback/cleanup:** Delete only the test object and container created for this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T20 — Usage data source integration

- **Execution record:** [`T20.md`](T20.md) — simulated fixture source implemented and verified; real OpenStack metering remains **Blocked**.

- **Prerequisites:** Named usage source confirmed (Ceilometer/Gnocchi, compatible CloudKitty where present, or scoped API snapshots); explicit approval for any read-only metering endpoint contact and sample data if needed.
- **Dependencies:** T07; T17 for usage from a test VM, and T18/T19 only for their respective usage dimensions. Fixture data may be used if no real workload is approved/available.
- **Implementation steps:** Added `FixtureUsageSource` and project-scoped `FixtureUsageCollector` with explicit source, UTC period, units, simulated provenance, and no implicit conversion or imputation. A separate `usage.viewer` role is required for fixture `usage.read`. No meter endpoint was contacted; actual Ceilometer/Gnocchi/CloudKitty support remains unverified.
- **Tests:** Source attribution, permissions, time range, repeatability, and incomplete-data handling.
- **Acceptance criteria:** Reports do not claim billing-grade completeness; simulated usage is labelled.
- **Verification:** `tests/test_usage.py` — 11 tests passed; the 183-test full offline suite is recorded in `T20.md`.
- **Rollback/cleanup:** Remove only task-created local samples; do not alter monitoring/cloud data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T21 — Usage reporting and demonstration pricing

- **Execution record:** [`T21.md`](T21.md) — simulated usage summary verified; price estimate remains **Blocked** pending owner-approved formula and rates.

- **Prerequisites:** T20 passes; pricing formula and non-production label are agreed.
- **Dependencies:** T20.
- **Implementation steps:** Added deterministic fixture usage summaries with per-item source, interval, units, and provenance. Price estimation is omitted because no pricing formula or rates have been approved; no payment integration exists.
- **Tests:** Deterministic calculation, time-window boundaries, missing usage, and visible non-production labelling.
- **Acceptance criteria:** Every value cites its source; demo pricing cannot be mistaken for an invoice or charge.
- **Verification:** `tests/test_usage_reporting.py` — 6 focused tests passed; the 183-test complete offline suite is recorded in `T21.md`.
- **Rollback/cleanup:** Remove only task-created report outputs if needed; no payment accounts/records are created.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T22 — Repeatable infrastructure automation (optional)

- **Prerequisites:** T17 workflow accepted; approved and compatible Heat, Terraform, or Ansible approach; explicit approval enumerates every resource create/update/delete and cleanup action.
- **Dependencies:** T17; T18 if storage resources are part of the automation.
- **Implementation steps:** Automate only an agreed repeatable infrastructure workflow; keep state local/secret-safe and document exact versions. Skip if compatibility or resource budget is inadequate.
- **Tests:** Plan/check or equivalent dry run first; then approved apply and cleanup against task-owned resources; verify repeatability and error reporting.
- **Acceptance criteria:** Automation is reproducible, scope-limited, and cleanup is proven; otherwise the incompatibility is documented.
- **Rollback/cleanup:** Destroy only resources/state created by this task after review; never use broad destroy against shared infrastructure.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; exclude secrets and generated state.

### T23 — GPU feasibility assessment (optional)

- **Execution record:** [`T23.md`](T23.md) — existing audit evidence meets the feasibility-report criteria; no fresh GPU probe was performed.

- **Prerequisites:** T01 evidence; explicit approval for any further GPU probe; no model download required for capability checks.
- **Dependencies:** T01; independent of OpenStack tasks.
- **Implementation steps:** Recorded prior RTX 2060 Docker-container visibility, failed host NVML check, and unverified Nova PCI passthrough. No passthrough or MIG configuration was attempted; a fresh probe remains approval-gated.
- **Tests:** Read-only GPU/runtime checks where approved; distinguish Docker visibility from host and guest visibility.
- **Acceptance criteria:** Evidence-based support matrix; H100/MIG and multi-tenant GPU are explicitly unsupported; Nova passthrough remains unverified unless proven separately.
- **Rollback/cleanup:** None for read-only checks; remove only task-created diagnostic output.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T24 — Sample AI workload (optional)

- **Prerequisites:** T23; user approval for model source/license, image/download, disk, runtime, and data handling.
- **Dependencies:** T23.
- **Implementation steps:** Run a bounded sample workload on the supported host/container path; use a VM only if separately proven and approved. Do not upload user data.
- **Tests:** Record model/runtime, successful result, latency, peak VRAM/RAM, errors, and CPU fallback.
- **Acceptance criteria:** Workload result and resource use are reproducible; conclusions are limited to RTX 2060 6 GB and the tested runtime.
- **Rollback/cleanup:** Remove only this task's model/container artifacts if approved; never prune unrelated Docker resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; omit private data.

### T25 — Security validation

- **Prerequisites:** T11 and T13; agreed security test scope and permission to inspect exposure/audit behavior.
- **Dependencies:** T11, T13, T15, T16.
- **Implementation steps:** Test project isolation, API authorization, secret handling, exposed ports/firewall rules, TLS and endpoint configuration, and auditability. Keep environment private; do not perform unapproved scans or configuration changes.
- **Tests:** Positive/negative authorization, secret scan of scoped diff, port/rule and TLS review, and audit event check.
- **Acceptance criteria:** Negative cases fail safely; no secrets enter Git; only required ports are exposed or deviations are documented.
- **Rollback/cleanup:** Revert only task-created test settings or identities with approval; preserve existing environment settings.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; record sanitized evidence.

### T26 — Lightweight logging and monitoring (optional)

- **Execution record:** [`T26.md`](T26.md) — no verified source or application logging/metrics implementation; monitoring remains **Blocked**.

- **Prerequisites:** Stable workflow and verified metric/log sources; explicit approval for any new service or remote metric endpoint query.
- **Dependencies:** T14 and relevant operational tasks.
- **Implementation steps:** Add lightweight structured logs and necessary verified metrics; avoid a full observability stack by default.
- **Tests:** Compare displayed values with source, test error diagnosis and secret redaction, and measure bounded resource use.
- **Acceptance criteria:** Every displayed metric names its source; unavailable/simulated data is clear and resource fit is documented.
- **Rollback/cleanup:** Stop/remove only monitoring components and data created by this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T27 — Backup and restore procedure

- **Execution record:** [`T27.md`](T27.md) — verified real backups plus successful operator isolated restore/recovered guest boot; **In Progress** pending repository closeout. Restart validation and final POC acceptance are separate next checkpoints.

- **Prerequisites:** Identify POC data that matters and approved local backup destination; stable workflow.
- **Dependencies:** T14 and only the implemented state-bearing tasks (for example T18-T22 and T26); skipped optional tasks do not block a backup of the remaining approved POC data.
- **Implementation steps:** Define backup scope excluding exposed secrets; create backup and perform a local restore test.
- **Tests:** Manifest, integrity check, restore into a test location, and confirmation that live data was not overwritten.
- **Acceptance criteria:** Restore succeeds or the limitation is documented; no production DR/offsite claim.
- **Rollback/cleanup:** Preserve recovery copy until acceptance; remove only task-created temporary test restore data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; never commit backup secrets/data.

### T28 — End-to-end demonstration and final acceptance

**Historical record:** T28's earlier statement that a portal was optional is superseded by the active browser-ready goal and T29–T41 acceptance track above. T28 remains useful only for its original fixture/core demonstration evidence.

- **Prerequisites:** Historical T28 prerequisites; active browser workflow acceptance is defined by T40.
- **Dependencies:** T10, T11, T14, T17, and applicable T18-T27 tasks.
- **Implementation steps:** Original T28 demonstrated the fixture/core scope available at that time. The active browser, usage, billing, and audit demonstration is specified by T40; clearly label simulated/deferred steps.
- **Tests:** Run the documented end-to-end sequence against real or fixture adapters; verify authorization, failure states, evidence, resource cleanup, and final status.
- **Acceptance criteria:** Every step has evidence; unsupported steps are labelled; task cleanup is confirmed; all applicable requirements are satisfied or recorded as blocked/unavailable.
- **Rollback/cleanup:** Stop/remove only portal and task-created resources/state; preserve all pre-existing cloud resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; record final SHA and push result.
