BEGIN {
    in_ssl = 0
    in_gnutls = 0
    unsafe = 0
}
/^[[:space:]]*<IfModule[[:space:]]+ssl_module[[:space:]]*>[[:space:]]*$/ { in_ssl = 1 }
/^[[:space:]]*<IfModule[[:space:]]+mod_gnutls\.c[[:space:]]*>[[:space:]]*$/ { in_gnutls = 1 }
/^[[:space:]]*Listen[[:space:]].*443/ {
    if ((in_ssl || in_gnutls) && $0 ~ /^[[:space:]]*Listen[[:space:]]+443[[:space:]]*$/) {
        next
    }
    unsafe = 1
}
{ print }
/^[[:space:]]*<\/IfModule>[[:space:]]*$/ { in_ssl = 0; in_gnutls = 0 }
END {
    if (unsafe) {
        exit 2
    }
}
