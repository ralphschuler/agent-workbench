#!/usr/bin/env python3
"""Initialize/check a dedicated Hindsight Codex login, or start device login.

Run on the Docker host after `scripts/init.sh` and building the agent image.
Credentials stay in a writable named volume shared only by agent and Hindsight.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
AGENT_AUTH_HOME = "/home/agent/.hindsight-auth/codex"

INIT = """
import os
from pathlib import Path
path = Path('/auth/codex')
if path.is_symlink():
    raise SystemExit('Refusing a symlink as the dedicated authentication directory.')
path.mkdir(mode=0o700, exist_ok=True)
os.chmod(path, 0o700)
os.chown(path, 1000, 1000)
"""

CHECK = """
import json
from pathlib import Path
path = Path('/auth/codex/auth.json')
try:
    data = json.loads(path.read_text())
    tokens = data.get('tokens') or {}
    ready = data.get('auth_mode') == 'chatgpt' and all(
        isinstance(tokens.get(name), str) and bool(tokens[name])
        for name in ('access_token', 'refresh_token', 'account_id')
    )
except (OSError, ValueError, AttributeError, TypeError):
    ready = False
if not ready:
    raise SystemExit('Dedicated Codex ChatGPT login is missing or incomplete. Run hindsight-login.py login.')
print('Dedicated Codex ChatGPT login is configured; no inference was attempted.')
"""


def configuration():
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"], cwd=ROOT,
        check=True, capture_output=True, text=True, timeout=30,
    )
    config = json.loads(result.stdout)
    image = config["services"]["agent"]["image"]
    volume = config["volumes"]["hindsight-auth"]["name"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
        raise ValueError("invalid named volume")
    subprocess.run(["docker", "image", "inspect", image], check=True, capture_output=True, timeout=30)
    return image, volume, config["name"]


def ensure_volume(volume, project):
    result = subprocess.run(["docker", "volume", "inspect", volume],
                            capture_output=True, timeout=30)
    if result.returncode:
        subprocess.run(["docker", "volume", "create", "--label",
                        "com.docker.compose.project=" + project, "--label",
                        "com.docker.compose.volume=hindsight-auth", volume],
                       check=True, capture_output=True, timeout=30)


def run_container(command, timeout, *, compose=False):
    name = "agent-hindsight-auth-" + uuid.uuid4().hex[:12]
    position = command.index("--rm") + 1
    command = command[:position] + ["--name", name] + command[position:]
    try:
        subprocess.run(command, cwd=ROOT if compose else None, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
        try:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        raise


def volume_command(image, volume, action):
    command = ["docker", "run", "--pull", "never", "--rm", "--network", "none",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
    if action == "init":
        command += ["--user", "0:0", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
                    "--cap-add", "DAC_OVERRIDE"]
        mount = f"type=volume,source={volume},target=/auth"
    else:
        command += ["--user", "1000:1000"]
        mount = f"type=volume,source={volume},target=/auth,readonly"
    return command + ["--mount", mount, "--entrypoint", "python3", image,
                      "-c", INIT if action == "init" else CHECK]


def login_command():
    return ["docker", "compose", "run", "--rm", "--no-deps", "--user", "1000:1000",
            "--env", "CODEX_HOME=" + AGENT_AUTH_HOME, "--entrypoint", "codex", "agent",
            "-c", 'cli_auth_credentials_store="file"', "login", "--device-auth"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["login", "check", "init"], default="login")
    args = parser.parse_args(argv)
    try:
        image, volume, project = configuration()
        ensure_volume(volume, project)
        if args.action in {"init", "login"}:
            run_container(volume_command(image, volume, "init"), timeout=180)
        if args.action == "init":
            print("Dedicated authentication directory initialized as UID/GID1000; no login performed.")
            return 0
        if args.action == "login":
            run_container(login_command(), timeout=900, compose=True)
        run_container(volume_command(image, volume, "check"), timeout=180)
        return 0
    except KeyboardInterrupt:
        print("Hindsight login setup cancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Compose output contains runtime environment values; never dump it.
        print("Hindsight login setup failed. Run scripts/init.sh, build the agent image, "
              "and check Docker access and the login result.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
