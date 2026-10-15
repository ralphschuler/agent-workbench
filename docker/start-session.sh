#!/bin/sh
set -eu
/opt/agent-venv/bin/python /opt/agent/docker/wait-for-display.py --bus
exec xfce4-session
