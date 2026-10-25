#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

if [[ ${EUID} -ne 0 ]]; then
    die 'Run this certificate setup as root.'
fi

for command_name in chmod cut flock grep ln mktemp openssl rm sha256sum stat; do
    command -v "$command_name" >/dev/null 2>&1 || die "Required command is unavailable: $command_name"
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
extensions_file=$script_dir/server-cert-extensions.cnf
ca_key=/etc/ssl/private/horizon-poc-local-ca.key
ca_cert=/etc/ssl/certs/horizon-poc-local-ca.crt
server_key=/etc/ssl/private/horizon-poc-local.key
server_cert=/etc/ssl/certs/horizon-poc-local.crt
lock_file=/run/lock/horizon-poc-local-cert.lock

[[ -d /etc/ssl/private && -d /etc/ssl/certs ]] || die 'Expected /etc/ssl certificate directories are missing.'
[[ -f "$extensions_file" && ! -L "$extensions_file" ]] || die 'Certificate extension configuration is missing or is not a regular file.'
[[ $(stat -c '%d' /etc/ssl/private) == "$(stat -c '%d' /etc/ssl/certs)" ]] || die 'Certificate directories are on different filesystems; refusing non-atomic hard-link publication.'

umask 077
exec 9>"$lock_file"
flock -n 9 || die 'Another local Horizon certificate operation is already running.'

all_paths=("$ca_key" "$ca_cert" "$server_key" "$server_cert")
existing=0
for path in "${all_paths[@]}"; do
    if [[ -e "$path" || -L "$path" ]]; then
        existing=$((existing + 1))
    fi
done

assert_file() {
    local path=$1 expected_mode=$2
    [[ -f "$path" && ! -L "$path" ]] || die "Expected a regular, non-symlink file: $path"
    [[ $(stat -c '%u:%g' -- "$path") == '0:0' ]] || die "Expected root ownership for: $path"
    [[ $(stat -c '%a' -- "$path") == "$expected_mode" ]] || die "Unexpected permissions on $path; expected $expected_mode."
}

assert_key_matches_certificate() {
    local key=$1 certificate=$2 key_digest certificate_digest
    key_digest=$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum | cut -d ' ' -f 1)
    certificate_digest=$(openssl x509 -in "$certificate" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d ' ' -f 1)
    [[ "$key_digest" == "$certificate_digest" ]] || die "Private key does not match certificate: $certificate"
}

assert_certificate_validity() {
    local certificate=$1 minimum_seconds=$2 label=$3
    openssl x509 -in "$certificate" -noout -checkend "$minimum_seconds" >/dev/null || die "$label certificate is expired or expires too soon: $certificate"
}

validate_certificate_set() {
    local root_ca_key=$1 root_ca_cert=$2 leaf_key=$3 leaf_cert=$4 extensions

    assert_key_matches_certificate "$root_ca_key" "$root_ca_cert"
    assert_key_matches_certificate "$leaf_key" "$leaf_cert"
    assert_certificate_validity "$root_ca_cert" 2592000 'Local CA'
    assert_certificate_validity "$leaf_cert" 604800 'localhost server'

    openssl x509 -in "$root_ca_cert" -noout -ext basicConstraints | grep -Eq 'CA:TRUE, pathlen:0' || die 'Local CA certificate is not constrained to a signing depth of zero.'
    openssl x509 -in "$root_ca_cert" -noout -ext keyUsage | grep -Eq 'Certificate Sign' || die 'Local CA certificate cannot sign certificates.'
    extensions=$(openssl x509 -in "$root_ca_cert" -noout -ext nameConstraints)
    grep -Fq 'DNS:localhost' <<<"$extensions" || die 'Local CA is not constrained to DNS localhost.'
    grep -Fq 'IP:127.0.0.1/255.255.255.255' <<<"$extensions" || die 'Local CA is not constrained to IPv4 loopback.'
    openssl x509 -in "$leaf_cert" -noout -ext basicConstraints | grep -Eq 'CA:FALSE' || die 'localhost certificate is not marked CA:FALSE.'
    extensions=$(openssl x509 -in "$leaf_cert" -noout -ext subjectAltName)
    grep -Fq 'DNS:localhost' <<<"$extensions" || die 'localhost certificate is missing the DNS localhost SAN.'
    grep -Fq 'IP Address:127.0.0.1' <<<"$extensions" || die 'localhost certificate is missing the 127.0.0.1 SAN.'
    openssl x509 -in "$leaf_cert" -noout -ext extendedKeyUsage | grep -Eq 'TLS Web Server Authentication' || die 'localhost certificate is missing the serverAuth extended key usage.'
    openssl verify -verify_hostname localhost -CAfile "$root_ca_cert" "$leaf_cert" >/dev/null || die 'localhost DNS identity does not verify against the local CA.'
    openssl verify -verify_ip 127.0.0.1 -CAfile "$root_ca_cert" "$leaf_cert" >/dev/null || die 'localhost loopback IP identity does not verify against the local CA.'
}

if [[ $existing -eq 4 ]]; then
    assert_file "$ca_key" 600
    assert_file "$ca_cert" 644
    assert_file "$server_key" 600
    assert_file "$server_cert" 644
    validate_certificate_set "$ca_key" "$ca_cert" "$server_key" "$server_cert"
    openssl x509 -in "$ca_cert" -noout -fingerprint -sha256
    printf '%s\n' 'Existing Horizon localhost certificate set passed validation; no files changed.'
    exit 0
fi

if [[ $existing -ne 0 ]]; then
    die 'Some Horizon certificate files already exist; refusing to overwrite a partial set. Review existing paths manually.'
fi

tmp_dir=$(mktemp -d /etc/ssl/.horizon-poc-local-cert.XXXXXX)
published_paths=()
cleanup() {
    local status=$?
    trap - EXIT
    if [[ $status -ne 0 ]]; then
        for path in "${published_paths[@]}"; do
            rm -f -- "$path" || true
        done
    fi
    if [[ -n ${tmp_dir:-} && -d "$tmp_dir" && ! -L "$tmp_dir" && "$tmp_dir" == /etc/ssl/.horizon-poc-local-cert.* ]]; then
        rm -rf -- "$tmp_dir" || true
    fi
    exit "$status"
}
trap cleanup EXIT

openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -keyout "$tmp_dir/ca.key" \
    -out "$tmp_dir/ca.crt" \
    -subj '/CN=OpenStack-POC-Local-CA' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'nameConstraints=critical,permitted;DNS:localhost,permitted;IP:127.0.0.1/255.255.255.255' \
    -addext 'subjectKeyIdentifier=hash'

openssl req -newkey rsa:3072 -nodes -sha256 \
    -keyout "$tmp_dir/server.key" \
    -out "$tmp_dir/server.csr" \
    -subj '/CN=localhost'

openssl x509 -req -sha256 -days 90 \
    -in "$tmp_dir/server.csr" \
    -CA "$tmp_dir/ca.crt" \
    -CAkey "$tmp_dir/ca.key" \
    -CAcreateserial \
    -extfile "$extensions_file" \
    -out "$tmp_dir/server.crt"

chmod 0600 "$tmp_dir/ca.key" "$tmp_dir/server.key"
chmod 0644 "$tmp_dir/ca.crt" "$tmp_dir/server.crt"
validate_certificate_set "$tmp_dir/ca.key" "$tmp_dir/ca.crt" "$tmp_dir/server.key" "$tmp_dir/server.crt"

# Hard links publish validated files without replacing existing paths.
# Temporary files are under /etc/ssl, on the same filesystem as the destinations.
for pair in \
    "$tmp_dir/ca.key:$ca_key" \
    "$tmp_dir/ca.crt:$ca_cert" \
    "$tmp_dir/server.key:$server_key" \
    "$tmp_dir/server.crt:$server_cert"; do
    source_path=${pair%%:*}
    destination_path=${pair#*:}
    ln -- "$source_path" "$destination_path" || die "Refusing to replace an existing path: $destination_path"
    published_paths+=("$destination_path")
done

validate_certificate_set "$ca_key" "$ca_cert" "$server_key" "$server_cert"
assert_file "$ca_key" 600
assert_file "$ca_cert" 644
assert_file "$server_key" 600
assert_file "$server_cert" 644

openssl x509 -in "$ca_cert" -noout -fingerprint -sha256
printf '%s\n' 'Created and validated Horizon localhost certificates. Import only the CA certificate into the intended browser trust store after independently comparing its SHA-256 fingerprint.'
