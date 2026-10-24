# Task 11 — real application provider integration

Status: implemented and offline-tested; privileged host acceptance pending. The successful infrastructure and member-project tests are recorded separately in [live deployment evidence](task11-live-deployment-evidence.md). No real application job/VM outcome is claimed yet.

## Application boundary

`openstack_cloud_poc/real_openstack.py` adds `RealOpenStackAdapter` and `RealLifecycleManagementService` alongside the existing fixture path. It reuses `LifecycleRequest`, `Operation` and catalog records. The existing shared contract/catalog/identity files are included as direct dependencies without changing fixture behavior. No HTTP server, extra daemon, database server or Python package installation is introduced. The POC invokes the application service in-process using the installed OpenStack SDK.

- Authentication comes from an explicitly injected SDK session and expected project/user IDs. Project scope and reader/member roles are checked against Keystone before metadata reads, submission, worker execution and job lookup. Admin/service/system tokens are rejected by this application path. Public request payloads contain resource references only.
- Catalog records preserve real provider IDs, MiB/GiB units and provenance. Visible active images/networks have API observation evidence. Flavor availability remains unverified for scheduling/quota; a listed flavor is not a placement guarantee. Storage categories remain missing because no Cinder integration is established.
- Inspection is synchronous. Create/start/stop/delete use one worker with at most two accepted jobs. API acceptance returns an `openstack-job-*` ID with no resource ID; it does not imply provider completion. A job separately records the returned Nova UUID as soon as available. Fixture IDs are rejected.
- Completion requires observed Nova status, power and no pending task; deletion requires a subsequent provider 404. Mutations require matching project, `poc_run_id`, originating `poc_job_id` and a recorded application create. Operator acceptance additionally checks libvirt/KVM and Neutron rather than trusting the application journal alone.
- The initial POC path accepts UUID references, including a UUID flavor ID. Numeric flavors and other provider namespaces are not supported by this restricted path. It is not a general production OpenStack management service.

The asynchronous server model follows the [Compute API](https://docs.openstack.org/api-ref/compute/). Role interpretation follows [Keystone 2024.1 default roles](https://docs.openstack.org/keystone/2024.1/admin/service-api-protection.html). Installed SDK signatures were checked locally. Actual policy and provider behavior must still pass live tests.

## Durable jobs and failure semantics

The standard-library SQLite journal is `/root/openstack-poc-acceptance/application-jobs.sqlite` (0600, parent 0700). Its lifetime advisory lock prevents multiple service processes opening it concurrently. SQLite uses full synchronous commits; state does not rely on the fixture in-memory tracker. Job IDs, actor/project/run, validated request IDs, a hashed idempotency key, timestamps, known Nova UUID and allowlisted errors are persisted. Credentials, tokens and raw provider exceptions are not stored.

Identical project/actor/operation/idempotency-key submissions return the same job, including after reopening the journal; conflicting payloads are rejected. One resource cannot have concurrent tracked mutations. Completion and submission are distinct. Response loss, timeout after a possibly accepted operation, and interrupted pending/running jobs remain `unverified`, retaining any known server ID. Duplicate submission never replays such an operation. This is not an exactly-once guarantee or automatic crash recovery.

Unverified jobs require operator reconciliation: inspect the known Nova UUID; if create returned no ID, use the exact journal job ID as the Nova name and require both `poc_job_id` and `poc_run_id` plus the recorded project. Inspect instance actions, task/power state, ports and libvirt as appropriate. Do not reset a journal row to pending, clear the journal, or submit a fresh create key to hide an uncertain outcome. Automatic reconciliation/cancellation, distributed workers, multi-user routing, retention policies and production durability/availability are outside this implementation. A worker close waits for accepted jobs to finish.

## Host acceptance scope and command

The existing root-only tenant credentials and manifests are required. Codex does not have an authenticated sudo session. Execute in the existing Ubuntu 22.04 terminal:

```bash
sudo /usr/bin/python3 /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/validate_application_create.py
```

The script uses tenant A from the verified isolation run. Admin access prepares only the new private flavor grant and reads source-image/inventory data. The application uses the member session for catalog reads and Nova operations. It creates:

1. One task-marked DHCP subnet on tenant A's existing empty private network. It chooses a CIDR outside observed host routes and Neutron subnets, preferring `172.30.241.0/24`. A gateway address is reserved but no router/external connectivity is created.
2. One owned security group with TCP/22 and ICMP ingress limited to that network's OVN metadata address `/32`. Unexpected broader/duplicate rules stop the stage. A user-scoped keypair reuses the retained root-only test key.
3. One private 1-vCPU, 256-MiB, 1-GiB flavor, available only to the task project. One private tenant image copied from the verified admin image; SHA256 and exact size are checked. The original image is not shared, replaced or deleted.
4. One application-created VM, after checking tenant quotas and at least 768 MiB host available memory. Nova/Placement remain authoritative for actual scheduling. The original infrastructure-test VM stays running.

Additional root-only operational files are `application.json` and `application-cirros-source.qcow2`, plus the journal and its lock. The manifest preallocates image/flavor IDs before creation and records dependency IDs and job/server IDs. Existing same-name objects require exact owner/marker matches. An incomplete upload/source copy is preserved for diagnosis, not overwritten or deleted. The original verified pre-Caracal backup remains untouched. No host routes, OVS/OVN service settings, WSL configuration or unrelated resources are changed.

Acceptance requires actual member catalog references, application rejection of foreign scope, accepted job distinct from Nova UUID, observed job success, same durable job for duplicate submission, synchronous settled `ACTIVE` inspection, domain type KVM plus QMP KVM enabled, and an active Neutron port with the recorded group/address. These do not establish guest DHCP, metadata delivery, packet isolation, or application lifecycle/cleanup; those are the next dependency-ordered checks.

## Recovery, cleanup and remaining plan

On failure, retain credentials, journal, manifests, source image and all resources. Diagnose the first failed dependency; reuse the same recorded IDs/idempotency key. No automatic rollback deletes resources or replays an uncertain mutation. No infrastructure service is installed or restarted by this stage.

After guest and packet acceptance, stop/start and delete only the application-created server through the real service. Verify Nova absence, libvirt domain/disk cleanup and Neutron compute-port cleanup independently before deleting owned image/key/flavor/subnet/security-group dependencies. The infrastructure-test VM is a distinct run resource and requires its own final cleanup validation. Preserve historical VM disks, operational/billing data and all existing recovery backups. Take a protected operational backup of the new root state and a consistent journal backup (or file copy only after the service is closed) before final cleanup.

Remaining sequence:

1. Execute application create/inspect acceptance and record actual IDs/results.
2. Verify guest DHCP/metadata/MTU and cross-tenant packet boundaries with positive controls.
3. Execute application stop/start/delete and independent cleanup acceptance, retaining journal evidence.
4. Complete owned test-resource cleanup; review local listener exposure, logrotate operation and WSL restart persistence where feasible.
5. Update actual architecture, task status and start/stop instructions; rerun applicable acceptance checks and commit only verified related work. Do not mark staging/production readiness or the entire POC complete from fixture results.

Workspace verification: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` passed **275 tests**. Of these, 16 new tests exercise the real management boundary using SDK doubles, including uncertain response handling, explicit Nova errors, missing completion, task ownership, idempotency, reopen behavior and safe errors; 6 new tests exercise acceptance-script image/rule safeguards. The non-root guard and `git diff --check` passed. Host acceptance remains pending.
