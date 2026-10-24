# Task 11 — real application provider integration

Status: real application create/inspect, catalog, image integrity, KVM, Neutron binding, guest/ARP checks, stop/start, verified recovery backup, deletion and temporary dependency cleanup passed in the operator terminal. Both temporary projects/users and the supplemental default group are removed; original demo remains `ACTIVE`. [Operational closeout](task11-operations-and-closeout.md) remains pending. Earlier commands/results form a chronological record; do not replay completed acceptance or cleanup commands.

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

## Verified guest/ARP result and prepared application lifecycle

The operator's retry passed application SSH/identity/DHCP/metadata/MTU checks, three same-network ARP reply windows on each network with matching Neutron MACs, and three cross-network requests per direction without replies. Before/after controls and guest boot continuity passed. The stage completed successfully; its limited ARP-path scope above still applies.

The next stage operates only on application server `db0d12b8-3bf4-4e53-9a63-4bbea9971831`. The explicit command-line UUID must match the protected manifest and durable create job. It requires the prior guest/packet success, rechecks ownership through member-scoped APIs, and runs application `compute.stop`/`compute.start` jobs with stable idempotency keys. Stop must be independently shut off in Nova/libvirt. Start must have a new boot ID and recovered SSH/DHCP/metadata/MTU/KVM. It then stops the same VM again for backup before deletion.

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/validate_application_lifecycle.py --delete-owned-test-vm db0d12b8-3bf4-4e53-9a63-4bbea9971831
```

This command **backs up and deletes that task-created application VM after verified stop/start recovery**. It preserves the original infrastructure-test VM, images, networks/subnets/security groups, credentials, historical billing/metering data and backups. No service/network configuration is changed. No manual removal of instance directories or ports is used to make a failed cleanup appear successful.

The recovery point is created under `/root/openstack-poc-acceptance/recovery-<application UUID>-<unique suffix>` (0700). Files are root-only (0600). The path is recorded before copying; failed attempts remain intact and a retry can create a distinct attempt. At least 2 GiB Linux filesystem free space is required. Windows physical free space is not inferred from this Linux check; write failure stops the stage before deletion. The protected bundle contains:

- `domain.xml`, the source disk path/format/ownership metadata and `guest-root.qcow2`. Only one ordinary writable disk at the exact Nova instance path is supported. Unexpected attachments, NVRAM, TPM, filesystem passthrough or host devices stop the backup. The VM must be `SHUTOFF` with no pending task and present in libvirt's shutoff list before and after the snapshot. `qemu-img convert` creates a flattened, independent copy; `qemu-img check`, exact virtual size and lack of backing file are required, followed by guest-visible content comparison against the offline source. No lock-bypassing `-U` or source repair is used. See [QEMU's offline image utility](https://www.qemu.org/docs/master/tools/qemu-img.html).
- `application-jobs.sqlite`, made through SQLite's backup API and checked using `PRAGMA integrity_check`; no active-journal file copy is used.
- `cloud-databases.sql`, covering Keystone, Glance, Placement, Nova API/cell databases and Neutron. The helper checks that all seven exist and their base tables use InnoDB, then invokes `mariadb-dump --no-defaults --protocol=socket --user=root --single-transaction --quick --routines --events --triggers --hex-blob --databases keystone glance placement nova_api nova nova_cell0 neutron`. It checks successful completion and expected database sections without printing data. Nontransactional tables stop the planned dump. Consistency assumes no simultaneous DDL/schema migration; see [MariaDB's dump documentation](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump).
- `open_vswitch.ovsdb`, `ovn_northbound.ovsdb`, `ovn_southbound.ovsdb`, each generated with `ovsdb-client backup <verified Unix endpoint> <verified database name>` and checked using `ovsdb-tool db-name`. These are individually consistent, sequential live snapshots; they are not a distributed transaction with SQL or each other.
- `operational-files.tar.gz`, containing the root manifests, retained SSH keys/trust, tenant and control-plane secret files, the verified source image and relevant current OpenStack, Apache, database, RabbitMQ, libvirt, OVS/OVN, network, logrotate and KVM-unit configuration. Symlinks are preserved without following them; ordinary active `.db` files are excluded. Gzip CRC and expected top-level members are checked. No archive contents, credentials or SQL rows are printed.
- `RECOVERY.md` and `recovery.json`, recording the exact VM/disk identity, required artifacts, SHA256 hashes and verification results. Files/directories are synced before the bundle is accepted. Every required artifact and hash must pass immediately before deletion. Backup errors retain private diagnostics instead of echoing potential secrets.

No full backup restoration or guest boot from the snapshot is claimed. The non-destructive recovery option is to upload the self-contained disk as a new private image and launch a new VM UUID with the recorded project/network/key/flavor after approving that recovery scope. Full same-UUID rollback would rewind all seven cloud databases and requires separate explicit approval, service-write quiescence and preservation of any newer data. Verify hashes first; use the matching MariaDB client and existing service users; restore compatible OVS/OVN snapshots through the existing quiescence procedure; recreate only the absent recorded instance directory with its saved ownership/mode; convert the saved disk back to the recorded format through a new temporary file and install only when its destination is absent; recover the recorded libvirt domain and start through Nova after database/service checks. Never overwrite a live instance directory or restore SQL into an actively changing cloud. Detailed constraints are retained in `RECOVERY.md`. This is a deletion recovery point on the existing host, not a Windows/bare-metal backup; unchanged RabbitMQ/service-user state and historical pre-Caracal backups remain separate dependencies.

After the verified backup, the application submits `compute.delete` with a stable key and retains its job ID separately from the Nova UUID. Completion requires operator-side Nova 404, Neutron port 404 and no compute ports, no libvirt domain, absence of the original Nova instance disk/directory, empty Placement allocations, and zero tenant compute quota usage. It checks that the original infrastructure-test VM remains `ACTIVE`. Network/image/key/flavor dependencies are retained for the following cleanup stage. Disk caches and recovery copies are intentionally not treated as leaked live instance allocations.

Failures preserve durable job/phase evidence and may leave the application VM stopped. Pending/uncertain jobs are not automatically replayed. A persisted deletion intent allows later independent cleanup verification using the same durable job, including when the instance is already absent. A failed/changed recovery bundle prevents any new delete request. No automatic whole-cloud rollback runs.

Verification: all **330 offline tests** passed. Eight recovery tests include real offline conversion/flattening/comparison of an owned temporary disk with nonzero data, SQLite snapshot independence, archive handling, private diagnostics and damaged/missing artifact rejection. Seven lifecycle tests cover ordering, attachment readiness after start, backup gates, exact HTTP absence semantics, Placement/quota checks and interrupted deletion recovery. These do not execute the live lifecycle/backup/delete sequence. Required command binaries were found; the non-root guard and whitespace check passed. Host execution remains pending.

## Verified application lifecycle and deletion recovery point

The operator executed the lifecycle stage for exact application VM `db0d12b8-3bf4-4e53-9a63-4bbea9971831`. Each application job was observed `running` then `succeeded`; independent host/provider checks also passed:

| Operation | Application job | Observed result |
| --- | --- | --- |
| Stop | `openstack-job-4bc1cd0aeab040668063da6c7b492dd9` | Nova `SHUTOFF`, power 4, libvirt shutoff |
| Start | `openstack-job-391f200327624391bafd93a48f306d33` | Changed guest boot ID; recovered SSH, DHCP, metadata, MTU-sized packets and actual KVM |
| Stop for backup | `openstack-job-0954ef5bcd1f4fefa0f1d44ef9dce9f6` | Offline recovery point permitted only after shutdown |
| Delete | `openstack-job-983d0e16c19f4ee6ae76a7fbf1295554` | Operator Nova/Neutron absence, libvirt/disk cleanup, empty Placement allocations and zero tenant compute usage |

The verified root-only recovery bundle is `/root/openstack-poc-acceptance/recovery-db0d12b8-3bf4-4e53-9a63-4bbea9971831-lu4cuzr1`. Offline disk, SQLite, transactional cloud SQL, live OVSDB and operational archive checks passed before the application delete request. The detailed artifact checks and recovery constraints above apply. This establishes backup integrity, not a rehearsed restore or one distributed atomic snapshot. All historical recovery points remain preserved.

Original infrastructure-test VM `82382430-4b64-4386-a55b-8468e04da011` remains `ACTIVE`. Application images, network dependencies, protected credentials, retained source image, pinned keys and durable journal remain intact for scoped dependency cleanup. No service restart or host topology reconfiguration was required by this lifecycle acceptance result.

## Prepared dependency cleanup; live execution pending

The [cleanup script](../../scripts/local-openstack/cleanup_application_dependencies.py) requires the exact run marker, protected manifests and completed create journal, verified application deletion, and an intact application VM recovery bundle. It first inventories both task-created isolation projects/users. It refuses unexpected VMs, networks, subnets, images (including hidden images), ports, routers, floating IPs, security groups, user keypairs, group memberships, application credentials, role assignments, cross-group references, sharing policies or private-flavor consumers/grants. Additional catalog service families require a separate inventory before project deletion; no storage-service emptiness is assumed.

It creates and verifies a new 0700 root-only `dependency-recovery-<run>-<suffix>` bundle before any deletion: SQLite backup-API snapshot, transactional cloud SQL, three live OVSDB snapshots, protected operational/configuration archive, exact cleanup plan and recovery notes. SHA256 checks, required artifacts and the exact plan must pass. The prior VM bundle supplies the preserved offline guest disk; neither bundle replaces historical backups. Same-ID SQL/OVSDB rollback still requires approved write quiescence and protection of newer data. For ordinary recovery, prefer a new project/resource scope using retained images or the flattened disk. No restore is executed or claimed.

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/cleanup_application_dependencies.py --apply-run b0a2694420364856a755347a048ea36a
```

This removes only the recorded application subnet/security group/private image/private flavor/user keypair, both task tenant networks and unused default security groups, the two task users' recorded member assignments, and the otherwise empty task users/projects. Keypair queries/deletion explicitly select the task user with Nova microversion 2.10; keys are user-scoped rather than project-scoped ([Compute API reference](https://docs.openstack.org/api-ref/compute/)). Standard Neutron API deletion handles metadata-port dependencies; it never forces removal of OVS objects or Linux namespaces ([Networking API reference](https://docs.openstack.org/api-ref/network/v2/index.html)).

The script checks ownership before each mutation and records each observed absence. On retry, it binds the persisted plan to the original manifests again, verifies the same recovery point, skips completed removals, and accepts only HTTP 404 as absence. Authorization failures, timeouts and unexpected consumers stop rather than being interpreted as success. It requires the two selected OVN logical switches, metadata namespaces and Neutron ports to disappear through normal service reconciliation. It finally checks that the original demo VM remains `ACTIVE`.

It retains the original demo VM/network/subnet/group/key/flavor/base image, shared roles, service identities, `br-int`, all historical billing/metering data, configurations, backups, root credential files, manifests, SSH trust/source files and job history. No file deletion, package operation, SQL mutation, service restart, raw network mutation or automatic recovery occurs. Completed temporary tenant identities cease to authenticate after deletion; old acceptance scripts must not be replayed to recreate them. Future demonstrations of the general application path require a fresh authorized member scope and new job/resource references.

Eleven added offline tests cover protected/duplicate/changed plan targets, ownership/sharing drift, exact OVN metadata-port consumers, user-scoped public-key matching, project/keypair/role consumers, HTTP error classification, backup gates, absent-resource retries and partial progress. All **341 offline tests** passed, along with the non-root execution guard and whitespace check. These tests do not establish live dependency cleanup. Operational closeout remains separate: actual service/version/endpoint evidence, listener exposure review, startup/shutdown guidance, logrotate status, capacity/recovery limitations and requirement mapping must reflect the retained implementation.

## Partial dependency cleanup and keypair HTTP correction

The operator executed the cleanup command. Protected inventory passed for 13 resources, and the fresh metadata recovery bundle passed at `/root/openstack-poc-acceptance/dependency-recovery-b0a2694420364856a755347a048ea36a-wfk7feoy`. The script observed and recorded absence of these eight dependencies:

| Kind | Recorded resource ID |
| --- | --- |
| Application subnet | `fed30af1-2abb-4bb5-9482-a97b0d0a85c9` |
| Tenant-A network | `f8d7419d-42c4-4518-8269-56eaead75c79` |
| Application security group | `87efd1ea-ffea-4085-8982-289e5cc1e56d` |
| Tenant-A default security group | `c0d7015a-6e54-4795-83f7-5a2cc614dfab` |
| Tenant-B network | `d7b42155-3ebd-45a8-aeda-cdd700af0bd9` |
| Tenant-B default security group | `171c2d4d-7486-40cd-bd90-9240ffe72f5d` |
| Application private image | `5719231d-5e8a-4ff8-a782-533cb852d0e8` |
| Application private flavor | `bb382ac7-4f5b-46d6-a3be-992bcfc6e5a0` |

The next keypair step raised `KeyError: 'keypair'`. The live HTTP status/body and whether the keypair delete request had already completed were not captured; keypair absence remains unverified. Neither user/project deletion nor the final OVN/namespace/demo checks completed in this invocation. No original demo asset, root operational file or backup was selected for deletion.

Installed SDK source inspection identified an error-handling omission: `openstack.proxy.Proxy.request` defaults to `raise_exc=False`. A raw HTTP 404 response therefore reaches `.json()['keypair']` as an error envelope, rather than raising the SDK absence exception expected by the cleanup code. Nova's installed show controller and [Compute API reference](https://docs.openstack.org/api-ref/compute/) use a `keypair` envelope for successful show and HTTP 404 for absence. A post-delete 404 is a plausible trigger for the observed failure, reproduced offline; it is not claimed as the captured live response.

The correction explicitly translates error responses with `openstack.exceptions.raise_from_response` before parsing show/list bodies. This preserves SDK `status_code` semantics used by the existing absence classifier; simply enabling keystoneauth exceptions would instead expose `http_status`. GET requires HTTP 200 and the expected envelope, DELETE requires HTTP 204 at microversion 2.10, and all keypair operations retain the exact task `user_id`. HTTP 401/403/409/500, unexpected success statuses and malformed bodies stop the stage. Response bodies and key material are not added to console diagnostics by the parser.

Six new regressions include the installed SDK and keystoneauth stack with an in-memory Requests transport that opens no sockets. They cover successful show/delete followed by an actual HTTP 404-shaped response, an already-absent keypair without a repeated DELETE, non-404 errors, malformed responses/list envelopes, and resuming after eight removals using the existing verified recovery point. These supplement the earlier doubles that missed raw HTTP error semantics. All **347 offline tests** passed; fixture tests still verify no cloud-client import from their execution path. Non-root execution and whitespace checks passed. No live retry result is claimed.

Retry the same cleanup command above in the normal Ubuntu terminal. The saved eight completed indices are skipped, both existing recovery bundles are verified again, and no replacement backup or resource is created. A remaining keypair is deleted only after exact user/name/public-key checks; HTTP 404 permits reconciliation if it is already gone. The task users/projects are processed only after the remaining consumer/assignment guards pass. Completion still requires independent final absence, OVN/namespace reconciliation and original demo `ACTIVE` checks. Keep all manifests and recovery points intact.

## Keypair absence verified; network-resource guard needs read-only inventory

The operator's retry observed `keypair absent: task11-app-b0a269442036-key`, then stopped with `project still contains network resources; no identity deletion`. Nine planned removals are now observed. The first identity deletion and assignment removal follow this guard in the code, so neither ran in this invocation. Final OVN/namespace reconciliation and demo-health checks remain pending. The error did not identify the resource type/ID.

Installed Neutron source exposed a flaw in the guard: `SecurityGroupDbMixin.get_security_groups` in `/usr/lib/python3/dist-packages/neutron/db/securitygroups_db.py` calls `_ensure_default_security_group` on the requested project before returning the list, including for an admin caller. That helper can create a missing default group. The cleanup's original `project_empty` called this list after removing the recorded default group. It could therefore recreate its own blocker. This is verified behavior of the installed implementation and a likely explanation of the live failure, not yet the observed identity of the remaining object. Earlier assumptions that a security-group list had no creation side effect were incorrect.

The guard now obtains metadata for the six previously checked network resource families through host-local SQL in an explicit `READ ONLY` consistent-snapshot transaction. It makes no Neutron requests, performs no SQL mutation and reports remaining resource kinds before refusing identity deletion. The generic compute/image emptiness and ownership/role guards remain in place. A resource whose ID is not in the recorded plan is not automatically adopted or deleted.

Run only this read-only probe next:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/inspect_cleanup_dependencies.py --run b0a2694420364856a755347a048ea36a
```

The [inventory script](../../scripts/local-openstack/inspect_cleanup_dependencies.py) reads only the protected manifests and selected Neutron database metadata. It requires the exact run and revalidates the original cleanup plan. It opens no cloud session and reads no credential file, environment values or configuration contents. It writes no file/object and prints no SQL row contents beyond a fixed metadata schema: IDs/project IDs, resource kind, default-name/description flags, the authoritative project-default mapping flag, bound-port/external-reference/sharing/rule counts, and creation/update timestamps. It reports whether each ID belongs to the recorded plan. No names, descriptions, rules, keys or authentication data are printed. Unexpected output fields are rejected. The installed model table/column names were inspected; live SQL execution of this probe remains pending.

All **352 offline tests** passed. Five added tests cover SQL project scoping/UUID injection rejection, explicit read-only transaction boundaries, rejection of unexpected metadata/foreign rows, default-group metadata without rule/config contents, and execution of the installed Neutron list method with storage/ensure doubles to reproduce its lazy-default hook. The existing guard regression now proves it never calls the network API while still rejecting network, compute and image consumers. Tests contact no live database/cloud. The non-root guard and whitespace checks passed.

Review the probe result before another cleanup run. If it confirms a recreated default group, verify its exact project/default mapping, no consumers/sharing/external references, preserve the new metadata and extend the recovery/deletion scope explicitly in a subsequent change. An unexpected object remains preserved for investigation. Do not reset the saved completion indices, modify SQL rows, remove namespaces/OVS entries, or delete resources by name to bypass the guard.

## Confirmed remaining group and prepared supplemental recovery step

The operator's SQL `READ ONLY` probe completed successfully. The saved completion indices remain `[0,1,2,3,4,5,6,7,8]`. Tenant A (`c039831922bc457a8b9a2bc903e4d997`) contains exactly one network resource: security group `4799b49b-78db-4681-9050-95bf1a0288ff`, outside the original plan. Default name/description and the authoritative project-default mapping flags are true; bound ports, external group references and sharing policies are all zero; rule count is four. Creation/update timestamps are both `2026-10-24 16:47:13`. Tenant B (`47db38086d98448f9576d233c51a3529`) has no network resources. This is live operator-reported inventory and supports the lazy-default diagnosis. The probe performed no cleanup.

The corrected cleanup command now accepts a separate explicit supplemental UUID. It is bound to this exact group, run and tenant-A project; it cannot select arbitrary default groups. It rechecks default mapping/metadata, both projects' remaining network scope, completed prior dependencies, project ownership, no remaining tenant VMs/images, and preserved demo state. Decimal count strings such as `"0"` and `"4"` from MariaDB JSON output are accepted with strict nonnegative-integer parsing; booleans, floats, signs and malformed strings are rejected for counts. Unexpected resources, timestamps, consumers or ownership stop the stage.

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/cleanup_application_dependencies.py --apply-run b0a2694420364856a755347a048ea36a --remove-recreated-default-group 4799b49b-78db-4681-9050-95bf1a0288ff
```

Before deleting the supplemental group, this command creates and verifies another uniquely named root-only metadata recovery bundle using the existing checked SQLite/SQL/live-OVSDB/configuration snapshot procedure. Its snapshot plan includes the new group; the protected manifest captures its observed metadata and recovery phase. The original deletion plan, completion indices and original `backup_directory` remain unchanged. Supplemental backup attempts and their accepted directory are recorded separately under `dependency_cleanup.recreated_default`; older bundles remain intact. No restoration has been rehearsed, and snapshots still do not form a distributed atomic transaction.

The command rechecks current consumers and backup integrity, records a deletion intent, then uses only single-object Neutron GET/DELETE for the exact group. It requires SQL/API absence and both task network inventories to be empty, then resumes the original four user/project removals and final OVN/namespace/demo checks. It never calls the project-filtered security-group list in these cleanup checks. An interrupted request with a verified saved recovery point can reconcile HTTP 404 without another DELETE; disappearance before any recorded intent is refused. Retrying the supplemental stage requires the same explicit UUID. No automatic adoption of another regenerated group or automatic recovery occurs.

Four new regressions cover the reviewed scope/protected-ID boundary, SQL count/flag formats and drift, supplemental backup creation/integrity gates, snapshot target inclusion, and interrupted deletion reconciliation. All **356 offline tests** passed, along with the non-root guard and whitespace checks. These tests establish failure behavior, not live supplemental deletion. Execution and final cleanup remain pending. The original demo VM/assets, billing/metering data, credentials and all recovery bundles remain outside deletion scope.

## Verified final dependency cleanup

The operator completed the exact supplemental command. The new recovery bundle passed at `/root/openstack-poc-acceptance/dependency-recovery-b0a2694420364856a755347a048ea36a-eiocsu57`; the reviewed default group was absent and both task network inventories empty. User `21978ae5c98d448095fd250db7d248c3` and project `c039831922bc457a8b9a2bc903e4d997`, then user `9b3cfdc4a68b48ffb557611f91ccdd23` and project `47db38086d98448f9576d233c51a3529`, were observed absent. The script's final checks confirmed all scoped dependencies absent, selected OVN logical switches/metadata namespaces removed and the original demo `ACTIVE`. The stage completed successfully.

This closes the application/isolation dependency cleanup, including recovery from the keypair HTTP parser error and lazy-default-group recreation. It does not claim that the whole host has no resources: the original demo and all historical assets/backups remain intentionally retained. Temporary tenant credentials remain protected for recovery/history but those identities no longer authenticate. [The operations record](task11-operations-and-closeout.md) documents the actual deployment, known versions/endpoints, requirement boundaries, backups, manual pause/resume guidance and the next operational review command. Final service/listener/logrotate review and the retained guest CPU smoke result are still pending.

The subsequent operator operations review passed current required services, actual authenticated resources/agents/inventory and the retained guest CPU arithmetic workload, recorded at `/root/openstack-poc-acceptance/operations-review-68f79bba5a35404ab2573968235db55a.json`. It confirmed wildcard API/broker listeners and an unbounded OVS hook/infinite logrotate job timeout. The [next scoped correction](task11-operations-and-closeout.md) backs up and limits the new API listeners/workers, bounds logrotate and validates actual reopen hooks while keeping the timer stopped. RabbitMQ/EPMD metadata is collected without mutation for its following correction. All **373 offline tests** passed; live hardening and final operational closeout remain pending.
