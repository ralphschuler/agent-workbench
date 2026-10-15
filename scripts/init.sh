#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
mkdir -p .secrets workspace
python3 - <<'PY'
import os
from pathlib import Path
import secrets
import string
environment = Path('.env')
try:
    descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    template = Path('.env.example').read_text()
    template = template.replace('HINDSIGHT_API_KEY=\n', 'HINDSIGHT_API_KEY=' + secrets.token_urlsafe(32) + '\n')
    with os.fdopen(descriptor, 'w') as output:
        output.write(template)
path = Path('.secrets/vnc_password')
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8)))
PY
printf '%s\n' 'Local configuration prepared. Desktop password: .secrets/vnc_password (not printed).'
