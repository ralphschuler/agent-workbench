# Desktop container and Chrome sandbox

The image runs XFCE, Xvfb, a shared D-Bus session, x11vnc, and noVNC as uid
1000. `agent-browser` starts Chrome with accessibility enabled for Cua Driver.
The desktop and agents share `DISPLAY=:99` and
`DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/runtime-agent/bus`.

On Linux, the bind-mounted workspace must be writable by UID 1000. The default
setup assumes the host developer also has UID 1000. If your UID differs,
arrange ownership or a narrowly scoped ACL for UID 1000 on `workspace/` before
starting. For a fresh, empty workspace, an administrator can use this
non-recursive ownership change:

```sh
docker compose run --rm --user 0 --cap-add CHOWN --entrypoint chown agent 1000:1000 /workspace
```

This changes only the workspace directory's owner. Review ownership separately
for an existing workspace; initialization does not change its contents or
permissions. New workspace directories use mode 0755 without granting other
users write access. Named home volumes are initialized from the image with the
correct ownership.

`scripts/init.sh` creates `.secrets/` with mode 0700 and its VNC password file
with mode 0444. The private directory prevents other host users from reading
the password; the readable file lets the Docker-mounted secret work for UID
1000 even when the host UID differs. Keep the directory private and do not move
the file into a shared directory. Existing password files are preserved. If an
older mode-0600 password prevents startup, check that both paths are regular
entries rather than symlinks, then apply:

```sh
test -d .secrets && test ! -L .secrets && \
  test -f .secrets/vnc_password && test ! -L .secrets/vnc_password && \
  chmod 0700 .secrets && chmod 0444 .secrets/vnc_password
```

VNC requires a mounted password file containing exactly eight printable ASCII
characters without spaces. A trailing newline is accepted. Classic VNC
uses only eight password bytes; longer inputs are rejected. The launcher sends
the password to x11vnc through stdin and writes its VNC authentication file with
mode 0600. Compose binds noVNC to the host's loopback interface; use an SSH
tunnel when accessing a remote host. VNC authentication does not encrypt the
connection, so do not expose port 6080 directly to the Internet.

## Chrome sandbox

Chrome's sandbox remains enabled. Docker's default seccomp policy blocks the
user namespace system calls Chrome needs. Compose therefore uses the vendored
`docker/seccomp-chrome.json`, adapted from Microsoft's official Playwright
profile. Its source, license, checksum, and two local changes are recorded in
[the third-party notices](../docker/third-party-notices.md).

The equivalent runtime options, from the repository root, are:

```sh
docker run --rm --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=./docker/seccomp-chrome.json \
  IMAGE agent-browser --headless --dump-dom about:blank
```

The profile permits user namespace creation while retaining an allowlist of
system calls. Chrome additionally creates its own namespace and seccomp
sandboxes. No `SYS_ADMIN` capability or privileged container is needed. The
host kernel must support unprivileged user namespaces; the container cannot
enable a feature or permission the host denies.

Ubuntu 23.10 and newer can also restrict user namespaces through host AppArmor.
When the host denies them, Chrome reports `Operation not permitted` or `No
usable sandbox`. A host administrator must allow `userns` in the AppArmor policy
applied to this container and select that loaded profile with Docker's
`--security-opt apparmor=PROFILE`. Keep its other confinement rules. Changing
files inside the image does not change host AppArmor policy. The repository
does not disable host restrictions or use an unconfined profile automatically.

Check host audit logs to distinguish an AppArmor denial from a seccomp or kernel
limitation. Chromium documents Ubuntu's restrictions here:
<https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md>.

For disposable debugging against trusted local content, explicitly setting
`CHROME_DISABLE_SANDBOX=1` makes `agent-browser` pass `--no-sandbox` and prints a
warning. This removes a critical browser protection. It is unsuitable for
browsing untrusted pages and is never enabled automatically.

## Builds through a trusted HTTPS proxy

The image normally uses public system certificate authorities. For a cloud or
enterprise proxy that uses an additional trusted CA, pass the host's complete
trusted PEM bundle as an optional BuildKit secret:

```sh
docker build --platform linux/amd64 \
  --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
  --secret id=build_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -t agent-desktop:dev .
```

The proxy arguments use existing exported variables. `build_ca` is mounted only
for network installation steps. Curl, apt, Node/npm and pip use that bundle;
the temporary apt configuration is removed before committing the layer. The CA
bundle and proxy settings are not embedded in the image. TLS, apt signatures,
the Google signing-key fingerprint and dependency hashes stay enabled.

Runtime proxy access needs its own proxy environment and CA mount if your host
requires them. A build secret does not configure runtime trust. Mount the PEM
bundle read-only, set `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and
`NODE_EXTRA_CA_CERTS` to its container path, and pass the required proxy variables
through a local Compose override. Keep that host-specific override outside the
published image and out of source control.
