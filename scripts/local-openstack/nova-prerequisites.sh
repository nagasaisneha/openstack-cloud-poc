#!/usr/bin/env bash
# Task 11, stage 1: local Nova databases, RabbitMQ account, and retained secrets.
# Run manually with sudo on the approved Ubuntu 22.04 WSL2 host.
set +x
set -euo pipefail
umask 077

fail() { printf 'STOP: %s\n' "$*" >&2; exit 1; }
progress() { printf 'Task 11 Nova prerequisites: %s\n' "$*"; }

[[ $EUID -eq 0 ]] || fail 'run with sudo in the actual Ubuntu 22.04 WSL terminal.'
for command in mysql rabbitmqctl openssl flock stat awk uname mktemp; do
    command -v "$command" >/dev/null || fail "required command unavailable: $command"
done
. /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 22.04 ]] || fail 'Ubuntu 22.04 is required.'
[[ $(uname -r) == *[Mm]icrosoft* ]] || fail 'this script is scoped to the approved WSL host.'

secret_file=/root/openstack-poc-keystone-secrets
[[ -f $secret_file && ! -L $secret_file ]] || fail 'protected Keystone secret file missing or symlinked.'
[[ $(stat -c '%u:%a' "$secret_file") == 0:600 ]] || fail 'secret file must be owned by root with mode 0600.'

exec 9>/run/lock/openstack-poc-nova-prerequisites.lock
flock -n 9 || fail 'another Nova prerequisite run holds the lock.'
progress BEGIN

# Parse only task-generated hexadecimal values; never source or print the file.
read_secret() {
    local key=$1 row value
    local -a rows=()
    mapfile -t rows < <(awk -v prefix="$key=" 'index($0, prefix) == 1 {print}' "$secret_file")
    (( ${#rows[@]} <= 1 )) || fail "duplicate secret key: $key"
    if (( ${#rows[@]} == 0 )); then
        printf ''
        return
    fi
    row=${rows[0]}
    value=${row#*=}
    [[ $value =~ ^[0-9a-f]{64}$ ]] || fail "invalid stored format for $key; preserving it."
    printf '%s' "$value"
}

NOVA_DBPASS=$(read_secret NOVA_DBPASS)
RABBIT_PASS=$(read_secret RABBIT_PASS)

# Existing assets without this task's retained password require inspection.
db_assets=$(mysql --batch --skip-column-names --execute="SELECT (SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ('nova_api','nova','nova_cell0')) + (SELECT COUNT(*) FROM mysql.user WHERE User='nova');")
[[ $db_assets =~ ^[0-9]+$ ]] || fail 'unexpected MariaDB inventory response.'
if [[ -z $NOVA_DBPASS && $db_assets != 0 ]]; then
    fail 'Nova database/account assets exist without a retained task password; preserving them.'
fi
rabbit_users=$(rabbitmqctl list_users -q)
rabbit_exists=false
if awk '$1 == "openstack" {found=1} END {exit !found}' <<< "$rabbit_users"; then
    rabbit_exists=true
fi
if [[ $rabbit_exists == true && -z $RABBIT_PASS ]]; then
    fail 'RabbitMQ openstack account exists without a retained task password; preserving it.'
fi

for key in NOVA_DBPASS NOVA_PASS NEUTRON_PASS RABBIT_PASS METADATA_SECRET; do
    value=$(read_secret "$key")
    if [[ -z $value ]]; then
        value=$(openssl rand -hex 32)
        printf '%s=%s\n' "$key" "$value" >> "$secret_file"
    fi
    printf -v "$key" '%s' "$value"
done
unset value key rabbit_users
progress 'credentials retained in the existing root-only file (values hidden)'

# Commands containing passwords are captured and redacted on failure.
run_private() {
    local label=$1 result
    shift
    if result=$("$@" 2>&1); then
        return
    fi
    result=${result//"$NOVA_DBPASS"/[REDACTED]}
    result=${result//"$RABBIT_PASS"/[REDACTED]}
    printf '%s\n' "$result" >&2
    fail "$label failed; retained passwords were not rotated."
}

run_private 'MariaDB setup' mysql <<SQL
CREATE DATABASE IF NOT EXISTS nova_api CHARACTER SET utf8 COLLATE utf8_general_ci;
CREATE DATABASE IF NOT EXISTS nova CHARACTER SET utf8 COLLATE utf8_general_ci;
CREATE DATABASE IF NOT EXISTS nova_cell0 CHARACTER SET utf8 COLLATE utf8_general_ci;
CREATE USER IF NOT EXISTS 'nova'@'127.0.0.1' IDENTIFIED BY '${NOVA_DBPASS}';
GRANT ALL PRIVILEGES ON nova_api.* TO 'nova'@'127.0.0.1';
GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'127.0.0.1';
GRANT ALL PRIVILEGES ON nova_cell0.* TO 'nova'@'127.0.0.1';
SQL

client_options=$(mktemp /run/openstack-poc-nova-client.XXXXXX)
trap 'rm -f -- "$client_options"' EXIT
printf '[client]\nuser=nova\npassword=%s\nhost=127.0.0.1\nprotocol=TCP\n' "$NOVA_DBPASS" > "$client_options"
for database in nova_api nova nova_cell0; do
    run_private "database login ($database)" mysql --defaults-extra-file="$client_options" \
        --database="$database" --batch --skip-column-names --execute='SELECT 1;'
done
progress 'database creation and Nova account authentication: PASS'

if [[ $rabbit_exists == false ]]; then
    run_private 'RabbitMQ account creation' rabbitmqctl add_user openstack "$RABBIT_PASS"
fi
run_private 'RabbitMQ account authentication' rabbitmqctl authenticate_user openstack "$RABBIT_PASS"
run_private 'RabbitMQ permissions' rabbitmqctl set_permissions -p / openstack '.*' '.*' '.*'
progress 'RabbitMQ account authentication and permissions: PASS'
progress 'COMPLETE (Nova config, cells, Keystone identities and VM operations remain pending)'
