# Implementation task backlog

This is a 28-task breakdown of the requirements in [`requirements-specification.md`](../requirements/requirements-specification.md), refined from the earlier grouped plan into independently verifiable and committable units. The architecture source is [`architecture-diagram.png.jpeg`](../architecture/architecture-diagram.png.jpeg), preserving the supplied actual filename. Tasks are ordered by dependency; tasks marked optional or conditional may be skipped with the reason recorded. Coverage labels below describe planned traceability, not completed implementation. This backlog does not authorize installation, configuration, external access, or cloud changes.

## Required workflow for every approved task

1. Obtain explicit approval for the task and its scope. Inspect `git status` and preserve unrelated work.
2. Implement only the task scope. Run its listed tests and record actual results and whether the capability is real, simulated, or unavailable.
3. Update relevant documentation with results, limitations, cleanup, and decisions.
4. Review the complete diff, including staged changes, for secrets and unrelated changes. Do not stage credentials, tokens, generated state, or unrelated existing files.
5. After verification passes, create a focused commit, push it to the existing GitHub remote, and record the full commit SHA and push result in the task record.
6. If no remote exists, do not invent one and do not claim a push. Stop and report that pushing is pending remote configuration/approval; the user decides whether to configure one. Do not proceed to the next task until the required task record is complete.

### Initial Git state and first-commit procedure

Read-only inspection found branch `main` with no `HEAD` commit (an unborn branch), no configured remote names, zero tracked files, and existing untracked `README.md` and `docs/`. Preserve all of these files. No GitHub URL, branch strategy, credentials, or push permission is assumed.

For the first approved task, agree the branch strategy and remote with the user. If the user has configured a remote, inspect its name without printing a credential-bearing URL. Stage only the explicitly approved task paths; never use broad staging to sweep in the existing untracked tree. Review the proposed root commit diff for secrets and unrelated files before committing. A root commit may be made only after task approval and passing verification. If no remote is configured, do not invent one: report `locally committed; push pending` if a local commit was authorized and created, mark the task incomplete, and stop before starting the next task. Do not claim push success or test permission by attempting a push without an approved remote.

### Cloud and system mutation approval gate

No task may create, update, or delete a cloud resource, change a quota/flavor, change local system configuration, expose a port, install software, or apply infrastructure automation without explicit user approval for that task. Approval must identify the operation, resource scope, expected cost/quota, and cleanup action. Task-level approval covers only the operations and resources it explicitly names. Read-only API contact also requires approval of the endpoint/scope; no cloud endpoint may be contacted in the current review. Never delete pre-existing resources. Test cleanup deletions require explicit authorization as part of the approved task scope.

## Original requirement coverage

The IDs below are the original identifiers in the requirements specification. They remain unchanged; the T-IDs are implementation slices. The requirements' acceptance criteria and safety constraints remain authoritative. The image reference uses the actual supplied path `docs/architecture/architecture-diagram.png.jpeg`; the requirements' intended `architecture-diagram.jpeg` path does not match that actual filename.

Status definitions: **Covered** means the planned tasks address all stated clauses; **Partially covered** means a clause is blocked by an unresolved decision or unavailable evidence; **Deferred** means the requirements explicitly make it optional/out of scope; **Missing** means no task or explicit disposition exists. These statuses do not imply execution is complete.

| Original requirement | Task IDs | Status | Coverage note / reason |
|---|---|---|---|
| POC-001 — Read-only laptop and virtualization audit | T01, T02 | Covered | Includes Windows/WSL context where available, OS, CPU/RAM/disk/GPU/driver, KVM, Docker, Git, listening ports, and resource constraints; missing evidence is labelled unknown. |
| POC-002 — Git/GitHub audit and project baseline | T01, T02 | Partially covered | Status/branch/remotes/ignore rules are inspected safely; this repository has no remote, so push permissions cannot be verified until the user configures one. |
| POC-003 — OpenStack availability and deployment inventory | T02, T07 | Covered | Local package/container/service/config and endpoint-presence checks plus approved read-only API inventory; credential presence may be recorded, values are never printed, and no resources are changed. |
| POC-004 — OpenStack and VM-launch feasibility | T02, T03, T06, T07 | Partially covered | Management-layer-first and fixture-first scope is selected. Real endpoint integration and VM-launch feasibility remain conditional on a verified endpoint or separately approved deployment plan; Nova VM lifecycle execution remains a later gated task. |
| POC-005 — Keystone project and role validation | T06, T11, T25 | Covered | Project/user/role scope and authorized/unauthorized cases; creating identities requires explicit approval. |
| POC-006 — Glance image workflow | T07, T16 | Covered | Read image metadata; trusted test image use only with approval in a VM test. |
| POC-007 — Neutron network workflow | T07, T15, T17 | Covered | Inspect networks/subnets/routers/security groups/floating IPs, prepare an approved path, then test guest connectivity without disrupting existing networks. |
| POC-008 — Nova instance lifecycle | T16, T17 | Covered | Small instance create/inspect/start/stop/delete, recorded states, authorized cleanup. |
| POC-009 — Horizon and API consistency | T07 | Covered | Compare dashboard visibility with scoped API/CLI results when Horizon exists. |
| POC-010 — Flavors and quotas | T08 | Covered | Inspect actual capacity; change only safe, explicitly approved values. |
| POC-011 — Repeatable VM lifecycle workflow | T16, T17 | Covered | Repeat runs and failure reporting are tested in the repeatable workflow. |
| POC-012 — Cinder storage workflow | T18 | Covered | Backend-gated volume lifecycle or documented unsupported blocker. |
| POC-013 — Network and security acceptance tests | T15, T17, T25 | Covered | Positive/negative connectivity, security groups, SSH, routing and floating IPs where available; exposed ports/auditability in T25. |
| POC-014 — Resource catalog | T08, T10 | Covered | Catalog uses observed images/flavors/networks/storage and labels unavailable items. |
| POC-015 — Management API design and skeleton | T12 | Covered | Contract, auth, schemas, errors, and tests precede implementation. |
| POC-016 — OpenStack SDK integration | T13 | Covered | Scoped read/provision operations; cloud mutations need explicit task-specific approval. |
| POC-017 — Job tracking | T14 | Covered | Job IDs, pending/running/succeeded/failed/cancelled states where applicable, timestamps, and failure reasons. |
| POC-018 — Usage data collection | T20 | Covered | Metering compatibility, source, interval, units, and limitations. |
| POC-019 — Monitoring and dashboards | T26 | Covered | Only compatible sources within the resource budget; validate values against source. |
| POC-020 — Usage reporting and automation | T21, T22 | Covered | Labelled reports and optional approved Heat/Terraform/Ansible automation with dry-run/cleanup. |
| POC-021 — GPU feasibility assessment | T23 | Covered | Separates RTX host/container checks from Nova passthrough; no MIG assumption. |
| POC-022 — Sample AI workload | T24 | Covered | Supported approved host/container/VM route only; record resource use and limitations. |
| POC-023 — Security validation | T11, T25, T26 | Covered | Project/API isolation, secret handling, exposed ports, auditability, and safe negative cases. |
| POC-024 — Backup and recovery test | T27 | Covered | Backup plus restore evidence or a specific limitation. |
| POC-025 — End-to-end demonstration | T28 | Covered | Auth through cleanup, with networking/storage/workload/monitoring/usage where supported and simulation labels. |

### Remaining requirement crosswalk

| Requirement group | Task IDs | Status | Reason / disposition |
|---|---|---|---|
| Project goal, source-file preservation, small tasks and no production claims (§1–2) | T01–T03, T28 | Covered | Audit and mapping retain the actual image path; production/H100 equivalence is excluded. |
| Layer 1 — Keystone, Horizon, CLI/SDK, optional portal/mobile, honest billing | T06–T14, T21, T28 | Covered | Keystone is the OpenStack identity source, not a full Keycloak equivalent; mobile is deferred, portal optional, demo pricing non-production. |
| Layer 2 — local security controls and TLS | T07, T15, T25 | Covered | Private-by-default access, Neutron/host firewall rules, required ports, TLS and endpoint configuration checks are planned. |
| Layer 2 — Cloudflare/DDoS, WAF, separate gateway, Octavia | T28 | Deferred | Requirements make these optional; no public ingress or external protection is assumed. Reconsider only if a required workflow and supported deployment justify it. |
| Layer 3 — catalog, provisioning, jobs, database/queue restraint, metering, demo price/payment | T08–T14, T20–T22 | Covered | PostgreSQL/Redis/Celery/payment integrations are not defaults; data source and pricing limits are reported. |
| Layer 4 — Nova/Glance/Neutron/Cinder and repeatable orchestration | T07–T19, T22 | Partially covered | Core APIs are conditional; diagram's Proxmox/Nova timing needs user decision before VM implementation. |
| Layer 4 — Kubernetes/JupyterHub/Harbor/Ceph, GPU scheduler/MIG | T22–T24, T28 | Deferred | Requirements make these optional and this laptop does not support H100 MIG or production multi-tenant GPU scheduling. |
| Layer 5 — CPU VM, AI workload, block/object storage | T16–T19, T23–T24 | Covered | Each service is conditional on verified support; host GPU is kept distinct from guest GPU. |
| Layer 5 — H100/MIG profiles, GPU isolation, managed academy stack | T23–T24, T28 | Deferred | H100/MIG and multi-tenant GPU isolation are unsupported; JupyterHub/academy environments are optional follow-on work. |
| Layer 6 — laptop/network record; no physical data center build | T01–T03 | Covered | Laptop/network evidence is recorded; H100 server, future servers, facility and power/cooling construction are explicitly out of scope. |
| Layer 7 — verified monitoring, automation, backup/restore, logging and alerts | T14, T22, T26–T27 | Covered | Lightweight only, based on named sources and within budget; full SIEM/offsite/production incident response deferred. |
| Layer 8 — payment, DNS, email, offsite storage | T04, T21, T26, T28 | Deferred | External integrations are not active by assumption; no payments or uploads without separate approval; missing credentials must fail safely; local logs/mail catcher or test hostnames remain optional. |
| Cross-cutting requirements 1–10 (§4) | T04, T06, T10–T17, T25–T28 | Covered | Isolation, lifecycle, full job states, errors, idempotency, observability, docs, resource safety, config separation, and private defaults are assigned. |
| Non-functional requirements (§5) | T01–T03, T28 | Covered | WSL2 support, read-only initial audit, preservation, small tasks, reproducible evidence, compatibility, no production claims, and real/simulated distinction. |
| Suggested repository structure (§9) | T01, T04 | Covered | Adapt the suggested structure to existing paths; do not duplicate files or stage unrelated untracked content. |
| Final acceptance criteria (§10) | T01, T11, T16–T28 | Covered | Aggregate acceptance requires evidence or explicit limitation for identity/isolation, VM/network/storage, API/jobs, usage/monitoring, AI, security, backup/restore, end-to-end, and successful Git gates. |
| Task-record fields and Git/GitHub workflow (§7–8) | All T01–T28 | Partially covered | The backlog requires per-POC records with required fields and commit/push evidence. First commit/remote setup remains pending user decision; this current review will not commit or push. |
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

- **Prerequisites:** T03 and explicit approval for exact tools, versions, and install method.
- **Dependencies:** T04.
- **Implementation steps:** Install only necessary client/development tools; use a project-local environment where practical. Do not install OpenStack/Kubernetes unless a separately approved task authorizes it.
- **Tests:** Version/import/smoke checks and provenance/version record; verify credentials are absent from the repository.
- **Acceptance criteria:** Required tools work at recorded versions and match the selected route.
- **Rollback/cleanup:** Use documented uninstall/environment cleanup for only task-installed components; preserve unrelated packages/data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; commit manifests, not local environments.

### T06 — Secure identity configuration and authentication

- **Prerequisites:** T05; explicit approval for any OpenStack authentication endpoint contact and least-privilege test identity, or approved fixture identity.
- **Dependencies:** T05.
- **Implementation steps:** Configure secure local auth input and implement a minimal Keystone authentication check or fixture equivalent. Never print tokens or credential-bearing URLs.
- **Tests:** Successful auth for the approved scope; safe failure for invalid credentials; inspect logs/output for secret leakage.
- **Acceptance criteria:** Auth result is reproducible and its real-vs-simulated status is documented.
- **Rollback/cleanup:** Remove only task-created local credentials/config; revoke temporary credentials only if authorized.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; commit placeholders only.

### T07 — Read-only OpenStack service inventory

- **Prerequisites:** T06 authentication; explicit user approval for the specific endpoint and read-only query scope.
- **Dependencies:** T06.
- **Implementation steps:** Discover packages, containers, services, configs and endpoint presence/reachability for Keystone, Nova, Glance, Neutron, Cinder, Horizon, and metering. Read image metadata and existing network data; where Horizon is available compare its project/resource views with read-only API/CLI output. Record release/source and scope. Inspect config locations without printing secrets; any endpoint/API query is read-only and limited to the user-approved scope.
- **Tests:** Approved read-only API/list calls; confirm no creates/updates/deletes; redact identities and endpoints as needed; verify dashboard/API comparison within same project scope.
- **Acceptance criteria:** Inventory distinguishes present, absent, inaccessible, and unverified services; Horizon/API consistency is checked when both are available.
- **Rollback/cleanup:** None; read-only.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T08 — Capability and resource catalog model

- **Prerequisites:** T04 scaffold, T05 approved development prerequisites, and T02 findings; T07 inventory is optional input when separately approved and available.
- **Dependencies:** T04 and T05; T07 is an optional data source, not a blocker for the local schema/model.
- **Implementation steps:** Define a manager-independent catalog contract for flavors, images, networks, and storage. For fixture-first development, use illustrative deterministic fixture data explicitly labelled simulated; do not claim it is observed cloud capacity. Later merge only verified read-only inventory from T07. Configure a flavor/quota only if separately approved, safe, and needed for a later test.
- **Tests:** Unit checks for missing/partial data, accurate units, and available/simulated/unavailable/unverified states; ensure fixture data is labelled and no capacity is represented as observed without evidence. Any quota/flavor change requires separate approval and is not part of this task.
- **Acceptance criteria:** Catalog schema and fixture path work without a cloud endpoint; every datum has a source/status; real inventory is accepted only after T07 verification; no cloud changes occur.
- **Rollback/cleanup:** Remove only task-created local test data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T09 — Deterministic fixture adapter

- **Prerequisites:** T04 scaffold, T05 approved development prerequisites, and T08 manager-independent contract.
- **Dependencies:** T04, T05, and T08; no real endpoint or VM manager is required.
- **Implementation steps:** Add deterministic fixture-backed management responses and adapter methods for the approved early POC scope. Keep provider/VM-manager selection behind an independent interface so Nova can be added later without changing management contracts. Do not implement Proxmox integration absent explicit approval.
- **Tests:** Repeat fixture reads and compare stable outputs; cover missing-service and error responses; assert simulated provenance is visible and adapter contract does not import or require a Nova/Proxmox client.
- **Acceptance criteria:** The management-layer workflow is usable offline from deterministic fixtures, clearly marked simulated, with a VM-manager-independent boundary; no fixture result is represented as a real cloud resource.
- **Rollback/cleanup:** Delete only task-created fixtures/test outputs if rejected.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T10 — Capability status reporting

- **Prerequisites:** T08 and T09.
- **Dependencies:** T09.
- **Implementation steps:** Expose `available`, `simulated`, `unavailable`, and `unverified` status with source/freshness for each capability.
- **Tests:** Status mapping for real, fixture, absent, and error cases; verify API/UI text does not imply unsupported features.
- **Acceptance criteria:** Users can tell real OpenStack data from fixture and unknown data.
- **Rollback/cleanup:** Remove only task-created local state.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T11 — Project isolation verification

- **Prerequisites:** T06; two approved test scopes/projects or fixture scopes.
- **Dependencies:** T10.
- **Implementation steps:** Exercise allowed project-scoped reads and prohibited cross-project access using the selected adapter.
- **Tests:** Positive authorized request and negative unauthorized request; secret-redacted logs.
- **Acceptance criteria:** Real Keystone authorization is proven when available; fixture-only checks are labelled simulation.
- **Rollback/cleanup:** Remove only task-created identities/projects with explicit approval; otherwise preserve and document them.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; omit identifying data.

### T12 — Provisioning API contract

- **Prerequisites:** T08-T10 and a demonstrated need for a custom API.
- **Dependencies:** T10.
- **Implementation steps:** Define minimal request/status schemas, validation, error codes, and real-vs-simulated adapter contract.
- **Tests:** Schema validation and contract tests for invalid, unsupported, and accepted requests.
- **Acceptance criteria:** Contract distinguishes accepted request from completed resource creation.
- **Rollback/cleanup:** Remove only task-created contract files if rejected.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T13 — Synchronous management adapter

- **Prerequisites:** T12 and T03 route. Implement/test the fixture adapter first. Real OpenStack integration additionally requires a verified endpoint or a separately approved deployment plan; endpoint contact needs explicit scope approval. Any real adapter that can create/update/delete resources requires approval naming each operation, allowed resource scope, and cleanup. Keep the management contract independent of the VM manager.
- **Dependencies:** T03, T07, and T12.
- **Implementation steps:** Implement one narrow service call through SDK or fixture adapter; no database/queue unless justified.
- **Tests:** Success, service unavailable, permission denied, timeout, and no false-success behavior.
- **Acceptance criteria:** Adapter returns a clear result and source status; errors preserve diagnosable context without secrets.
- **Rollback/cleanup:** Remove only local test state or explicitly approved resources created by this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T14 — Job status and failure reporting

- **Prerequisites:** T13 shows an operation that needs asynchronous status tracking; any remote cancellation action is separately authorized in the task scope.
- **Dependencies:** T13.
- **Implementation steps:** Add minimal job ID and pending/running/succeeded/failed states, plus cancelled where the operation supports cancellation; add durable storage/queue only if a demonstrated requirement exists.
- **Tests:** State transitions including cancellation where supported, retry/timeout behavior, idempotency as applicable, restart behavior if persisted, and secret redaction.
- **Acceptance criteria:** Each long operation is traceable and failure never appears as success.
- **Rollback/cleanup:** Remove only task-created job records/state; keep required audit records sanitized.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T15 — Neutron network preparation and security-group validation (conditional)

- **Prerequisites:** T03 confirms the approved workflow uses OpenStack/Neutron; T07 confirms Neutron. Explicit approval enumerates any network/subnet/router/security-group/floating-IP creation or change, intended rules, and cleanup. Read-only inspection may be scoped separately.
- **Dependencies:** T03 and T07.
- **Implementation steps:** Inspect existing topology and prepare a usable approved test network/security-group path for T16. Prefer existing resources; create/change only resources explicitly approved for this task.
- **Tests:** Verify network/subnet/router associations and security-group rules; verify no public exposure by default. Guest SSH/routing/floating-IP connectivity is tested in T17 after an approved VM exists.
- **Acceptance criteria:** A suitable Neutron path is evidenced for T16 or marked unavailable; WSL reachability is not overstated.
- **Rollback/cleanup:** Remove only resources created for this task; retain pre-existing network/security-group resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T16 — Nova instance lifecycle (conditional)

- **Prerequisites:** T07 confirms Nova at a verified endpoint or a separately approved deployment plan; T15 provides an approved usable network; explicit approval enumerates image, flavor, quota, cost, instance resources, and cleanup/deletion scope. Implement Nova behind the manager-independent T09 adapter boundary. Proxmox remains deferred unless explicitly approved.
- **Dependencies:** T14 and T15.
- **Implementation steps:** Implement create/inspect/start/stop/delete for one small approved Linux instance.
- **Tests:** Lifecycle state checks and failure cases; confirm only the task-created instance is deleted. T17 verifies repeat runs and failure reporting.
- **Acceptance criteria:** Lifecycle works against real Nova or is explicitly reported unavailable with a fixture path.
- **Rollback/cleanup:** Delete only this task's instance and dependencies; preserve all pre-existing resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; include sanitized evidence.

### T17 — Compute and network end-to-end acceptance

- **Prerequisites:** T15 and T16 pass; user approval covers the specific repeat-run resource operations and cleanup scope.
- **Dependencies:** T16 (which depends on T15).
- **Implementation steps:** Build/run the repeatable script or service wrapper for create, inspect, stop/start, and delete through the adapter; capture status and connectivity evidence.
- **Tests:** Repeat runs, error reporting, positive/negative connectivity where authorized, and cleanup confirmation.
- **Acceptance criteria:** A full real workflow is reported as real only if Nova and Neutron operations succeeded; otherwise report simulation/unavailability accurately.
- **Rollback/cleanup:** Confirm task-created resources are removed; do not alter unrelated cloud objects.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T18 — Cinder block-storage validation (conditional)

- **Prerequisites:** T07 confirms compatible Cinder backend; explicit approval enumerates volume/snapshot create, attach, detach, and delete operations and storage use.
- **Dependencies:** T07; T16 only if attachment to the approved test instance is required. Do not block standalone backend checks on Neutron/T17.
- **Implementation steps:** Validate create, attach, detach, snapshot, and delete only for task-created test volume(s).
- **Tests:** Read/write, attachment state, snapshot, and cleanup checks.
- **Acceptance criteria:** Cinder capabilities are reported from observed backend behavior; absent backends are marked unavailable.
- **Rollback/cleanup:** Remove only task-created volumes/snapshots after detach; never touch existing data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T19 — Object storage validation (optional/conditional)

- **Prerequisites:** T07 confirms Swift or another locally approved compatible service; explicit approval enumerates test object/container create and delete operations.
- **Dependencies:** T07; independent of Cinder unless the approved use case explicitly needs both.
- **Implementation steps:** Test a small local object put/get/delete; no external/offsite upload.
- **Tests:** Round trip, permissions, cleanup, and backend identification.
- **Acceptance criteria:** Report only verified object-store functionality; local files are not represented as object storage.
- **Rollback/cleanup:** Delete only the test object and container created for this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T20 — Usage data source integration

- **Prerequisites:** Named usage source confirmed (Ceilometer/Gnocchi, compatible CloudKitty where present, or scoped API snapshots); explicit approval for any read-only metering endpoint contact and sample data if needed.
- **Dependencies:** T07; T17 for usage from a test VM, and T18/T19 only for their respective usage dimensions. Fixture data may be used if no real workload is approved/available.
- **Implementation steps:** Check Ceilometer/Gnocchi compatibility and CloudKitty presence if installed; collect a bounded supported usage sample and record source, period, units, and missing-data behavior.
- **Tests:** Source attribution, permissions, time range, repeatability, and incomplete-data handling.
- **Acceptance criteria:** Reports do not claim billing-grade completeness; simulated usage is labelled.
- **Rollback/cleanup:** Remove only task-created local samples; do not alter monitoring/cloud data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T21 — Usage reporting and demonstration pricing

- **Prerequisites:** T20 passes; pricing formula and non-production label are agreed.
- **Dependencies:** T20.
- **Implementation steps:** Produce a usage summary and optional demonstration-only estimate; no payment integration.
- **Tests:** Deterministic calculation, time-window boundaries, missing usage, and visible non-production labelling.
- **Acceptance criteria:** Every value cites its source; demo pricing cannot be mistaken for an invoice or charge.
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

- **Prerequisites:** T01 evidence; explicit approval for any further GPU probe; no model download required for capability checks.
- **Dependencies:** T01; independent of OpenStack tasks.
- **Implementation steps:** Record verified RTX 2060 container visibility and assess host/container route, CUDA/runtime compatibility, and the separate unverified Nova PCI passthrough path. Do not configure passthrough or MIG.
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

- **Prerequisites:** Stable workflow and verified metric/log sources; explicit approval for any new service or remote metric endpoint query.
- **Dependencies:** T14 and relevant operational tasks.
- **Implementation steps:** Add lightweight structured logs and necessary verified metrics; avoid a full observability stack by default.
- **Tests:** Compare displayed values with source, test error diagnosis and secret redaction, and measure bounded resource use.
- **Acceptance criteria:** Every displayed metric names its source; unavailable/simulated data is clear and resource fit is documented.
- **Rollback/cleanup:** Stop/remove only monitoring components and data created by this task.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow.

### T27 — Backup and restore procedure

- **Prerequisites:** Identify POC data that matters and approved local backup destination; stable workflow.
- **Dependencies:** T14 and only the implemented state-bearing tasks (for example T18-T22 and T26); skipped optional tasks do not block a backup of the remaining approved POC data.
- **Implementation steps:** Define backup scope excluding exposed secrets; create backup and perform a local restore test.
- **Tests:** Manifest, integrity check, restore into a test location, and confirmation that live data was not overwritten.
- **Acceptance criteria:** Restore succeeds or the limitation is documented; no production DR/offsite claim.
- **Rollback/cleanup:** Preserve recovery copy until acceptance; remove only task-created temporary test restore data.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; never commit backup secrets/data.

### T28 — End-to-end demonstration and final acceptance

- **Prerequisites:** Core tasks accepted; user confirms whether a minimal portal is needed and explicitly approves the demonstration scope, any cloud operations, and cleanup/deletion.
- **Dependencies:** T10, T11, T14, T17, and applicable T18-T27 tasks.
- **Implementation steps:** Demonstrate authentication → project selection → catalog/provisioning → networking → storage where available → workload → monitoring/usage → cleanup. Add a minimal portal only if approved. Clearly identify simulated/deferred steps; do not contact optional external integrations unless specifically approved and configured, and fail safely when credentials are absent.
- **Tests:** Run the documented end-to-end sequence against real or fixture adapters; verify authorization, failure states, evidence, resource cleanup, and final status.
- **Acceptance criteria:** Every step has evidence; unsupported steps are labelled; task cleanup is confirmed; all applicable requirements are satisfied or recorded as blocked/unavailable.
- **Rollback/cleanup:** Stop/remove only portal and task-created resources/state; preserve all pre-existing cloud resources.
- **Git:** After verification, apply the required review, documentation, commit, push, and SHA recording workflow; record final SHA and push result.
