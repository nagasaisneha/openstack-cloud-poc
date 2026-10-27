# Existing Ubuntu browser POC

Open **https://localhost:8443/horizon/** in Windows. The existing constrained
localhost CA is trusted in this Windows user's certificate store; a normal
certificate-validated Windows HTTPS request reached the login page. Domain:
**Default**. Accounts: **admin**, **poc-browser-a**, **poc-browser-b**.

To deliberately display only these login passwords locally, run in PowerShell
from this checkout's existing Ubuntu path:

```powershell
wsl.exe --distribution Ubuntu --user root --exec /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/show_browser_access.py --show-passwords
```

Keep that output local. Without `--show-passwords`, the command prints names
and the URL only. It never prints collector credentials or private keys.
Existing passwords have not been reset. The protected credential source is
under `/root/openstack-poc-acceptance/browser-poc-20261027/`.

## Browser journeys

- **Administrator:** Identity → Projects / Users for project and user
  management; project action → Modify Quotas. Admin → Other → Usage & Billing
  for immutable demo rate versions, project statements, itemized charges,
  CSV/JSON downloads and billing-action audit.
- **Tenant:** Project → Compute → Instances for launch/status and Console;
  Compute → Key Pairs for importing a public key; Network → Networks for a
  private network/subnet; Volumes → Volumes for a volume; Object Store →
  Containers for private containers and file upload/download. Project → Other
  → Usage & Billing shows the selected project's observations and statements.
- Launch with **POC Browser CirrOS**, **poc.browser.tiny** (1 vCPU, 256 MiB RAM,
  1 GiB local disk), the selected tenant's own network and imported key.
  Choose **Create New Volume: No** for the verified local-disk boot path.
  Open the instance's Console tab for interactive access over HTTPS/WSS.

The two sample projects each retain one ACTIVE tiny VM, one private network
and DHCP subnet, one imported public key, one available 1 GiB Cinder volume,
one private Swift container and a 40-byte sample text object. The original
retained VM, image, storage and keys remain separate and preserved.
Limits per sample project are 2 VMs/2 cores/768 MiB RAM and 2 volumes/2 GiB.
T49 adds per-tenant routers/floating IPs on an isolated logical provider network.
Browser consoles and native Windows SSH/application paths are verified. Tenant A
SSH is `127.0.0.1:2221` and app `http://127.0.0.1:8081/`; tenant B uses SSH port
2222 and app port 8082. No physical NIC, Windows route or public exposure was
added. The forwarded connections traverse Neutron's floating-IP/router path.

The native Windows verification places only demo tenant keys and pinned
known-hosts in `%LOCALAPPDATA%\OpenStackPocAccess-20261028`, with access limited
to the current user and SYSTEM. For example, in PowerShell:

```powershell
ssh.exe -p 2221 -i "$env:LOCALAPPDATA\OpenStackPocAccess-20261028\tenant-a-ed25519" -o "UserKnownHostsFile=$env:LOCALAPPDATA\OpenStackPocAccess-20261028\tenant-a-known-hosts" -o StrictHostKeyChecking=yes cirros@127.0.0.1
```

Use B's key/known-hosts and port 2222 for tenant B. These are local operator
access paths, not a staging bastion. The app directory is separately marked
inside each guest; keep guest/application lifecycle receipts in [T49](tasks/T49.md).

## Usage and demonstration charges

The separate scoped reader collects enrolled projects once per minute.
Refresh provider usage collects immediately. History stores immutable
observations and selects the latest cumulative snapshot; it never adds
cumulative snapshots together. The project panel shows instance count,
volume count/provisioned GiB, object bytes/count and Nova allocation hours.
Unknown observations display **Unavailable**.

`poc-demo-20261027` uses explicit demonstration rates in INR: 1 per vCPU
allocation hour, 0.001 per RAM MiB-hour and 0.1 per local-disk GiB-hour.
These are demonstration inputs, not commercial prices. Issued statements
retain their original rate version and source quantities. Later rates or
collections do not rewrite historical statements.

Active-only compute intervals, network traffic and storage-hour rating do
not yet have verified meter sources. Current-month periods remain open.
Statements show priced allocation lines and coverage gaps, remain
**provisional**, and have no complete total. They are not invoices or
payment requests. See [T48](tasks/T48.md) for exact acceptance evidence and
the [completion checklist](poc-completion-checklist.md) for broader work.

## Operations and preservation

`poc-usage-collector.timer` should be active/waiting. Its oneshot service is
normally inactive after a successful run; inspect its exit code and journal
for each collection. Runtime databases/configuration are Horizon-owned mode
0600 under `/var/lib/openstack-poc-billing` mode 0700. The configured Horizon
WSGI daemon uses one process and multiple threads to honor ledger locking.

Code releases live under `/opt/openstack-poc/releases`; the installer verifies
service-user imports/templates before reloading. Cinder uses the registered
member/reader policies (`enforce_new_defaults=true`). The dedicated collector
is denied Nova/Cinder writes; Swift middleware allows only its own account
HEAD and denies object bodies, container access and writes.

The noVNC proxy binds only `127.0.0.1:6080` behind the existing TLS vhost.
Console tokens expire and are excluded from access-log query strings. New
guests have VNC enabled. The original retained guest was booted before this
change; its existing pinned SSH access is verified, and it has not been
power-cycled to add graphics.

Leave the verified Windows VHDX unchanged. Do not run historical cold-recovery,
backup, restore, package-rollback rehearsal, OS-upgrade or distro-import
instructions as part of this POC. Full WSL restore verification remains
**unverified** and is not a development gate. No additional distribution or
OS upgrade was performed. Historical runbooks are evidence, not instructions
to resume those workflows.
