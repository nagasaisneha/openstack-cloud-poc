# Environment audit

**Date:** 2026-10-18
**Scope:** Read-only audit for a laptop-based OpenStack management POC inside Ubuntu on WSL2.

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
- The user selected **Option B — OpenStack management POC first**: prefer an existing OpenStack endpoint if available; otherwise use deterministic fixture-backed simulation for early development. This does not resolve the Proxmox-versus-Nova VM-manager scope recorded in [`architecture-gaps.md`](architecture-gaps.md); no choice is inferred from Option B.
- At 7.7 GiB RAM, a multi-service OpenStack control plane plus guest VMs, monitoring, Kubernetes, and AI services cannot be assumed to fit. The service plan prioritizes a tiny, measured deployment or a remote/existing OpenStack endpoint; unsupported workflows should be represented with mocks or fixtures.
- Docker CUDA `nvidia-smi` verified that the container can see the RTX 2060. Host NVML access, Nova GPU passthrough, GPU isolation, and MIG remain unverified or unsupported. The laptop GPU is not an H100 substitute and offers no H100 MIG profiles.
- The diagram path differs from the requirements' intended path. The actual image is preserved and referenced by its actual filename.

## Safety boundary

This audit changed no system configuration, installed no packages, made no cloud/API calls, created no resources, and deleted nothing. No secrets were exposed.
