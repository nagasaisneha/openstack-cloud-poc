# Hardware feasibility

This is a point-in-time assessment of the laptop development/local POC environment. It does not assess a separate staging environment or the organization’s physical data-center production environment. The current scope continues the local POC; AWS-hosted OpenStack and AWS-native tenant provisioning are deferred and outside the approved implementation scope.

## Available laptop resources

| Resource | Finding | Practical effect |
|---|---|---|
| CPU | 12 logical CPUs | Enough for a small API, CLI work, and a limited number of light processes; not a substitute for a multi-node cloud. |
| RAM | 7.7 GiB total; 5.9 GiB available at audit | Tight for OpenStack plus guest VMs. Budget must be measured; do not plan to run OpenStack, Kubernetes, full monitoring/logging, and inference together. |
| Swap | 2.0 GiB configured | May reduce abrupt memory pressure but is not usable RAM and can make workloads slow. |
| Virtualization | WSL2; QEMU 6.2.0; QEMU process using `-accel kvm` started and stopped successfully | Verified minimal KVM-accelerated QEMU operation. Full OpenStack VM provisioning and Neutron networking remain unverified. |
| Container runtime | Docker Desktop 4.90.0; Docker Engine 29.7.2; context `default`; local endpoint `unix:///var/run/docker.sock` | Latest local output verified working daemon connectivity from Ubuntu WSL and `docker info --format 'CPUs={{.NCPU}} Memory={{.MemTotal}}'` values of 12 CPUs and 8248930304 bytes (about 7.68 GiB) Engine-visible memory. This is not current free memory and does not verify Windows Desktop resource-limit settings. An earlier query at 2026-10-18 02:31:46 +05:30 failed with permission denied; record it as an earlier transient observation, cause unknown. |
| GPU | RTX 2060 with 6 GB VRAM; CUDA container ran `nvidia-smi` successfully | Verified container GPU visibility. Host NVML access failed during audit; Nova GPU passthrough, guest visibility, MIG, and tenant isolation remain unverified or unsupported. |
| WSL root filesystem | 1007G total, 24G used, 932G available, 3% used (`/dev/sdd` mounted at `/`) | `df -h /` measured **2026-10-18 02:31:46 +05:30** (**2026-10-17 21:01:46 UTC**). This is not necessarily Docker Desktop's separate virtual disk/storage limit. |
| Network | WSL2 environment; topology not measured | Local/private access is the default. Public ingress, stable inbound reachability, and tenant network semantics are unproven. |

## Actual Ubuntu 22.04 WSL host follow-up — 2026-10-24

The earlier rows above retain their original point-in-time measurements. A current read-only host check found 7.7 GiB total RAM with 4.6 GiB available and 2 GiB swap. `/dev/kvm` and `/dev/vhost-net` now exist, but both are `root:993`, mode `0660`, and inaccessible to `sneha`; the local `kvm` group is gid 109 even though `sneha` belongs to it. Thus KVM device presence is verified, but usable non-root KVM access is **blocked** until a separately approved ownership/mapping correction is assessed and verified.

The current host has active libvirt, Open vSwitch, and OVN services. These components may be reusable, but their active state does not establish a complete OpenStack deployment or prove compatibility with DevStack. The active `ovn-controller-vtep` control socket is unresponsive to the observed log-reopen request; Neutron VTEP topology is not established. The exact current evidence and limits are recorded in the [actual-host environment audit follow-up](environment-audit.md#actual-ubuntu-2204-wsl-host-follow-up--2026-10-24).

**Current feasibility:** the laptop has ample disk in the observed WSL root filesystem but tight available RAM for a control plane plus a tenant VM. More immediately, KVM access for the normal user is blocked, and the existing service state must be recovered or isolated before deployment. This does not select a DevStack release or establish that a selected release supports Jammy/WSL2; release compatibility remains a separate decision.

### DevStack release compatibility evidence — 2026-10-24

No DevStack release is selected here. Current DevStack documentation says it attempts to support the two latest Ubuntu LTS releases and identifies Ubuntu 24.04 as the most-tested version; it does not document Ubuntu 22.04 as a current supported target ([DevStack quick start](https://docs.openstack.org/devstack/latest/)). A June 2026 OpenStack QA discussion states that Jammy was intended to be dropped from normal DevStack testing after the 2025.1 branch; this is evidence of reduced CI coverage, not a compatibility guarantee ([OpenStack QA discussion](https://meetings.opendev.org/irclogs/%23openstack-qa/%23openstack-qa.2026-06-18.log.html)). The OpenStack release status page lists 2025.1 as unmaintained and 2025.2/2026.1/2026.2 as maintained as of this review ([OpenStack release status](https://releases.openstack.org/)).

Accordingly, a maintained DevStack branch with verified Ubuntu 22.04 support has **not been established**. The previous Jammy/2025.1 pairing may be available as a best-effort historical path, but current support and maintenance are not proven. The OpenStack installation guide documents Ubuntu Cloud Archive packages for some releases on Jammy, including Caracal; that is a different deployment method and does not establish DevStack compatibility ([Ubuntu package installation guide](https://docs.openstack.org/install-guide/environment-packages-ubuntu.html)). Choosing an older or best-effort release, changing the OS constraint, or selecting another deployment method remains an owner decision. No packages or deployment scripts were installed or run.

**Jammy package candidate (proposed, unapproved):** The OpenStack installation guide maps Ubuntu 22.04 to OpenStack 2024.1 Caracal through Ubuntu Cloud Archive. Canonical's release-cycle page lists the Caracal UCA package stream through **April 2027**, while the upstream release tracker classifies Caracal as **unmaintained**. This may be a bounded local POC candidate if Jammy is fixed, but it has a short remaining package-maintenance window and is not a DevStack deployment. It is not selected or approved here ([Ubuntu package guide](https://docs.openstack.org/install-guide/environment-packages-ubuntu.html), [Canonical package lifecycle](https://canonical.com/openstack/docs/latest/reference/release-cycle-and-supported-versions/), [upstream release status](https://releases.openstack.org/)).

## Feasibility by workload

| Workload | Assessment |
|---|---|
| Documentation, Python/Go management client, small REST API | Feasible locally, once approved prerequisites are installed if needed. |
| OpenStack CLI/API client against an existing cloud | Likely feasible; endpoint availability and auth are unknown. Protect credentials and avoid logging tokens. |
| Full OpenStack control plane | High risk / likely constrained at 7.7 GiB RAM. No deployment method is selected until version, disk, virtualization, and resource limits are checked. A remote/existing cloud or service-by-service simulation is the fallback. |
| Small Nova VM | Unverified and conditional. QEMU KVM start/stop was verified, but Nova provisioning, image/disk capacity, and functioning Neutron are not established. |
| Kubernetes + JupyterHub + GPU Operator | Not suitable as a default local stack alongside OpenStack at this memory size. Defer or use a remote environment; a single lightweight container/mock is suitable for workflow demonstrations. |
| Cinder / Ceph / ZFS | No backend evidence. Full Ceph on this laptop is unsupported as a credible production-like storage test. Cinder operations are conditional on an already available backend; otherwise simulate. |
| RTX 2060 inference | Docker GPU visibility is verified. A model workload is not yet benchmarked. A small quantized model or modest batch may fit; record actual model, runtime, VRAM, and quality if approved. |
| H100 / MIG profiles / multi-tenant GPU service | Unsupported by this hardware. The laptop cannot validate H100 performance, H100 MIG partition sizes, or isolated multi-tenant GPU scheduling. Represent these as unavailable or simulated only. |
| Production HA, redundant infrastructure, public cloud ingress, DR | Unsupported in a single WSL2 laptop. Keep explicitly out of scope. |

## Resource guardrails for later approved work

- Recheck free disk before large downloads; the WSL root filesystem had 932G available at the recorded measurement. Docker Engine currently reports 12 CPUs and 8248930304 bytes (about 7.68 GiB) visible memory. Ubuntu host RAM was separately recorded as 7.7 GiB total and 5.9 GiB available at the earlier point-in-time check; neither the Engine figure nor that earlier availability value establishes current free memory or the Windows Docker Desktop resource-limit settings. Inspect the latter manually in Docker Desktop for Windows → Settings → Resources without changing it.
- Establish a workload budget and stop if host memory pressure becomes sustained; reserve capacity for Ubuntu/WSL and the desktop host.
- Keep one major stack at a time. Avoid co-locating OpenStack, Kubernetes, observability databases, and inference.
- These laptop-local resource guardrails apply only to development and the local POC. Keep fixtures for workflows that cannot run locally; they do not establish staging or data-center readiness.
- Current local discovery found no `openstack` executable, OpenStack SDK/Nova client modules, matching package names or service processes, standard OpenStack configuration files, or endpoint indicators in the checked standard locations. This does not prove no remote endpoint exists. No cloud endpoint was contacted. The Docker daemon is currently reachable and Engine-visible resources are recorded above; Windows Docker Desktop resource-limit settings remain unknown. Nova is the chosen tenant compute path and Proxmox is deprecated; neither is verified as operational by this local audit.
- Distinguish host GPU checks, Docker GPU visibility, and guest GPU passthrough. The Docker CUDA check passed; it does not prove Nova GPU passthrough or MIG.
