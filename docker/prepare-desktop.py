"""Validate desktop settings and store the VNC secret without argv exposure."""

import os
from pathlib import Path
import re
import subprocess
import sys


def main() -> None:
    for variable in ("DESKTOP_WIDTH", "DESKTOP_HEIGHT"):
        value = os.environ.get(variable, "")
        if not value.isdecimal() or not 640 <= int(value) <= 7680:
            raise ValueError(f"{variable} must be an integer from 640 to 7680")
    if not re.fullmatch(r":[0-9]{1,3}", os.environ.get("DISPLAY", "")):
        raise ValueError("DISPLAY must be a local display such as :99")
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    if runtime != Path("/tmp/runtime-agent"):
        raise ValueError("XDG_RUNTIME_DIR must be /tmp/runtime-agent")
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") != f"unix:path={runtime}/bus":
        raise ValueError("DBUS_SESSION_BUS_ADDRESS must use the desktop runtime bus")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runtime.is_symlink() or runtime.stat().st_uid != os.getuid():
        raise ValueError("Desktop runtime directory must be owned by this user")
    runtime.chmod(0o700)

    secret = Path(os.environ["VNC_PASSWORD_FILE"])
    try:
        password = secret.read_bytes().rstrip(b"\r\n")
    except OSError as error:
        raise ValueError("Mount an 8-character VNC password at VNC_PASSWORD_FILE") from error
    # RFB's classic password scheme truncates after 8 bytes. Reject longer
    # passwords instead of creating a misleading impression of extra entropy.
    if len(password) != 8 or any(byte < 33 or byte > 126 for byte in password):
        raise ValueError("The VNC password must contain exactly 8 printable ASCII characters (no spaces)")
    target = runtime / "vnc.passwd"
    if target.is_symlink():
        raise ValueError("VNC password destination cannot be a symlink")
    # x11vnc's one-argument mode accepts password/confirmation on stdin. Never
    # place the plaintext password in command arguments or logs.
    result = subprocess.run(
        ["x11vnc", "-storepasswd", str(target)],
        input=password + b"\n" + password + b"\ny\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not target.is_file():
        raise ValueError("x11vnc could not prepare desktop authentication")
    target.chmod(0o600)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"Desktop setup failed: {error}", file=sys.stderr)
        sys.exit(1)
