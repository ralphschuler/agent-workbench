#!/usr/bin/env python3
"""Encrypted, cold backups of this single-host Compose project using restic."""

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510"
TAG = "agent-workbench-v1"
PENDING_TAG = "agent-workbench-pending-v1"
ROLES = ("agent-home", "hindsight-data", "hindsight-auth")
SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}")


class BackupError(Exception):
    """A safe error message, containing no command output or credentials."""


def run(command, *, cwd=None, timeout=3600, check=True):
    # Compose and restic errors can contain environment values/file contents.
    # Capture everything; callers only print selected, validated metadata.
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise BackupError("Command failed; check Docker access, the recovery key and available disk space.")
    return result


def mount(kind, source, target, readonly=False):
    source = str(source)
    if any(char in source for char in (",", "\n", "\r")):
        raise BackupError("Mount paths must not contain commas or line breaks.")
    return ["--mount", f"type={kind},source={source},target={target}" + (",readonly" if readonly else "")]


class Backup:
    def __init__(self, root=ROOT, environ=None):
        self.root = Path(root).resolve()
        env = os.environ if environ is None else environ
        self.repository = self.path(env.get("BACKUP_DIR") or ".backups/restic")
        self.password = self.path(env.get("BACKUP_PASSWORD_FILE") or ".secrets/backup_password")
        if self.repository.is_symlink():
            raise BackupError("Repository root must not be a symlink.")
        if self.password.resolve().is_relative_to(self.repository.resolve()) or \
                self.repository.resolve().is_relative_to((self.root / ".secrets").resolve()):
            raise BackupError("Keep the repository outside .secrets and the recovery key outside the repository.")

    def path(self, value):
        path = Path(value).expanduser()
        return Path(os.path.abspath(self.root / path if not path.is_absolute() else path))

    @contextmanager
    def lock(self):
        self.repository.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A repository-level lock also serializes stop/restart across projects.
        canonical = self.repository.resolve()
        with (canonical.parent / ("." + canonical.name + ".workbench.lock")).open("a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackupError("Another backup operation is running for this repository.") from None
            yield

    def ensure_image(self):
        if run(["docker", "image", "inspect", IMAGE], check=False, timeout=30).returncode:
            run(["docker", "pull", "--platform", "linux/amd64", IMAGE], timeout=300)

    def key_ready(self):
        if self.password.is_symlink() or not self.password.is_file():
            raise BackupError("Recovery key missing. Supply BACKUP_PASSWORD_FILE; existing repositories never get a new key.")
        if self.password.stat().st_mode & 0o077 or not self.password.stat().st_size:
            raise BackupError("Recovery key must be nonempty and readable only by its owner (chmod 600).")

    def helper(self, args, *, mounts=(), shell=False, repository=True, timeout=3600):
        name = "workbench-backup-" + uuid.uuid4().hex[:12]
        command = ["docker", "run", "--rm", "--name", name, "--pull", "never",
                   "--platform", "linux/amd64", "--network", "none",
                   "--security-opt", "no-new-privileges:true", "--read-only",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m"]
        if repository:
            command += mount("bind", self.repository, "/repository")
            command += mount("bind", self.password, "/run/secrets/restic-password", True)
            command += ["--env", "RESTIC_REPOSITORY=/repository", "--env",
                        "RESTIC_PASSWORD_FILE=/run/secrets/restic-password"]
        for item in mounts:
            command += mount(*item)
        command += ["--entrypoint", "/bin/sh"] if shell else []
        command += [IMAGE] + (args if shell else ["--no-cache"] + args)
        try:
            return run(command, timeout=timeout).stdout
        finally:
            # Also covers interruption: the Docker client exiting need not stop its container.
            with ignore_interrupts():
                run(["docker", "rm", "--force", name], check=False, timeout=30)

    def initialize(self):
        if self.repository.is_symlink() or (self.repository.exists() and
                (not self.repository.is_dir() or any(self.repository.iterdir()))):
            raise BackupError("Repository already exists or is not empty; init never replaces a key or repository.")
        self.repository.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.password.exists():
            self.password.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with os.fdopen(os.open(self.password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
                file.write(secrets.token_urlsafe(48) + "\n")
        self.key_ready()
        self.helper(["init", "--repository-version", "2"])
        print("Encrypted repository initialized. Keep a separate, secure copy of the recovery key.")

    def repository_owner(self):
        # Restic needs root to preserve the container UIDs, but encrypted archive
        # files should remain copyable by the invoking host user.
        if self.repository.is_dir() and not self.repository.is_symlink():
            self.helper(["-c", 'chown -hR "$1:$2" /repository', "sh", str(os.getuid()), str(os.getgid())],
                        mounts=[("bind", self.repository, "/repository")], shell=True, repository=False)

    def configuration(self):
        config = json.loads(run(["docker", "compose", "--profile", "memory", "config", "--format", "json"],
                                cwd=self.root, timeout=30).stdout)
        volumes = {role: config["volumes"][role]["name"] for role in ROLES}
        if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) for name in volumes.values()):
            raise BackupError("Invalid Compose volume name.")
        workspaces = [v for v in config["services"]["agent"]["volumes"] if v.get("target") == "/workspace"]
        if len(workspaces) != 1 or workspaces[0].get("type") != "bind":
            raise BackupError("Expected one bind-mounted /workspace in the agent service.")
        workspace = Path(workspaces[0]["source"])
        if not workspace.is_absolute() or workspace.is_symlink():
            raise BackupError("Workspace must be an absolute directory without a symlink at its root.")
        return config["name"], volumes, workspace

    def running(self):
        output = run(["docker", "compose", "--profile", "memory", "ps", "--status", "running",
                      "--quiet", "agent", "hindsight"], cwd=self.root, timeout=30).stdout
        identifiers = output.split()
        if not all(re.fullmatch(r"[0-9a-f]{12,64}", item) for item in identifiers):
            raise BackupError("Invalid running container identifiers.")
        return identifiers

    @contextmanager
    def stopped(self):
        original = self.running()
        try:
            if original:
                run(["docker", "stop", "--time", "60"] + original, timeout=90)
                states = [json.loads(line) for line in run(
                    ["docker", "inspect", "--format", "{{json .State}}"] + original,
                    timeout=30).stdout.splitlines()]
                if any(state["Running"] or state.get("OOMKilled") or
                       state.get("ExitCode") not in (0, 143) for state in states):
                    raise BackupError("Services did not stop cleanly; no cold snapshot was taken.")
            yield
        finally:
            if original:
                with ignore_interrupts():
                    try:
                        run(["docker", "start"] + original, timeout=90)
                    except (OSError, subprocess.SubprocessError, BackupError):
                        raise BackupError("Could not restart every originally running container. Inspect docker compose ps and restart them.") from None

    def volume_exists(self, name):
        return run(["docker", "volume", "inspect", name], check=False, timeout=30).returncode == 0

    def idle_volumes(self, names):
        for name in names:
            if run(["docker", "ps", "--quiet", "--filter", "volume=" + name], timeout=30).stdout.strip():
                raise BackupError("A container is still using a backup/restore volume. Stop other writers first.")

    def stage_config(self, target):
        target.mkdir(mode=0o700)
        envfile, secret_dir = self.root / ".env", self.root / ".secrets"
        if envfile.is_symlink() or secret_dir.is_symlink():
            raise BackupError("Configuration roots must not be symlinks.")
        if envfile.exists():
            if envfile.resolve() == self.password.resolve() or os.path.samefile(envfile, self.password):
                raise BackupError("The recovery key cannot also be .env.")
            shutil.copy2(envfile, target / ".env")
        if secret_dir.exists():
            def exclude(directory, names):
                return [name for name in names if (Path(directory) / name).resolve() in
                        {self.password.resolve(), self.repository.resolve()}
                        or ((Path(directory) / name).is_file() and os.path.samefile(Path(directory) / name, self.password))]
            shutil.copytree(secret_dir, target / ".secrets", symlinks=True, ignore=exclude)

    def backup(self):
        self.key_ready()
        project, volumes, workspace = self.configuration()
        if not workspace.is_dir():
            raise BackupError("Workspace is missing; initialize the workbench first.")
        if self.repository.resolve().is_relative_to(workspace.resolve()) or \
                workspace.resolve().is_relative_to(self.repository.resolve()) or \
                self.password.resolve().is_relative_to(workspace.resolve()):
            raise BackupError("Keep the repository and recovery key outside the workspace, without overlapping directories.")
        with self.stopped(), tempfile.TemporaryDirectory(prefix="workbench-backup-") as directory:
            temporary = Path(directory)
            config = temporary / "config"
            self.stage_config(config)
            present = [role for role, name in volumes.items() if self.volume_exists(name)]
            self.idle_volumes(volumes[role] for role in present)
            manifest = {"version": 1, "volumes": present}
            (config / "backup-manifest.json").write_text(json.dumps(manifest))
            mounts = [("bind", config, "/source/config", True), ("bind", workspace, "/source/workspace", True)]
            mounts += [("volume", volumes[role], "/source/" + role, True) for role in present]
            args = ["backup", "--json", "--host", "agent-workbench", "--tag", PENDING_TAG]
            output = self.helper(args + ["/source"], mounts=mounts)
            summaries = [item for item in (json.loads(line) for line in output.splitlines())
                         if item.get("message_type") == "summary"]
            identifier = summaries[-1].get("snapshot_id", "") if summaries else ""
            if not SNAPSHOT_ID.fullmatch(identifier):
                raise BackupError("Restic did not report a complete snapshot.")
            # Exit3 can publish an incomplete snapshot. Only an exit0 backup is
            # promoted to the completed tag used by snapshots/restore latest.
            promoted = [json.loads(line) for line in self.helper(
                ["tag", "--json", "--add", TAG, "--remove", PENDING_TAG, identifier]).splitlines()]
            changed = [item for item in promoted if item.get("message_type") == "changed"]
            summaries = [item for item in promoted if item.get("message_type") == "summary"]
            if len(changed) != 1 or changed[0].get("old_snapshot_id") != identifier or \
                    not SNAPSHOT_ID.fullmatch(changed[0].get("new_snapshot_id", "")) or \
                    len(summaries) != 1 or summaries[0].get("changed_snapshots") != 1:
                raise BackupError("Snapshot completion could not be confirmed; pending snapshots are not restored.")
            identifier = changed[0]["new_snapshot_id"]
        print("Snapshot " + identifier + " created; originally running services restarted.")

    def snapshots(self):
        self.key_ready()
        snapshots = json.loads(self.helper(["snapshots", "--json", "--tag", TAG, "--host", "agent-workbench"]))
        if not isinstance(snapshots, list) or any(not SNAPSHOT_ID.fullmatch(item.get("id", "")) for item in snapshots):
            raise BackupError("Invalid snapshot metadata.")
        return [item for item in snapshots if PENDING_TAG not in item.get("tags", [])]

    def check(self):
        self.key_ready()
        self.helper(["check", "--read-data"])
        print("Repository integrity and encrypted data verified.")

    def empty_volume(self, name):
        if not self.volume_exists(name):
            return True
        output = self.helper(["-c", 'find /destination -mindepth 1 -maxdepth 1 -print -quit'],
                             mounts=[("volume", name, "/destination", True)], shell=True, repository=False)
        return not output.strip()

    def restore_preflight(self, volumes, workspace):
        if self.running():
            raise BackupError("Restore requires stopped destination services.")
        self.idle_volumes(volumes.values())
        if any((self.root / path).exists() or (self.root / path).is_symlink() for path in (".env", ".secrets")):
            raise BackupError("Restore requires absent .env and .secrets. Keep the recovery key outside this checkout.")
        if workspace.is_symlink() or (workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir()))):
            raise BackupError("Restore refuses a nonempty workspace.")
        if any(not self.empty_volume(name) for name in volumes.values()):
            raise BackupError("Restore refuses existing volume data. Use an empty Compose project.")

    @contextmanager
    def staging(self):
        with tempfile.TemporaryDirectory(prefix="workbench-restore-") as directory:
            stage = Path(directory)
            try:
                yield stage
            finally:
                # Restored source trees retain arbitrary original UIDs/modes.
                with ignore_interrupts():
                    self.helper(["-c", 'find /staging -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'],
                                mounts=[("bind", stage, "/staging")], shell=True, repository=False)

    def restore(self, snapshot):
        self.key_ready()
        candidates = self.snapshots()
        if snapshot == "latest":
            if not candidates:
                raise BackupError("No Agent Workbench snapshot exists.")
            snapshot = max(candidates, key=lambda item: datetime.fromisoformat(item["time"].replace("Z", "+00:00")))["id"]
        if not SNAPSHOT_ID.fullmatch(snapshot) or snapshot not in {item["id"] for item in candidates}:
            raise BackupError("Select a full snapshot ID from snapshots, or latest.")
        project, volumes, workspace = self.configuration()
        self.restore_preflight(volumes, workspace)
        with self.staging() as stage:
            self.helper(["restore", snapshot, "--target", "/staging", "--verify"],
                        mounts=[("bind", stage, "/staging")])
            source_root = stage / "source"
            config = source_root / "config"
            if any(path.is_symlink() or not path.is_dir() for path in (source_root, config)):
                raise BackupError("Missing or invalid restored configuration directory.")
            # Only host configuration changes owner across hosts. Volume/workspace owners are restored unchanged.
            self.helper(["-c", 'chown -hR "$1:$2" /staging/source/config', "sh", str(os.getuid()), str(os.getgid())],
                        mounts=[("bind", stage, "/staging")], shell=True, repository=False)
            manifest = json.loads((config / "backup-manifest.json").read_text())
            if manifest.get("version") != 1 or not isinstance(manifest.get("volumes"), list) or \
                    not set(manifest["volumes"]).issubset(ROLES):
                raise BackupError("Unsupported backup manifest.")
            if any(path.name not in {".env", ".secrets", "backup-manifest.json"} or path.is_symlink()
                   for path in config.iterdir()):
                raise BackupError("Unexpected restored configuration.")
            if source_root.is_symlink() or not source_root.is_dir() or any(
                    (source_root / role).is_symlink() or not (source_root / role).is_dir()
                    for role in manifest["volumes"] + ["workspace", "config"]):
                raise BackupError("Missing or invalid restored source directories.")
            for name, expected_directory in ((".env", False), (".secrets", True)):
                path = config / name
                if path.exists() and not (path.is_dir() if expected_directory else path.is_file()):
                    raise BackupError("Invalid restored configuration file type.")
            # Authentication/content verification finishes before any user destination is written.
            self.restore_preflight(volumes, workspace)
            for role in manifest["volumes"]:
                name = volumes[role]
                self.idle_volumes([name])
                if not self.empty_volume(name):
                    raise BackupError("Destination volume became nonempty; restore stopped without overwriting it.")
                if not self.volume_exists(name):
                    run(["docker", "volume", "create", "--label", "com.docker.compose.project=" + project,
                         "--label", "com.docker.compose.volume=" + role, name], timeout=30)
                # Keep the directory node itself: snapshot:/subtree discards
                # its root UID/mode. Writable tmpfs parents let native restic
                # restore all supported metadata on the destination mount root.
                self.helper(["restore", snapshot, "--target", "/tmp/restore", "--include", "/source/" + role, "--verify"],
                            mounts=[("volume", name, "/tmp/restore/source/" + role)])
            workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
            if workspace.is_symlink() or any(workspace.iterdir()):
                raise BackupError("Workspace became nonempty; restore stopped without overwriting it.")
            self.helper(["restore", snapshot, "--target", "/tmp/restore", "--include", "/source/workspace", "--verify"],
                        mounts=[("bind", workspace, "/tmp/restore/source/workspace")])
            for name in (".env", ".secrets"):
                source = config / name
                if source.exists():
                    # Stage may be on another filesystem; only destination paths proven absent are copied.
                    if source.is_dir():
                        destination = self.root / name
                        destination.mkdir(mode=0o700)
                        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
                        destination.chmod(0o700)
                    else:
                        with (self.root / name).open("xb") as file:
                            os.chmod(file.fileno(), 0o600)
                            file.write(source.read_bytes())
        print("Snapshot " + snapshot + " restored. Services remain stopped; review configuration before starting.")


@contextmanager
def ignore_interrupts():
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "backup", "snapshots", "check", "restore"])
    parser.add_argument("--snapshot", default="latest")
    args = parser.parse_args(argv)
    old_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        backup = Backup()
        with backup.lock():
            backup.ensure_image()
            try:
                if args.action == "restore":
                    backup.restore(args.snapshot)
                elif args.action == "snapshots":
                    for item in backup.snapshots():
                        print(item["id"] + " " + str(item.get("time", "")))
                else:
                    getattr(backup, "initialize" if args.action == "init" else args.action)()
            finally:
                with ignore_interrupts():
                    backup.repository_owner()
        return 0
    except KeyboardInterrupt:
        print("Backup operation cancelled; originally running services were restarted where applicable.", file=sys.stderr)
        return 130
    except BackupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Backup operation failed. Check Docker, disk space, Compose configuration and recovery key; "
              "no credentials or command output are logged. Restore never starts services.", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
