# Task 11 — Host recovery evidence and package installation gate

**Assessment date:** 2026-10-24
**Status:** Host recovery evidence recorded; OpenStack package installation is not approved or performed.

## Recovery evidence

The following results were supplied by the operator after the approved recovery actions:

| Area | Verified result | Remaining limitation |
|---|---|---|
| OVN logrotate hook | `ovn-controller`, `ovn-northd`, Northbound DB, and Southbound DB returned `OK`; bounded hook exited 0. | `logrotate.timer` remains inactive. This verifies the hook at that time, not timer reactivation or future recurrence. |
| KVM device access | `kvm-device-permissions.service` is enabled and `active (exited)` with successful `ExecStart`; `/dev/kvm` and `/dev/vhost-net` are `root:kvm` (GID 109), mode `0660`; normal-user read/write checks passed. | Persistence after WSL restart is unverified. |

Independent read-only inspection of systemd properties also returned `LoadState=loaded`, `ActiveState=active`, `SubState=exited`, `Result=success`, `ExecMainStatus=0`, and `UnitFileState=enabled` for `kvm-device-permissions.service`. No successful real Nova or Neutron operation is established by these recovery checks.

## Current local inventory evidence

| Item | Observation | Boundary |
|---|---|---|
| Linux filesystem | `/` is 1007 GiB total, 21 GiB used, 936 GiB available. | Capacity is ample at the filesystem level; this does not establish peak temporary space required by the transaction. |
| Windows C: | `Get-Volume` reported 1,023,098,957,824 bytes total and 811,521,736,704 bytes remaining (about 756 GiB). WSL `/mnt/c` showed 953 GiB total and 756 GiB available. | Host figures are from the current check; no Windows setting was changed. |
| libvirt networks | `default` is persistent, active, and autostarted; its definition uses NAT on `virbr0` and `192.168.122.0/24` with DHCP range `.2`–`.254`. | Do not replace or reconfigure it without approval. |
| libvirt storage pools | `virsh pool-list --all` listed none; querying volume list for pool `default` returned “Storage pool not found.” | No VM or disk inventory follows from the absence of a pool. |
| VM/image files | `/var/lib/libvirt/images` exists, but its contents were not readable by the unprivileged inspection. No candidate disks/images were found under `/home/sneha`. | System-wide disk/image inventory is incomplete; root-readable paths and `/home/stack` siblings remain to be inventoried. The known nonexistent `/home/stack/devstack` checkout was not inspected again. |
| OVS/OVN database files | `/var/lib/openvswitch/conf.db` (18,239 bytes), `/var/lib/ovn/ovnnb_db.db` (16,397 bytes), and `/var/lib/ovn/ovnsb_db.db` (21,121 bytes) were observed, along with lock files. `/etc/openvswitch/system-id.conf` exists; `/etc/ovn` exists and had no files in the unprivileged filename listing. | File presence and size are not a verified database backup. The operator subsequently verified the active sockets and primary DB names listed below; topology output was not supplied. |
| Other service configuration | `/etc/logrotate.d/ovn-common` (645 bytes) and `/etc/logrotate.d/openvswitch-switch` (413 bytes) exist. `/etc/nova`, `/etc/neutron`, `/etc/glance`, and their checked `/var/lib` counterparts were absent. | No OpenStack service configuration was found at those paths; alternate/containerized locations were not ruled out. Do not expose configuration contents in diagnostic output. |
| Existing backups | `/var/backups/ovn-common.before-codex-20261024` exists (633 bytes); apt/dpkg backup entries also exist. | This is not evidence of a complete OVS/OVN, VM, or WSL backup. Do not overwrite it. |

The operator verified these live endpoints with `ovsdb-client list-dbs`:

| Socket | Primary database | Also listed |
|---|---|---|
| `unix:/run/openvswitch/db.sock` | `Open_vSwitch` | `_Server` |
| `unix:/run/ovn/ovnnb_db.sock` | `OVN_Northbound` | `_Server` |
| `unix:/run/ovn/ovnsb_db.sock` | `OVN_Southbound` | `_Server` |

All three endpoints responded. `_Server` is the built-in OVSDB server status/configuration database; it is not the OVS, OVN Northbound, or OVN Southbound network-state database. `ovs-vsctl show`, `ovn-nbctl show`, and `ovn-sbctl show` output has not been supplied, so bridge, logical-network, and chassis topology remain unknown. The VTEP unit and logrotate timer are intentionally left stopped/inactive per the recovery instructions.

## Corrected package-impact finding

The initial package report is [`task11-caracal-package-impact.md`](task11-caracal-package-impact.md). A repeat of its signed, temporary-index `apt-get -s` resolution explicitly included `ubuntu-cloud-keyring`, because the actual host does not have that package installed. The revised solver result is **11 upgraded, 229 newly installed, 0 removed, and 22 not upgraded**. The one-package increase from the earlier 228-new result is `ubuntu-cloud-keyring`.

The package transaction still upgrades installed OVS 2.17 to 3.3.9 and OVN 22.03 to 24.03.6, while adding the Caracal Keystone, Glance, Placement, Nova and Neutron packages, MariaDB, and RabbitMQ. It does not upgrade the installed libvirt or QEMU packages. The resolver output is in the temporary directory used for this assessment and is not a durable repository artifact. APT simulation does not establish service restart behavior, database migration safety, disk demand, or successful deployment.

The actual system's `apt` source configuration must not be changed until a backup and reviewed install plan are approved. If using the Ubuntu Cloud Archive, the trust bootstrap must be explicit: install the Jammy `ubuntu-cloud-keyring` package before enabling the Caracal source, then update indexes and install the reviewed service set. The corrected simulation includes the keyring in the aggregate new-package count. No packages or sources were changed for this assessment.

## Backup and recovery gate

Do not begin package installation until the following preflight evidence is collected and reviewed:

1. A root-readable, filename-and-size-only inventory of VM disks, images, database files, backups, and configuration under libvirt/OpenStack data paths and `/home/stack` siblings. Do not traverse the known absent `devstack` checkout. Preserve every discovered artifact and record its exact path, size, owner, and timestamp without printing configuration values.
2. OVSDB socket paths and primary database names are verified in the table above. Before backup, collect `ovs-vsctl show`, `ovn-nbctl show`, and `ovn-sbctl show` output and verify each endpoint still lists its expected primary DB. The active database service model remains unknown.
3. Exact installed versions for the complete relevant package set and a copy of current apt source configuration. Keep the existing OVN logrotate backup and edited conffile separately. Record current systemd enabled/active state for OVS, OVN, libvirt, the KVM permissions unit, logrotate timer, and VTEP.
4. An approved backup destination with enough free space. Back up the VM/image artifacts as immutable copies, libvirt network XML, service configuration, and OVS/OVN databases using the supported online OVSDB backup mechanism for the verified sockets/database names. Do not treat copying live OVSDB files as a consistent database backup. Use a new destination that does not overwrite existing backups; restrict permissions because service configuration may contain secrets.
5. Verify each backup by checking archive/file integrity, expected file membership, sizes, and hashes; verify OVSDB snapshot readability with the matching OVSDB tooling without restoring it. Record the recovery commands and exact package versions. Do not perform the restore as part of verification.

After a package failure, the safe recovery path is not `apt autoremove` or blind removal. Stop only newly introduced services with separate approval; restore the verified pre-change data/configuration snapshots and use a reviewed explicit package-version rollback. OVS/OVN downgrade safety is not established by the simulation. If an in-place downgrade cannot be proven safe, recovery requires a separately approved full WSL snapshot/export strategy or other verified recovery point. No distribution export or backup has been made by this assessment.

## Proposed installation commands — not executed

These commands are a proposal for review only. The package transaction must not proceed until the backup gate above is closed and the operator explicitly approves installation. The initial keyring package step is separate so the host can authenticate the Ubuntu Cloud Archive before adding it:

```bash
sudo apt-get install ubuntu-cloud-keyring
sudo add-apt-repository cloud-archive:caracal
sudo apt-get update
sudo apt-get install --no-install-recommends \
  keystone glance placement-api \
  nova-api nova-conductor nova-scheduler nova-compute \
  neutron-server neutron-plugin-ml2 neutron-ovn-metadata-agent \
  python3-openstackclient mariadb-server rabbitmq-server \
  apache2 libapache2-mod-wsgi-py3 \
  openvswitch-switch ovn-central ovn-host
```

Expected scope from the aggregate simulation: 11 package upgrades (including the active OVS/OVN major-version changes), 229 new packages (including `ubuntu-cloud-keyring`), zero planned removals, and 22 held-back packages. Apache and mod_wsgi are already installed and simulated as already current. Do not add `apt autoremove`. Package installation may restart or reconfigure OVS/OVN and other services; the exact runtime effects are not known from simulation. Keep `logrotate.timer` inactive and do not start VTEP unless a separately reviewed operational decision approves it.

## Outstanding approval and evidence

- The root-readable asset/configuration inventory and OVS/OVN runtime topology are incomplete. The privileged inventory command attempted from Codex failed because sudo required an interactive password; no privileged host changes or inventory reads were made by that attempt.
- No complete verified backup exists in the evidence collected here.
- KVM permission persistence after WSL restart is not yet verified.
- Installation of Ubuntu Cloud Archive keyring, enabling the Caracal source, package installation/upgrades, and service configuration each require explicit approval before execution.
- No OpenStack deployment or real Nova/Neutron validation has been performed.
