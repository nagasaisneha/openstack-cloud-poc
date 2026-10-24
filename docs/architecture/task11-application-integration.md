# Task 11 — real application provider integration

Status: real application create/inspect, catalog, image integrity, KVM and Neutron binding passed in the operator terminal. Guest/packet, application stop/start/delete and cleanup remain pending. Infrastructure and member-project results are recorded in [live deployment evidence](task11-live-deployment-evidence.md).

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

1. One task-marked DHCP subnet on tenant A's existing private network. Before subnet creation an operator-visible inventory must show no subnets and either no ports or one exact empty OVN metadata port. It chooses a CIDR outside observed host routes and Neutron subnets, preferring `172.30.241.0/24`. A gateway address is reserved but no router/external connectivity is created.
2. One owned security group with TCP/22 and ICMP ingress limited to that network's OVN metadata address `/32`. Unexpected broader/duplicate rules stop the stage. A user-scoped keypair reuses the retained root-only test key.
3. One private 1-vCPU, 256-MiB, 1-GiB flavor, available only to the task project. One private tenant image copied from the verified admin image; SHA256 and exact size are checked. The original image is not shared, replaced or deleted.
4. One application-created VM, after checking tenant quotas and at least 768 MiB host available memory. Nova/Placement remain authoritative for actual scheduling. The original infrastructure-test VM stays running.

Additional root-only operational files are `application.json` and `application-cirros-source.qcow2`, plus the journal and its lock. The manifest preallocates image/flavor IDs before creation and records dependency IDs and job/server IDs. Existing same-name objects require exact owner/marker matches. The corrected image path described below preserves incomplete uploads/source copies for diagnosis; the original SDK helper had an implicit cleanup handler. The original verified pre-Caracal backup remains untouched. No host routes, OVS/OVN service settings, WSL configuration or unrelated resources are changed.

Acceptance requires actual member catalog references, application rejection of foreign scope, accepted job distinct from Nova UUID, observed job success, same durable job for duplicate submission, synchronous settled `ACTIVE` inspection, domain type KVM plus QMP KVM enabled, and an active Neutron port with the recorded group/address. These do not establish guest DHCP, metadata delivery, packet isolation, or application lifecycle/cleanup; those are the next dependency-ordered checks.

## Recovery, cleanup and remaining plan

On failure, retain credentials, journal, manifests, source image and resources. Diagnose the first failed dependency; reuse the recorded IDs/idempotency key. The corrected path has no automatic rollback deletion or uncertain upload replay. An explicit image registration HTTP 409 permits one new image UUID, with the rejected attempt preserved in the manifest, as detailed below. No infrastructure service is installed or restarted by this stage.

After guest and packet acceptance, stop/start and delete only the application-created server through the real service. Verify Nova absence, libvirt domain/disk cleanup and Neutron compute-port cleanup independently before deleting owned image/key/flavor/subnet/security-group dependencies. The infrastructure-test VM is a distinct run resource and requires its own final cleanup validation. Preserve historical VM disks, operational/billing data and all existing recovery backups. Take a protected operational backup of the new root state and a consistent journal backup (or file copy only after the service is closed) before final cleanup.

Remaining sequence:

1. Execute application create/inspect acceptance and record actual IDs/results.
2. Verify guest DHCP/metadata/MTU and cross-tenant packet boundaries with positive controls.
3. Execute application stop/start/delete and independent cleanup acceptance, retaining journal evidence.
4. Complete owned test-resource cleanup; review local listener exposure, logrotate operation and WSL restart persistence where feasible.
5. Update actual architecture, task status and start/stop instructions; rerun applicable acceptance checks and commit only verified related work. Do not mark staging/production readiness or the entire POC complete from fixture results.

Workspace verification: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` passed **275 tests**. Of these, 16 new tests exercise the real management boundary using SDK doubles, including uncertain response handling, explicit Nova errors, missing completion, task ownership, idempotency, reopen behavior and safe errors; 6 new tests exercise acceptance-script image/rule safeguards. The non-root guard and `git diff --check` passed. Host acceptance remains pending.

## Pre-subnet inventory guard correction

The first operator attempt reached member project `c039831922bc457a8b9a2bc903e4d997` and stopped with `tenant network has unexpected existing consumers; preserving it`. The original guard rejected any port or subnet, without reporting its classification. This invocation stopped before the subnet, image, flavor or Nova create calls; only the root-only application manifest had been initialized. The supplied output does not identify the live consumer.

Inspection of the installed Caracal Neutron driver confirmed that `OVNClient.create_network` calls `create_metadata_port`. That function creates a `network:distributed` port with `device_id=ovnmeta-<network UUID>`, the network's project ID, and an empty fixed-IP list when there are no DHCP subnets. The original guard therefore cannot distinguish normal OVN metadata infrastructure from a workload consumer. This is a verified defect in the guard and a likely explanation of the operator stop, not a confirmed classification of that live port.

The corrected preflight queries ports and subnets with the existing operator connection, so member filtering cannot hide consumers. It emits only normalized port/subnet UUIDs, owner/network/device-match booleans and fixed-IP counts. It allows zero subnets and at most one empty port matching the exact network, project, distributed owner and metadata device ID. It records that inventory before subnet creation and preserves the port. Workload/router/unbound ports, malformed IDs, other projects/networks, an inexact metadata device ID, allocated/unknown IPs, duplicate metadata ports, or any subnet still stop the run. No port is deleted, relabelled or reset.

Retry the same command above with the same protected manifest and IDs. An accepted metadata port will be explicitly reported; an unexpected consumer will produce the limited inventory and stop before subnet creation. No host diagnosis is inferred from offline tests.

Verification after correction: **12 application safety tests**, including six new inventory regressions, passed; all **281 offline tests** passed. The non-root guard and whitespace check passed. Live retry/application acceptance remains pending.

## SDK checksum failure and image recovery correction

The next operator run classified port `78314f94-b2b5-4213-804f-307af1fc0c7b` as the exact empty OVN metadata port: owner, device ID, project and network matched, fixed-IP count was zero and no subnets existed. The inventory gate passed and preserved that port. The run registered the task public key and then stopped with `SDKException: Image creation failed: Image checksum verification failed`. The application job submission/Nova create path was not reached. The subnet/group/key/flavor preparation had already been reached; retained IDs remain in the protected manifest.

The original call supplied only `sha256` with `validate_checksum=True`. In the installed SDK, `Proxy._upload_image_put` compares Glance's legacy `checksum` to either the supplied MD5 or SHA256 string. Glance's [API defines the legacy field as MD5 and provides secure multihash fields separately](https://docs.openstack.org/api-ref/image/v2/index.html). A nonempty MD5 cannot match a SHA256 value, so this call can report failure for correct bytes. This is an integration defect in the upload call; it does not establish image corruption.

The installed helper's exception handler calls `delete_image` for the just-created image. The prior generic retention message therefore did **not** guarantee that failed new image record survived. Its present state, deletion outcome and checksum fields have not been read back from the host. The original source image and historical backups are distinct from that new upload target. No database record is deleted or edited by the correction.

The replacement path performs the following checks and bounded actions:

1. GET the exact recorded image ID first. Only HTTP 404 permits fresh registration. A present image must match its ID, project, name, private visibility, format and run marker. Active matching images are reused after verification. A queued image with unknown/uncertain upload history stops for reconciliation.
2. Verify the retained source file against its pinned SHA256 and exact size. Register metadata separately with public SDK `Image.create`, then upload through `Image.upload`. These installed resource methods have no helper cleanup/delete handler. Persist registration/upload intent before requests; retain image and phase on failure or timeout.
3. If registration explicitly returns HTTP 409 after the lookup returned 404, preserve the rejected ID/statuses in `previous_image_attempts`, allocate one fresh UUID and record it before one further metadata request. This accommodates an ID retained by a deleted record without deleting history or claiming that a tombstone is the confirmed conflict cause. Timeouts, server errors, ambiguous direct-operation state, or a missing image already used by an application job never trigger this replacement.
4. Wait for `active`, require the exact expected size, compare legacy MD5 to MD5 when present, and require a matching secure `sha256` or `sha512` multihash. Independently stream the entire image back and require the pinned SHA256 and exact byte count. The readback has a bounded deadline and closes its response on errors. No full image bytes, arbitrary properties, tokens or passwords are printed.
5. Record current image integrity only after all checks pass, then continue to application catalog and Nova acceptance. A verification retry clears current image success before checking. Failure retains the upload and evidence instead of silently deleting it.

Retry the normal command above. It first reports whether the recorded image is present or absent and safely reuses the existing subnet, group, key and flavor. A later blocked queued/uploading state requires inspection rather than another upload request. Credentials and pre-Caracal recovery assets remain protected.

Verification after this correction: **27 application safety tests** passed, including 15 new tests for algorithm-aware hashes, complete readback, identity checks, separate registration/upload, retained failed uploads, exact conflict handling and refusal to replay uncertain requests. All **296 offline tests** passed. The installed SDK's metadata request serialization was inspected using nonsecret sample fields without endpoint contact. The non-root guard and whitespace check passed. Actual image integrity and live application acceptance remain pending.

## Verified application create result

The operator's next run found the prior image absent (HTTP 404), observed registration HTTP 409 and preserved that ID before allocating a fresh one. Image `active`, MD5/secure multihash and full downloaded SHA256 checks passed. The conflict is compatible with an ID retained after deletion; its actual database cause remains unverified.

The application catalog, scope rejection and accepted job `openstack-job-171247d792da4aeeb6e2e1cd64e62706` passed. The job progressed `running` to `succeeded` with Nova server `db0d12b8-3bf4-4e53-9a63-4bbea9971831`. Synchronous inspect, duplicate same-job lookup, actual KVM and active Neutron binding passed. These are live operator-reported results. The source image and infrastructure-test VM remain distinct resources. This stage does not establish the new guest's DHCP/metadata/packets, application stop/start/delete or cleanup.

## Prepared application guest and ARP observations

Run in the existing Ubuntu 22.04 terminal:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/validate_application_guest.py
```

The script checks the root-only application manifest against a read-only SQLite lookup of the completed create job, actor/project/run and resource references. It checks current Nova metadata/ownership, Neutron network/subnet/port/group relationships, permitted probe rules and the metadata port/source namespace. It reuses the retained SSH private key, creates a dedicated 0600 `application-known_hosts` file without replacing existing trust, and verifies the application guest's DHCP lease, IPv4/MAC/MTU and metadata UUID/name. Existing guest helper defaults are preserved; the application supplies its own expected server name and trust path explicitly.

After successful guest checks and MTU-sized ping, the script uses both task guests as positive controls. It reads `arp_ignore` and `arp_filter` for each guest's `all` and active interface settings and requires zero values; configured off-subnet ARP suppression would make the intended negative test inconclusive. It requires three same-network ARP reply windows matching each known Neutron guest MAC. It then sends three layer-2 ARP requests per direction from each metadata namespace to the other guest's recorded IPv4 address, requiring no matching replies. It repeats the same-network controls, guest identity/boot continuity and MTU pings afterward. Any foreign reply, incomplete observation, unavailable raw socket, failed control or reboot keeps the packet result unverified.

The [standard-library ARP child](../../openstack_cloud_poc/packet_probe.py) binds only the previously validated metadata interface, uses its actual hardware address and assigned metadata source IPv4, and emits at most three requests per invocation. Socket deadlines, frame receive limits and subprocess timeouts bound execution. It validates Ethernet/ARP types, operation, hardware/protocol lengths, sender/target IPv4 and MAC consistency. Unlike a routed cross-subnet ping, the ARP probe does not depend on a missing IP route; no temporary address, route, router or security-group change is needed. The namespace access path is described in [Neutron's OVN reference architecture](https://docs.openstack.org/neutron/2024.1/admin/ovn/refarch/refarch.html).

This is limited active evidence about ARP resolution from the two metadata namespaces to these two IPv4 targets, with positive controls. It is not proof of all IPv4/IPv6/protocol isolation, hostile-tenant resistance, routed/external connectivity, all project pairs or multi-host overlays. Guest readiness and packet evidence have separate success fields in `application.json`; failures retain both VMs, policies, topology, pinned keys and diagnostics. No infrastructure configuration or VM lifecycle is changed by this stage. Unsuccessful ARP tests do not trigger speculative network fixes.

Verification: all **310 offline tests** passed. Fourteen added tests cover unchanged/default versus explicit guest identity/trust behavior, ARP frame validation, completed send/receive observations, manifest/journal scope, separate protected trust files, malformed/failed probes, ARP suppression, before/after controls and guest continuity. The tests use doubles and send no packets. The non-root guard and whitespace check passed. Live execution of this new stage remains pending.

## Verified application guest and interface parser correction

The operator ran the guest stage against `db0d12b8-3bf4-4e53-9a63-4bbea9971831`. The read-only durable-job/scope guards completed, and the script explicitly reported key-only SSH, matching guest identity, applied DHCP, metadata and MTU-sized ping **PASS**. It then stopped with `KeyError: 'address'`. Guest success was saved separately; packet success was not recorded. The error occurred while assembling metadata-interface probe endpoints, before any ARP child invocation.

The interface parser incorrectly assumed `ip -j -4 addr show` includes the Ethernet `address` field. A read-only comparison using this host's loopback output confirmed that the IPv4 address JSON omits that link-layer field while `ip -j link show` includes it. The loopback check was only an output-format diagnostic, not an Ethernet/namespace test. Earlier workflow tests mocked interface discovery and missed this realistic shape.

The correction selects the interface owning the recorded metadata IPv4 using the address inventory, then explicitly reads `ip -j link show dev <interface>` in the same namespace. It requires exactly one matching interface name/index, an active Ethernet link and a valid unicast MAC. It does not guess a MAC or use a stale address-dump value; missing/changed/invalid data stops before probes. No routes, addresses, policies, services, guests or pinned keys are changed.

Retry the guest-stage command above. Fresh guest checks act as positive controls for the pending negative packet observations; they do not reinstall services or repeat image upload/KVM deployment. Verification after this correction: **12 application guest tests** passed, including five new interface-discovery regressions; all **315 offline tests** passed. Whitespace validation passed. The live parser retry and ARP isolation result remain pending.
