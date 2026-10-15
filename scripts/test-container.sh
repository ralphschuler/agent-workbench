#!/usr/bin/env bash
# Isolated end-to-end check; stops and removes only the container it creates.
set -euo pipefail
image="${1:-agent-desktop:dev}"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
secret_dir="$(mktemp -d)"
container_id=""
cleanup() {
  if [[ -n "$container_id" ]]; then docker rm -f -v "$container_id" >/dev/null; fi
  rm -rf "$secret_dir"
}
trap cleanup EXIT
python3 - "$secret_dir/vnc_password" <<'PY'
import os, secrets, string, sys
with open(sys.argv[1], 'x') as output:
    output.write(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8)))
# Docker file mounts are read by container uid 1000, not necessarily the host UID.
os.chmod(sys.argv[1], 0o444)
PY
container_id="$(docker run -d --shm-size=1g --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  --security-opt "seccomp=$repository_root/docker/seccomp-chrome.json" \
  --mount "type=bind,src=$secret_dir/vnc_password,dst=/run/secrets/vnc_password,readonly" \
  "$image")"
for attempt in {1..90}; do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$container_id")"
  if [[ "$status" == healthy ]]; then break; fi
  if [[ "$status" == unhealthy ]] || [[ "$(docker inspect --format '{{.State.Running}}' "$container_id")" != true ]]; then
    docker logs --tail 60 "$container_id"
    exit 1
  fi
  if [[ "$attempt" == 90 ]]; then
    docker logs --tail 60 "$container_id"
    printf '%s\n' 'Desktop did not become healthy.' >&2
    exit 1
  fi
  sleep 2
done
docker exec "$container_id" /opt/agent-venv/bin/python /opt/agent/scripts/smoke.py
