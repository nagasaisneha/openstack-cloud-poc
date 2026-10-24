# Task 11 — OVS/OVN endpoint inventory and pre-upgrade backup proposal

**Assessment date:** 2026-10-24
**Status:** Historical inventory/pre-upgrade backup record; these backups, subsequent package deployment, real acceptance and temporary dependency cleanup are verified in [live evidence](task11-live-deployment-evidence.md). [Operational closeout](task11-operations-and-closeout.md) remains pending. Preserve these original recovery procedures; do not replay the historical install proposal against the deployed host.

## Verified database endpoints

The operator reports that `ovsdb-client list-dbs` succeeded against all three sockets:

| Endpoint | Primary service database | Built-in database also listed |
|---|---|---|
| `unix:/run/openvswitch/db.sock` | `Open_vSwitch` | `_Server` |
| `unix:/run/ovn/ovnnb_db.sock` | `OVN_Northbound` | `_Server` |
| `unix:/run/ovn/ovnsb_db.sock` | `OVN_Southbound` | `_Server` |

The sockets responded at the time of that check. Operator-provided evidence also identifies OVS 2.17.12, bridge `br-int`, OVN chassis `DESKTOP-Sneha`, and Geneve endpoint `172.19.169.140`. Full bridge ports and OVN logical switch/router/port topology were not included. The earlier file inventory observed `/var/lib/openvswitch/conf.db`, `/var/lib/ovn/ovnnb_db.db`, and `/var/lib/ovn/ovnsb_db.db`. Those live files must not be copied as a substitute for an online snapshot.

`_Server` is OVSDB's built-in, read-only server status/configuration database. The proposed backup below targets the three service databases, not `_Server`; server unit/configuration files are preserved separately. (See the [OVSDB server schema](https://www.openvswitch.org/support/dist-docs/ovsdb-server.5.pdf).)

Open vSwitch documents `ovsdb-client backup SERVER DATABASE` as an atomic snapshot mechanism that works across OVSDB service models. `ovsdb-tool show-log` can verify that each snapshot is readable. Snapshots omit ephemeral columns by design. `ovsdb-client restore` is atomic when used against a running service, but changes row UUIDs and requires a compatible schema. See [OVSDB backup and recovery](https://docs.openvswitch.org/en/latest/ref/ovsdb.7/) and the [ovsdb-client manual](https://www.openvswitch.org/support/dist-docs/ovsdb-client.1.html).

## Inventory gaps

The operator supplied protected path and service inventory output. Under `/home/stack`, it found `billing.csv` (7,938,792 bytes), numerous dated billing CSV snapshots, compute/floating-IP/volume state CSVs, metering CSVs, `meter.log`, metering Python scripts, and `run_meter.sh`. The complete paths and sizes are in the operator's terminal output; file contents were not read. The backup proposal archives all of `/home/stack`, `/var/backups`, and `/var/lib/libvirt/images`, so it preserves these artifacts without selecting individual billing files. The known `/var/backups/ovn-common.before-codex-20261024` must remain untouched and is included through the `/var/backups` archive.

The filtered file inventory showed no candidate VM disk/image filename in the searched paths. This does not prove there are no disks; the proposed archive includes `/var/lib/libvirt/images` and checks for running libvirt guests before copying. It also found `/etc/placement/placement.conf`, `/etc/libvirt` configuration and network XML, `/etc/openvswitch/system-id.conf`, `/etc/logrotate.d/ovn-common`, and `/etc/systemd/system/kvm-device-permissions.service`. Values were not displayed. These paths must be preserved.

The sanitized process listing showed `ovn-controller` and `ovn-northd`, with no billing/metering process-name match. The service listing showed `libvirtd`, OVS, OVN Northbound/Southbound/controller services active and `ovn-controller-vtep` inactive; no billing/metering service-name match was shown. The first cron filter was malformed; the operator then reran a corrected read-only cron filter and reported no output. Therefore no matching billing/metering/Nova/Neutron/OVN/OpenStack entries were found in `/etc/crontab`, `/etc/cron.d`, or `/var/spool/cron/crontabs` at that time. No host files or services were changed by the inventory.

The current host inventory is therefore incomplete for:

- Any VM disk not matched by the filename-filtered scan, current running-guest state, and Nova database/recovery files in unsearched custom directories.
- Nova/recovery data in unsearched custom directories and any VM disk not matched by the filename-filtered scan. The archive includes the searched operational roots and the current running-guest check guards image archiving.
- Full OVS/OVN port and logical topology, package state, and any custom paths outside those searched.

The corrected cron result and sanitized process/service inventory found no billing/metering writer by the searched names. The approved backup command still checks that no libvirt guest is running before archiving image paths. No billing/metering file contents have been inspected. Do not infer consistency from a tar archive of a live mutable database.

## Read-only inventory commands for the operator

These commands show paths, sizes, ownership, timestamps, process executable/script basenames, and redacted scheduler matches. They do not print configuration contents or process arguments. They are for manual execution in the Ubuntu terminal; no sudo password was provided to Codex.

```bash
sudo find /home/stack /var/lib/libvirt /var/lib/openvswitch /var/lib/ovn \
  /var/lib/nova /var/lib/glance /var/lib/neutron /var/backups /opt/stack \
  -xdev -path /home/stack/devstack -prune -o -type f \
  \( -iname '*.qcow2' -o -iname '*.qcow' -o -iname '*.raw' -o -iname '*.img' \
     -o -iname '*.vmdk' -o -iname '*.vdi' -o -iname '*.vhd' -o -iname '*.vhdx' \
     -o -iname '*.ova' -o -iname '*.sql*' -o -iname '*.db*' \
     -o -iname '*backup*' -o -iname '*billing*' -o -iname '*meter*' \) \
  -printf '%p\t%s bytes\t%u:%g\t%TY-%Tm-%Td %TH:%TM:%TS\n' 2>/dev/null

sudo python3 - <<'PY'
import glob
import os
import re

terms = re.compile(r"billing|meter|nova|neutron|ovn|openvswitch", re.I)
print("PID UID executable script-basename")
for proc in glob.glob("/proc/[0-9]*"):
    try:
        argv = [part.decode(errors="replace") for part in open(proc + "/cmdline", "rb").read().split(b"\0") if part]
        if not argv:
            continue
        executable = os.path.basename(argv[0])
        script = os.path.basename(argv[1]) if len(argv) > 1 and argv[1].endswith((".py", ".sh")) else "-"
        if terms.search(executable + " " + script):
            print(os.path.basename(proc), os.stat(proc).st_uid, executable, script)
    except (OSError, ValueError):
        pass
PY

sudo systemctl list-units --type=service --all --no-legend | \
  awk 'tolower($0) ~ /(billing|meter|nova|neutron|ovn|openvswitch|libvirt)/ {print $1, $3, $4}'
sudo systemctl list-timers --all --no-legend | \
  awk 'tolower($0) ~ /(billing|meter|nova|neutron|ovn|openvswitch)/ {print $1, $2, $3, $4}'

sudo find /etc/openvswitch /etc/ovn /etc/libvirt /etc/nova /etc/neutron \
  /etc/glance /etc/keystone /etc/placement /etc/logrotate.d \
  /etc/systemd/system -maxdepth 4 -type f \
  -printf '%p\t%s bytes\t%u:%g\t%TY-%Tm-%Td %TH:%TM:%TS\n' 2>/dev/null

sudo bash -c 'for f in /etc/crontab /etc/cron.d/* /var/spool/cron/crontabs/*; do
  [ -f "$f" ] || continue
  awk '\''tolower($0) ~ /(billing|meter|nova|neutron|ovn|openstack)/ && $0 !~ /^[[:space:]]*#/ {print FILENAME ":" FNR ": [matching row redacted]"}'\'' "$f"
done'

sudo ovs-vsctl show
sudo ovn-nbctl show
sudo ovn-sbctl show
sudo virsh list --all --details
```

If the inventory finds recovery or operational data outside `/home/stack`, `/var/backups`, and `/var/lib/libvirt/images`, add those exact paths to the archive plan before approval. Do not inspect `/home/stack/devstack`; it was previously confirmed absent.

## Backup execution evidence

The operator ran the approved backup block on 2026-10-24 and reported:

- Backup directory: `/home/sneha/openstack-poc-pre-caracal-20261024-150119`.
- All three expected database names were confirmed from their live sockets and snapshotted with `ovsdb-client backup`.
- The command reached and passed database readability (`ovsdb-tool show-log` and `db-name`) and archive membership checks.
- `sha256sum -c` returned `OK` for every listed payload, including the operational-data and service-configuration archives, all three OVSDB snapshots, topology files, libvirt network XML, package versions, unit files, and service state.
- The command reported `Backup created and checks passed`.

The reported digest results provide integrity evidence for the files at creation time. No restore was performed, so recoverability has not been proven by a restore exercise. The backup is root-owned and contains sensitive operational configuration; do not paste or commit its contents.

## Authorized backup commands — execution record

The command block below is the procedure that produced the verified backup above. It writes a new, root-owned mode-0700 directory under `/home/sneha`; `mkdir` intentionally fails if the timestamped path already exists. It snapshots the three live OVSDB service databases using the verified endpoints. It does **not** copy their active database files. It is retained for traceability and must not be rerun over the verified backup.

The inventory found no matching billing/metering process, service, timer, or cron writer. The archive contains private operational data and configuration; keep it root-only and do not upload or paste its contents. The command rechecks endpoints, running libvirt guests, and verifies the generated artifacts. It adds `/etc/placement` and archives the existing apt package cache for rollback support.

```bash
sudo bash <<'ROOT'
set -euo pipefail
umask 077

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/home/sneha/openstack-poc-pre-caracal-${STAMP}"

# Refuse to begin if any libvirt guest is running; do not print guest names.
RUNNING_GUESTS="$(virsh list --state-running --name | sed '/^[[:space:]]*$/d')"
if [ -n "$RUNNING_GUESTS" ]; then
  printf 'STOP: running guest(s) require an approved consistent disk backup method.\n' >&2
  exit 20
fi

mkdir -m 0700 "$BACKUP_DIR"

# Reconfirm the exact primary databases before creating snapshots.
ovsdb-client list-dbs unix:/run/openvswitch/db.sock | grep -Fx Open_vSwitch
ovsdb-client list-dbs unix:/run/ovn/ovnnb_db.sock | grep -Fx OVN_Northbound
ovsdb-client list-dbs unix:/run/ovn/ovnsb_db.sock | grep -Fx OVN_Southbound

# Atomic snapshots from the live database services.
ovsdb-client backup unix:/run/openvswitch/db.sock Open_vSwitch > "$BACKUP_DIR/open_vswitch.ovsdb"
ovsdb-client backup unix:/run/ovn/ovnnb_db.sock OVN_Northbound > "$BACKUP_DIR/ovn_northbound.ovsdb"
ovsdb-client backup unix:/run/ovn/ovnsb_db.sock OVN_Southbound > "$BACKUP_DIR/ovn_southbound.ovsdb"

# Preserve VM/image and operational data after the running-guest preflight.
tar --sparse --acls --xattrs --numeric-owner -cpf "$BACKUP_DIR/host-operational-data.tar" \
  --exclude=home/stack/devstack -C / home/stack var/lib/libvirt/images var/backups var/cache/apt/archives

# Preserve service configuration, logrotate edits, custom KVM unit, cron jobs,
# apt source definitions, package versions, and libvirt network definition.
CONFIG_PATHS=()
for p in etc/openvswitch etc/ovn etc/libvirt \
         etc/logrotate.d/ovn-common etc/logrotate.d/openvswitch-switch \
         etc/systemd/system/kvm-device-permissions.service \
         etc/systemd/system/kvm-device-permissions.service.d etc/placement \
         etc/cron.d etc/crontab var/spool/cron/crontabs \
         etc/apt/sources.list etc/apt/sources.list.d \
         etc/apt/preferences.d etc/apt/keyrings; do
  [ -e "/$p" ] && CONFIG_PATHS+=("$p")
done
tar --acls --xattrs --numeric-owner -cpf "$BACKUP_DIR/service-config.tar" -C / "${CONFIG_PATHS[@]}"

dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' \
  > "$BACKUP_DIR/package-versions.tsv"
systemctl list-unit-files --no-legend > "$BACKUP_DIR/unit-files.txt"
systemctl list-units --type=service --all --no-legend > "$BACKUP_DIR/service-state.txt"
virsh net-dumpxml default > "$BACKUP_DIR/libvirt-default-network.xml"
ovs-vsctl show > "$BACKUP_DIR/ovs-topology.txt"
ovn-nbctl show > "$BACKUP_DIR/ovn-northbound-topology.txt"
ovn-sbctl show > "$BACKUP_DIR/ovn-southbound-topology.txt"

# Validate each database snapshot and both archives without restoring anything.
for db in "$BACKUP_DIR"/*.ovsdb; do
  test -s "$db"
  ovsdb-tool show-log "$db" >/dev/null
  ovsdb-tool db-name "$db" >/dev/null
done
tar -tf "$BACKUP_DIR/host-operational-data.tar" >/dev/null
tar -tf "$BACKUP_DIR/service-config.tar" >/dev/null
test -s "$BACKUP_DIR/libvirt-default-network.xml"

# Record file integrity after all payloads are complete.
find "$BACKUP_DIR" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$BACKUP_DIR/SHA256SUMS"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
printf 'Backup created and checks passed: %s\n' "$BACKUP_DIR"
ROOT
```

The `CONFIG_PATHS` list must be extended with exact custom configuration paths discovered by inventory before approval. If the operational-data tar contains an active billing/metering database or other live writer, omit that path from the bulk tar and add its approved application-consistent backup procedure instead. The command does not stop services or change the network; the only writes are the new protected backup directory and its contents. A failure leaves a partial directory; do not label it verified or overwrite it. Preserve it for diagnosis and use a new timestamped directory for a later approved attempt.

## Recovery procedure and limits

1. Preserve this backup directory unchanged. Verify its `SHA256SUMS` again before recovery; inspect archive membership and database names without extracting into live paths.
2. A recovery rehearsal must use an isolated copy/restore target. Never restore over the running system as an integrity test.
3. For an in-place recovery after an OVS/OVN package upgrade, first obtain separate approval to quiesce the affected services. Use exact pre-upgrade package versions and compatible package artifacts, then restore the pre-upgrade DB snapshots using a method appropriate to the actual OVSDB service model and schema. The service model and Caracal-to-Jammy downgrade compatibility are not yet verified; this procedure is not yet a safe executable rollback sequence.
4. Restore service configuration, libvirt network XML, and guest/operational data only after checking ownership, paths, and service compatibility. Revalidate OVS/OVN DBs, bridges, OVN topology, libvirt network, VM disks, and billing/metering data before resuming services.
5. Because the package transaction upgrades OVS 2.17 to 3.3.9 and OVN 22.03 to 24.03.6, it can restart active networking services and trigger database/config migration or conffile decisions. The apt simulation does not establish those runtime effects or guarantee package downgrade availability. Do not run `apt autoremove`.

## Deployment gate

- The pre-install backup has been created and its reported integrity checks passed.
- The master execution instruction authorizes the Caracal deployment after this backup gate; no additional architecture approval is pending for the package installation.
- The apt package simulation previously reported 11 upgrades, 229 new packages, zero removals, and 22 held packages. It includes OVS 2.17.12→3.3.9 and OVN 22.03→24.03.6; the actual package plan must be simulated again after the host UCA keyring/source bootstrap.
- No package installation has started. The local terminal operator must perform commands requiring sudo authentication and return their output so the next step can be verified.
- No repository tests or cloud calls were made for this documentation task.
