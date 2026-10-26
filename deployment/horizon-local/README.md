# Local Horizon browser slice

**Status:** Horizon 24.0.1 is installed; the package global Apache config is disabled; localhost TLS and the dedicated loopback vhost are installed and locally verified. Windows browser trust and Keystone sign-in remain unverified.

This package targets the existing Ubuntu 22.04 / OpenStack Caracal WSL POC. It keeps the shared Apache service and existing OpenStack APIs in place. It is not a staging or production deployment.

## Package and host impact observed

The current Jammy Caracal apt candidate is `openstack-dashboard` `4:24.0.1-0ubuntu0.24.04.2~cloud0`, matching the Horizon 24.0.1/Caracal series. A read-only `apt-get -s --no-install-recommends install 'openstack-dashboard=4:24.0.1-0ubuntu0.24.04.2~cloud0'` plans 18 new packages, one upgrade (`python3-yaml` from `5.4.1-1ubuntu1` to `6.0.1-2build2~cloud0`), zero removals, and 20 packages not upgraded. Do not run `apt autoremove`; the simulation reports a separate set of packages as no longer required, but it is outside this change. This is a solver plan, not a guarantee of installation success or runtime resource use.

The package's maintainer script has material shared-service effects:

- It creates the `horizon` system user/group, collects/compresses static assets, and restarts Memcached.
- It enables `/etc/apache2/conf-available/openstack-dashboard.conf` and requests an Apache reload through `apache2_invoke`. On this host, the sourced Apache maintainer helper resets `APACHE2_MAINTSCRIPT_DEFER` when Apache is already installed, so setting that variable in the `apt-get` environment does not defer the action.
- That default Apache config mounts Horizon globally at `/horizon`, publishes static assets, and grants access. The packaged settings default to `ALLOWED_HOSTS = ['*']`; secure-cookie settings are commented out. Enabling that config as-is would expose an HTTP login route on the current wildcard port 80.

The operator installed the pinned package after the protected rollback snapshot passed. The transaction matched the reviewed 1-upgrade/18-new/0-remove plan, but the attempted maintscript deferral did not work: the sourced helper resets `APACHE2_MAINTSCRIPT_DEFER` when Apache is already installed. The operator disabled the global config, `apache2ctl configtest` returned `Syntax OK`, and `a2query` reports it disabled by the site administrator. After reload, `/horizon/` returned 404, `/` returned 200, direct Keystone and Placement endpoints returned 200, and the existing `/identity` and `/placement` alias responses remained at their previously recorded 503 and 500. Configtest emitted existing proxy worker-sharing warnings. The install also performed the planned `python3-yaml` upgrade and can restart Memcached; verify current service health.

## Browser boundary

The configuration binds only `127.0.0.1:8443`. The operator-reported installer PASS and an independent CA-validated request returned HTTP 302 from `https://localhost:8443/horizon/`; the listener check found only `127.0.0.1:8443` and no port-443 listener. This verifies the local WSL endpoint, not the Windows browser path. No administrator or tenant password is stored here; Horizon authenticates against the existing Keystone service.

The certificate helper created a local CA and 90-day localhost server certificate under `/etc/ssl`; both passed validation. The CA is constrained to `localhost` and `127.0.0.1`; its private key remains root-only in `/etc/ssl/private`. The operator has the public CA SHA-256 fingerprint; browser trust has not yet been imported.

To trust the certificate in a Windows browser after an approved host deployment:

1. Use the CA SHA-256 fingerprint printed when the certificate helper completed successfully. Do not copy or import the private key.
2. Export only the public CA certificate as DER and get its WSL path:

   ```bash
   sudo openssl x509 -in /etc/ssl/certs/horizon-poc-local-ca.crt -outform DER -out /tmp/horizon-poc-local-ca.cer
   sudo chmod 0644 /tmp/horizon-poc-local-ca.cer
   wslpath -w /tmp/horizon-poc-local-ca.cer
   ```

3. In Windows PowerShell, run `certutil -hashfile '<path from wslpath>' SHA256` and compare the result with the helper's printed fingerprint (ignoring colons). Import only after the values match:

   ```powershell
   Import-Certificate -FilePath '<path from wslpath>' -CertStoreLocation Cert:\CurrentUser\Root
   ```

4. Remove that exact certificate from `Cert:\CurrentUser\Root` after the local demonstration, using the `Thumbprint` returned by `Import-Certificate`. Remove the temporary DER export as well. Do not import this local CA into a machine-wide store.

Windows/WSL localhost forwarding and the Apache `Require local` rule must still be verified in the intended browser. The local site does not change the existing port 80 listener or API vhosts. Do not bind this dashboard to a non-loopback address or expose it to the LAN. Trusting the local CA gives its root-only key the ability to issue certificates for the constrained localhost names, so protect that host key and remove browser trust when it is no longer needed.

## Planned deployment sequence

1. From the actual Ubuntu WSL terminal, run the root-only, default-mode readiness check:

   ```bash
   sudo /usr/bin/python3 -B scripts/local-openstack/review_restart_readiness.py
   ```

   On 2026-10-26 the operator reported that its recovery source/rehearsal, Keystone session, service, retained-VM and guest checks all passed; no restart was performed. It writes a protected report. Keep that report root-only and share only its final status line.

2. Prepare and verify the protected package rollback snapshot:

   ```bash
   sudo bash deployment/horizon-local/prepare-package-rollback.sh
   ```

   It prints `BEGIN`/`PASS` markers for each preflight and snapshot phase, rechecks the exact package scope, then saves the old `python3-yaml` package, package versions and Apache configuration under the root-only POC acceptance directory. It makes no service or package changes. The apt simulation can take time; if it fails, the last phase and `STOP` line identify where. If no first `BEGIN` appears, check whether `sudo` is still waiting for authentication or whether the command returned before the script started. The old `python3-yaml` package was downloaded to `/tmp` and its digest is pinned in the helper. Preserve the script's root-only output path; do not copy the snapshot into Git.
3. The pinned dashboard package is installed. The package maintainer enabled its default global Apache config despite the attempted environment deferral. The operator disabled it, passed `apache2ctl configtest`, reloaded Apache, and confirmed `/horizon/` returns 404 while the existing API endpoints still respond. Keep this config disabled.
4. The operator ran the certificate helper; it created and validated the private localhost certificate set. For a fresh setup, use:

   ```bash
   sudo bash deployment/horizon-local/generate-local-cert.sh
   ```

   Keep only the printed CA SHA-256 fingerprint for the Windows trust check; never copy or import the private key. The installer has since enabled the `ssl` Apache module.
5. The operator ran the guarded helper successfully. It installed the secure settings block and dedicated Apache config, passed configtest, gracefully reloaded Apache, confirmed the HTTPS response and loopback listener, and verified unchanged API route statuses. The same command is idempotent for an already installed, matching configuration:

   ```bash
   sudo bash deployment/horizon-local/install-loopback-config.sh
   ```

   It preserves the packaged settings and secret, creates a protected configuration snapshot, removes only inactive wildcard `Listen 443` directives from `ports.conf`, and refuses to proceed if port 443 is occupied. It verifies the local TLS response, exact loopback listener, disabled HTTP `/horizon/` route, and before/after status of existing API routes. Do not enable the package global dashboard config or restart the shared Apache service.
6. The installer confirmed the listener is exactly `127.0.0.1:8443` and local HTTPS serves Horizon. Verify that URL in the intended Windows browser and confirm existing routes and services there.
7. Verify the certificate in the Windows browser and sign in using an existing Keystone account. No credentials are provided by this repository.

The package installation has completed. The certificate helper and loopback config installer are explicit root-run host actions: they write the local certificates/configuration, and the installer reloads Apache only after its checks pass. No credentials are stored in Git.
