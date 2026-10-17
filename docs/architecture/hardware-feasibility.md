# Hardware feasibility

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
- Prefer an existing/remote OpenStack endpoint when available. A fixture-backed adapter is the safe fallback for workflows that cannot run on this laptop.
- Current local discovery found no `openstack` executable, OpenStack SDK/Nova client modules, matching package names or service processes, standard OpenStack configuration files, or endpoint indicators in the checked standard locations. This does not prove no remote endpoint exists. No cloud endpoint was contacted. The Docker daemon is currently reachable and Engine-visible resources are recorded above; Windows Docker Desktop resource-limit settings remain unknown. The user-selected Option B prioritizes the OpenStack management POC and deterministic fixtures when no usable endpoint is available, while the separate Proxmox-versus-Nova VM-manager ambiguity remains unresolved.
- Distinguish host GPU checks, Docker GPU visibility, and guest GPU passthrough. The Docker CUDA check passed; it does not prove Nova GPU passthrough or MIG.
