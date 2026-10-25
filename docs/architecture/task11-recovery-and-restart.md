# Local POC recovery and restart acceptance

**Updated:** 2026-10-25

**Status:** Isolated recovery, restart readiness, the controlled service cycle and the guarded WSL distribution restart all passed. Protected checkpoint `/root/openstack-poc-acceptance/wsl-restart.json` is independently validated: `phase: verified`, `wsl_restart_verified: true`, and `kernel_restart_observed: true`. Final POC documentation/Git closeout remains.

The user approved continuing restore rehearsal → restart validation → documentation reconciliation → final local POC acceptance. Existing [core-cloud/application and hardening evidence](task11-operations-and-closeout.md) remains valid for its recorded observations. The operator's completed live rehearsal and service-cycle verification, followed by the guarded WSL distribution restart, are recorded below.

## Operator rehearsal update

The operator first reported `PASS` for the journal, operational files, seven isolated SQL databases, `Open_vSwitch`, `OVN_Northbound`, `OVN_Southbound` and disk contents. The subsequent final result was:

```text
POC recovery rehearsal: PASS; all owned child processes stopped; report=/root/openstack-poc-acceptance/restore-rehearsal-5zlr6r17/report.json
```

The implemented final `PASS` requires the networkless recovered guest to reach a serial login prompt with QMP-confirmed KVM, its child to stop, its copied base disk to remain unchanged, and every original recovery artifact checksum to pass again. This is real protected-backup restore/boot evidence, distinct from the synthetic tests. It was initially operator-reported; the subsequent root WSL review independently validated every required protected-report stage and source checksum. Preserve the original bundle, restored copies and diagnostics; do not repeat a successful restore as a restart command.

## 1. Isolated restore rehearsal

This command produced the verified rehearsal and is retained for traceability; it is not the next step:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/rehearse_recovery.py
```

The [rehearsal tool](../../scripts/local-openstack/rehearse_recovery.py) is restricted to the existing application bundle `recovery-db0d12b8-3bf4-4e53-9a63-4bbea9971831-lu4cuzr1` under `/root/openstack-poc-acceptance`. It validates the protected application manifest, completed application deletion and temporary cleanup, and every recovery checksum. It uses the existing exclusive task lock. A different source requires a reviewed scope change.

It creates a new root-only `restore-rehearsal-<suffix>` directory and short private scratch paths under `/var/tmp`. The protected report lists every scratch directory. The stages are:

1. Restore the backed-up SQLite journal into a new file using the backup API. Verify integrity and exact table contents against the backup, without opening the current application journal.
2. Materialize regular operational/configuration files under the rehearsal directory. Validate archive paths and types before extraction. Preserve private modes; skip links rather than following or installing them into live paths.
3. Initialize a separate MariaDB datadir with `--no-defaults`. Import all seven cloud databases through its explicit private Unix socket, check their inventory/tables, and stop that exact child. TCP networking, replication startup, binary logging and scheduled events are disabled. No client uses the host MariaDB socket.
4. Obtain each OVS/OVN snapshot's schema and table counts from a private seed copy. Create an empty database, restore with `ovsdb-client restore` on another explicit private socket, compare table counts, and stop both owned children. No controller, bridge or system OVSDB endpoint is involved.
5. Convert the recovered disk into a new standalone 1 GiB qcow2 and check integrity/byte contents. Boot a separate copy/overlay as `libvirt-qemu`, with one vCPU and 256 MiB RAM. Require KVM evidence through QMP and a serial login prompt within 240 seconds. This guest has no network device, host filesystem share or Nova resource. Stop the child and verify that the copied base disk is unchanged.
6. Recheck every original artifact checksum. Only then mark the new report `verified: true`.

Prerequisites: 768 MiB available RAM, 3 GiB free scratch space, existing command binaries, the existing unprivileged `libvirt-qemu` account and KVM access. No package is installed. Stages have timeouts; normal exit/failure stops only owned child processes. Failures retain an incomplete protected report, restored copies and private diagnostics. A rerun creates fresh locations. Abrupt host/process termination is not a successful cleanup; inspect its saved scratch/process evidence before retrying.

Console output contains stage status, fixed failure reasons and report paths. Return only the final `PASS`/`STOP` lines; do not paste raw SQL, archives, console logs or the protected report. No original recovery point or live service data is overwritten, and the tool deletes no files.

This proves only the stages actually completed: logical database import, file materialization and isolated recovered-disk boot. It does not rewind live cloud services, recreate deleted tenant resources, establish Nova/Neutron recovery or prove distributed atomicity. OVSDB checks cover schema and per-table counts. Skipped archive links remain an explicit limitation. Existing [whole-cloud rollback cautions](task11-ovs-ovn-backup-procedure.md) still apply.

## 2. Restart validation after restore passes

The [restart readiness reviewer](../../scripts/local-openstack/review_restart_readiness.py) is prepared. After the rehearsal prints its final `PASS`, run:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/review_restart_readiness.py
```

The default command rejects an incomplete latest rehearsal even if an older successful report exists, checks the original recovery checksums again, and examines only the existing scoped host/cloud. It verifies the sole retained Nova/libvirt guest, no active/interrupted application jobs, current Nova/OVN/WSL addressing, required service state, KVM device metadata, authenticated API/agent/Placement state and pinned-key guest CPU arithmetic. It writes a fresh protected report and changes no guest, service, configuration or networking. A readiness `PASS` is prerequisite evidence; it does not claim a service-cycle or WSL restart passed. The explicit start option below is a separate mutating mode.

### Initial readiness stop and diagnostic correction

The operator's initial attempt saved `/root/openstack-poc-acceptance/restart-readiness-ff786ee99e1d48a8bf81a1d77f3f2b6c.json`, then reported `STOP: restart prerequisites unverified: RuntimeError; no service/guest changes performed.` The original reviewer collected its complete result before updating the report, so an exception inside a shared helper lost the failed check's identity. This output does not identify the underlying host/cloud failure.

The reviewer now checkpoints each check's running/passed/failed state, including individual systemd units, before proceeding. Console output identifies the failed check with safe error categories and allowlisted command names/exit codes. It does not print arbitrary SDK exceptions, service output, command arguments, credentials or tokens. Original exception tracebacks, without local variable capture, are retained only in a new 0600 `.diagnostic.txt` beside the protected report; existing reports are preserved. Return the first `FAIL` line and final `STOP`/report-path lines after retrying the same read-only command, not the diagnostic contents. A failure still prevents readiness success and any later restart work.

A separate read-only, unprivileged systemd observation outside the sandbox successfully parsed every reviewed unit's properties and found the required core units active. `logrotate.service` was inactive, its timer active, and VTEP was observed active. These are fresh service-state observations, not a diagnosis of the earlier exception or full cloud/guest acceptance. The VTEP result differs from historical intentionally stopped state and must be reviewed before the WSL restart stage; this continuation changes no unit state/enablement.

Verification after the diagnostic correction: **13 focused readiness tests passed**; full suite **438 discovered, 436 passed, 2 opt-in daemon tests skipped**. New regressions cover exact failed-stage persistence, prior successful checks surviving later failure, running-state checkpoints before actions, suppression of sensitive return values/exception text, allowlisted command failure metadata and private diagnostic permissions/no-overwrite behavior. The operator's retry remains pending; no underlying runtime failure is claimed fixed yet.

### Retained demo shut off: guarded start prepared

The operator's next review passed recovery checks, credentials/Keystone authentication, the manifest, exclusive-demo/job/addressing/KVM checks and all service-unit checks. It then stopped at `cloud` with category `demo_not_active`; protected report: `/root/openstack-poc-acceptance/restart-readiness-f89ff803e4ca42799c6035db3d4e95b7.json`. This category means Nova did not report the combination `ACTIVE`, running power and no task; the supplied output alone does not disclose the exact Nova fields.

A separate outside-sandbox read-only query of exact retained UUID `82382430-4b64-4386-a55b-8468e04da011` returned `shut off (unknown)` from libvirt. Nova action history later showed `SHUTOFF`, power state 4, and no pending task. No VM was changed by these observations.

### Guest self-shutdown after verified WSL restart

The protected service-cycle record reports `verified` at 10:54:13 UTC, with the retained demo `ACTIVE` and libvirt-running. The WSL restart record reports `verified` at 12:07:53 UTC, again with the demo `ACTIVE` and libvirt-running. Nova's action history then records a successful `start` at 12:07:35 UTC and a `stop` at 12:13:01 UTC. The latter has no user attribution. At 12:13:02 UTC, Nova-compute's `_sync_instance_power_state` logged database power state 1 versus hypervisor power state 4 and said the instance had shut down by itself before Nova issued its reconciliation stop. This indicates the Nova stop record followed the shutdown; it does not identify why the hypervisor/guest went off.

Guest `acpid` logs contain `PWRF/00000080` power-button events matching the earlier controlled service-cycle and WSL-preparation stops. No corresponding event appears around guest time 13:13 for the 12:13 UTC self-shutdown. The guest console-output API returned HTTP 404. The guest console contains the normal `NMI watchdog: Perf NMI watchdog permanently disabled` boot notice; this is not evidence of a watchdog timeout. The offline qcow2 overlay reports no corruption, and the root ext4 partition was inspected through a private sparse copy. Its persistent logs did not expose a shutdown, panic, OOM or watchdog fault for the later boot. The source image was not mounted or modified; the attempted NBD mapping exposed zero capacity and was disconnected automatically.

**Historical result:** after the 12:13 UTC reconciliation, the retained demo was `SHUTOFF` and its shutdown trigger was unknown. The earlier service-cycle and WSL restart acceptances remain valid for their recorded times. Subsequent guarded recovery and readiness evidence is recorded below.

The offline CirrOS root filesystem shows `/etc/rc.sysinit` running `/etc/rc3.d/S*` and then executable `/etc/rc.local`. The `S95-cirros-userdata` hook delegates to `cirros-per instance userdata cirros-userdata`; the wrapper itself contains no shutdown command. The repository's retained-VM creation call does not pass a `user_data` argument ([launcher](../../scripts/local-openstack/launch_test_vm.py)), so custom user data is not indicated for this VM. This makes user data an unlikely explanation, but does not establish the cause of the earlier hypervisor shutdown.

### Reviewed one-shot retry and current readiness

After preserving the earlier intent and Nova action evidence, the operator ran the new `--retry-reviewed-demo` mode. It required the saved prior active observation, a later successful Nova `compute_stop_instance` event, settled Nova/libvirt shutoff and sufficient available memory. It persisted a distinct protected intent before submitting exactly one Nova start; it does not replay an existing/uncertain retry intent.

The operator reported the protected readiness run `/root/openstack-poc-acceptance/restart-readiness-4d81f9e891334d2dae0317c963a87aea.json` passed recovery/source and service checks, observed the retained demo `SHUTOFF` before the reviewed start, then passed `reviewed_retry_demo`, `guest_kvm`, a six-minute stability window, full cloud checks and pinned guest CPU smoke. No service or WSL restart was performed by this run. This is current readiness evidence at that observation time; it does not explain the earlier shutdown or prove indefinite guest stability. Do not repeat the service cycle or WSL restart solely on this result.

The reviewed retry implementation and regression tests were committed locally as `440e69f`. Verification: **38 focused restart-readiness and demo-history tests passed**, Python compilation passed, and `git diff --check` passed. No full-suite result is claimed for this change.

The earlier guarded `--resume-demo` invocation is retained for traceability; its later cloud check failed and that result is superseded for current readiness by the reviewed one-shot run above:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/review_restart_readiness.py --resume-demo 82382430-4b64-4386-a55b-8468e04da011
```

This option may start that existing VM through Nova; it creates no new VM, project, network or storage. Before submitting a start it repeats all earlier prerequisites, verifies project/run ownership, requires Nova `SHUTOFF`/power 4/no task, confirms the sole libvirt domain is shut off, and requires at least 768 MiB available host memory for the existing 1-vCPU/256-MiB demo. It prints only allowlisted status/power fields and whether a task is pending. `ERROR`, paused/suspended states, inconsistent power/state, pending tasks, scope drift or low capacity stop without a new request.

The start intent is saved in root-only `/root/openstack-poc-acceptance/demo-resume.json` before contacting Nova. A retry observes an interrupted/uncertain request rather than issuing another start. An already active demo needs no start. The tool waits up to 180 seconds for observed Nova `ACTIVE`/power 1/no task plus libvirt running, checks actual guest KVM, reruns cloud/network/Placement checks, and waits up to 120 seconds for the existing pinned-key SSH CPU check. Success confirms current demo readiness; it does not establish service pause/resume or WSL reboot persistence. No service/configuration/restart, host-network change, direct libvirt start, force reset, resource deletion or backup modification occurs in this mode.

The operation follows the installed SDK's `start_server` method and the documented [Nova start action](https://docs.openstack.org/api-ref/compute/#start-server-os-start-action): request acceptance must be followed by observed provider state. If Nova disagrees with libvirt or a start outcome is uncertain, preserve the report/intent and diagnose instead of forcing a state change.

Verification for the added mode: **20 focused readiness tests passed**; full offline suite **445 discovered, 443 passed, 2 opt-in daemon tests skipped**. Added regressions verify safe state output, ownership/settled-state checks, independent libvirt and memory gates, persisted intent before the single API call, no replay after uncertainty, reconciliation on observed active state, no duplicate start for an already active demo, and waiting for pinned guest access. Subsequent live results follow.

### Nova start/KVM passed; inspect the saved cloud-check failure

The operator reran the guarded command. It confirmed Nova `SHUTOFF`, power 4 and no task before starting, passed every earlier prerequisite, submitted one start, observed `resume_demo: PASS`, then observed `guest_kvm: PASS`. Under the implemented checks, those results establish Nova `ACTIVE`/power 1/no task, independent libvirt running state and actual KVM acceleration. The later `cloud` check stopped with `RuntimeError`/`observation_failed`. Report: `/root/openstack-poc-acceptance/restart-readiness-3203dd7a3bc14c4fa69d9f1c129aba54.json`. The guest CPU check did not run. No service/WSL change occurred; do not start/recreate the demo again to diagnose this failure.

The latest default reviewer maps the network/metadata/Placement/catalog guards to fixed safe categories. An additional `--explain-report` mode reads only the existing report and matching protected diagnostic. It prints a safe category, failed check and allowlisted source module/function/line. It never prints raw exception/traceback contents or credentials, makes no cloud calls, writes no files and performs no lifecycle action. Run:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/review_restart_readiness.py --explain-report /root/openstack-poc-acceptance/restart-readiness-3203dd7a3bc14c4fa69d9f1c129aba54.json
```

Return only the `POC readiness diagnostic:` line. The decoder accepts only root-owned 0600 matching report/diagnostic files within the protected acceptance directory, with bounded sizes. Unknown failure messages remain private; even those can identify an allowlisted code location. It does not relax any readiness guard or infer that the underlying fault is fixed. Preserve both the saved start intent and failure evidence.

Verification: **24 focused readiness tests passed**; full offline suite **449 discovered, 447 passed, 2 opt-in daemon tests skipped**. New tests cover known-guard decoding without traceback disclosure, sensitive unknown exception/argument suppression, exact protected report/diagnostic pairing and diagnostic mode making no cloud/lifecycle calls. The operator subsequently supplied the diagnostic below.

### Metadata namespace source-address failure identified

The operator's saved diagnostic returned `metadata_namespace_address_missing`, originating in `validate_guest.py` / `validate_attachment`, line 170. It also confirmed `demo_start_observed: true` and `guest_kvm_passed: true`. This means the prior namespace IPv4 inventory did not contain the manifest's expected source `172.30.240.2`; it does not mean the VM start or KVM check failed.

Read-only host observations outside the sandbox established:

- The retained namespace exists: `ovnmeta-6ee5ad82-efc5-405d-bf5c-a4953e9a063b`.
- The exact retained Northbound switch references two ports. Metadata port `d348cb87-d830-4f23-914f-b40117377f52` has type `localport` and CIDR `172.30.240.2/24` in both Northbound and Southbound. Its Southbound MAC/IP field includes `172.30.240.2`.
- Compute port `74677cfa-f5cb-4adc-8e6e-5c0ca26e2136` has the normal VIF type, guest CIDR `172.30.240.154/24` in both databases, and a Southbound chassis binding. No raw external-ID maps, credentials or unrelated topology were printed.
- The installed Neutron metadata agent's `_active_subnets_cidrs` / `_process_cidrs` / `provision_datapath` code manages metadata-port subnet addresses on its namespace veth for active bound VIF subnets. It also removes inactive/stale CIDRs. This argues for inspecting service reconciliation rather than manually adding an address or weakening the guest's source-IP security-group rules; the exact cause is not established yet.

The operator ran the following read-only observation for that exact namespace:

```bash
sudo ip netns exec ovnmeta-6ee5ad82-efc5-405d-bf5c-a4953e9a063b ip -br -4 addr show
```

The observed output was:

```text
lo               UNKNOWN        127.0.0.1/8
tap6ee5ad82-e1@if7 UP             169.254.169.254/32 172.30.240.2/24
```

The expected source is now on the up metadata interface and matches both OVN databases. This is consistent with reconciliation completing after the earlier readiness observation, but the exact cause/timing is not independently proven. No manual address, route, security-group or service change was performed in this diagnosis, and none is justified by the latest address snapshot.

The reviewer now waits within a 60-second retry window only when the complete cloud observation raises the exact known `metadata_namespace_address_missing` guard. Every attempt reruns the full observation, including ownership, VM/image state, network/security/address checks, agents and Placement. Other errors, authorization/ownership/policy drift and malformed/failed commands stop immediately. If the address never appears, the original failure/category is retained. Retries read existing state only and never start/stop a VM or restart a service. Individual API/command calls retain their own bounds.

Verification: **28 focused readiness tests passed**; full offline suite **453 discovered, 451 passed, 2 opt-in daemon tests skipped**. Added tests cover delayed-source recovery only after a full successful observation, persistent absence at deadline, immediate failure on other errors and ownership drift arising between attempts.

The next operator command is the default read-only review, without `--resume-demo`:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/review_restart_readiness.py
```

The operator completed that review and reported final `PASS` at `/root/openstack-poc-acceptance/restart-readiness-7103dd5dc08d424bb7d1b0ae03d27f4b.json`. Recovery/source checks, authentication, scope/jobs/addressing/KVM-device checks, required services, Nova `ACTIVE`/power 1/no task, the complete cloud/network/agent/Placement observation and pinned-key guest CPU arithmetic passed. This is operator-reported current readiness, distinct from the earlier incomplete attempts. No service/WSL restart was performed by this review.

### Controlled service pause/resume: passed

The operator ran this command in the existing Ubuntu terminal; it is retained for traceability:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/validate_service_cycle.py --apply-demo 82382430-4b64-4386-a55b-8468e04da011
```

The [service-cycle tool](../../scripts/local-openstack/validate_service_cycle.py) revalidates the protected recovery and latest successful readiness evidence, performs fresh baseline/guest/KVM checks, and records the current host boot ID. It temporarily stops the existing 1-vCPU/256-MiB/1-GiB retained demo through Nova, requires independent libvirt shutdown, pauses the seven dedicated workers below, observes them inactive, resumes them and waits for API/compute/Placement/OVN recovery, then starts the same demo through Nova after rechecking at least 768 MiB available host memory. No new cloud resource is created and no existing resource is deleted.

The exact worker scope is `nova-compute`, `nova-scheduler`, `nova-conductor`, `nova-api`, `neutron-server`, `neutron-ovn-metadata-agent` and `glance-api`. Shared Apache, MariaDB, RabbitMQ, Memcached, libvirt, OVS/OVN and the observed timer/KVM/VTEP state remain outside the mutation scope. Their loaded/active/enablement profiles are compared during/after the pause, excluding the periodic `logrotate.service` job. Worker enablement is also preserved. No configuration, package, quota, flavor, firewall/rule, host route, backing disk or recovery bundle is changed. Metadata helper namespaces may reconcile normally when workers resume; the tool waits for the exact expected namespace/address rather than assigning an address manually.

The original global task lock plus an exclusive existing application-journal lock prevent concurrent task/worker operations. All VM and systemd intents are recorded before submission in `/root/openstack-poc-acceptance/service-cycle.json`. An interrupted intent is observed on retry without another request; an uncertain request that never reaches its target stops for review. The checkpoint is bound to project/run/VM and host boot ID, so a WSL reboot during an unfinished cycle prevents blind continuation. A previously verified checkpoint is retained without replaying the cycle. On failure, a new protected `service-cycle-attempt-<UUID>.json` and private diagnostic preserve the failed phase; the demo/workers may remain stopped pending recovery. No broad rollback or cleanup runs.

Successful acceptance requires Nova plus libvirt to confirm shutdown and subsequent running state; all seven workers to reach observed inactive then active states; unchanged shared service/enablement profiles; a changed guest kernel boot ID; pinned-key SSH with full DHCP/address/MAC/MTU/metadata identity validation; actual KVM; three MTU-sized DF ping replies; and the CPU arithmetic result. Phase/result evidence must be complete before final `PASS`. Each wait and command has its own bound; API request acceptance alone never counts as completion. WSL is not terminated or rebooted by this stage, and its persistence remains separate.

Verification at preparation: **16 focused service-cycle tests passed**; full offline suite **469 discovered, 467 passed, 2 opt-in daemon tests skipped**. Tests cover operation order and exact scope, saved intent before calls, no request replay after VM/service uncertainty, partial pause preservation, shared/enablement drift, independent libvirt proof, changed guest boot ID, pinned SSH trust, exclusive journal locking, authorization failures, rejection of incomplete acceptance/older readiness, namespace-reconciliation waiting and non-root/wrong-UUID refusal. Installed SDK start/stop methods and systemctl's `--no-block` behavior were checked.

The subsequent operator result was:

```text
POC service cycle: previous cycle PASS retained; no cycle replayed; report=/root/openstack-poc-acceptance/service-cycle.json
```

Codex then used the Windows WSL launcher as root to run the [read-only WSL reviewer](../../scripts/local-openstack/review_wsl_restart.py). Protected report `/root/openstack-poc-acceptance/wsl-restart-review-6fdb77f29a084b6ea7d0252fc0fffcb9.json` independently validated the saved cycle's complete VM/guest acceptance, all seven workers active → inactive → active with unchanged enablement, and unchanged shared-service profiles during/after the pause. It also revalidated recovery evidence/checksums and performed fresh authenticated cloud/network/agent/Placement, KVM-device/addressing and pinned guest CPU checks. No cycle was replayed.

Ubuntu had systemd enabled, demo libvirt autostart disabled and zero reviewed active units with disabled/masked/runtime-only boot enablement. VTEP was active at this observation; earlier stopped-state entries are historical. Two login sessions (UIDs 0 and 1000) and 51 running services were observed. Thirty-six services were outside the explicit twenty-unit review, including underlying OVS/OVN daemons and normal OS services; that count does not establish thirty-six unrelated workloads. The selected process inventory included MariaDB, QEMU, Node and Python. Docker inventory was unavailable, so its running-container count is unknown. No process arguments, environment or raw secrets were collected. Saving Ubuntu terminal/editor work remains an operator responsibility before shutdown.

### WSL distribution restart checkpoint

The [restart manager](../../scripts/local-openstack/manage_wsl_restart.py) binds the existing Ubuntu distribution, sole retained demo UUID/project/run, current kernel boot ID and PID 1 start time in protected `/root/openstack-poc-acceptance/wsl-restart.json`. The additional PID 1 fingerprint distinguishes distribution restarts even if another WSL distribution keeps the shared kernel running. [Microsoft's systemd description](https://github.com/microsoft/WSL/blob/master/doc/docs/technical-documentation/systemd.md) explains systemd's PID 1 role and that WSL startup may continue after a bounded readiness wait; the manager therefore observes required services itself.

Preparation rechecks current readiness and pinned guest identity, boot policy, sole-guest scope, job quiescence and available backup space. With the global task lock and exclusive application-journal lock held, it stops the same demo through Nova and observes Nova/libvirt shutdown, then pauses only the seven dedicated workers. It preserves shared active/enablement state and writes a fresh protected recovery point: offline flattened/checked/byte-compared demo disk and domain XML, SQLite backup, seven transactional SQL schemas, three live-endpoint OVSDB snapshots and operational configuration/credentials. All artifacts and the recovery manifest are checksum-pinned. The original recovery material remains unchanged. The new recovery point is integrity evidence, not another completed restore rehearsal or an atomic whole-host backup.

Preparation command (already executed when the current checkpoint below reports `PREPARED`):

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_wsl_restart.py --distro Ubuntu --prepare-demo 82382430-4b64-4386-a55b-8468e04da011
```

**Preparation result:** Codex prepared the checkpoint through the Windows WSL launcher. The demo reached Nova/libvirt `SHUTOFF`; all seven workers reached inactive; shared services and enablement remained unchanged. Fresh recovery point: `/root/openstack-poc-acceptance/recovery-82382430-4b64-4386-a55b-8468e04da011-wsl-auiv00ve`; its recovery-manifest SHA-256 is `7e7ba750fd195ab30ca08825829be2dd556a87ab62f28d4c02e0957c8f00975d`.

Save Ubuntu work, then run this single command in **Windows PowerShell**. This is the operator-controlled session boundary and has not been executed by Codex:

```powershell
wsl --distribution Ubuntu --user root --exec /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_wsl_restart.py --distro Ubuntu --poweroff-demo 82382430-4b64-4386-a55b-8468e04da011
```

The command rechecks the original distribution identity, protected backup hashes, sole shut-off guest/autostart, paused workers, unchanged shared services and quiescent journal. It persists shutdown intent, then requests `systemctl poweroff` without force. [systemd documents poweroff as asynchronous](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#poweroff); successful request submission does not prove completion. Check from Windows:

```powershell
wsl --list --verbose
```

Wait until `Ubuntu` shows `Stopped` before reopening it. If it stays running, preserve the checkpoint/output and investigate rather than forcing termination or replaying an uncertain shutdown request. Other distributions are outside this scope. [Microsoft's command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands) distinguishes distribution selection from `wsl --shutdown`, which affects every running distribution and the shared WSL 2 VM.

After Ubuntu stopped, the operator reopened it and ran this verification from PowerShell:

```powershell
wsl --distribution Ubuntu --user root --exec /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_wsl_restart.py --distro Ubuntu --verify-demo 82382430-4b64-4386-a55b-8468e04da011
```

The operator reported:

```text
POC WSL restart: boot_observed
POC WSL restart: controls_ready
POC WSL restart: vm_start_intent
POC WSL restart: vm_start_submitted
POC WSL restart: vm_active
POC WSL restart: verified
POC WSL restart: PASS; distro restart, boot services, addressing, KVM and retained guest verified; report=/root/openstack-poc-acceptance/wsl-restart.json
```

Codex independently read and validated the protected report with `assert_complete` and rechecked every pinned recovery artifact. It confirms `phase: verified`, `wsl_restart_verified: true`, and `kernel_restart_observed: true`. Manager evidence requires systemd as PID 1 with changed distribution fingerprint, unchanged service enablement, automatic recovery of required workers/shared units, matching WSL/Nova/OVN addressing and unchanged KVM/vhost device metadata. The demo remained shut off with autostart disabled until those checks passed. Then one durable Nova start intent was submitted and Nova/libvirt running, pinned-key DHCP/MAC/IP/MTU/metadata identity, changed guest boot ID, actual KVM, three MTU-sized DF replies, CPU arithmetic and pinned recovery hashes passed. Failure phases preserve protected intent/diagnostics; uncertain starts are not blindly resubmitted.

If shutdown is postponed after successful preparation, restore the demo/workers on the **original distribution boot** with:

```bash
sudo /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_wsl_restart.py --distro Ubuntu --cancel-demo 82382430-4b64-4386-a55b-8468e04da011
```

Cancellation requires the prepared checkpoint and backup, resumes only the seven workers and same demo, verifies recovered behavior, and records `wsl_restart_verified: false`. It cannot count as restart acceptance. Incomplete early preparation or boot/address drift needs the saved-phase review; do not rerun historical installation/creation/deletion commands.

Verification before live restart: **496 discovered, 494 passed, 2 opt-in daemon tests skipped**. The WSL manager has **19 focused tests** covering durable scoped intents, uncertain requests without replay, partial backup retention, unknown/changed distribution boot, service/enablement/address drift, automatic boot observations, unchanged backup-manifest hash, shutdown without force, cancellation without false acceptance, and startup 503 retries without retrying authorization errors. The read-only WSL review has **8 focused tests**; the earlier **28 readiness** and **16 cycle** tests remain passing. Isolated SQL/OVSDB restore round trips separately passed **2 tests** with private synthetic data.

## 3. Documentation and final local acceptance

The scoped live sequence is now complete: core application/network/lifecycle and cleanup; recovery rehearsal; read-only readiness; controlled Nova/worker cycle; and WSL distribution restart with service, addressing, KVM and guest recovery. Current results, task mappings and dated historical blockers are being reconciled. Genuine limits remain explicit: whole-cloud distributed rollback and offsite recovery are untested; Cinder and Swift backends, local metering/monitoring integrations, AI/GPU features and optional external services remain unavailable, simulated or deferred as their task records describe. Staging and production are not part of this acceptance.

At the recovery/restart checkpoint, the reviewed tools, tests and runbook were committed locally as `523e3ffc14f75bee24faa5cf7dec97b179a62241` (`ops: verify local recovery and WSL restart`). The later repository closeout commit `d07edefb0044797d09de42cfc83f22d88c9a48b0` contains the reconciled task crosswalk and sanitized related POC work and was pushed to `origin/main`. Protected artifacts remained outside Git.

## Earlier verification history

The counts below belong to earlier implementation checkpoints and remain for traceability. The latest full offline suite at the WSL manager verification point discovered **496 tests: 494 passed, 2 opt-in isolated-daemon tests skipped**. Focused counts at that point were **28 readiness**, **16 service-cycle**, **8 WSL review**, and **19 WSL manager** tests. The separate private synthetic MariaDB/OVSDB round-trip run passed **2 tests**. The final documentation changes after that suite were not code changes.

The scoped local core acceptance sequence passed. Repository closeout is published. Whole-cloud rollback and offsite recovery remain untested. Cinder/Swift backends, integrated metering/monitoring, AI/GPU workloads and optional external services remain as their task records classify them. This acceptance does not pass staging or production gates.

The design follows installed MariaDB 10.6, OVS 3.3 and QEMU 6.2 tooling, with official references for [MariaDB options](https://mariadb.com/docs/server/server-management/starting-and-stopping-mariadb/mariadbd-options), [OVSDB private endpoints](https://www.openvswitch.org/support/dist-docs/ovsdb-server.1.html), [QEMU networking/sandboxing](https://www.qemu.org/docs/master/system/qemu-manpage.html) and [WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).
