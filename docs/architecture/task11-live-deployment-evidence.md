# Task 11 — Local deployment evidence

Recorded 2026-10-24 for the approved Ubuntu 22.04 WSL2 laptop POC. Host evidence below was supplied by the operator from the actual Ubuntu terminal. Codex's restricted execution environment cannot independently validate those services. This record does not establish staging or production readiness.

## Preservation and package gate

The verified pre-Caracal backup is `/home/sneha/openstack-poc-pre-caracal-20261024-150119`. Its archive and snapshot checks are recorded in [the backup procedure](task11-ovs-ovn-backup-procedure.md). No restoration has been exercised. Existing operational, billing, metering, VM recovery, and network data must remain preserved.

The operator installed the reviewed Caracal package set. The subsequent `dpkg --audit` returned no output. OVS reports 3.3.9, OVN packages report 24.03.6, and all three existing OVSDB sockets respond. The protected Placement and OVN logrotate configuration hashes matched the pre-install backup after installation. Package presence and running daemons alone do not establish functional service integration.

## Verified local API and image milestones

| Capability | Operator-supplied result | Limit |
|---|---|---|
| Keystone | Configured MariaDB, initialized schema and Fernet/credential keys, bootstrapped identity; `GET http://127.0.0.1:5000/v3` returned 200; later admin token requests succeeded | Tenant negative authorization and application identity integration remain untested |
| Placement | Replaced obsolete `dbcounter` database URL plugin; created database/schema, service identity and endpoints; API returned 200; authenticated resource-provider list succeeded; Nova later registered the provider recorded below | Registration does not prove guest launch or network operation |
| Glance | Created database/schema, service identity and endpoints; API discovery returned 300 and authenticated image listing succeeded; later Nova used the image for the instance below | Guest network and metadata checks remain pending |
| Glance image upload | Private `cirros-0.6.3-x86_64-poc`, ID `da953e4e-38ca-47d2-b97a-b62d3f33646a`, qcow2, 21,692,416 bytes, reached `active` | Image upload and the first KVM-backed VM launch are real; remaining acceptance is pending |
| Image source integrity | CirrOS 0.6.3 x86_64 disk image SHA-256 matched `7d6355852aeb6dbcd191bcda7cd74f1536cfe5cbf8a10495a7283a8396e4b75b` | Source download retained at `/tmp/openstack-poc-cirros.FcgL2d/cirros-0.6.3-x86_64-disk.img`; temporary storage is not a recovery copy |
| OVN database listeners | NB `ptcp:6641:127.0.0.1`, SB `ptcp:6642:127.0.0.1`; socket listener output confirmed loopback bindings | This change alone does not restrict other API or RabbitMQ listeners or prove tenant networking |

Keystone and Placement are served by Apache `mod_wsgi`, not standalone systemd units. Initial HTTP 500 errors came from Keystone's missing Fernet keys and Placement's unsupported SQLAlchemy plugin. Glance's first immediate post-restart probe refused connections; later listener and authenticated API checks passed.

## Current capacity and network inventory

The operator reported 12 logical CPUs, 7.7 GiB WSL RAM with 2.7 GiB available, 2 GiB swap with approximately 178 MiB used, and 934 GiB free on the Linux filesystem. These are point-in-time observations, not a capacity guarantee.

The WSL `eth0` address is `172.19.169.140/20`, default gateway `172.19.160.1`. The preserved libvirt network uses `192.168.122.1/24` on `virbr0`. OVS has `br-int`; OVN has one chassis named `DESKTOP-Sneha` with Geneve endpoint `172.19.169.140`. Northbound topology was empty. Do not assume the WSL address survives restart.

## Nova setup boundary

The initial large terminal setup block produced no output. A subsequent operator check found all five planned Nova/Neutron/RabbitMQ secret keys absent, no `/root/nova.conf.before-poc`, no Nova MariaDB databases, and only the default RabbitMQ `guest` account. Nova daemons were active, but `nova-manage cell_v2 list_cells` failed against SQLite with `no such table: cell_mappings`. Nova initialization had not been demonstrated at that checkpoint; the later stages below supersede that status.

The operator ran the resumable [Nova prerequisite script](../../scripts/local-openstack/nova-prerequisites.sh). It reported **PASS** for creation of the three Nova databases and authentication with the retained Nova database credentials, and **PASS** for RabbitMQ account authentication and permissions. Credentials were retained in the existing root-only file. This stage did not initialize Nova cells, change Nova configuration, create Keystone service identities, restart services, or create guests.

The operator then ran the [Nova configuration script](../../scripts/local-openstack/configure_nova.py). Both Nova and Neutron service-identity authentications passed. Local compute and network catalog endpoints were registered, and both Nova configuration files were backed up and updated. The network endpoint remains a pending deployment entry until Neutron is configured and validated. Cell initialization refuses conflicting existing mappings, and only safe cell fields are printed. The four POC Nova units were restarted and actual API, compute, hypervisor, cell mapping and Placement registration checks passed. Failures preserve credentials and backups for resumption; the script refuses reconfiguration with live Nova instances.

| Nova registration evidence | Operator-supplied result |
|---|---|
| cell0 | `00000000-0000-0000-0000-000000000000` |
| cell1 | `2def30ce-6f08-4cce-996c-98b63447ea34` |
| Compute service | `DESKTOP-Sneha`, `up/enabled`; host discovery/mapping passed |
| Hypervisor | `DESKTOP-Sneha.localdomain`, type `QEMU` |
| Placement compute provider | `4cef7739-1c06-4430-a1f0-94851fb69153` |
| VCPU inventory | total 12, reserved 0, allocation ratio 1.0 |
| MEMORY_MB inventory | total 7,866, reserved 6,144, allocation ratio 1.0; 1,722 remain before guest allocations |
| DISK_GB inventory | total 1,006, reserved 0, allocation ratio 1.0; this is inventory, not current free filesystem space |

Libvirt's reported hypervisor type `QEMU` does not demonstrate software emulation or KVM acceleration for a guest. The configuration selects KVM, but the running guest's domain type/acceleration still needs independent verification during VM acceptance.

The compute configuration uses KVM/libvirt, the current `eth0` address, loopback API/metadata listeners, one API/metadata worker, no CPU/RAM/disk overcommit, and 6,144 MiB reserved for the host/control plane. The reservation is scheduler accounting, not physical memory allocation; actual memory pressure must still be monitored. VNC is disabled because noVNC is outside this stage. Neutron metadata credentials are prepared, but Neutron networking remains unverified.

The configuration script stores initial file backups under `/root/nova.conf.before-poc` and `/root/nova-compute.conf.before-poc`, with original owner/mode information in adjacent `.metadata.json` files. Backups are preserved on reruns. If a configuration rollback is required, first inspect any actual VM state and preserve newly created databases; restore only these configuration files from the protected copies using their recorded owner/mode, then restart only the four POC Nova services. Do not automatically drop databases, remove service accounts, delete cell mappings, restore old OVSDBs, or undo other service milestones. Config rollback does not undo schema migration or catalog registration. Escalate recovery beyond configuration if migrations fail.

Workspace verification: Python syntax and the non-root rejection check passed. All 12 focused tests in `tests/test_nova_setup.py` passed, including config preservation, retry behavior, identity/endpoint conflicts, cell validation, credential redaction, and compute inventory readiness. The full offline suite passed **195 tests** (`python3 -m unittest discover -s tests`). These tests use local fixtures/mocks and do not execute the privileged host setup, contact OpenStack, create a VM, or prove Nova/Neutron integration.

## Neutron ML2/OVN control-plane evidence

The operator ran the [Neutron configuration script](../../scripts/local-openstack/configure_neutron.py). Neutron identity authentication, database/account authentication, and schema migration passed. Configuration backups and updates completed with underlay MTU 1,500. After restarting the Neutron API and metadata service, the authenticated network-list query passed with **zero networks**, router/security-group/port-security extensions were present, and `OVN Controller Gateway agent` plus `OVN Metadata agent` were both alive/admin-up on `DESKTOP-Sneha`. These are real control-plane checks, not tenant packet evidence.

The stage configures MariaDB, Keystone authentication, Nova notifications, ML2/OVN, port security/security groups, and the OVN metadata agent. It uses the installed package's actual metadata config filename and configures `/etc/default/neutron-server` to load the ML2 file. API/RPC worker counts are one; OVN metadata uses its process-local default of zero fork workers. Neutron's API binds to loopback.

This stage enables Geneve tenant networks only. Physical provider networks, external bridge mappings, routers with external gateways, host routing/NAT, floating IPs, and Windows/WSL external access require later scoped validation. The ML2 path MTU comes from the current `eth0` interface; the Geneve maximum header size is 38 bytes with IPv4 overlay overhead accounted for by Neutron. These choices follow the [Neutron 2024.1 OVN manual](https://docs.openstack.org/neutron/2024.1/install/ovn/manual_install.html). No guest MTU or packet-path test has been run.

The script preserves the existing `br-int`, OVS system ID, encapsulation address/type, bridge mappings, physical interfaces, host routes, and libvirt network. It checks for encapsulation drift and loopback database listeners before changing configuration. It backs up the existing `ovn-remote` value and sets only that OVS external ID to `tcp:127.0.0.1:6642`, so the local controller can reconnect after the earlier database listener restriction. OVN synchronization stays in `log` mode, which reports inconsistencies without automatic topology repair/deletion. Only the Neutron API and metadata services are restarted; OVS/OVN/libvirt services are not restarted. The script refuses reconfiguration if live Nova guests or Neutron networks exist.

Protected config backups are `/root/neutron.conf.before-poc`, `/root/ml2_conf.ini.before-poc`, `/root/neutron_ovn_metadata_agent.ini.before-poc`, and `/root/neutron-server.defaults.before-poc`, with owner/mode sidecars. The prior remote is saved in `/root/neutron-ovn-remote.before-poc.json`. Recovery requires inspecting resources created since setup before restoring only these configuration files with recorded metadata. Restore the prior remote using `ovs-vsctl set Open_vSwitch . external_ids:ovn-remote=VALUE`, or `ovs-vsctl remove Open_vSwitch . external_ids ovn-remote` if the key was originally absent. Use the exact saved value without guessing; restart only Neutron API/metadata if needed. No automated database deletion/downgrade, topology restoration, account removal, or guest deletion is part of rollback. No restore has been exercised.

Readiness requires an authenticated network-list query, the router/security-group/port-security extensions, and live/admin-up OVN controller and metadata agents on the same host. This verifies control-plane registration, not DHCP, metadata delivery to a guest, tenant isolation or packet connectivity. The next acceptance stage must create task-owned resources and verify those behaviors.

Workspace verification: all **9 Neutron setup tests** passed, including MTU validation, plugin-loader edits, backend drift/listener checks, atomic config backup, and readiness rejection for missing/dead/mismatched agents or extensions. All **204 offline tests** passed after adding this stage and refactoring the shared config writer. Python syntax and the non-root execution guard passed. Host configuration was executed by the operator; Codex did not independently execute it.

## First real VM launch evidence

The operator ran the [test VM launch script](../../scripts/local-openstack/launch_test_vm.py). Its image, quota, memory and CIDR checks passed, and the task-owned Nova instance progressed from `BUILD` to `ACTIVE`. Libvirt domain XML reported `type=kvm`, and the read-only QEMU `query-kvm` response confirmed KVM present/enabled. These verify a real running accelerated guest; guest boot completion, networking and lifecycle tests still require the checks below. This first infrastructure smoke test uses the authenticated admin project, so it does not establish tenant-user authorization or cross-project isolation.

| Launch evidence | Operator-supplied result |
|---|---|
| Run marker | `b0a2694420364856a755347a048ea36a` |
| Private network | `6ee5ad82-efc5-405d-bf5c-a4953e9a063b` |
| DHCP-enabled subnet | `25f8362e-436b-405e-8158-2e2d552cd2e7`, `172.30.240.0/24` |
| Security group | `5a3b3c35-d5d6-4cbe-bf62-41ca04aec923` |
| Nova server | `82382430-4b64-4386-a55b-8468e04da011`, `ACTIVE` |
| KVM acceleration | Domain type `kvm`, QEMU reports present/enabled |
| Neutron allocated address | `172.30.240.154`; this alone does not prove the guest applied a DHCP lease |
| Metadata namespace probe source | `172.30.240.2/32`; only ICMP and TCP/22 ingress from this source are allowed by the created group |
| Network MTU | 1,442 bytes |

The script checks image state/ownership/minimum size, project compute quota, at least 768 MiB host `MemAvailable`, and CIDR overlap against host IPv4 addresses/routes and observed Neutron subnets. Candidate `/24` networks are `172.30.240.0/24`, `10.203.0.0/24`, and `192.168.240.0/24`; it chooses a non-overlapping candidate or stops.

The stage creates one private Geneve network, DHCP-enabled IPv4 subnet, security group, private 1-vCPU/256-MiB/1-GiB flavor, SSH public-key registration, and one Nova instance. Only ICMP and TCP/22 ingress from that network's OVN metadata port `/32` are allowed in the created group. No physical interface, host route, NAT, external bridge mapping, external gateway, floating IP, or public image is added. Existing networks, VM disks, images, backups, and identities are preserved. CirrOS is a cloud test image with known default login credentials; this test has no external gateway, consistent with the [OpenStack image guide](https://docs.openstack.org/image-guide/obtain-images.html).

The root-only directory `/root/openstack-poc-acceptance` retains a run manifest (`run.json`) containing project/run markers and provider resource IDs, and a separately protected SSH test key. No bearer tokens or passwords are stored in that manifest or Git. Names and ownership markers support recovery after interrupted API responses; resources with conflicting markers/project/shape are preserved and rejected. Nova's returned server ID is recorded before fetching its full state, and a repeated launch command inspects/reuses that task-owned instance instead of creating a second VM. A known resource that was manually removed requires review rather than silent replacement.

After Nova reports `ACTIVE`, the script independently reads the libvirt domain XML and the read-only QEMU `query-kvm` result. Both domain `type=kvm` and monitor `present=true, enabled=true` are required for the acceleration check to pass. A Neutron fixed-IP allocation is reported separately from DHCP success inside the guest. The stage does not yet verify guest boot completion, DHCP lease/application, SSH, metadata content, MTU packets, security-group enforcement, lifecycle operations or deletion. No config drive is requested; metadata behavior still requires direct guest validation.

On failure, the script retains task resources and the manifest for diagnosis; it does not delete or recreate failed instances automatically. Later authorized cleanup must validate ownership markers/project and remove only this run's resources in dependency order, verify guest/disk/port removal, and preserve the uploaded base image and all historical recovery data. Cleanup and networking/lifecycle acceptance remain pending.

Workspace verification: **7 launch safety tests** passed, covering CIDR conflicts, ownership/project checks, duplicate prevention and interrupted creation recovery, constrained probe rules, and actual-KVM evidence requirements. All **211 offline tests** passed. Python syntax and the non-root execution guard passed. These tests did not launch a VM or contact OpenStack.

## Guest networking, metadata and MTU packet evidence

The operator ran the [guest validation script](../../scripts/local-openstack/validate_guest.py) against server `82382430-4b64-4386-a55b-8468e04da011`. Key-only SSH, guest IPv4/MAC matching, DHCP lease matching, guest MTU **1,442**, and HTTP metadata UUID/name checks all reported **PASS**. The subsequent MTU ping did not execute successfully: the installed host `ping` rejected the script's `-e` option with exit 2. This was a probe-command compatibility defect; it does not establish a failed packet path or a passed MTU packet test.

The correction removes `-e` and retains source binding, three probes, bounded reply waits, DF, and the MTU-sized payload. After the file-check correction below, the operator ran `--ping-only` and reported **PASS** for all three MTU-sized IPv4 echo replies through the OVN metadata namespace. With network MTU 1,442, the payload is 1,414 bytes plus the 28-byte IPv4/ICMP headers. This is local single-host packet-path evidence, not multi-host Geneve or external-routing validation. Earlier guest checks are operator-supplied evidence here; the initial script had deferred manifest persistence until after ping, so it did not persist those successful guest fields before failing. The corrected full run records guest inspection before the separate ping result, and a failed ping cannot leave a stale successful ping flag. The ping-only retry records only its own evidence and does not invent guest fields.

The first ping-only retry stopped before probing because `load_run` still checked the SSH `known_hosts` file against its root-owned/regular/0600 policy. The subsequent metadata-only host readback confirmed a regular `root:root` file with mode **0644**. File contents were not exposed. Ping-only mode now validates the protected manifest and resource/attachment scope without reading or changing SSH files or requiring the SSH executable. SSH mode keeps its existing strict file checks and pre-creates a missing dedicated trust file with mode 0600 before OpenSSH opens it. The next policy stage explicitly tightens only the observed task trust file from 0644 to 0600 after secure descriptor/ownership/link-count checks, verifies unchanged pinned key content, and then uses the normal strict SSH checks. That permission correction has not yet run on the host.

The script verifies the protected manifest, project/run markers, private network/subnet ownership and DHCP settings, the instance's sole port/security group, and the existing metadata namespace source address before probing. It connects only to the recorded guest address through that namespace, binds the known source `/32`, and authenticates using the retained SSH key. Password and keyboard-interactive authentication, agent forwarding, proxy commands and configured port forwarding are disabled. A dedicated root-only known-hosts file uses first-contact key acceptance and requires that pinned key on later connections; this local POC trust bootstrap is not production host-key verification.

Guest commands inspect interface IPv4, MAC, MTU and kernel boot ID, dump the DHCP lease using `dhcpcd -U`, and retrieve OpenStack metadata from `http://169.254.169.254/openstack/latest/meta_data.json`. The script requires the applied address/prefix and lease to match Neutron, the guest MAC to match its port, the MTU to match the network, and metadata UUID/name to match the task instance (and project ID if the endpoint includes it). It then sends three IPv4 echo probes with DF set and payload `network MTU - 28`, requiring all three replies. Full metadata, credentials and tokens are not written to the evidence record; only selected verified fields and a timestamp are added to the root-only manifest.

This stage changes no guest configuration, Neutron policy, VM lifecycle state, services or host networking. It writes only the protected SSH known-hosts file and acceptance results. It does not yet prove security-group denial, cross-project API isolation, routed/external connectivity, multi-host overlays, stop/start/reboot/delete, cleanup or application provider integration. Those remain separate acceptance steps after guest reachability is established.

Workspace verification after both retry corrections: **13 guest validation tests** passed, covering complete/missing/ambiguous guest evidence, provider/lease/MAC/MTU/metadata mismatches, restricted SSH arguments, bounded readiness failures, compatible ping arguments, MTU payload/full-reply checks, separate evidence persistence and stale-success clearing. Regression checks confirm ping-only works without a private SSH key and leaves a 0644 test trust file unchanged, while SSH mode still rejects that mode. All **224 offline tests** passed. The non-root guard also passed with `--ping-only`. These offline tests did not execute actual guest/packet checks; host results are recorded separately above.

## Pending owned security-group policy acceptance

The [policy validation script](../../scripts/local-openstack/validate_policy.py) is prepared; host execution is pending. It acts only on the manifest-owned instance/security group and requires the two exact task-created ingress rules (ICMP and TCP/22 from the metadata source `/32`). Unexpected broader rules, different markers or project ownership cause it to stop before policy changes. The existing private key is reused, and the existing pinned host key is preserved during the targeted trust-file permission correction described above.

The experiment requires baseline pinned-key SSH, matching guest identity, ICMP and a fresh TCP/22 connection. It records a restoration marker before deleting only the owned TCP/22 rule. It then requires three consecutive connection timeouts using distinct source ports, confirms the same guest/path still responds to ICMP, restores the identical narrow rule, and requires fresh TCP/22 plus pinned-key SSH again. An unchanged guest boot ID rules out a reboot during the test. Fresh TCP connections avoid treating a stateful existing connection as proof of a new rule decision; see [Neutron's security-group behavior](https://docs.openstack.org/neutron/2024.1/doc-neutron.pdf). Connection refusal, another socket error, reused source tuples, partial observations, or failure to recover are not accepted as policy-denial proof.

Restoration runs in `finally` on ordinary errors and is recorded in the protected manifest. On a process/host interruption, rerunning the stage first restores a pending task rule using its exact source/protocol/port/marker, before requiring guest readiness or SSH. It reconciles an already restored identical rule and does not reset or replace unrelated rules. Original/restored rule IDs can differ and are retained for traceability. A failed restore leaves its marker for recovery; it is not marked passed. OVS, Neutron and libvirt services, physical networks, routes, other groups, guests, credentials and recovery backups are not reconfigured by this stage.

This experiment covers ingress TCP/22 on the one owned local path. It does not prove all protocols, egress isolation, cross-project authorization, routed/external traffic or multi-host isolation. It does not stop, start, reboot or delete the VM; those are the next acceptance step after policy restoration is verified.

Workspace verification: **9 policy tests** passed, covering safe trust-file mode correction without key replacement, owner/symlink refusal, exact rule scope, distinct/consecutive connection evidence, rejection of connection refusal, restoration after failure, retained recovery markers, and idempotent narrow-rule restoration. All **233 offline tests** passed. Python syntax and the non-root guard passed. The real policy experiment and trust-file correction have not been executed from Codex.

## Remaining acceptance work

Complete security-group denial checks, lifecycle and tenant isolation; exercise authorized cleanup; integrate the POC application with real scoped APIs; and run the required acceptance tests. Review all local control-plane listener exposure before completion. KVM permissions across WSL restart, recovery restoration, and staging acceptance remain unverified. Keep `logrotate.timer` inactive until the existing runtime validation/review requirement is satisfied; VTEP remains stopped.

The offline fixture lifecycle evidence remains separate from real-provider evidence. The operator verified a real Nova/KVM guest, Neutron resource creation, key-only SSH, the applied DHCP lease/address/MAC, guest MTU, instance metadata identity and MTU-sized packet delivery on the local path. Policy denial, remaining lifecycle operations, tenant authorization, cleanup and application integration remain pending. Local repository commits do not imply full POC completion or authorize remote pushes.
