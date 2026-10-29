# Inspect the existing MariaDB databases from Windows

Verified on 2026-10-29 with installed HeidiSQL **12.22.0.7382** and its
**libmariadb.dll 3.4.9**, against the existing Ubuntu WSL MariaDB
**10.6.23-MariaDB-0ubuntu0.22.04.1**.

Create a new HeidiSQL session named **OpenStack local viewer**:

| Setting | Value |
|---|---|
| Network type | **MariaDB or MySQL (TCP/IP)** |
| Library | **libmariadb.dll** |
| Hostname / IP | **127.0.0.1** |
| Port | **3306** |
| User | **openstack_viewer** |
| Password | Use the generated password below at the connection prompt |
| Prompt for credentials | **Enabled**; save the session with its password field empty |
| Use Windows authentication | **Disabled** |
| Use SSL | **Unchecked** |
| Database(s) | Leave empty to show every permitted schema |

The network-type text was checked in this installed Windows executable.
HeidiSQL's [connection help](https://www.heidisql.com/help.php#connecting)
documents the credentials prompt and database filtering.

The existing server has `have_ssl=DISABLED`. The first native Windows client
attempt returned TLS error 2026 because Connector/C 3.4 requires TLS by default.
Explicitly turning SSL off for this loopback connection succeeded. This is a
local connection without transport encryption; it is not a remote database
connection recipe. No MariaDB TLS settings or service configuration changed.

## Retrieve and use the password locally

A random password with 256 bits of entropy has already been generated and
installed. It is retained only in the root-owned mode **0600** file
`/root/openstack-poc-acceptance/mariadb-viewer/credentials.json`, inside a
mode **0700** directory. It was never printed in the verification output,
embedded in command arguments, or written into this checkout. SQL setup used
the password hash rather than the plaintext password.

In your own Windows PowerShell, deliberately display only the viewer password:

```powershell
wsl.exe --distribution Ubuntu --user root --exec /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_database_viewer.py --show-password
```

Store it in your password manager, enable **Prompt for credentials**, and paste
it into the password prompt when opening the HeidiSQL session. Keep the saved
session's Password field empty. Keep terminal output local; do not redirect it
to a report or paste it into chat. If using the clipboard, turn off clipboard
history/cloud sync before copying and clear it after use with
`Set-Clipboard -Value ''`. The Windows user controlling this WSL distribution
can access its root-owned credential through `wsl.exe --user root`.

## Access scope and verification

The only new MariaDB account is **`openstack_viewer`@`127.0.0.1`**. Its only
data privilege is **SELECT**, granted separately on:

```text
cinder       glance       keystone    neutron
nova         nova_api     nova_cell0  octavia
placement    poc_meter_sources
```

The first nine are the installed OpenStack service schemas; `poc_meter_sources`
contains the POC's metering sources. Swift object data and the separate SQLite
billing ledger are outside this MariaDB account's scope. Database names with
underscores are escaped in grants so they do not authorize similarly named
future databases. The viewer also sees filtered `information_schema` metadata.
There is no global SELECT, grant option, role, proxy privilege, view-definition
privilege, or access to `mysql`, `sys`, or `performance_schema` data.

Actual checks passed:

- Linux MariaDB listener: **127.0.0.1:3306**; service active. No restart or
  listener reconfiguration was needed.
- Windows localhost forwarding: **127.0.0.1:3306** only, using WSL's existing
  default NAT/localhost forwarding; no `netsh portproxy` entries were present.
- Real Windows login using HeidiSQL's bundled MariaDB library authenticated as
  **openstack_viewer@127.0.0.1**, listed exactly the permitted schemas plus
  `information_schema`, and executed a SELECT in each schema.
- A no-row UPDATE (`WHERE 1=0`) and a system-table SELECT were denied, on both
  Linux and Windows. An incorrect password was denied on Linux. The account's
  grant records were checked for exactly SELECT-only access.
- Windows connection to WSL's non-loopback address on port 3306 failed. Both
  Linux and Windows port 3306 listeners were loopback-only. No firewall opening,
  broad host grant, port proxy, or LAN exposure was added.
- Fingerprints of all pre-existing authentication and grant records matched
  before and after setup and on independent verification. Root's existing
  `mysql_native_password` plus `unix_socket` authentication and every OpenStack
  service account were preserved. No database contents were changed by checks.

Sanitized evidence: [Linux verification](evidence/mariadb-viewer-linux-20261029.json)
and [Windows verification](evidence/mariadb-viewer-windows-20261029.json).
The Windows verification used the client library directly; it did not automate
the HeidiSQL GUI or save a session/password in Windows.

You can confirm the connected identity in HeidiSQL's Query tab:

```sql
SELECT CURRENT_USER(), VERSION();
SHOW DATABASES;
SELECT COUNT(*) FROM keystone.project;
```

## Recheck or remove the viewer

Read-only Linux recheck from Windows PowerShell:

```powershell
wsl.exe --distribution Ubuntu --user root --exec /usr/bin/python3 -B /home/sneha/projects/openstack-cloud-poc/scripts/local-openstack/manage_database_viewer.py --verify
```

The separate [Windows verification script](../scripts/local-openstack/verify_database_viewer.ps1)
uses the installed HeidiSQL library. Run it in 64-bit Windows PowerShell from
this checkout's `scripts/local-openstack` directory with
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\verify_database_viewer.ps1`.
That execution policy applies only to the verification process.

The [account helper](../scripts/local-openstack/manage_database_viewer.py)
also supports `--apply` for this existing host. It never resets an existing
account/password, broadens a host pattern, changes service accounts, or changes
root authentication. It refuses unexpected accounts, grants or changed access
baselines. If an initial setup is interrupted, inspect the viewer's partial
grants before manually completing them; it does not silently adopt partial or
unowned accounts. Missing/new databases are not automatically granted.

To withdraw access later, close viewer sessions and have the local operator
run only `DROP USER 'openstack_viewer'@'127.0.0.1';` through the existing root
socket connection. Retire the protected viewer credential and its password
manager entry after confirming removal. This is a rollback instruction, not
an action performed during setup. Existing accounts and database data need no
rollback because they were not changed.
