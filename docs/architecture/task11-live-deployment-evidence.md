# Task 11 — Local deployment evidence

Recorded 2026-10-24 for the approved Ubuntu 22.04 WSL2 laptop POC. Host evidence below was supplied by the operator from the actual Ubuntu terminal. Codex's restricted execution environment cannot independently validate those services. This record does not establish staging or production readiness.

## Preservation and package gate

The verified pre-Caracal backup is `/home/sneha/openstack-poc-pre-caracal-20261024-150119`. Its archive and snapshot checks are recorded in [the backup procedure](task11-ovs-ovn-backup-procedure.md). No restoration has been exercised. Existing operational, billing, metering, VM recovery, and network data must remain preserved.

The operator installed the reviewed Caracal package set. The subsequent `dpkg --audit` returned no output. OVS reports 3.3.9, OVN packages report 24.03.6, and all three existing OVSDB sockets respond. The protected Placement and OVN logrotate configuration hashes matched the pre-install backup after installation. Package presence and running daemons alone do not establish functional service integration.

## Verified local API and image milestones

| Capability | Operator-supplied result | Limit |
|---|---|---|
| Keystone | Configured MariaDB, initialized schema and Fernet/credential keys, bootstrapped identity; `GET http://127.0.0.1:5000/v3` returned 200; later admin token requests succeeded | Tenant negative authorization and application identity integration remain untested |
| Placement | Replaced obsolete `dbcounter` database URL plugin; created database/schema, service identity and endpoints; API returned 200; authenticated resource-provider list succeeded | Nova has not registered a compute provider |
| Glance | Created database/schema, service identity and endpoints; API discovery returned 300 and authenticated image listing succeeded | No Nova guest has used the image yet |
| Glance image upload | Private `cirros-0.6.3-x86_64-poc`, ID `da953e4e-38ca-47d2-b97a-b62d3f33646a`, qcow2, 21,692,416 bytes, reached `active` | Upload is real; VM execution remains unverified |
| Image source integrity | CirrOS 0.6.3 x86_64 disk image SHA-256 matched `7d6355852aeb6dbcd191bcda7cd74f1536cfe5cbf8a10495a7283a8396e4b75b` | Source download retained at `/tmp/openstack-poc-cirros.FcgL2d/cirros-0.6.3-x86_64-disk.img`; temporary storage is not a recovery copy |
| OVN database listeners | NB `ptcp:6641:127.0.0.1`, SB `ptcp:6642:127.0.0.1`; socket listener output confirmed loopback bindings | This change alone does not restrict other API or RabbitMQ listeners or prove tenant networking |

Keystone and Placement are served by Apache `mod_wsgi`, not standalone systemd units. Initial HTTP 500 errors came from Keystone's missing Fernet keys and Placement's unsupported SQLAlchemy plugin. Glance's first immediate post-restart probe refused connections; later listener and authenticated API checks passed.

## Current capacity and network inventory

The operator reported 12 logical CPUs, 7.7 GiB WSL RAM with 2.7 GiB available, 2 GiB swap with approximately 178 MiB used, and 934 GiB free on the Linux filesystem. These are point-in-time observations, not a capacity guarantee.

The WSL `eth0` address is `172.19.169.140/20`, default gateway `172.19.160.1`. The preserved libvirt network uses `192.168.122.1/24` on `virbr0`. OVS has `br-int`; OVN has one chassis named `DESKTOP-Sneha` with Geneve endpoint `172.19.169.140`. Northbound topology was empty. Do not assume the WSL address survives restart.

## Nova setup boundary

The attempted large terminal setup block produced no output. A subsequent operator check found all five planned Nova/Neutron/RabbitMQ secret keys absent, no `/root/nova.conf.before-poc`, no Nova MariaDB databases, and only the default RabbitMQ `guest` account. Nova daemons were active, but `nova-manage cell_v2 list_cells` failed against SQLite with `no such table: cell_mappings`. Nova initialization therefore has not been demonstrated.

The resumable [Nova prerequisite script](../../scripts/local-openstack/nova-prerequisites.sh) prepares only the three Nova databases, a database account, a RabbitMQ account and permissions, and retained task credentials. It checks for conflicting pre-existing assets, retains generated passwords on retries, and validates database and RabbitMQ authentication. It does not initialize Nova cells, change Nova configuration, create Keystone service identities, restart services, or create guests. Host execution is pending. Shell syntax validation and review do not establish host execution success.

## Remaining acceptance work

Configure and validate Nova and Neutron, register the KVM compute host and Placement inventory, launch a real guest, validate lifecycle and tenant networking/isolation, exercise authorized cleanup, integrate the POC application with real scoped APIs, and run the required acceptance tests. Review all local control-plane listener exposure before completion. KVM permissions across WSL restart, recovery restoration, and staging acceptance remain unverified. Keep `logrotate.timer` inactive until the existing runtime validation/review requirement is satisfied; VTEP remains stopped.

The offline fixture lifecycle evidence remains separate from real-provider evidence. No real Nova guest or Neutron tenant network has been verified by this record. Local repository commits do not imply deployment completion or authorize remote pushes.
