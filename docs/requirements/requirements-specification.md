# Local OpenStack Cloud Management POC --- Requirements Specification

**Version:** 1.0\
**Status:** Draft for implementation planning\
**Target:** Windows 11 laptop with WSL2 Ubuntu and an existing OpenStack
POC\
**Purpose:** Convert the supplied Cloud Management Software Architecture
diagram into a testable, laptop-scale implementation plan for Codex.

## 1. Project goal

Build a local proof of concept (POC) that validates the main
cloud-management workflows shown in the architecture diagram. Codex must
implement the POC as small, dependency-ordered tasks. Each task must be
tested, documented, committed to Git, and pushed to GitHub before the
next task begins.

The POC is intended to validate software workflows and OpenStack
integration. It is **not** a production data center build and must not
attempt to reproduce an NVIDIA H100 production environment on the
laptop.

## 1.1 Source files and documentation rules

The project must keep both the visual architecture and the written
requirements under version control:

- Architecture image: `docs/architecture/architecture-diagram.jpeg`
- Requirements specification: `docs/requirements/requirements-specification.md`

Codex must read both files before finalizing architecture mapping, scope,
dependencies, or implementation tasks. Use the architecture image as the
visual source for the intended layers and workflow; use this Markdown
document as the initial requirements specification. If the image and
written requirements appear inconsistent, record the discrepancy in
`docs/architecture/architecture-gaps.md` and ask for a decision rather
than silently choosing one.

The image filename above is the intended repository path. Preserve the
actual image extension if the supplied file is not JPEG, and update the
reference accordingly. Do not assume that either file exists until
verified in the repository.

## 2. Scope and constraints

### In scope

-   Begin from the current Ubuntu-on-WSL2 environment and verify what is
    actually installed. The user reports that the previous setup has been
    uninstalled; do not assume OpenStack, Docker, or project files exist.
-   Perform a read-only environment and hardware audit before installation
    or configuration.
-   Map architecture components to OpenStack-native services or
    justified external components.
-   Validate identity, project isolation, compute, images, networking,
    storage, APIs, provisioning workflows, usage reporting, monitoring,
    logging, automation, backup procedures, and a small AI workload
    where feasible.
-   Provide a management API and/or simple portal only where required by
    the final task backlog.
-   Keep code, scripts, tests, decisions, and documentation in Git.
-   Commit and push every successfully completed and verified task.

### Out of scope

-   Production data center deployment.
-   Provisioning or purchasing H100 hardware.
-   Claiming H100 MIG support on the laptop GPU.
-   Production-grade high availability from a single laptop.
-   Real payment processing unless explicitly approved and safely
    sandboxed.
-   Destructive changes to existing OpenStack resources without explicit
    approval.

### Known hardware constraints

The currently known laptop GPU is an NVIDIA RTX 2060 with 6 GB VRAM. It
is not equivalent to an H100 80 GB GPU and does not support the H100 MIG
profiles shown in the diagram. GPU passthrough and nested virtualization
must be verified, not assumed. Small inference workloads may be tested
directly in WSL2 if drivers and memory allow.

## 3. Architecture layers and requirements

### Layer 1 --- Users and Access

**Diagram roles** - Students / AI Academy users - Developers /
businesses - Partners / enterprises - Administrators - Web portal,
CLI/SDK, and optional mobile access

**POC requirements** - Use Keystone identities, projects, roles, and
scoped access where available. - Validate that users can access only
their authorized projects and resources. - Use Horizon for the existing
OpenStack dashboard if installed. - Use OpenStackClient and/or OpenStack
SDK for CLI and programmatic access. - A custom web portal is optional
and should be implemented only if the specifications/backlog require
it. - Mobile application is deferred for the laptop POC. - Billing
screens must not imply that real payment processing exists unless a
sandbox integration is implemented.

**Acceptance criteria** - Authentication works using the configured
identity endpoint. - A test user can access the intended project. -
Authorization checks reject an unauthorized action. - Dashboard/API
behavior and limitations are documented.

### Layer 2 --- Edge and Security

**Diagram components** - Internet ingress - DDoS protection - Web
application firewall - Load balancer - API gateway - Identity and access
management

**POC requirements** - Do not expose the local OpenStack environment
publicly by default. - Use existing Neutron security groups and host
firewall controls. - Verify TLS and endpoint configuration where
relevant. - Treat Cloudflare, external DDoS protection, a WAF, and a
separate API gateway as optional external integrations, not mandatory
OpenStack services. - Evaluate Octavia only if load-balancing behavior
is required and the local deployment supports it. - Use Keystone as the
OpenStack identity service; do not treat it as an exact equivalent of
every Keycloak feature. - Keep secrets outside source control.

**Acceptance criteria** - Only required ports are exposed. -
Security-group rules are documented and tested. - No credentials or
tokens are committed. - Any unimplemented edge protection is explicitly
marked deferred/simulated.

### Layer 3 --- Cloud Management Control Plane

**Diagram components** - User and tenant management - Resource catalog
and pricing - Provisioning engine - Usage metering and monitoring -
Billing and payments - Database - Job queue

**POC requirements** - Use Keystone for identity, projects, and roles. -
Read Nova flavors, Glance images, Neutron networks, and supported
storage options to build a resource catalog. - Use Nova, Glance,
Neutron, and Cinder APIs for supported resource operations. - Use Heat
or Terraform for repeatable infrastructure workflows where compatible. -
Build a custom provisioning API only if required to join the OpenStack
workflows into the intended user experience. - Track long-running
operations and their outcomes. Redis/Celery may be used only if a custom
job service is justified; do not add them by default. - Use PostgreSQL
only if the custom application requires persistent application state. Do
not duplicate OpenStack service databases. - Evaluate Ceilometer/Gnocchi
for usage data according to the installed OpenStack release and
deployment. - CloudKitty and payment-provider integrations are optional
and must be separately verified. - Pricing in the POC may be a clearly
labelled demonstration calculation based on usage data; it must not be
represented as real billing without a validated implementation.

**Acceptance criteria** - Catalog entries reflect resources actually
available from OpenStack. - Provisioning requests return a job/resource
identifier and a clear status. - Failed operations are reported and do
not show false success. - Usage reports identify their data source and
limitations. - Any demo pricing is clearly labelled as non-production.

### Layer 4 --- Infrastructure Orchestration

**Diagram components** - VM management - GPU scheduling and
partitioning - Container orchestration - Virtual networking - Block
storage - Image registry

**POC requirements** - Use Nova for instances, Glance for VM images,
Neutron for networking, and Cinder for block volumes where available. -
Validate instance lifecycle operations: create, inspect, start, stop,
and delete. - Validate volume creation, attachment, detachment, and
snapshots only if a compatible Cinder backend exists. - Use Heat or
Terraform for repeatable infrastructure deployment where compatible with
the installed cloud. - Kubernetes, JupyterHub, Harbor, Ceph, and other
supporting platforms are optional integrations, not default installation
requirements. - Do not implement GPU scheduling or NVIDIA MIG as if it
were a native Nova feature. - Assess PCI passthrough only after
host/hypervisor capability checks. - The image registry may use Glance
for VM images. Container/model registries are separate concerns and may
be deferred.

**Acceptance criteria** - A small test instance can be provisioned and
deleted if compute virtualization is supported. - Network connectivity
is tested with documented security rules. - Storage tests pass only when
a suitable backend is present. - Unsupported components are recorded
with evidence and a fallback/simulation plan.

### Layer 5 --- Cloud Services

**Diagram components** - CPU virtual machines - GPU instances / MIG
profiles - AI Academy environments - LLM inference services -
Object/file storage

**POC requirements** - Demonstrate small Linux VM workflows if Nova
compute works. - Define flavors based on actual available capacity, not
the H100 example profiles. - Test GPU-enabled containers or small
inference directly on the host only if the NVIDIA driver/container
runtime works. - Treat JupyterHub and managed academy environments as
optional follow-on work. - A lightweight inference API may be developed
as a separate application after the core provisioning workflow is
stable. - Use Cinder for persistent block storage and Swift or a
compatible external service for object storage only if available and
needed. - Do not promise multi-tenant GPU isolation on the RTX 2060.

**Acceptance criteria** - A sample CPU workload completes. - If GPU
inference is feasible, record model, VRAM use, runtime, and
limitations. - The system clearly distinguishes CPU VM resources from
host GPU resources. - Object/file storage claims are limited to tested
capabilities.

### Layer 6 --- Physical Infrastructure

**Diagram components** - Router and internet connectivity - Single H100
GPU server - Future compute/storage/management/backup servers - Power,
cooling, and physical facility controls

**POC requirements** - Do not build or configure physical data center
infrastructure. - Record the laptop hardware and network topology
relevant to the POC. - Future servers, UPS, cooling, physical fire
protection, redundant ISP, and production rack design are deferred. -
Treat production topology in the diagram as context, not laptop
implementation scope.

**Acceptance criteria** - Hardware and network constraints are
documented. - No production hardware purchases or facility changes are
included in the POC tasks.

### Layer 7 --- Operations, Monitoring, and Security

**Diagram components** - Prometheus and Grafana - Loki / Wazuh /
Elasticsearch - Ansible and Terraform - Backup and disaster recovery -
Alerting and incident management

**POC requirements** - Inspect existing monitoring tools before
installing new ones. - Add host/OpenStack/application metrics only where
the relevant exporters and data sources are compatible. - Document which
metrics are genuine, simulated, or unavailable. - Use Ansible/Terraform
for repeatable configuration only where practical. - Implement a backup
procedure for POC configuration and any data that matters; test
restoration where feasible. - Add local logging and basic alerting only
if it does not overload the laptop. - Wazuh, a full SIEM, external
alerting, offsite backups, and production incident response are
optional/deferred. - Do not claim disaster recovery based only on
creating a backup file; perform a restore test.

**Acceptance criteria** - A dashboard shows verified metrics from a
named source, if monitoring is implemented. - A documented backup can be
restored in a test or the limitation is recorded. - Logs and failure
messages help diagnose a failed provisioning task. - Security and
operations limitations are listed.

### Layer 8 --- External Integrations

**Diagram components** - Payment gateway - Domain and DNS -
Email/notifications - Offsite object storage

**POC requirements** - Keep integrations optional and behind explicit
configuration. - Use payment sandbox mode only if payment workflow is
required; never store payment secrets in Git. - Local DNS or test
hostnames may be used; public DNS changes are not required. - Email can
be represented by local logs or a development mail catcher until a real
SMTP provider is approved. - Offsite storage may be simulated or
deferred; do not upload data externally without approval. - External
integrations must fail safely when credentials or configuration are
missing.

**Acceptance criteria** - Each integration is marked implemented,
simulated, deferred, or unsupported. - No external service is assumed to
be active without a successful test. - Credentials are provided through
environment configuration or a secrets manager.

## 4. Cross-cutting functional requirements

1.  **Identity and isolation:** use project-scoped access and least
    privilege.
2.  **Resource lifecycle:** create, inspect, and clean up supported test
    resources.
3.  **Provisioning status:** expose pending, running, succeeded, failed,
    and where applicable cancelled states.
4.  **Error handling:** preserve useful error details without leaking
    credentials.
5.  **Idempotency:** repeatable automation should avoid unnecessary
    duplicate resources where practical.
6.  **Observability:** record operation identifiers, timestamps,
    outcomes, and relevant logs.
7.  **Documentation:** provide setup, execution, verification, known
    limitations, and cleanup instructions.
8.  **Resource safety:** avoid consuming all laptop RAM/disk or creating
    uncontrolled cloud resources.
9.  **Configuration:** separate environment-specific values from code.
10. **Security:** do not expose the dashboard or APIs publicly by
    default.

## 5. Non-functional requirements

-   Must be manageable from the existing Windows 11/WSL2 environment.
-   Must not install, reset, reconfigure, or delete infrastructure during
    the initial audit.
-   Must preserve any existing files, Git changes, and resources discovered
    during the audit, even though the user expects a fresh setup.
-   Must use small tasks that can be reviewed independently.
-   Must provide reproducible verification steps.
-   Must document versions and compatibility assumptions.
-   Must not claim production readiness.
-   Must distinguish verified behavior from mocks, simulations, and
    assumptions.

## 6. Proposed implementation phases and task backlog

The IDs below are proposed task IDs. Codex must inspect the environment
and specifications before finalizing them.

### Phase 1 --- Environment and foundation

**POC-001 --- Read-only laptop and virtualization audit** - Inspect
Windows/WSL2 context where available, Ubuntu version, CPU, RAM, disk,
GPU/driver, virtualization/KVM visibility, Docker/container tooling,
Git, ports, and available resources. Record that the user reports a
fresh/uninstalled setup, but verify actual state. - Acceptance: an
evidence-based audit report exists; no installation, configuration,
cleanup, or system changes were made.

**POC-002 --- Git/GitHub audit and project baseline** - Inspect
repository status, remotes, branch strategy, ignore rules, and push
permissions. - Create/adapt project documentation structure. -
Acceptance: current work is preserved, secrets are excluded, and a safe
commit/push procedure is documented.

**POC-003 --- OpenStack availability and deployment inventory** - First
determine whether any OpenStack packages, containers, services, configs,
credentials, endpoints, or resources remain. If none are present, record
OpenStack as not installed and document that there is no deployment to
inventory. If remnants or an active deployment are found, identify the
release, deployment method, endpoints, compute/storage backends, resources,
and service health without changing them. - Acceptance: inventory includes
evidence and unknowns; no resources were changed.

**POC-004 --- OpenStack and VM-launch feasibility** - If OpenStack is
installed, verify CLI authentication, key endpoints, Horizon access,
hypervisor/KVM capability, and VM-launch feasibility without launching or
changing resources unless separately approved. If OpenStack is absent,
assess WSL2 virtualization constraints and document prerequisites and
safe deployment options without installing anything. - Acceptance:
capabilities are classified as verified, blocked, or not applicable,
with evidence.

### Phase 2 --- OpenStack core

**POC-005 --- Keystone project and role validation** - Validate existing
projects, users, roles, and project-scoped access. Create test
identities only after approval. - Acceptance: authorized and
unauthorized access cases are tested.

**POC-006 --- Glance image workflow** - Inspect available images; use a
small trusted test image if needed and approved. - Acceptance: image
metadata and availability are verified.

**POC-007 --- Neutron network workflow** - Inspect existing networks,
subnets, routers, security groups, and floating IP support. -
Acceptance: required network path is tested without disrupting existing
networks.

**POC-008 --- Nova instance lifecycle** - Provision a small test
instance if supported, then inspect, stop/start, and delete it. -
Acceptance: each lifecycle action has recorded output and cleanup is
verified.

**POC-009 --- Horizon and API consistency** - Verify dashboard access
and compare key resource views with API/CLI results. - Acceptance:
login, project visibility, and resource access match expected
permissions.

### Phase 3 --- Cloud services

**POC-010 --- Flavors and quotas** - Inspect capacity and configure only
safe, approved test values. - Acceptance: flavor and quota behavior is
verified.

**POC-011 --- Repeatable VM lifecycle workflow** - Build a tested script
or service wrapper for create, inspect, stop/start, and delete. -
Acceptance: repeat runs behave predictably and failures are reported.

**POC-012 --- Cinder storage workflow** - Inspect storage backend and
test volume lifecycle only if supported. - Acceptance:
create/attach/detach/snapshot tests pass or a documented blocker exists.

**POC-013 --- Network and security acceptance tests** - Test
connectivity, security groups, SSH, routing, and floating IPs where
available. - Acceptance: positive and negative connectivity tests match
the security rules.

**POC-014 --- Resource catalog** - Retrieve images, flavors, networks,
and storage options from actual APIs. - Acceptance: the catalog does not
invent unavailable resources.

### Phase 4 --- Cloud management and automation

**POC-015 --- Management API design and skeleton** - Define endpoints,
authentication, schemas, errors, and API tests before implementation. -
Acceptance: API contract and minimal tests are documented.

**POC-016 --- OpenStack SDK integration** - Implement only the approved
provisioning/read operations using scoped credentials. - Acceptance: API
operations match OpenStack results and reject unauthorized requests.

**POC-017 --- Job tracking** - Track provisioning job IDs, states,
timestamps, and failure reasons. - Acceptance: successful and failed
jobs are represented correctly.

**POC-018 --- Usage data collection** - Determine which metering
services are installed and compatible; collect supported metrics. -
Acceptance: data source, interval, units, and limitations are
documented.

**POC-019 --- Monitoring and dashboards** - Integrate compatible
exporters and dashboards only if resource budget permits. - Acceptance:
displayed metrics are verified against their source.

**POC-020 --- Usage reporting and automation** - Produce sample usage
reports and automate repeatable infrastructure with
Heat/Terraform/Ansible as appropriate. - Acceptance: reports are
labelled correctly and automation can be verified and cleaned up.

### Phase 5 --- AI, operations, and end-to-end validation

**POC-021 --- GPU feasibility assessment** - Verify RTX 2060 host
access, CUDA/container support, and OpenStack passthrough constraints. -
Acceptance: evidence-based capability report; no assumed MIG support.

**POC-022 --- Sample AI workload** - Run a small workload on the
supported path (host/container or VM if proven feasible). - Acceptance:
workload result, resource use, and limitations are recorded.

**POC-023 --- Security validation** - Test project isolation, API
authorization, secret handling, exposed ports, and auditability. -
Acceptance: negative tests fail safely and no secrets appear in Git.

**POC-024 --- Backup and recovery test** - Back up relevant POC
configuration/data and perform a restore test where feasible. -
Acceptance: restore evidence exists or a specific limitation is
documented.

**POC-025 --- End-to-end demonstration** - Demonstrate authentication →
project selection → resource provisioning → networking → storage where
available → workload → monitoring/usage → cleanup. - Acceptance: each
step has evidence and all simulated/deferred steps are clearly labelled.

## 7. Required task record

Every task must have a Markdown task record containing: - ID and title -
Requirement IDs addressed - Objective and scope - Dependencies -
Prerequisites - Files and resources affected - Implementation plan -
Tests and exact commands - Acceptance criteria - Rollback/cleanup plan -
Risks and limitations - Status: Planned, In Progress, Blocked, Failed,
or Complete - Commit SHA and GitHub push status - Verification evidence

## 8. Git and GitHub workflow

For every task: 1. Check `git status` and preserve unrelated changes. 2.
Confirm task prerequisites and dependencies. 3. Use the approved branch
strategy. 4. Implement only the current task. 5. Run relevant tests. 6. Verify actual
OpenStack behavior where applicable. 7. Review the diff and scan for
secrets. 8. Update docs and task status. 9. Commit with a descriptive
message. 10. Push to the configured GitHub remote. 11. Record commit SHA
and test evidence. 12. Report completion before starting the next task. Do not start the
next task until the current task's acceptance criteria pass and its
commit is pushed successfully.

If tests fail, do not mark the task complete. If push fails, report
"locally committed; push pending," not complete. Avoid destructive Git
commands and broad staging that could include unrelated files.

Suggested commit style: -
`docs(audit): record laptop and OpenStack baseline` -
`feat(keystone): validate project role isolation` -
`feat(nova): add verified instance lifecycle workflow` -
`test(neutron): verify tenant network security` -
`feat(api): add OpenStack provisioning endpoint`

## 9. Suggested repository structure

``` text
openstack-cloud-poc/
├── docs/
│   ├── requirements/
│   │   └── requirements-specification.md
│   ├── architecture/
│   │   ├── architecture-diagram.jpeg
│   │   ├── openstack-service-mapping.md
│   │   ├── local-poc-architecture.md
│   │   ├── hardware-feasibility.md
│   │   └── architecture-gaps.md
│   ├── tasks/
│   │   ├── README.md
│   │   └── POC-001.md
│   ├── testing/
│   └── decisions/
├── infrastructure/
├── services/
├── scripts/
├── tests/
├── .gitignore
└── README.md
```

Adapt this layout to the existing repository rather than duplicating a
structure that already exists.

## 10. Final acceptance criteria

The POC is accepted when: - The environment and service mapping are
documented. - Supported identity and project-isolation workflows are
verified. - A test VM can be provisioned and cleaned up if the compute
path supports it. - Network and storage capabilities are tested where
available. - Management API/job tracking is implemented if required by
the approved scope. - Usage/monitoring reports use real, identified data
sources or are labelled simulated. - A feasible AI workload is
demonstrated or the hardware limitation is documented. - Backup/restore
and security checks have evidence. - The complete end-to-end workflow is
demonstrated. - Every completed task has a commit SHA and successful
GitHub push. - Known limitations and deferred features are documented.

## 11. First execution instruction for Codex

**Do not install, configure, reset, delete, or modify anything yet.**

First verify that these two project files exist and read both completely:

1. `docs/architecture/architecture-diagram.jpeg`
2. `docs/requirements/requirements-specification.md`

Use the architecture image as the visual source of the intended workflow
and this Markdown document as the initial requirements specification.
If either file is missing, report the exact missing path and stop before
finalizing the architecture plan; do not invent its contents.

Perform only POC-001 through POC-004 as read-only discovery and
validation. Avoid creating, launching, deleting, or modifying cloud
resources. Then prepare:

1. `docs/architecture/environment-audit.md`
2. `docs/architecture/openstack-service-mapping.md`
3. `docs/architecture/local-poc-architecture.md`
4. `docs/architecture/hardware-feasibility.md`
5. `docs/architecture/architecture-gaps.md`
6. `docs/tasks/README.md` with the dependency-ordered task backlog
7. Any blockers, risks, and decisions requiring user approval

For every architecture layer, classify each component as **native
OpenStack**, **OpenStack integration**, **custom application**, **external
service**, **simulated for the POC**, **deferred**, or **unsupported**.
Explicitly state which features can run locally, which can only be
simulated, and which are unsupported by the laptop/WSL2 environment.
Base those classifications on observed evidence and clearly label
unknowns.

Stop and wait for approval before any installation or infrastructure
changes. After approval, implement one small task at a time. For each
task, test it, verify actual behavior, update documentation, review the
diff for secrets and unintended changes, commit it, push it to GitHub,
and record the commit SHA and verification evidence. Never mark a task
complete if verification fails or the GitHub push is still pending.
