#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

readonly HORIZON_PACKAGE='openstack-dashboard=4:24.0.1-0ubuntu0.24.04.2~cloud0'
readonly HORIZON_VERSION='4:24.0.1-0ubuntu0.24.04.2~cloud0'
readonly YAML_VERSION='5.4.1-1ubuntu1'
readonly YAML_CANDIDATE='6.0.1-2build2~cloud0'
readonly YAML_DEB='/tmp/python3-yaml_5.4.1-1ubuntu1_amd64.deb'
readonly YAML_SHA256='7ef9b534046bb838f7357091bafb368f6d1f32fb7ad2887d7c377ff4c0081b32'
readonly PROTECTED_ROOT='/root/openstack-poc-acceptance'

die() {
    printf 'STOP: %s\n' "$1" >&2
    exit 1
}

stage='startup'
plan_file=''
backup_dir=''
incomplete=0
on_exit() {
    result=$?
    trap - EXIT
    if [[ -n ${plan_file:-} ]]; then
        rm -f -- "$plan_file" || true
    fi
    if [[ $result -ne 0 ]]; then
        if [[ $incomplete -eq 1 && -n ${backup_dir:-} && -d "$backup_dir" ]]; then
            printf 'Snapshot creation did not complete; do not use this directory as a rollback point.\n' \
                > "$backup_dir/INCOMPLETE.txt" || true
            chmod 0600 "$backup_dir/INCOMPLETE.txt" 2>/dev/null || true
            printf 'STOP: incomplete protected snapshot retained at %s\n' "$backup_dir" >&2
        fi
        printf 'STOP: rollback preparation exited during "%s" (exit %s)\n' "$stage" "$result" >&2
    fi
    exit "$result"
}
begin_stage() {
    stage=$1
    printf 'BEGIN: %s\n' "$stage"
}
pass_stage() {
    printf 'PASS: %s\n' "$stage"
}
trap on_exit EXIT

begin_stage 'root access, required commands, and protected recovery directory'
if [[ ${EUID} -ne 0 ]]; then
    die 'run from the actual Ubuntu WSL terminal with sudo; no host changes were made'
fi

for command_name in apt-cache apt-get apache2ctl a2query dpkg-query dpkg-deb install sha256sum ss stat systemctl tar; do
    command -v "$command_name" >/dev/null 2>&1 || die "required command unavailable: $command_name"
done

[[ -d "$PROTECTED_ROOT" && ! -L "$PROTECTED_ROOT" ]] || die 'protected POC recovery directory is unavailable'
[[ $(stat -c '%u:%g:%a' "$PROTECTED_ROOT") == '0:0:700' ]] || die 'protected POC recovery directory ownership or mode is unsafe'
pass_stage

begin_stage 'installed package versions and pinned rollback artifact'
if dpkg-query -W -f='${db:Status-Status}' openstack-dashboard >/dev/null 2>&1; then
    die 'Horizon is already installed; use the reviewed existing-install recovery plan'
fi

installed_yaml=$(dpkg-query -W -f='${Version}' python3-yaml 2>/dev/null) || die 'python3-yaml installed version is unavailable'
[[ $installed_yaml == "$YAML_VERSION" ]] || die 'python3-yaml version differs from the reviewed rollback artifact'

candidate=$(apt-cache policy openstack-dashboard | awk '/Candidate:/ {print $2; exit}')
[[ $candidate == "$HORIZON_VERSION" ]] || die 'Horizon package candidate differs from the reviewed Caracal version'

[[ -f "$YAML_DEB" && ! -L "$YAML_DEB" ]] || die 'verified python3-yaml rollback package is missing from /tmp'
actual_yaml_hash=$(sha256sum "$YAML_DEB" | awk '{print $1}')
[[ $actual_yaml_hash == "$YAML_SHA256" ]] || die 'python3-yaml rollback package digest mismatch'
[[ $(dpkg-deb -f "$YAML_DEB" Package) == 'python3-yaml' ]] || die 'rollback package name mismatch'
[[ $(dpkg-deb -f "$YAML_DEB" Version) == "$YAML_VERSION" ]] || die 'rollback package version mismatch'
pass_stage

begin_stage 'Apache, Memcached, and loopback listener preflight'
for unit in apache2 memcached; do
    systemctl is-active --quiet "$unit" || die "required pre-install service is not active: $unit"
done
apache2ctl configtest >/dev/null 2>&1 || die 'existing Apache configuration check failed'
if ss -H -ltn | awk '$4 ~ /:8443$/ {found=1} END {exit !found}'; then
    die 'port 8443 is already in use'
fi
if a2query -c openstack-dashboard >/dev/null 2>&1; then
    die 'default global Horizon Apache configuration is already enabled'
fi
pass_stage

begin_stage 'exact Horizon apt simulation (no packages will be installed)'
plan_file=$(mktemp)
LC_ALL=C apt-get -s --no-install-recommends install "$HORIZON_PACKAGE" >"$plan_file" 2>&1 \
    || die 'Horizon package simulation failed; no host changes were made'
grep -Eq '^1 upgraded, 18 newly installed, 0 to remove([, ]|$)' "$plan_file" \
    || die 'Horizon package transaction no longer matches the reviewed 1-upgrade/18-new/0-remove scope'
grep -Fq "Inst python3-yaml [${YAML_VERSION}] (${YAML_CANDIDATE} " "$plan_file" \
    || die 'python3-yaml upgrade differs from the reviewed rollback package plan'
pass_stage

begin_stage 'write protected pre-install rollback snapshot'
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$(mktemp -d "$PROTECTED_ROOT/horizon-package-rollback-${timestamp}.XXXXXX")
chmod 0700 "$backup_dir"
incomplete=1

install -o root -g root -m 0600 "$YAML_DEB" "$backup_dir/python3-yaml_${YAML_VERSION}_amd64.deb"
cp "$plan_file" "$backup_dir/apt-install-plan.txt"
chmod 0600 "$backup_dir/apt-install-plan.txt"
tar -czf "$backup_dir/apache2-before.tar.gz" -C / etc/apache2
chmod 0600 "$backup_dir/apache2-before.tar.gz"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' \
    > "$backup_dir/packages-before.tsv"
chmod 0600 "$backup_dir/packages-before.tsv"
{
    printf 'created_at_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'horizon_package\t%s\n' "$HORIZON_PACKAGE"
    printf 'python3_yaml_installed_before\t%s\n' "$installed_yaml"
    printf 'python3_yaml_rollback_sha256\t%s\n' "$YAML_SHA256"
    printf 'apt_scope\t1 upgraded, 18 newly installed, 0 removed\n'
    printf 'apache2_configtest\tpassed\n'
    printf 'apache2\tactive\nmemcached\tactive\n'
    printf 'listener_8443\tunused\n'
    printf 'default_horizon_apache_conf\tdisabled\n'
} > "$backup_dir/metadata.tsv"
chmod 0600 "$backup_dir/metadata.tsv"
(
    cd "$backup_dir"
    sha256sum apt-install-plan.txt apache2-before.tar.gz packages-before.tsv \
        "python3-yaml_${YAML_VERSION}_amd64.deb" metadata.tsv > SHA256SUMS
)
chmod 0600 "$backup_dir/SHA256SUMS"

begin_stage 'verify protected snapshot contents and hashes'
tar -tzf "$backup_dir/apache2-before.tar.gz" | grep '^etc/apache2/' >/dev/null \
    || die 'Apache configuration archive failed its membership check'
(
    cd "$backup_dir"
    sha256sum --check --status SHA256SUMS
) \
    || die 'protected rollback snapshot failed its hash check'

incomplete=0
rm -f -- "$plan_file"
plan_file=''
pass_stage
printf 'PASS: Horizon package rollback snapshot created; contents remain root-only.\n'
printf 'Protected snapshot path: %s\n' "$backup_dir"
printf 'Rollback package SHA-256: %s\n' "$YAML_SHA256"
