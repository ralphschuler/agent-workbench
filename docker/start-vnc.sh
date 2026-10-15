#!/bin/sh
set -eu
/opt/agent-venv/bin/python /opt/agent/docker/wait-for-display.py
exec x11vnc -display "$DISPLAY" -rfbauth "$XDG_RUNTIME_DIR/vnc.passwd" \
    -rfbport 5900 -localhost -forever -shared -noxdamage -xkb
