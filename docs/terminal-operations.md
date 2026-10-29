# Terminal operations for the existing Ubuntu OpenStack POC

This guide is for the existing **Ubuntu 22.04 WSL / OpenStack Caracal** POC.
OpenStack CLI **6.6.0**, the listed local service names, and CLI help for the
main VM, volume, object and load-balancer commands were checked on 2026-10-29.
No services or cloud resources were changed to prepare this guide. Resource
examples below are instructions for the operator; they were not executed.

## Open Ubuntu from Windows

Run in **Windows PowerShell**:

```powershell
wsl.exe --list --verbose
wsl.exe --distribution Ubuntu
```

In the Ubuntu terminal:

```bash
cd /home/sneha/projects/openstack-cloud-poc
openstack --version
```

If a new `wsl.exe` launch reports `WSAETIMEDOUT`, use the already responsive
Ubuntu terminal. Recent administrator/Windows interop launches from the agent
session timed out, while the existing Ubuntu and PowerShell terminals worked.
This operating procedure does not require shutting down or restarting WSL.

Linux `sudo` uses your Ubuntu login password. OpenStack uses its own `admin`
or tenant account and password. MariaDB's `openstack_viewer` is a separate
database account. These passwords are not interchangeable.

## Start the existing services

Most POC units are already enabled for startup. Check them first:

```bash
systemctl is-active apache2 mariadb rabbitmq-server nova-compute neutron-server cinder-volume
systemctl status poc-usage-collector.timer poc-operations.timer --no-pager
systemctl --failed --no-pager
```

If bringing existing services back up, run the following in order. `start`
leaves an already active service running. These commands do not reinstall the
cloud or create replacement storage. If a command fails, inspect its status
and journal before continuing.

```bash
sudo systemctl start mariadb rabbitmq-server memcached
sudo systemctl start kvm-device-permissions.service libvirtd
sudo systemctl start openvswitch-switch ovn-central ovn-host ovn-controller-vtep

sudo systemctl start cinder-poc-loop.service
sudo systemctl start 'srv-swift\x2dpoc-poc1.mount'
sudo systemctl start poc-ingress-bridge.service

sudo systemctl start apache2 glance-api
sudo systemctl start neutron-server neutron-ovn-metadata-agent
sudo systemctl start nova-api nova-conductor nova-scheduler nova-compute nova-novncproxy
sudo systemctl start tgt cinder-scheduler cinder-volume

sudo systemctl start swift-account swift-container swift-object
sudo systemctl start swift-container-updater swift-object-updater swift-proxy
sudo systemctl start octavia-api octavia-driver-agent

sudo systemctl start poc-provider-meter.service
sudo systemctl start poc-guest-a-ssh poc-guest-a-http poc-guest-b-ssh poc-guest-b-http poc-vip-http
sudo systemctl start poc-usage-collector.timer poc-operations.timer
```

Keystone, Placement, Horizon and the Cinder HTTP API are served through Apache
in this deployment. Swift uses only the six selected services above; do not
start wildcard Swift services, since background units are deliberately masked.
The storage prerequisites attach/mount existing images; leave their image
files and retained state in place.

Starting services and starting a stopped VM are separate actions. Use Nova's
`openstack server start` command below for a VM. Do not rerun deployment,
acceptance, cleanup or restore scripts as a startup procedure. A WSL shutdown,
OS upgrade, database restore, or storage reinitialization is not required.

The dashboard remains **https://localhost:8443/horizon/** in Windows.

## Authenticate the OpenStack CLI

For administrator inspection, use the same OpenStack `admin` password used
for Horizon. Run in **Ubuntu Bash**:

```bash
unset OS_CLOUD OS_AUTH_TOKEN OS_TOKEN OS_SYSTEM_SCOPE OS_PROJECT_ID OS_USER_ID
export OS_AUTH_TYPE=v3password
export OS_AUTH_URL=http://127.0.0.1:5000/v3
export OS_IDENTITY_API_VERSION=3
export OS_USER_DOMAIN_NAME=Default
export OS_PROJECT_DOMAIN_NAME=Default
export OS_USERNAME=admin
export OS_PROJECT_NAME=admin
export OS_REGION_NAME=RegionOne
export OS_INTERFACE=internal
export OS_VOLUME_API_VERSION=3

read -r -s -p 'OpenStack admin password: ' OS_PASSWORD
printf '\n'
export OS_PASSWORD

openstack catalog list
```

The password is entered without echo and stays in this shell's environment;
do not save it into a script or commit an environment/credentials file. If
you omit `OS_PASSWORD`, the installed CLI can prompt interactively instead.
Avoid debug logging or configuration dumps containing credentials.

To deliberately display the existing admin and two demo tenant passwords
locally, use the existing helper; keep its output local:

```bash
sudo /usr/bin/python3 -B scripts/local-openstack/show_browser_access.py --show-passwords
```

For routine tenant resource operations, switch to **tenant A**:

```bash
export OS_USERNAME=poc-browser-a
export OS_PROJECT_NAME=poc-browser-20261027-a
unset OS_PASSWORD
read -r -s -p 'Tenant A OpenStack password: ' OS_PASSWORD
printf '\n'
export OS_PASSWORD
openstack catalog list
```

Tenant B uses `poc-browser-b` with project `poc-browser-20261027-b` and that
user's own password. To switch back to administrator inspection, restore
`OS_USERNAME=admin`, `OS_PROJECT_NAME=admin` and enter the admin password again.
Clear `OS_PASSWORD` when finished:

```bash
unset OS_PASSWORD
```

## Inspect the cloud

These commands use the current authenticated project:

```bash
openstack server list --long
openstack image list
openstack flavor list
openstack network list
openstack subnet list
openstack port list
openstack router list
openstack floating ip list
openstack security group list
openstack keypair list
openstack volume list
openstack volume snapshot list
openstack container list
openstack loadbalancer list
openstack quota show
```

Use administrator authentication for administrative inventories:

```bash
openstack project list
openstack user list
openstack role list
openstack service list
openstack endpoint list
openstack compute service list
openstack network agent list
openstack volume service list
openstack hypervisor list
openstack server list --all-projects
openstack volume list --all-projects
```

## Operate a VM

Select the exact existing VM ID from `openstack server list`:

```bash
read -r -p 'VM ID: ' SERVER_ID
openstack server show "$SERVER_ID"
```

Run the desired action individually:

| Action | Command |
|---|---|
| Start a stopped VM | `openstack server start "$SERVER_ID"` |
| Stop a VM | `openstack server stop "$SERVER_ID"` |
| Soft reboot | `openstack server reboot --soft "$SERVER_ID"` |
| Inspect status | `openstack server show "$SERVER_ID"` |
| Show its ports | `openstack port list --server "$SERVER_ID"` |
| Console log | `openstack console log show --lines 50 "$SERVER_ID"` |
| Get a noVNC console URL | `openstack console url show --novnc "$SERVER_ID"` |

Repeat `server show` after a lifecycle request until the requested state is
settled. An accepted request is not proof that the VM has started. Keep console
URLs local; they contain temporary access tokens. The original retained VM
predates VNC enablement; the browser demo VMs have noVNC configured.

To create an additional small VM, first use **tenant authentication**, check
`openstack quota show`, and select that tenant's existing network, security
group and keypair. The installed catalog includes `POC Browser CirrOS` and
`poc.browser.tiny`. Demo quotas and available laptop memory limit new guests.
Set the following variables from the preceding list commands:

```bash
read -r -p 'Tenant network ID: ' NETWORK_ID
read -r -p 'Tenant security group ID: ' SECURITY_GROUP_ID
read -r -p 'Existing tenant keypair name: ' KEYPAIR_NAME
read -r -p 'New VM name: ' NEW_VM_NAME

openstack server create \
  --image 'POC Browser CirrOS' \
  --flavor poc.browser.tiny \
  --network "$NETWORK_ID" \
  --security-group "$SECURITY_GROUP_ID" \
  --key-name "$KEYPAIR_NAME" \
  --wait "$NEW_VM_NAME"
```

The command uses the image's local boot disk. It does not create a Cinder boot
volume. Existing Windows localhost forwards target the retained demo guests;
they are not automatically assigned to a newly created VM.

Delete only a disposable VM you intentionally selected; deletion is permanent:

```bash
openstack server delete --wait "$SERVER_ID"
```

## Volumes, snapshots, networks, objects and load balancers

Use tenant authentication. Run examples individually and select your own IDs:

| Operation | Command |
|---|---|
| Inspect a volume | `openstack volume show VOLUME_ID` |
| Create a 1 GiB volume | `openstack volume create --size 1 terminal-demo-volume` |
| Attach a volume | `openstack server add volume SERVER_ID VOLUME_ID` |
| Detach a volume | `openstack server remove volume SERVER_ID VOLUME_ID` |
| Snapshot an available, detached volume | `openstack volume snapshot create --volume VOLUME_ID terminal-demo-snapshot` |
| Inspect a network | `openstack network show NETWORK_ID` |
| Inspect a router | `openstack router show ROUTER_ID` |
| Inspect security rules | `openstack security group rule list SECURITY_GROUP_ID` |
| List objects in a container | `openstack object list CONTAINER_NAME` |
| Create a container | `openstack container create terminal-demo-container` |
| Upload an existing file | `openstack object create terminal-demo-container ./example.txt` |
| Inspect a load balancer | `openstack loadbalancer show LOAD_BALANCER_ID` |
| List listeners | `openstack loadbalancer listener list` |
| List pools | `openstack loadbalancer pool list` |
| List pool members | `openstack loadbalancer member list POOL_ID` |

Replace the uppercase ID/name placeholders with values from the list commands;
they are not literal resources. Detach a volume only after stopping writes and
unmounting it in the guest. Do not reinitialize an existing guest disk. The
local provider network is isolated to this computer and does not provide
general LAN or public connectivity. Account for current quotas before creates.

## Connect to the existing demo guests from Windows

Run in **Windows PowerShell** for tenant A:

```powershell
ssh.exe -p 2221 -i "$env:LOCALAPPDATA\OpenStackPocAccess-20261028\tenant-a-ed25519" -o "UserKnownHostsFile=$env:LOCALAPPDATA\OpenStackPocAccess-20261028\tenant-a-known-hosts" -o StrictHostKeyChecking=yes cirros@127.0.0.1
curl.exe http://127.0.0.1:8081/
```

For tenant B, use port `2222`, `tenant-b-ed25519`, `tenant-b-known-hosts` and
HTTP port `8082`. The demo load-balancer HTTP forward is `http://127.0.0.1:8083/`.

## Health, monitoring, metering and logs

Run in Ubuntu. `Ctrl+C` stops following a journal without stopping its service.
Use `exit` to leave an SSH shell.

```bash
systemctl --failed --no-pager
sudo systemctl status nova-compute neutron-server cinder-volume --no-pager
sudo journalctl -u nova-compute -n 100 --no-pager
sudo journalctl -u neutron-server -n 100 --no-pager
sudo journalctl -u cinder-volume -n 100 --no-pager
sudo journalctl -u apache2 -n 100 --no-pager
sudo journalctl -u nova-compute -f
```

Inspect periodic collection and its last result:

```bash
systemctl list-timers poc-usage-collector.timer poc-operations.timer --no-pager
sudo journalctl -u poc-usage-collector.service -n 50 --no-pager
sudo systemctl show poc-usage-collector.service -p Result -p ExecMainStatus
sudo /usr/bin/python3 -m json.tool /var/lib/openstack-poc-operations/status.json
```

To request an immediate usage collection or monitoring observation:

```bash
sudo systemctl start poc-usage-collector.service
sudo systemctl start poc-operations.service
```

These two commands update POC observation/history data; they do not provision
cloud resources. A completed oneshot service can correctly appear inactive
while its timer is active/waiting. The custom usage/billing ledger is separate
from Nova's `openstack usage list`; the latter is not the POC billing statement.

## Inspect databases

Open a read-only MariaDB viewer session in Ubuntu:

```bash
mariadb --no-defaults --protocol=TCP --host=127.0.0.1 --port=3306 --user=openstack_viewer --password
```

At the MariaDB prompt:

```sql
SHOW DATABASES;
USE nova;
SHOW TABLES;
SELECT id, display_name, vm_state FROM instances LIMIT 20;
EXIT;
```

The viewer password and connection setup are in the
[HeidiSQL runbook](mariadb-heidisql-access.md). Preserve the root socket login:

```bash
sudo mariadb --no-defaults --protocol=socket --user=root
```

Use the SQLite viewing snapshots rather than changing live database
permissions. The current export can also be inspected from Ubuntu:

```bash
SNAPSHOT_DIR=/mnt/c/Users/ACER/AppData/Local/OpenStackPocSnapshots-20261029T134627Z-00e112b2
sqlite3 -readonly "$SNAPSHOT_DIR/usage.sqlite" '.tables'
sqlite3 -readonly "$SNAPSHOT_DIR/billing.sqlite" '.tables'
sqlite3 -readonly "$SNAPSHOT_DIR/application-jobs.sqlite" '.tables'
```

## Where the data is stored

| Data | Existing location |
|---|---|
| Repository, command guide and scripts | `/home/sneha/projects/openstack-cloud-poc` |
| Deployed application code | `/opt/openstack-poc/releases/` |
| Live SQLite usage history | `/var/lib/openstack-poc-billing/usage.sqlite` |
| Live SQLite billing ledger | `/var/lib/openstack-poc-billing/billing.sqlite` |
| Live SQLite application job journal | `/root/openstack-poc-acceptance/application-jobs.sqlite` |
| MariaDB service database storage | `/var/lib/mysql/`; confirm with `SELECT @@datadir;` through root socket login |
| Nova VM disks/state | `/var/lib/nova/instances/` |
| Glance image storage | `/var/lib/glance/images/` |
| Existing Cinder LVM backing image | `/var/lib/cinder-poc/cinder-backend.img` |
| Existing Swift backing image | `/var/lib/swift-poc/swift-backend.img`, mounted at `/srv/swift-poc/poc1` |
| Monitoring state | `/var/lib/openstack-poc-operations/` |
| Provider metering evidence | `/var/lib/openstack-poc-meter/evidence.sqlite` |
| Swift metering journal | `/var/lib/openstack-poc-swift-meter/events.sqlite` |
| OpenStack service configuration | `/etc/nova`, `/etc/neutron`, `/etc/glance`, `/etc/cinder`, `/etc/swift`, `/etc/octavia`, `/etc/keystone` |
| Protected OpenStack administrator/service secret source | `/root/openstack-poc-keystone-secrets` |
| Protected browser demo credentials/state | `/root/openstack-poc-acceptance/browser-poc-20261027/` |
| Protected MariaDB viewer credential | `/root/openstack-poc-acceptance/mariadb-viewer/credentials.json` |

The exact **Windows viewing snapshot files** are:

```text
C:\Users\ACER\AppData\Local\OpenStackPocSnapshots-20261029T134627Z-00e112b2\usage.sqlite
C:\Users\ACER\AppData\Local\OpenStackPocSnapshots-20261029T134627Z-00e112b2\billing.sqlite
C:\Users\ACER\AppData\Local\OpenStackPocSnapshots-20261029T134627Z-00e112b2\application-jobs.sqlite
```

They are standalone read-only snapshots, with access limited to your Windows
account and SYSTEM. They do not automatically refresh as the POC changes.
See the [SQLite export runbook](sqlite-viewer-snapshots.md) for the online
backup API procedure and verified snapshot evidence. The live Linux paths
above are inside the existing Ubuntu WSL distribution; the repository can
also be browsed from Windows at
`\\wsl.localhost\Ubuntu\home\sneha\projects\openstack-cloud-poc`.
Actual database files, VM disks and credentials are not in Git.

## Find every available command

```bash
openstack --help
openstack help server create
openstack help volume create
openstack help loadbalancer create
```

For commands that require a subcommand, use its complete name, such as
`openstack help server create`. Reference documentation matching the installed
client/release: [authentication](https://docs.openstack.org/python-openstackclient/2024.1/cli/authentication.html),
[servers](https://docs.openstack.org/python-openstackclient/2024.1/cli/command-objects/server.html),
and [volumes](https://docs.openstack.org/python-openstackclient/2024.1/cli/command-objects/volume.html).
