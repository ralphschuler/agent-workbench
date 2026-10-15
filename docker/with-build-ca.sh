#!/bin/sh
# Optional enterprise/cloud proxy trust, scoped to a single build command.
# BuildKit mounts the CA bundle; it is never copied into the resulting image.
set -eu
ca_file=/run/secrets/build_ca
apt_config=/etc/apt/apt.conf.d/99agent-build-ca
if [ -s "$ca_file" ]; then
    export SSL_CERT_FILE="$ca_file" REQUESTS_CA_BUNDLE="$ca_file"
    export CURL_CA_BUNDLE="$ca_file" NODE_EXTRA_CA_CERTS="$ca_file"
    export NPM_CONFIG_CAFILE="$ca_file" PIP_CERT="$ca_file"
    printf 'Acquire::https::CaInfo "%s";\n' "$ca_file" > "$apt_config"
    trap 'rm -f "$apt_config"' EXIT HUP INT TERM
fi
"$@"
