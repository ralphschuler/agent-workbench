#!/bin/sh
set -eu
umask 077

# Individual CLI commands can run without starting or authenticating a desktop.
if [ "${1:-desktop}" != desktop ]; then
    exec "$@"
fi

/opt/agent-venv/bin/python /opt/agent/docker/prepare-desktop.py
/opt/agent-venv/bin/python /opt/agent/scripts/configure-agent.py
exec /usr/bin/supervisord --nodaemon --configuration /etc/supervisord.conf
