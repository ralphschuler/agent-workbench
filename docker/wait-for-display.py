"""Wait for X11 and, for the desktop session, the shared session bus."""

import os
from pathlib import Path
import subprocess
import sys
import time


for _attempt in range(150):
    display_ready = subprocess.run(
        ["xdpyinfo", "-display", os.environ["DISPLAY"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    bus_ready = "--bus" not in sys.argv or Path("/tmp/runtime-agent/bus").is_socket()
    if display_ready and bus_ready:
        sys.exit(0)
    time.sleep(0.2)
print("Desktop X11/session bus did not become ready", file=sys.stderr)
sys.exit(1)
