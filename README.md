# OpenStack Cloud Management POC

## Goal
Deliver a browser-ready, secure, multi-tenant OpenStack self-service platform with administration, project-scoped resource provisioning, real usage tracking, project-level billing, and auditability. The POC is not complete when it exposes APIs or installs Horizon alone: the acceptance workflow must pass through the browser, including authorization, usage, and billing checks. See the [browser-ready goal and audit](docs/architecture/browser-ready-cloud-goal.md) and its [task breakdown](docs/tasks/README.md#browser-ready-platform-delivery-track).

## Environment
Ubuntu on WSL2 is the local development and POC environment. The staging environment has not been selected or approved. The long-term production direction remains OpenStack on organization-owned data-center infrastructure.

## Architecture and scope
The local POC uses OpenStack Keystone, Glance, Nova, and Neutron; Nova is the selected tenant compute path and Proxmox is deprecated. Operator records verify the core API/resource path, but the repository has no browser portal, Horizon is not installed in the current local package inventory, and Cinder and Swift are not established. The billing ledger has an offline domain implementation, while its usage source is fixture-only; neither real billing nor browser workflows are complete. These are active delivery gaps, not completed or optional parts of the platform goal. The separate fixture path remains simulated. The [staging release plan](docs/staging-release-plan.md) maps local implementation evidence to capabilities that must be revalidated in a separate environment. AWS-hosted OpenStack and AWS-native EC2 provisioning are outside the approved implementation scope.

The [restore/restart sequence](docs/architecture/task11-recovery-and-restart.md) passed for the existing core services. That is a verified infrastructure baseline, not acceptance of the expanded browser-ready platform goal. The new T29–T41 track adds the missing browser, service catalogue, metering, billing, audit, and staging evidence requirements. Horizon and optional services must be deployed and verified without disrupting the retained VM or existing services.

## Staging and production delivery
[`docs/staging-release-plan.md`](docs/staging-release-plan.md) defines how the local POC informs a distinct staging validation, followed by controlled production rollout. It links the original requirements, architecture, implementation task IDs, and acceptance evidence, and identifies where local results are insufficient.

## Implementation rules
- Use OpenStack-native services where appropriate.
- Implement one task at a time.
- Test and document every task.
- Commit each verified task locally in a focused Git commit. Push only when explicitly requested.
- Preserve existing work and avoid destructive changes.
- Do not treat the local WSL2 environment or fixture results as production evidence.
- Enforce authorization in backend service paths; UI visibility alone is not access control.
- Keep project-level usage and billing separate from individual user identities; never store or commit credentials.
- Mark simulated and estimated data clearly and do not describe payment collection or tax-compliant invoicing as implemented.
- Do not add AWS-specific APIs, SDKs, services, or infrastructure provisioning to the local POC; AWS-hosted OpenStack and AWS-native tenant provisioning are outside current implementation scope.
- Use staging as the production release gate; production changes require explicit scoped approval.

## Existing development configuration (historical T04 deliverable)

- [`config/poc.example.yaml`](config/poc.example.yaml) is a development-only template for the separate deterministic fixture adapter with real integration disabled. Copy it to ignored `config/local.yaml` for local-only settings; keep credentials and endpoint details out of tracked files. The real application uses protected operator configuration described in the operational record; this sample file does not initiate connections.
- [`.gitignore`](.gitignore) excludes local environment files, `config/local.yaml`, logs, Python bytecode caches and `state/`. Real manifests, journals, recovery material and diagnostics remain in protected host directories and must not be committed.
