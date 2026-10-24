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
| Glance | Created database/schema, service identity and endpoints; API discovery returned 300 and authenticated image listing succeeded | No Nova guest has used the image yet |
| Glance image upload | Private `cirros-0.6.3-x86_64-poc`, ID `da953e4e-38ca-47d2-b97a-b62d3f33646a`, qcow2, 21,692,416 bytes, reached `active` | Upload is real; VM execution remains unverified |
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

## Pending Neutron ML2/OVN stage

The [Neutron configuration script](../../scripts/local-openstack/configure_neutron.py) is prepared and tested offline; host execution is pending. It configures a MariaDB database/account with a retained generated password, Keystone authentication using the already verified Neutron identity, Nova notifications, ML2/OVN, port security/security groups, and the OVN metadata agent. It uses the installed package's actual metadata config filename and configures `/etc/default/neutron-server` to load the ML2 file. API/RPC worker counts are one; OVN metadata uses its process-local default of zero fork workers. Neutron's API binds to loopback.

This stage enables Geneve tenant networks only. Physical provider networks, external bridge mappings, routers with external gateways, host routing/NAT, floating IPs, and Windows/WSL external access require later scoped validation. The ML2 path MTU comes from the current `eth0` interface; the Geneve maximum header size is 38 bytes with IPv4 overlay overhead accounted for by Neutron. These choices follow the [Neutron 2024.1 OVN manual](https://docs.openstack.org/neutron/2024.1/install/ovn/manual_install.html). No guest MTU or packet-path test has been run.

The script preserves the existing `br-int`, OVS system ID, encapsulation address/type, bridge mappings, physical interfaces, host routes, and libvirt network. It checks for encapsulation drift and loopback database listeners before changing configuration. It backs up the existing `ovn-remote` value and sets only that OVS external ID to `tcp:127.0.0.1:6642`, so the local controller can reconnect after the earlier database listener restriction. OVN synchronization stays in `log` mode, which reports inconsistencies without automatic topology repair/deletion. Only the Neutron API and metadata services are restarted; OVS/OVN/libvirt services are not restarted. The script refuses reconfiguration if live Nova guests or Neutron networks exist.

Protected config backups are `/root/neutron.conf.before-poc`, `/root/ml2_conf.ini.before-poc`, `/root/neutron_ovn_metadata_agent.ini.before-poc`, and `/root/neutron-server.defaults.before-poc`, with owner/mode sidecars. The prior remote is saved in `/root/neutron-ovn-remote.before-poc.json`. Recovery requires inspecting resources created since setup before restoring only these configuration files with recorded metadata. Restore the prior remote using `ovs-vsctl set Open_vSwitch . external_ids:ovn-remote=VALUE`, or `ovs-vsctl remove Open_vSwitch . external_ids ovn-remote` if the key was originally absent. Use the exact saved value without guessing; restart only Neutron API/metadata if needed. No automated database deletion/downgrade, topology restoration, account removal, or guest deletion is part of rollback. No restore has been exercised.

Readiness requires an authenticated network-list query, the router/security-group/port-security extensions, and live/admin-up OVN controller and metadata agents on the same host. This verifies control-plane registration, not DHCP, metadata delivery to a guest, tenant isolation or packet connectivity. The next acceptance stage must create task-owned resources and verify those behaviors.

Workspace verification: all **9 Neutron setup tests** passed, including MTU validation, plugin-loader edits, backend drift/listener checks, atomic config backup, and readiness rejection for missing/dead/mismatched agents or extensions. All **204 offline tests** passed after adding this stage and refactoring the shared config writer. Python syntax and the non-root execution guard passed. No Neutron host configuration was executed from Codex.

## Remaining acceptance work

Configure and validate Neutron, launch a real KVM-backed guest, validate lifecycle and tenant networking/isolation, exercise authorized cleanup, integrate the POC application with real scoped APIs, and run the required acceptance tests. Review all local control-plane listener exposure before completion. KVM permissions across WSL restart, recovery restoration, and staging acceptance remain unverified. Keep `logrotate.timer` inactive until the existing runtime validation/review requirement is satisfied; VTEP remains stopped.

The offline fixture lifecycle evidence remains separate from real-provider evidence. No real Nova guest or Neutron tenant network has been verified by this record. Local repository commits do not imply deployment completion or authorize remote pushes.
