#!/usr/bin/env bash
set -euo pipefail

# Official release and its published SHA-256 manifest:
# https://github.com/github/github-mcp-server/releases/tag/v2.0.2
# https://github.com/github/github-mcp-server/releases/download/v2.0.2/github-mcp-server_2.0.2_checksums.txt
test "${TARGETARCH:-amd64}" = amd64
release=v2.0.2
archive=github-mcp-server_Linux_x86_64.tar.gz
checksum=815e27d931c796af0c97f24fd211e14f8a4607d4528377719a5ad2352086b0b0
prefix=${1:-/usr/local}
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

curl --fail --silent --show-error --location \
    --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 180 --retry 2 \
    "https://github.com/github/github-mcp-server/releases/download/${release}/${archive}" \
    --output "${temporary}/${archive}"
printf '%s  %s\n' "$checksum" "${temporary}/${archive}" | sha256sum --check --status
tar --extract --gzip --file "${temporary}/${archive}" \
    --directory "$temporary" --no-same-owner github-mcp-server LICENSE
install -D -m 0755 "${temporary}/github-mcp-server" "${prefix}/bin/github-mcp-server"
install -D -m 0644 "${temporary}/LICENSE" \
    "${prefix}/share/licenses/github-mcp-server/LICENSE"
