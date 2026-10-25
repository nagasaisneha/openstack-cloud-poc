# Environment audit

**Date:** 2026-10-18
**Scope:** Historical, point-in-time read-only audit of the laptop development/POC environment (Ubuntu on WSL2). The final project-scope decision keeps the local POC in scope, with staging separate and the production direction on organization-owned data-center infrastructure. This audit does not assess a staging environment or data-center production feasibility.

## Repository and source inputs

- `git status --short --branch` reported `## No commits yet on main`, with existing untracked `README.md` and `docs/`. These were preserved.
- Requirements read: [`requirements-specification.md`](../requirements/requirements-specification.md).
- Diagram read: [`architecture-diagram.png.jpeg`](architecture-diagram.png.jpeg). Its actual name is `.png.jpeg`; the requirements mention `architecture-diagram.jpeg`. No rename was made.
- No `AGENTS.md` was found in the repository parent tree.
- No credentials, tokens, endpoint values, or environment variable contents were inspected or recorded.

## Verified environment and checks

| Area | Finding | Evidence / limit |
|---|---|---|
| OS | Ubuntu 22.04.5 LTS (Jammy) on WSL2 | Verified from `/etc/os-release`, kernel markers, and `uname` |
| CPU | 12 logical CPUs | Verified with `nproc` |
| Memory | 7.7 GiB total; 5.9 GiB available at audit | Verified with `free -h`; available memory is a point-in-time measurement |
| WSL root filesystem | 1007G total, 24G used, 932G available, 3% used (`/dev/sdd` mounted at `/`) | Measured with `df -h /` at **2026-10-18 02:31:46 +05:30** (**2026-10-17 21:01:46 UTC**); point-in-time host filesystem capacity, not Docker's virtual disk limit |
| Swap | 2.0 GiB configured; unused at audit | Verified with `free -h` |
| QEMU / KVM | QEMU x86_64 6.2.0; QEMU supports KVM | Verified: a minimal QEMU process with `-accel kvm` started and was stopped successfully |
| Docker Desktop | Docker Desktop 4.90.0; Docker Engine 29.7.2; active context `default`; endpoint `unix:///var/run/docker.sock` | Verified from the latest local Docker CLI output. The endpoint is the local Unix socket; no cloud endpoint was contacted. |
| Docker daemon-visible resources | 12 CPUs; `MemTotal=8248930304` bytes (about 7.68 GiB) | `docker info --format 'CPUs={{.NCPU}} Memory={{.MemTotal}}'` returned `CPUs=12 Memory=8248930304`; daemon connectivity from Ubuntu WSL was working for this check. These are Engine-visible resources, not a direct measurement of current free memory or proof of the Windows Docker Desktop resource-limit settings. |
| Docker GPU access | CUDA container ran `nvidia-smi` and detected NVIDIA RTX 2060 with 6 GB VRAM | Verified in a Docker CUDA container. This establishes container GPU visibility only, not guest passthrough or multi-tenant GPU scheduling |
| Host GPU access | Host `nvidia-smi` failed to initialize NVML during this audit | Host-side NVML visibility is not established; this does not negate the successful container check |
| libvirt CLI | `virsh` 8.0.0 is available | Verified CLI version; does not establish a running libvirt daemon or OpenStack integration |
| OpenStack clients and SDKs | `openstack` unavailable on PATH; Python `openstack` and `novaclient` modules absent | Verified with `command -v` and Python module-spec checks; does not establish whether a remote cloud exists |
| OpenStack packages / service processes | No matching installed packages or service processes found | Read-only `dpkg-query` package-name filter and process-name filter for OpenStack/Nova/Neutron/Keystone/Glance/Cinder/Swift/Heat/Placement and related services; process arguments and environment were not inspected |
| Standard OpenStack configuration | The checked user and system configuration paths were absent: `~/.config/openstack/clouds.yaml`, `secure.yaml`, `/etc/openstack/clouds.yaml`, `/etc/kolla/globals.yml`, and Nova/Neutron/Glance/Cinder/Keystone/Heat service configs | Presence-only checks; no file contents were displayed |
| Local endpoint indicators | No endpoint-related keys were found in the readable standard files checked; `OS_AUTH_URL` and `OS_CLOUD` variable names were not present in the current process environment | Values were never read or printed. This is not evidence that no remote OpenStack endpoint exists; repository or user-managed configuration outside the checked locations may exist. No endpoint was contacted. |
| Kubernetes client | `kubectl` unavailable on PATH | Verified with a command availability check |
| Terraform / Ansible | Both unavailable on PATH | Verified with command availability checks |

## Capabilities not verified

- A complete OpenStack deployment is not present or verified by this audit. Full Nova VM provisioning and lifecycle remain unverified.
- Neutron tenant networking, routing, security group enforcement, and guest connectivity remain unverified.
- Nova GPU scheduling, PCI assignment, and GPU passthrough into an OpenStack guest remain unverified. Successful Docker GPU access is not evidence for these capabilities.
- Cinder or Swift backends, service quotas, endpoint availability, and cloud credentials were not inspected or tested.
- Windows Docker Desktop CPU/memory limit settings have not been directly verified. Engine-visible CPU/memory are recorded above but do not establish the configured limits or actual free memory.

## Docker Desktop resource-limit check

An earlier read-only Docker info attempt at **2026-10-18 02:31:46 +05:30** failed with permission denied on `/var/run/docker.sock`; its zero fields were invalid. In the latest terminal evidence, daemon connectivity from Ubuntu WSL was working: Docker Desktop **4.90.0**, Docker Engine **29.7.2**, active context **`default`**, endpoint **`unix:///var/run/docker.sock`**, and `docker info --format 'CPUs={{.NCPU}} Memory={{.MemTotal}}'` returned **12 CPUs** and **8248930304 bytes** of daemon-visible memory (about **7.68 GiB**). Record the earlier failure as a separate transient observation; its cause is unknown.

The Docker Engine-visible memory figure is not current free memory and does not establish the Windows Docker Desktop resource-limit settings. Host RAM was separately recorded as 7.7 GiB total and 5.9 GiB available at the earlier `free -h` audit; available memory is a point-in-time value and is not inferred from the Docker figure. The Windows Docker Desktop CPU/memory settings remain **unknown** until inspected manually in Docker Desktop for Windows → **Settings → Resources**, without applying changes. If relevant, inspect an existing Windows `.wslconfig` read-only and distinguish configured values from defaults.

To verify manually without changing settings, open Docker Desktop on Windows and inspect **Settings → Resources** (the displayed section name can vary by Desktop version). Record the CPU and memory allocation shown; do not press Apply or change any value. If the WSL2 backend uses Windows `.wslconfig` limits, inspect the existing file read-only and distinguish explicit configured values from WSL defaults. Docker daemon-visible `NCPU`/`MemTotal`, when the CLI can connect, can be recorded as a cross-check but may not identify the Desktop configuration source.

## Audit interpretation

- The successful `-accel kvm` process verifies a minimal KVM-accelerated QEMU start/stop path. It does **not** establish that a complete OpenStack deployment, Nova's libvirt integration, full VM provisioning, or Neutron networking will work on this WSL2 host.
- The local check found no OpenStack CLI, SDK modules, matching packages/processes, standard configuration files, or endpoint-key indicators in the checked locations. An absent local CLI/configuration does **not** establish that no remote endpoint exists. No remote endpoint or API was contacted. Docker Engine-visible resources are now measured above; Windows Docker Desktop resource-limit settings remain unknown. WSL root filesystem capacity was measured above; Docker's separate storage allocation has not.
- Scope interpretation: this historical local audit is relevant to the original local POC. The current scope continues that POC and carries its evidence into a separate staging plan. AWS-hosted OpenStack and AWS-native tenant provisioning are deferred/out of implementation scope; this audit does not assess them or authorize deployment. Nova is the chosen tenant compute path and Proxmox is deprecated; this audit does not verify Nova or real cloud operation.
- At 7.7 GiB RAM, a multi-service OpenStack control plane plus guest VMs, monitoring, Kubernetes, and AI services cannot be assumed to fit. The service plan prioritizes a tiny, measured deployment or a remote/existing OpenStack endpoint; unsupported workflows should be represented with mocks or fixtures.
- Docker CUDA `nvidia-smi` verified that the container can see the RTX 2060. Host NVML access, Nova GPU passthrough, GPU isolation, and MIG remain unverified or unsupported. The laptop GPU is not an H100 substitute and offers no H100 MIG profiles.
- The diagram path differs from the requirements' intended path. The actual image is preserved and referenced by its actual filename.

## Safety boundary

This audit changed no system configuration, installed no packages, made no cloud/API calls, created no resources, and deleted nothing. No secrets were exposed.

## T07 local-only inventory — 2026-10-18

**Observation time:** 2026-10-18 22:21:09 +05:30. These are fresh local observations for T07. They supplement, and do not replace, the earlier 2026-10-18 audit above. No cloud endpoint or API was contacted; no environment-variable values or configuration contents were read.

### Findings

| Area | T07 finding | Classification and limits |
|---|---|---|
| OpenStack CLI | `command -v openstack` found no executable on `PATH`. | **Absent locally** at observation time; says nothing about a remote cloud. |
| Python clients | `importlib.util.find_spec` returned false for `openstack`, `novaclient`, and `keystoneauth1`. | **Absent locally** for these module names; no client was installed. |
| OpenStack packages | `dpkg-query` package-name filter for OpenStack, Nova, Neutron, Keystone, Glance, Cinder, Swift, Heat, Placement, Horizon, Kolla, MicroStack, and DevStack returned no matches. | **Absent from the queried installed-package set**; not evidence about containers or remote services. |
| Service units | The filtered `systemctl list-units --all --type=service --no-legend` query returned no unit list because it could not connect to the system bus (`Operation not permitted`). | **Inaccessible**; service-unit absence was not established. |
| Processes | Filtered `ps -eo comm=` returned no matching OpenStack/Nova/Neutron/Keystone/Glance/Cinder/Swift/Heat/Placement/Horizon process names. | No matching process names were observed; command checked names only, not arguments or environment. |
| Container runtime | Docker CLI is present. `docker ps -a` could not access the local daemon socket and returned permission denied. | Runtime daemon and relevant-container inventory are **inaccessible**. The empty fields from a formatted `docker info` response were not treated as valid inventory. |
| Conventional service ports | Filtered `ss -lntu` output contained none of ports 5000, 8774, 9292, 9696, 8776, 6080, 80, or 443. Only port numbers were recorded; listener addresses were not displayed. | No listeners on these conventional ports were observed. This does not establish service absence on other ports or remotely. |
| Standard configuration paths | Presence-only checks found the following absent: `~/.config/openstack/clouds.yaml`, `~/.config/openstack/secure.yaml`, `/etc/openstack/clouds.yaml`, `/etc/openstack/secure.yaml`, `/etc/kolla/globals.yml`, `/etc/nova/nova.conf`, `/etc/neutron/neutron.conf`, `/etc/glance/glance-api.conf`, `/etc/cinder/cinder.conf`, `/etc/keystone/keystone.conf`, and `/etc/heat/heat.conf`. | **Absent** at the checked paths. No file contents were opened. Other paths may exist. |
| QEMU / libvirt | `qemu-system-x86_64 --version` reported QEMU 6.2.0; `virsh --version` reported 8.0.0. | CLI presence only; does not establish a running hypervisor service or OpenStack integration. |
| KVM | `lsmod` listed `kvm_intel` and `kvm`; `/dev/kvm` was absent. | Kernel modules are **present**, but the device needed for a current local KVM access check is **absent**. Current usable KVM/QEMU acceleration is therefore **unverified**. |

The earlier audit recorded a minimal QEMU process using `-accel kvm` started and stopped successfully at 2026-10-18 02:31:46 +05:30. That earlier result is retained as historical evidence; it does not override T07's fresh finding that `/dev/kvm` is absent. Neither observation proves that a complete OpenStack deployment, Nova integration, VM launch, or Neutron networking is feasible.

### Verification methods

- CLI and modules: `command -v openstack`; `importlib.util.find_spec` for `openstack`, `novaclient`, and `keystoneauth1`.
- Packages: `dpkg-query -W -f='${binary:Package}\t${Version}\n'` filtered to the service/package names listed above.
- Service units and processes: filtered `systemctl list-units --all --type=service --no-legend` and `ps -eo comm=`; systemd was inaccessible as described above.
- Containers: `docker ps -a --format '{{.Names}} {{.Status}}'`; the local daemon denied access. `docker info --format 'Docker server={{.ServerVersion}} containers={{.Containers}} running={{.ContainersRunning}}'` returned empty server fields, so those fields were discarded as invalid evidence.
- Ports: `ss -lntu`, filtered to the conventional port numbers listed above.
- Config paths: Python `Path.exists()` checks on the exact paths listed above; contents were not read.
- Virtualization: `qemu-system-x86_64 --version`, `virsh --version`, `test -e /dev/kvm`, read-only `lsmod` filter.

### Scope and unknowns

The local package/process/path/port evidence does not establish that Keystone, Nova, Glance, Neutron, Cinder, Horizon, or metering is absent from all possible local containers or remote deployments. Service-manager and container inventory were inaccessible. Those services and all cloud resources are **unverified**. No Horizon/API comparison or image/network metadata query was performed. Fixture identity tests from T06 remain simulated and are not cloud inventory evidence.

This T07 slice performed only local read-only checks. No endpoint/API call, package installation, system configuration change, credential access, identity change, or cloud resource operation occurred.

## Actual Ubuntu 22.04 WSL host follow-up — 2026-10-24

This read-only follow-up was run outside the Codex sandbox after the Task 10B/10D reports. The prior T07 and earlier sandbox observations remain historical; in particular, the sandbox result that `/dev/kvm` was absent is not the current host result. The diagnostic command did not retain an exact observation timestamp beyond the journal times noted below.

| Area | Current host evidence | Classification and limit |
|---|---|---|
| OS and kernel | Ubuntu 22.04.5 LTS on `6.18.54.1-microsoft-standard-WSL2`, x86_64 | Verified from `/etc/os-release` and `uname -a`. |
| Current user groups | `sneha` is in numeric groups `109 (kvm)` and `125 (libvirt)` | Verified by `id`; this reports the current shell session. |
| KVM devices | `/dev/kvm` and `/dev/vhost-net` exist with uid 0, gid 993, mode 660; neither is readable or writable by `sneha`. The local `kvm` group is gid 109. | **Blocked** for non-root KVM access. GID 993 has no group mapping in the observed lookup. No permissions, group, or udev change was made. |
| Resources | 7.7 GiB total RAM, 4.6 GiB available, 2.0 GiB swap; `/` has 936 GiB available | Point-in-time values from the current host check; available RAM is not a deployment-capacity guarantee. |
| systemd | `systemctl is-system-running` reported `running` in the current host check. A logrotate start job was pending. | Current observation; earlier sandbox bus errors and the supplied `starting` observation are not equivalent host evidence. |
| OVS/OVN and VTEP | The supplied host findings report active OVS/OVN services and `ovn-controller-vtep`; current logrotate child targets `/var/run/ovn/ovn-controller-vtep.696.ctl`. | Confirms the blocked hook target. A service being active does not establish usable OVN topology or Neutron integration. |
| Nova/Neutron/OpenStack API services | The `openstack` CLI is absent from `PATH`; `neutron-server`, `nova-api`, `nova-compute`, `keystone`, `glance-api`, and `placement-api` systemd units reported `not-found`. No listeners were observed on the checked conventional API ports 5000, 8774, 9292, 9696, 8776, or 6080. | No local systemd/API path was found by these checks. Containers, alternate ports, and remote services were not inspected or contacted; overall service availability remains unverified. |
| VTEP topology | The supplied OVN Northbound query found no `Logical_Switch_Port` with `type=vtep`. | Evidence against a configured Neutron VTEP logical port. `vtep-ctl show` was not verified because noninteractive sudo required a password; physical-switch database state remains unknown. |
| Logrotate scheduling | `logrotate.timer` is active, with `OnCalendar=daily`, `AccuracySec=1h`, `Persistent=true`; `logrotate.service` has `Restart=no` and is triggered by the timer. Its `LastTriggerUSec` coincides with the stop/restart event. | Timer-triggered or queued activation is supported by the journal and unit properties. The exact scheduling event is not proven; the timer journal did not log the trigger itself. |
| Logrotate hook | `/etc/logrotate.d/ovn-common` iterates `/var/run/ovn/*.ctl` and invokes `ovs-appctl -t "$ctl" vlog/reopen`; the active child targeted the VTEP socket. The OVS hook separately iterates OVS PID files. | Exact matching local configuration and process target observed. No configuration change was made. |
| Cron fallback | `cron.service` is active, but `/etc/cron.daily/logrotate` exits when `/run/systemd/system` exists. | The daily cron wrapper is not the likely reactivation path in the current systemd boot. |
| DevStack checkout paths | `/home/stack/devstack`, `/opt/stack/devstack`, and `/opt/devstack` were absent. | Verified by path-presence checks. No Git repository or `local.conf` contents were accessed. |

### Bounded systemd and container refresh — 2026-10-24 (superseded by recovery follow-up below)

| Check | Finding | Interpretation |
|---|---|---|
| Pending systemd job | `systemctl list-jobs` showed job `20637`, `logrotate.service start running`. | The service was still in a start job at the time of this bounded read-only check. |
| Logrotate timer/service | Timer was active with `Persistent=yes`; its last trigger remained `2026-10-24 04:56:56 IST`. The service was `activating/start`, `Restart=no`, `KillMode=control-group`, and `TimeoutStopUSec=1min 30s`. | Confirms a live timer-associated start and that systemd controls the service process group. It does not identify the precise timer event that queued the job. |
| VTEP service | `ovn-controller-vtep.service` was active (`MainPID=696`), `Restart=on-failure`, `KillMode=control-group`, `TimeoutStopUSec=15s`; its `ExecStop` is `ovn-ctl stop_controller_vtep --no-monitor`. | A manual systemd stop is the reversible control path; do not disable or mask the unit. Stopping it may interrupt any VTEP-enabled physical-switch integration. |
| OVN runtime sockets/processes | The VTEP socket and the `ovn-controller`, OVN NB, and SB sockets existed. `logrotate` PID 287376 and `ovs-appctl` PID 287418 were present. | Confirms the logrotate hook was still blocked against a live VTEP control socket; the other listed sockets do not prove Neutron availability. |
| Docker inventory | `docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'` returned only `hello-world` in `Exited (0)` state; no OpenStack container was listed. | No containerized OpenStack service was found in the Docker inventory. This does not inspect other container runtimes or remote endpoints. |
| Recovery attempt | An approval-gated `sudo systemctl stop logrotate.timer` request could not authenticate because the execution shell had no terminal for the sudo password. | No service or timer state was changed by that attempt. Do not provide credentials to the shell. |

These checks were bounded and read-only. At that time the host had one live logrotate start job. The findings below supersede that transient state.

### KVM device-group follow-up — 2026-10-24

| Check | Finding | Interpretation |
|---|---|---|
| Numeric ownership | `/dev/kvm` is character device 10:232 and `/dev/vhost-net` is 10:238; both are `uid=0 gid=993 mode=660`. `getent group 993` returned no mapping; `kvm` is gid 109 and includes `sneha`. | The current login group membership cannot grant access to nodes owned by unmapped gid 993. |
| Installed udev rules | `systemd-udevd.service` is active. Packaged `/usr/lib/udev/rules.d/50-udev-default.rules` contains rules assigning both `kvm` and `vhost-net` to `GROUP="kvm", MODE="0660"`. | Required package rules exist, so the mismatch is not explained by a missing packaged rule. |
| Sysfs/udev device state | `/sys/devices/virtual/misc/kvm` exists and `udevadm info` identifies `/dev/kvm`. No matching `vhost-net` sysfs path was present; `udevadm info --name=/dev/vhost-net` reported unknown device. | The observed `/dev/vhost-net` node is not represented by the expected sysfs event path; a udev rule may not be sufficient to manage it. |
| udev journal | The bounded boot journal showed udev service start/stop/start entries but no specific rule-application or permission error for these nodes. | The exact producer of gid 993 and why the packaged ownership rule did not result in gid 109 remain unproven. A WSL-provided or pre-created device-node ownership is a plausible inference, not a verified root cause. |

No device, group, or rule was changed. A candidate persistent correction would run after WSL has created both device nodes and then set their owner group to the existing `kvm` group with mode 0660; its ordering and behavior for the sysfs-less `vhost-net` node must be verified before approval. Do not change the `kvm` group's numeric GID or create a GID-993 group without first establishing other system uses of that GID.

### Current readiness interpretation

- The historical T07 result that `/dev/kvm` was absent is superseded for the actual host: device nodes are present, but current-user access is still blocked by the numeric GID mismatch. KVM readiness therefore remains **not ready** for the unprivileged development user.
- Active libvirt, OVS, and OVN components are existing local infrastructure, not proof of a functioning Nova or Neutron deployment. No cloud endpoint was queried. The VTEP database and complete Neutron topology remain unverified.
- The current logrotate job is blocked in a package-provided OVN log-reopen hook targeting the unresponsive VTEP controller. The timer is a supported reactivation source; the timer should be paused before an approved recovery. No service was stopped, disabled, or restarted by this audit.
- At the time of this earlier audit, no DevStack release compatibility decision had been made; the later host-recovery follow-up below records the selected package-based candidate.

### Host recovery verification and release compatibility — 2026-10-24

This follow-up checked the actual Ubuntu host after the user-reported recovery. A separate sandbox attempt could not read the system bus or device nodes; that sandbox failure is not host evidence. The read-only host check observed:

| Area | Latest verified evidence | Interpretation |
|---|---|---|
| systemd | `systemctl is-system-running` returned `running`; `systemctl --failed` reported no failed units; `systemctl list-jobs` reported no jobs. | The earlier `starting` state, failed logrotate job, and pending start job are no longer current. |
| logrotate | `logrotate.timer` and `logrotate.service` are `inactive/dead`; service result is `success`; timer has not been re-enabled. | This is the intentionally paused state. No logrotate run was triggered by this check. |
| OVN VTEP | `ovn-controller-vtep.service` is `inactive/dead`, result `success`; its automatic restart policy remains `on-failure`. | It was stopped, not disabled or masked. Whether any external VTEP integration depends on it has not been independently proven. |
| local control services | `libvirtd.service` and `ovn-controller.service` are active/running; `openvswitch-switch.service` and `ovn-central.service` are active/exited. | Service activity alone does not prove Nova, Neutron, guest networking, or a working hypervisor. |
| KVM device nodes | `/dev/kvm` and `/dev/vhost-net` remain uid 0, unmapped gid 993, mode 660. | The normal-user KVM access blocker remains unresolved; no ownership change was made. |
| OVN logrotate hook | `/etc/logrotate.d/ovn-common` still invokes `ovs-appctl -t "$ctl" vlog/reopen` without a timeout. `ovs-appctl` supports `--timeout=SECS`; no package configuration was changed. | The prior blocking hook has no configured per-daemon timeout. A bounded-call edit is a proposed recovery, not applied. Keep the timer stopped until the hook is reviewed and safely validated. |
| Existing DevStack checkout | Read-only inspection of `/home/stack/devstack` and its `local.conf` was denied by host filesystem permissions. | Branch, commit, and configuration selectors are **unverified**; no `sudo` command was run for this inspection. |

#### Ubuntu 22.04 release compatibility (documentation check, not deployment)

- The current [DevStack quick start](https://docs.openstack.org/devstack/2026.1/) says DevStack targets the two latest Ubuntu LTS releases and identifies Ubuntu 24.04 as the most tested; Jammy is outside that stated two-LTS support window as of this review. The installation guide warns that DevStack substantially changes its host and should run only on a dedicated system.
- The archived [DevStack 2025.1 quick start](https://docs.openstack.org/devstack/2025.1/) describes Ubuntu 22.04 as the most tested for that release. The [OpenStack release status table](https://releases.openstack.org/) lists 2025.1 Epoxy as unmaintained since the estimated 2026-10-02 transition. This is historical compatibility evidence, not a maintained current release recommendation.
- The [OpenStack Ubuntu package guide](https://docs.openstack.org/install-guide/environment-packages-ubuntu.html) documents Ubuntu Cloud Archive Caracal (2024.1) for Jammy. Canonical's [package lifecycle table](https://canonical.com/openstack/docs/latest/reference/release-cycle-and-supported-versions/) lists 2024.1 UCA standard EOL as April 2027. That is a different deployment path from DevStack and has a short remaining standard maintenance window; it does not establish compatibility of the already-installed service mix or a supported DevStack deployment.
- At the time of this documentation check, no release had been selected or installed. The subsequent follow-up below selects the Jammy Caracal package path for this POC without authorizing installation. Ubuntu 22.04 remains outside current DevStack guidance.

At that point, no services, timer, device permissions, packages, network settings, or repository application code had been changed by these checks; the hook fix and KVM ownership correction were still pending. The later user-confirmed changes and subsequent checks are recorded below.

### Host recovery follow-up — 2026-10-24 (after user-confirmed changes)

The user confirmed that `/dev/kvm` and `/dev/vhost-net` were changed to `root:kvm` (GID 109), mode 0660, with normal-user read/write access; the DevStack checkout does not exist; the OVN hook was changed to use `ovs-appctl --timeout=5`; and `logrotate --debug /etc/logrotate.conf` reported no configuration errors. This section records independent non-root/read-only or bounded checks after those changes.

| Area | Finding | Limit |
|---|---|---|
| Current host state | systemd is `running`; no failed units or pending jobs. `logrotate.timer` and `logrotate.service` are inactive; `ovn-controller-vtep.service` is inactive. The main `ovn-controller` is running. | The timer remains stopped. No service was changed by this assessment. |
| KVM/QEMU | Normal-user probe `timeout ... qemu-system-x86_64 -accel kvm -machine none -nodefaults -display none -S -monitor none` emitted no KVM initialization error and ended when the three-second timeout sent SIGTERM (recognized exit 124). Device nodes were readable/writable and owned by `root:kvm(109)`, mode 0660. | Confirms QEMU could initialize KVM acceleration for an empty, paused machine. It did not boot a guest or prove Nova integration. The timeout termination was intentional. |
| libvirt | `virsh -c qemu:///system list --all` returned no VMs; `nodeinfo` reported x86_64, 12 logical CPUs, one NUMA cell, and about 7.68 GiB memory. `net-list --all` showed the existing `default` network active and persistent. | No VM was created; the existing network was only inspected. These figures are point-in-time host data. |
| KVM persistence | `/usr/lib/tmpfiles.d/static-nodes-permissions.conf` contains `z /dev/kvm 0660 - kvm -` and the equivalent for `/dev/vhost-net`. `systemd-tmpfiles-setup-dev.service` started at 11:58:33 on Oct 21; `/dev/vhost-net` birth time is 11:58:33.516 and `/dev/kvm` birth time is 11:58:35.758. | The installed rule exists, but the `/dev/kvm` node was created after the boot-time tmpfiles unit started; this supports a late-device-creation race as the likely cause of the prior mismatch. The exact ownership at birth and whether the rule ran before each node existed were not logged, so this remains a strong inference rather than proven root cause. No extra rule or service was installed, and persistence is not proven. |
| OVN hook | The current root-owned OVN sockets are mode 0750, so non-root hook execution is not permitted. The configured command is bounded with `--timeout=5`; debug parsing passed per user report. | Actual `vlog/reopen` postrotate operation has not run. No direct call or full log rotation was performed. The timer remains stopped pending approval for a bounded root execution against the current active sockets. |
| DevStack checkout | User confirmed `/home/stack/devstack` is absent. | No further inspection was attempted; no checkout or configuration was created. |

#### Selected Ubuntu 22.04 deployment method (not installed)

For the fixed Ubuntu 22.04 constraint, the chosen candidate is a **manual, single-node OpenStack 2024.1 Caracal package deployment using Ubuntu Cloud Archive**, following the official package installation guide. This is preferred over DevStack because current DevStack documentation targets the two latest Ubuntu LTS releases, while Ubuntu 22.04 is outside that support statement. Historical DevStack 2025.1 documentation did identify Jammy as most tested, but the 2025.1 OpenStack series is now unmaintained. Caracal is the Jammy package path documented by OpenStack and has a Canonical UCA standard maintenance end date of April 2027, which is a short remaining window as of this assessment. Caracal is itself marked unmaintained upstream. These facts make it a short-lived POC candidate, not a production recommendation.

The minimum target service set for real tenant VM lifecycle is Keystone, MariaDB, RabbitMQ, Glance, Placement, Nova API/scheduler/conductor/compute, and Neutron with OVN/OVS; supporting services include Apache/mod_wsgi and Memcached as required by the chosen package configuration. Cinder, Swift, Horizon, Heat, telemetry, and VTEP are outside the minimum lifecycle scope unless later accepted. OVN uses the existing OVS/OVN daemons, but the installed 2.17/22.03 packages, their configuration, Neutron integration, and systemd health are not yet validated as a coherent Caracal deployment. The Neutron 2024.1 matrix lists an OVS 2.16-era baseline and OVN 21.06.0; the installed versions are numerically newer, but this does not alone prove package or configuration compatibility.

No package source, package, service, endpoint, or cloud resource was changed. Enabling UCA, installing packages, upgrading OVS/OVN dependencies, configuring services, and enabling the logrotate timer each remain approval-gated.

### Task 11 approval follow-up — 2026-10-24

The user approved bounded execution of the OVN hook and installation of a KVM permission oneshot (without `Before=libvirtd.service`). The assistant attempted the hook command, but `sudo` returned “a terminal is required to read the password”; therefore the hook itself did **not** execute. A contemporaneous `systemctl is-active logrotate.timer` still returned `inactive`. The approved KVM unit was not installed because this shell cannot enter the user's sudo password. Exact terminal commands are pending user-side execution; no timer, system service, or device permissions were changed by the assistant in this step.

#### Package-impact inventory (read-only; solver plan incomplete)

The actual host's configured apt sources include Ubuntu Jammy, Jammy updates/security, and NodeSource; no Ubuntu Cloud Archive Caracal source is configured. No source was changed and no `apt update` was run. The local apt cache therefore reports the Jammy archive candidates, not Caracal candidates. The relevant installed and candidate records were:

| Packages | Installed | Candidate from currently cached sources |
|---|---|---|
| Open vSwitch | `openvswitch-switch` / `openvswitch-common` 2.17.12-0ubuntu0.22.04.1 | Same |
| OVN | `ovn-central`, `ovn-host`, `ovn-common` 22.03.8-0ubuntu0.22.04.1 | Same |
| libvirt / QEMU | libvirt 8.0.0-1ubuntu7.21; QEMU 6.2+dfsg-2ubuntu6.31 | Not simulated against Caracal |
| OpenStack control/compute | Keystone, Glance, Placement, Nova, Neutron, OpenStack client | Not installed; cached candidates are Jammy archive versions (e.g. Keystone 21.0.1, Nova 25.2.1, Neutron 20.5.0, Placement 7.0.0), not Caracal |
| Supporting services | MariaDB and RabbitMQ | Not installed; Jammy archive candidates are MariaDB 10.6.23 and RabbitMQ 3.9.27. Memcached 1.6.14 is installed. |

Canonical's Caracal UCA version tracker shows Caracal backports including OVS 3.3.0 and OVN 24.03.2 in its Updates column; these are major-version changes from the installed OVS 2.17/OVN 22.03 packages, and would affect active OVS/OVN services and possibly their databases/configuration. The tracker snapshot is not a host apt solver result and may not match the current Jammy package index. Neutron's Caracal documentation lists OVS 2.16-era and OVN 21.06 baselines, but it does not guarantee compatibility of this locally configured package set.

The Caracal-aware solver plan is now available in [`task11-caracal-package-impact.md`](task11-caracal-package-impact.md): it used signed temporary indexes in `/tmp` and the actual host dpkg status, without changing system apt sources or installing packages. It planned 11 upgrades, 228 new packages, zero removals, and left 22 packages not upgraded. The host apt cache still has no Caracal source. Maintainer-script restarts, conffile behavior, disk use, and rollback of OVS/OVN database changes are not established by simulation. Do not run a broad `upgrade`/`full-upgrade`; preserve existing OVS/OVN databases, VTEP state, libvirt network, VM/backups, and the local logrotate edit. Package installation remains approval-gated. Public references: [Caracal UCA version tracker](https://openstack-ci-reports.ubuntu.com/reports/cloud-archive/caracal_versions.html), [Neutron OVS/OVN requirements](https://docs.openstack.org/neutron/latest/install/ovs-ovn-requirements.html).
