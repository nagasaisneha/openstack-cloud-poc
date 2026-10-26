#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

readonly HORIZON_VERSION='4:24.0.1-0ubuntu0.24.04.2~cloud0'
readonly PROTECTED_ROOT='/root/openstack-poc-acceptance'
readonly SETTINGS_FILE='/etc/openstack-dashboard/local_settings.py'
readonly SETTINGS_BLOCK_NAME='local_settings.py.block'
readonly SETTINGS_BEGIN='# BEGIN openstack-cloud-poc Horizon overrides'
readonly SETTINGS_END='# END openstack-cloud-poc Horizon overrides'
readonly CONF_FILE='/etc/apache2/conf-available/horizon-local-ssl.conf'
readonly CONF_NAME='horizon-local-ssl'
readonly DEFAULT_CONF_LINK='/etc/apache2/conf-enabled/openstack-dashboard.conf'
readonly PORTS_FILE='/etc/apache2/ports.conf'
readonly CA_KEY='/etc/ssl/private/horizon-poc-local-ca.key'
readonly CA_CERT='/etc/ssl/certs/horizon-poc-local-ca.crt'
readonly SERVER_KEY='/etc/ssl/private/horizon-poc-local.key'
readonly SERVER_CERT='/etc/ssl/certs/horizon-poc-local.crt'
readonly LOCK_FILE='/run/lock/horizon-poc-local-config.lock'

phase='startup'
backup_dir=''
temporary_block=''
temporary_settings=''
before_routes=''
after_routes=''
ports_candidate=''
temporary_ports=''
settings_changed=0
conf_created=0
ports_changed=0
ssl_enabled=0
socache_enabled=0
setenvif_enabled=0
mime_enabled=0
conf_enabled=0
apply_started=0
reload_attempted=0

die() {
    printf 'STOP: %s\n' "$1" >&2
    exit 1
}

restore_previous_state() {
    local rollback_error=0
    if [[ $conf_enabled -eq 1 ]]; then
        a2disconf "$CONF_NAME" >/dev/null 2>&1 || rollback_error=1
    fi
    if [[ $conf_created -eq 1 ]]; then
        rm -f -- "$CONF_FILE" || rollback_error=1
    fi
    if [[ $ports_changed -eq 1 && -f "$backup_dir/ports.conf.before" ]]; then
        cp -a -- "$backup_dir/ports.conf.before" "$PORTS_FILE" || rollback_error=1
    fi
    if [[ $settings_changed -eq 1 && -f "$backup_dir/local_settings.py.before" ]]; then
        cp -a -- "$backup_dir/local_settings.py.before" "$SETTINGS_FILE" || rollback_error=1
    fi
    if [[ $ssl_enabled -eq 1 ]]; then
        a2dismod ssl >/dev/null 2>&1 || true
    fi
    if [[ $socache_enabled -eq 1 ]]; then
        a2dismod socache_shmcb >/dev/null 2>&1 || rollback_error=1
    fi
    if [[ $setenvif_enabled -eq 1 ]]; then
        a2dismod setenvif >/dev/null 2>&1 || rollback_error=1
    fi
    if [[ $mime_enabled -eq 1 ]]; then
        a2dismod mime >/dev/null 2>&1 || rollback_error=1
    fi
    if [[ $reload_attempted -eq 1 ]]; then
        if apache2ctl configtest && systemctl reload apache2; then
            :
        else
            rollback_error=1
        fi
    fi
    if [[ $rollback_error -eq 0 ]]; then
        printf 'Rollback restored the pre-install Horizon configuration state.\n' >&2
    else
        printf 'STOP: automatic rollback was incomplete; inspect the protected snapshot at %s\n' "$backup_dir" >&2
    fi
}

finish() {
    local result=$?
    trap - EXIT
    if [[ $result -ne 0 && $apply_started -eq 1 ]]; then
        restore_previous_state
        if [[ -n $backup_dir && -d $backup_dir ]]; then
            printf 'Configuration install failed; inspect before retrying.\n' > "$backup_dir/INSTALL-FAILED.txt" || true
            chmod 0600 "$backup_dir/INSTALL-FAILED.txt" 2>/dev/null || true
        fi
        printf 'STOP: configuration install exited during "%s" (exit %s)\n' "$phase" "$result" >&2
    fi
    for path in "$temporary_block" "$temporary_settings" "$before_routes" "$after_routes" "$ports_candidate" "$temporary_ports"; do
        if [[ -n "$path" ]]; then
            rm -f -- "$path" || true
        fi
    done
    exit "$result"
}
trap finish EXIT

for command_name in a2disconf a2dismod a2enconf a2enmod a2query apache2ctl awk cmp cp curl date dpkg-query flock grep install mktemp mv openssl rm sha256sum ss stat systemctl tar; do
    command -v "$command_name" >/dev/null 2>&1 || die "required command unavailable: $command_name"
done
[[ ${EUID} -eq 0 ]] || die 'run from the actual Ubuntu WSL terminal with sudo'

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SETTINGS_BLOCK="$script_dir/$SETTINGS_BLOCK_NAME"
readonly APACHE_TEMPLATE="$script_dir/horizon-loopback-ssl.conf"
readonly PORTS_FILTER="$script_dir/remove-inactive-wildcard-ssl-listeners.awk"
[[ -f "$SETTINGS_BLOCK" && ! -L "$SETTINGS_BLOCK" ]] || die 'managed Horizon settings block is missing or unsafe'
[[ -f "$APACHE_TEMPLATE" && ! -L "$APACHE_TEMPLATE" ]] || die 'loopback Apache template is missing or unsafe'
[[ -f "$PORTS_FILTER" && ! -L "$PORTS_FILTER" ]] || die 'Apache ports filter is missing or unsafe'
[[ -d "$PROTECTED_ROOT" && ! -L "$PROTECTED_ROOT" ]] || die 'protected POC recovery directory is unavailable'
[[ $(stat -c '%u:%g:%a' "$PROTECTED_ROOT") == '0:0:700' ]] || die 'protected POC recovery directory ownership or mode is unsafe'
exec 9>"$LOCK_FILE"
flock -n 9 || die 'another Horizon local configuration operation is running'

phase='package, Apache, and certificate preflight'
installed_version=$(dpkg-query -W -f='${Version}' openstack-dashboard 2>/dev/null) \
    || die 'pinned Horizon package is not installed'
[[ $installed_version == "$HORIZON_VERSION" ]] || die 'installed Horizon package version differs from the reviewed version'
[[ -f "$SETTINGS_FILE" && ! -L "$SETTINGS_FILE" ]] || die 'package local_settings.py is missing or is not a regular file'
[[ -f "$PORTS_FILE" && ! -L "$PORTS_FILE" ]] || die 'Apache ports.conf is missing or is not a regular file'
[[ ! -e "$DEFAULT_CONF_LINK" && ! -L "$DEFAULT_CONF_LINK" ]] \
    || die 'package global Horizon Apache config is enabled; disable it before this step'
set +e
a2query -c openstack-dashboard >/dev/null 2>&1
default_conf_status=$?
a2query -m wsgi >/dev/null 2>&1
wsgi_status=$?
a2query -m ssl >/dev/null 2>&1
ssl_status=$?
a2query -m socache_shmcb >/dev/null 2>&1
socache_status=$?
a2query -m setenvif >/dev/null 2>&1
setenvif_status=$?
a2query -m mime >/dev/null 2>&1
mime_status=$?
set -e
[[ $default_conf_status -eq 32 ]] || die 'package global Horizon Apache config is not confirmed disabled by the site administrator'
[[ $wsgi_status -eq 0 ]] || die 'Apache mod_wsgi is not enabled'
systemctl is-active --quiet apache2 || die 'Apache is not active'
systemctl is-active --quiet memcached || die 'Memcached is not active'
apache2ctl configtest || die 'current Apache configuration check failed'
if grep -RIEq '^[[:space:]]*Listen[[:space:]]+.*443' \
    /etc/apache2/mods-enabled /etc/apache2/conf-enabled /etc/apache2/sites-enabled; then
    die 'an active Apache configuration contains an additional port-443 listener'
fi

for path in "$CA_KEY" "$CA_CERT" "$SERVER_KEY" "$SERVER_CERT"; do
    [[ -f "$path" && ! -L "$path" ]] || die "required localhost certificate file is missing or unsafe: $path"
done
[[ $(stat -c '%u:%g:%a' "$CA_KEY") == '0:0:600' ]] || die 'local CA private key ownership or mode is unsafe'
[[ $(stat -c '%u:%g:%a' "$SERVER_KEY") == '0:0:600' ]] || die 'localhost private key ownership or mode is unsafe'
[[ $(stat -c '%u:%g:%a' "$CA_CERT") == '0:0:644' ]] || die 'local CA certificate ownership or mode is unsafe'
[[ $(stat -c '%u:%g:%a' "$SERVER_CERT") == '0:0:644' ]] || die 'localhost certificate ownership or mode is unsafe'
openssl verify -verify_hostname localhost -CAfile "$CA_CERT" "$SERVER_CERT" >/dev/null \
    || die 'localhost certificate does not verify for DNS localhost'
openssl verify -verify_ip 127.0.0.1 -CAfile "$CA_CERT" "$SERVER_CERT" >/dev/null \
    || die 'localhost certificate does not verify for IPv4 loopback'

phase='review managed settings and Apache config state'
temporary_block=$(mktemp)
if grep -Fq "$SETTINGS_BEGIN" "$SETTINGS_FILE"; then
    [[ $(grep -Fc "$SETTINGS_BEGIN" "$SETTINGS_FILE") -eq 1 ]] || die 'duplicate managed settings start markers found'
    [[ $(grep -Fc "$SETTINGS_END" "$SETTINGS_FILE") -eq 1 ]] || die 'managed settings end marker is missing or duplicated'
    awk -v begin="$SETTINGS_BEGIN" -v end="$SETTINGS_END" '
        $0 == begin { inside=1 }
        inside { print }
        $0 == end { inside=0; found=1 }
        END { if (!found || inside) exit 1 }
    ' "$SETTINGS_FILE" > "$temporary_block" || die 'managed settings markers are malformed'
    cmp -s -- "$temporary_block" "$SETTINGS_BLOCK" || die 'existing managed settings differ from the reviewed block'
    settings_block_present=1
else
    if grep -Fq "$SETTINGS_END" "$SETTINGS_FILE"; then
        die 'managed settings end marker exists without a start marker'
    fi
    settings_block_present=0
fi

if [[ -e "$CONF_FILE" || -L "$CONF_FILE" ]]; then
    [[ -f "$CONF_FILE" && ! -L "$CONF_FILE" ]] || die 'dedicated Apache config path exists but is not a regular file'
    cmp -s -- "$APACHE_TEMPLATE" "$CONF_FILE" || die 'dedicated Apache config exists with unexpected content'
    conf_created=0
else
    conf_created=1
fi
set +e
a2query -c "$CONF_NAME" >/dev/null 2>&1
custom_conf_status=$?
set -e

capture_routes() {
    local name url status
    while IFS=' ' read -r name url; do
        [[ -n "$name" ]] || continue
        status=$(curl --noproxy '*' --max-time 8 -sS -o /dev/null -w '%{http_code}' "$url") \
            || return 1
        printf '%s\t%s\n' "$name" "$status"
    done <<'ROUTES'
root http://127.0.0.1/
identity_alias http://127.0.0.1/identity
placement_alias http://127.0.0.1/placement
keystone_api http://127.0.0.1:5000/v3/
placement_api http://127.0.0.1:8778/
ROUTES
}

verify_tls_route() {
    local code
    code=$(curl --noproxy '*' --max-time 12 --cacert "$CA_CERT" -sS -o /dev/null -w '%{http_code}' \
        https://localhost:8443/horizon/) || return 1
    [[ $code == 200 || $code == 301 || $code == 302 ]] || return 1
    ss -H -ltn | awk '$4 == "127.0.0.1:8443" { exact=1 } $4 ~ /:8443$/ && $4 != "127.0.0.1:8443" { other=1 } $4 ~ /:443$/ { wildcard443=1 } END { exit !(exact && !other && !wildcard443) }'
}

if [[ $custom_conf_status -eq 0 ]]; then
    [[ $settings_block_present -eq 1 && $ssl_status -eq 0 ]] \
        || die 'dedicated config is enabled but the managed settings or ssl module is incomplete'
    cmp -s -- "$APACHE_TEMPLATE" "$CONF_FILE" || die 'enabled dedicated config differs from the reviewed template'
    verify_tls_route || die 'existing loopback TLS Horizon route or listener did not verify'
    printf 'PASS: loopback Horizon TLS config was already installed and verified.\n'
    exit 0
fi
[[ $custom_conf_status -eq 1 || $custom_conf_status -eq 32 || $custom_conf_status -eq 33 ]] \
    || die 'dedicated Apache config state is unexpected'
if ss -H -ltn | awk '$4 ~ /:8443$/ { found=1 } END { exit !found }'; then
    die 'port 8443 is already in use'
fi
if ss -H -ltn | awk '$4 ~ /:443$/ { found=1 } END { exit !found }'; then
    die 'port 443 is already in use; refusing to change its Apache configuration'
fi
ports_candidate=$(mktemp)
awk -f "$PORTS_FILTER" "$PORTS_FILE" > "$ports_candidate" \
    || die 'Apache ports.conf has an unexpected port-443 listener; refusing to edit it'

phase='pre-change route checks and protected snapshot'
before_routes=$(mktemp)
after_routes=$(mktemp)
capture_routes > "$before_routes" || die 'one or more existing Apache/OpenStack endpoints could not be queried before the change'
[[ $(curl --noproxy '*' --max-time 8 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/horizon/) == 404 ]] \
    || die 'default HTTP /horizon/ route is not confirmed disabled'

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$(mktemp -d "$PROTECTED_ROOT/horizon-local-config-${timestamp}.XXXXXX")
chmod 0700 "$backup_dir"
cp -a -- "$SETTINGS_FILE" "$backup_dir/local_settings.py.before"
cp -a -- "$PORTS_FILE" "$backup_dir/ports.conf.before"
tar -czf "$backup_dir/apache2-before.tar.gz" -C / etc/apache2
if [[ $conf_created -eq 0 ]]; then
    cp -a -- "$CONF_FILE" "$backup_dir/horizon-local-ssl.conf.before"
fi
{
    printf 'created_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'horizon_package\t%s\n' "$installed_version"
    printf 'default_horizon_conf\tdisabled_by_site_administrator\n'
    printf 'apache_before\tactive\nmemcached_before\tactive\n'
    printf 'port_443_before\tunused\nport_8443_before\tunused\n'
} > "$backup_dir/metadata.tsv"
cp "$before_routes" "$backup_dir/routes-before.tsv"
chmod 0600 "$backup_dir"/*
(
    cd "$backup_dir"
    sha256sum apache2-before.tar.gz local_settings.py.before ports.conf.before metadata.tsv routes-before.tsv \
        > SHA256SUMS
    if [[ -f horizon-local-ssl.conf.before ]]; then
        sha256sum horizon-local-ssl.conf.before >> SHA256SUMS
    fi
    sha256sum --check --status SHA256SUMS
)
chmod 0600 "$backup_dir/SHA256SUMS"

phase='install settings, TLS module, and loopback Apache config'
apply_started=1
if [[ $settings_block_present -eq 0 ]]; then
    settings_changed=1
    temporary_settings=$(mktemp "$(dirname "$SETTINGS_FILE")/.local_settings.py.XXXXXX")
    cp -p -- "$SETTINGS_FILE" "$temporary_settings"
    printf '\n' >> "$temporary_settings"
    cat "$SETTINGS_BLOCK" >> "$temporary_settings"
    mv -f -- "$temporary_settings" "$SETTINGS_FILE"
    temporary_settings=''
fi
if [[ $conf_created -eq 1 ]]; then
    install -o root -g root -m 0644 "$APACHE_TEMPLATE" "$CONF_FILE"
fi
if ! cmp -s -- "$ports_candidate" "$PORTS_FILE"; then
    ports_changed=1
    temporary_ports=$(mktemp "$(dirname "$PORTS_FILE")/.ports.conf.XXXXXX")
    cp -p -- "$PORTS_FILE" "$temporary_ports"
    cat "$ports_candidate" > "$temporary_ports"
    mv -f -- "$temporary_ports" "$PORTS_FILE"
    temporary_ports=''
fi
if [[ $ssl_status -ne 0 ]]; then
    ssl_enabled=1
    [[ $socache_status -eq 0 ]] || socache_enabled=1
    [[ $setenvif_status -eq 0 ]] || setenvif_enabled=1
    [[ $mime_status -eq 0 ]] || mime_enabled=1
    a2enmod ssl
fi
conf_enabled=1
a2enconf "$CONF_NAME"
apache2ctl configtest || die 'Apache configuration check failed; no reload performed'

phase='graceful reload and post-change endpoint verification'
reload_attempted=1
systemctl reload apache2 || die 'Apache reload failed'
systemctl is-active --quiet apache2 || die 'Apache is not active after reload'
systemctl is-active --quiet memcached || die 'Memcached is not active after reload'
capture_routes > "$after_routes" || die 'one or more existing Apache/OpenStack endpoints could not be queried after reload'
cmp -s -- "$before_routes" "$after_routes" || die 'existing root, identity, placement, Keystone, or Placement route status changed'
[[ $(curl --noproxy '*' --max-time 8 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/horizon/) == 404 ]] \
    || die 'default HTTP /horizon/ route became active'
verify_tls_route || die 'loopback HTTPS Horizon route or exact listener binding did not verify'

apply_started=0
printf 'PASS: secure loopback Horizon configuration is active; existing API route statuses are unchanged.\n'
printf 'Protected config snapshot: %s\n' "$backup_dir"
