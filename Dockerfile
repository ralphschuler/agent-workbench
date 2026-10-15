# syntax=docker/dockerfile:1.7
FROM node:24-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS node

FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS desktop-base
ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Put small inputs before the large install layer. This also keeps VFS-based
# development Docker engines from copying the full desktop at every install.
COPY --from=node /usr/local/ /usr/local/
COPY --chmod=0644 package.json package-lock.json /opt/agent-tools/
COPY --chmod=0644 requirements.txt /opt/agent/requirements.txt
COPY --chmod=0755 docker/with-build-ca.sh /usr/local/bin/with-build-ca
WORKDIR /workspace

RUN --mount=type=secret,id=build_ca,mode=0444 <<'INSTALL'
set -eu
with-build-ca bash -e -o pipefail <<'DEPENDENCIES'
# Google Chrome's packaged desktop is intentionally linux/amd64.
test "${TARGETARCH:-amd64}" = amd64
apt-get update
apt-get install --no-install-recommends -y \
    at-spi2-core ca-certificates curl dbus-x11 fonts-dejavu-core \
    fonts-liberation git gnupg libgail-common libxi6 libxkbcommon0 \
    novnc procps python3 python3-venv scrot supervisor tini \
    websockify x11-utils x11vnc xauth xdg-utils xdotool \
    xfce4-appfinder xfce4-panel xfce4-session xfce4-settings \
    xfdesktop4 xfwm4 xterm xvfb

# Verify Google's key, then let apt verify the repository and package signatures.
curl --fail --silent --show-error --location \
    --connect-timeout 15 --max-time 120 --retry 2 \
    https://dl.google.com/linux/linux_signing_key.pub \
    --output /tmp/google-linux-signing-key.asc
gpg --batch --show-keys --with-colons /tmp/google-linux-signing-key.asc \
    | awk -F: '$1 == "fpr" {print $10}' \
    | grep -Fx 'EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796'
gpg --batch --dearmor --output /usr/share/keyrings/google-chrome.gpg \
    /tmp/google-linux-signing-key.asc
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' \
    > /etc/apt/sources.list.d/google-chrome.list
apt-get update
apt-get install --no-install-recommends -y google-chrome-stable
rm -f /tmp/google-linux-signing-key.asc
rm -rf /var/lib/apt/lists/*

cd /opt/agent-tools
npm ci --omit=dev --no-audit --no-fund
npm cache clean --force
python3 -m venv /opt/agent-venv
/opt/agent-venv/bin/python -m pip install --no-cache-dir \
    --require-hashes -r /opt/agent/requirements.txt

# Reuse Ubuntu's UID 1000 without granting the runtime account sudo access.
if id ubuntu > /dev/null 2>&1; then
    usermod --login agent --home /home/agent --move-home ubuntu
    groupmod --new-name agent ubuntu
else
    useradd --uid 1000 --user-group --create-home --shell /bin/bash agent
fi
usermod --lock agent
chown agent:agent /workspace
ln -sf vnc.html /usr/share/novnc/index.html
DEPENDENCIES
INSTALL

# Keep GitHub tooling separate so updates do not reinstall the desktop or CLIs.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55 AS github-tools
ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY --chmod=0755 docker/with-build-ca.sh /usr/local/bin/with-build-ca
COPY --chmod=0755 docker/install-github-mcp.sh /usr/local/bin/install-github-mcp
RUN --mount=type=secret,id=build_ca,mode=0444 <<'INSTALL_GITHUB'
set -eu
with-build-ca bash -e -o pipefail <<'GITHUB_TOOLS'
test "${TARGETARCH:-amd64}" = amd64
apt-get update
apt-get install --no-install-recommends -y ca-certificates curl gnupg tar

# Official GitHub CLI keyring checksum and active signing-key fingerprint:
# https://github.com/cli/cli/blob/trunk/docs/install_linux.md
curl --fail --silent --show-error --location \
    --connect-timeout 15 --max-time 120 --retry 2 \
    https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    --output /usr/share/keyrings/githubcli-archive-keyring.gpg
echo '6084d5d7bd8e288441e0e94fc6275570895da18e6751f70f057485dc2d1a811b  /usr/share/keyrings/githubcli-archive-keyring.gpg' \
    | sha256sum --check -
gpg --batch --show-keys --with-colons /usr/share/keyrings/githubcli-archive-keyring.gpg \
    | awk -F: '$1 == "fpr" {print $10}' \
    | grep -Fx '7F38BBB59D064DBCB3D84D725612B36462313325'
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main' \
    > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install --no-install-recommends -y gh
install-github-mcp
gh --version
github-mcp-server --version
install -D -m 755 /usr/bin/gh /output/usr/local/bin/gh
install -D -m 755 /usr/local/bin/github-mcp-server /output/usr/local/bin/github-mcp-server
install -D -m 644 /usr/local/share/licenses/github-mcp-server/LICENSE \
    /output/usr/local/share/licenses/github-mcp-server/LICENSE
rm -rf /var/lib/apt/lists/*
GITHUB_TOOLS
INSTALL_GITHUB

FROM scratch AS runtime-assets
# Context is an allowlist; credentials and workspaces cannot enter this COPY.
COPY . /opt/agent/
COPY --from=github-tools /output/ /

FROM desktop-base AS runtime
# Merge runtime assets once to avoid full-desktop snapshots for each small COPY
# when a development Docker daemon uses the VFS storage driver.
COPY --from=runtime-assets / /
# The Cua Python launcher chmods its binary on every invocation. Use the pinned
# native executable directly so the runtime user can keep installed tools read-only.
RUN chmod a+rx /opt/agent-tools \
    && chmod -R a+rX /opt/agent \
    && chmod 755 /opt/agent/docker/entrypoint.sh /opt/agent/docker/start-session.sh \
       /opt/agent/docker/start-vnc.sh /opt/agent/docker/agent-browser \
    && ln -s /opt/agent/docker/agent-browser /usr/local/bin/agent-browser \
    && ln -s "$(/opt/agent-venv/bin/python -c 'from cua_driver.wrapper import get_binary_path; print(get_binary_path())')" \
       /usr/local/bin/cua-driver \
    && printf '\nXTerm*renderFont: true\nXTerm*faceName: DejaVu Sans Mono\nXTerm*faceSize: 11\n' \
       >> /etc/X11/app-defaults/XTerm \
    && install -m 644 /opt/agent/docker/agent-browser.desktop \
       /usr/share/applications/agent-browser.desktop \
    && install -D -m 644 /opt/agent/docker/LICENSE.github-cli \
       /usr/local/share/licenses/github-cli/LICENSE \
    && sed -i 's|^Exec=/usr/bin/google-chrome-stable|Exec=agent-browser|' \
       /usr/share/applications/google-chrome.desktop \
    && install -m 644 /opt/agent/docker/supervisord.conf /etc/supervisord.conf

ENV HOME=/home/agent \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/opt/agent-tools/node_modules/.bin:/usr/local/bin:/opt/agent-venv/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    DISPLAY=:99 \
    DESKTOP_WIDTH=1440 \
    DESKTOP_HEIGHT=900 \
    XDG_RUNTIME_DIR=/tmp/runtime-agent \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/runtime-agent/bus \
    VNC_PASSWORD_FILE=/run/secrets/vnc_password \
    BROWSER=agent-browser \
    GTK_MODULES=gail:atk-bridge \
    QT_ACCESSIBILITY=1 \
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 \
    NO_AT_BRIDGE=0 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

USER agent
EXPOSE 6080

HEALTHCHECK --interval=15s --timeout=10s --start-period=60s --retries=4 \
    CMD ["/opt/agent-venv/bin/python", "/opt/agent/scripts/healthcheck.py"]

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/agent/docker/entrypoint.sh"]
CMD ["desktop"]

LABEL org.opencontainers.image.title="Agent Workbench" \
      org.opencontainers.image.description="Ubuntu desktop with coding agents, GitHub tools, CUA and optional memory" \
      org.opencontainers.image.source="https://github.com/ralphschuler/agent-workbench"
