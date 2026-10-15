#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
python3 - <<'PY'
import os
from pathlib import Path
import secrets
import stat
import string
secret_directory = Path('.secrets')
if secret_directory.is_symlink():
    raise SystemExit('Refusing symlinked .secrets directory.')
secret_directory.mkdir(mode=0o700, exist_ok=True)
secret_directory.chmod(0o700)
workspace = Path('workspace')
try:
    workspace.mkdir(mode=0o755)
except FileExistsError:
    pass
else:
    workspace.chmod(0o755)
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
    if not stat.S_ISREG(path.lstat().st_mode):
        raise SystemExit('Refusing non-regular .secrets/vnc_password; existing entry was preserved.')
else:
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8)))
        # Local Compose secrets preserve host file ownership. UID 1000 in the
        # container must read this file even when the host UID differs. The
        # private parent directory protects it from other host users.
        os.fchmod(output.fileno(), 0o444)
PY
printf '%s\n' 'Local configuration prepared. Desktop password: .secrets/vnc_password (not printed).'
