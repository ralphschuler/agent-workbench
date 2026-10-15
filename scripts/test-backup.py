#!/usr/bin/env python3
"""Real, isolated restic roundtrip. Only random test projects/fixture data change."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workbench_backup", ROOT / "scripts/backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def command(args, *, cwd=None, env=None, success=True):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if (result.returncode == 0) != success:
        # Outputs can contain fixture secrets; assertions deliberately omit them.
        raise AssertionError("Unexpected exit status for " + args[0] + ": " + str(result.returncode))
    assert "fixture-private" not in result.stdout + result.stderr, "Secret leaked to command output"
    return result.stdout


def fixture(root, name):
    root.mkdir()
    (root / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/backup.py", root / "scripts/backup.py")
    config = {
        "name": name,
        "services": {
            "agent": {"image": backup.IMAGE, "network_mode": "none", "entrypoint": ["/bin/sh"],
                      "command": ["-c", 'trap "exit 0" TERM; while :; do sleep 1; done'],
                      "volumes": ["agent-home:/home/agent", "hindsight-auth:/home/agent/.hindsight-auth", "./workspace:/workspace"]},
            "hindsight": {"image": backup.IMAGE, "network_mode": "none", "entrypoint": ["/bin/sh"],
                          "command": ["-c", 'trap "exit 0" TERM; while :; do sleep 1; done'],
                          "profiles": ["memory"], "volumes": ["hindsight-data:/data", "hindsight-auth:/auth"]},
        },
        "volumes": {role: {} for role in backup.ROLES},
    }
    (root / "compose.yaml").write_text(json.dumps(config))


def main():
    suffix = uuid.uuid4().hex[:10]
    projects = ["workbench-backup-test-" + suffix + "-" + kind for kind in ("source", "target", "tampered")]
    original_compose = {key: value for key, value in os.environ.items() if key.startswith("COMPOSE_")}
    for key in original_compose:
        del os.environ[key]
    # No user Compose overrides, credentials, configuration or backup locations are inherited.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("COMPOSE_", "BACKUP_"))}
    with tempfile.TemporaryDirectory(prefix="workbench-backup-test-") as directory:
        base = Path(directory)
        source, target, tampered = [base / kind for kind in ("source", "target", "tampered")]
        for root, project in zip((source, target, tampered), projects):
            fixture(root, project)
        job = backup.Backup(source, {})
        try:
            job.ensure_image()
            (source / "workspace").mkdir()
            (source / ".env").write_text("PRIVATE=fixture-private-env\n")
            (source / ".env").chmod(0o600)
            (source / ".secrets").mkdir(mode=0o700)
            (source / ".secrets/vnc_password").write_text("fixture-private-vnc")
            (source / ".secrets/vnc_password").chmod(0o600)
            def cli(root, *args, overrides=None, success=True):
                return command([sys.executable, "scripts/backup.py", *args], cwd=root,
                               env=env | (overrides or {}), success=success)
            cli(source, "init")
            print("Restic test: repository initialized.", flush=True)
            recovery = base / "external-recovery-key"
            shutil.copyfile(job.password, recovery)
            recovery.chmod(0o600)
            command(["docker", "compose", "--profile", "memory", "up", "-d", "--pull", "never"], cwd=source, env=env)
            _, volumes, workspace = job.configuration()
            job.helper(["-c", '''
                mkdir -p /home/.config/browser /auth/codex /data/base
                printf fixture-private-browser > /home/.config/browser/profile
                printf fixture-private-oauth > /auth/codex/auth.json
                printf fixture-private-memory > /data/base/database-bytes
                printf fixture-workspace > /workspace/project.txt
                chown -R 1000:1000 /home /auth /data /workspace
                chmod 700 /home/.config /auth/codex /data/base
                chmod 600 /home/.config/browser/profile /auth/codex/auth.json /data/base/database-bytes /workspace/project.txt
            '''], mounts=[("volume", volumes["agent-home"], "/home"),
                          ("volume", volumes["hindsight-auth"], "/auth"),
                          ("volume", volumes["hindsight-data"], "/data"), ("bind", workspace, "/workspace")],
                       shell=True, repository=False)
            before = set(job.running())
            assert len(before) == 2, "Both disposable service containers must run"
            cli(source, "backup")
            print("Restic test: cold snapshot created.", flush=True)
            assert set(job.running()) == before, "Backup must restart the exact original containers"
            snapshots = cli(source, "snapshots").splitlines()
            identifier = snapshots[0].split()[0]
            assert backup.SNAPSHOT_ID.fullmatch(identifier)
            assert (job.repository / "config").stat().st_uid == os.getuid(), "Repository must be host-copyable"
            pending = base / "pending-source"
            pending.mkdir()
            (pending / "unfinished").write_text("fixture-pending")
            job.helper(["backup", "--json", "--host", "agent-workbench", "--tag", backup.PENDING_TAG, "/source"],
                       mounts=[("bind", pending, "/source", True)])
            job.repository_owner()
            assert cli(source, "snapshots").splitlines() == snapshots, "Pending snapshots must not become restore candidates"
            cli(source, "check")
            print("Restic test: repository data verified.", flush=True)
            common = {"BACKUP_DIR": str(job.repository), "BACKUP_PASSWORD_FILE": str(recovery)}
            cli(target, "restore", "--snapshot", "latest", overrides=common)
            print("Restic test: restored into a separate Compose project.", flush=True)
            restored = backup.Backup(target, common)
            _, new_volumes, new_workspace = restored.configuration()
            assert set(volumes.values()).isdisjoint(new_volumes.values()), "Different projects must use different volumes"
            assert (target / ".env").read_bytes() == (source / ".env").read_bytes()
            assert (target / ".env").stat().st_uid == os.getuid()
            assert (target / ".env").stat().st_mode & 0o777 == 0o600
            assert (target / ".secrets").stat().st_uid == os.getuid()
            assert not (target / ".secrets/backup_password").exists(), "Recovery key must not enter snapshots"
            assert (target / ".secrets/vnc_password").read_text() == "fixture-private-vnc"
            restored.helper(["-c", '''
                test "$(cat /home/.config/browser/profile)" = fixture-private-browser
                test "$(cat /auth/codex/auth.json)" = fixture-private-oauth
                test "$(cat /data/base/database-bytes)" = fixture-private-memory
                test "$(stat -c %u /auth/codex/auth.json)" = 1000
                test "$(stat -c %a /auth/codex/auth.json)" = 600
                test "$(stat -c %u /data/base/database-bytes)" = 1000
                test "$(stat -c %u /home)" = 1000
                test "$(stat -c %u /auth)" = 1000
                test "$(stat -c %u /data)" = 1000
                test "$(cat /workspace/project.txt)" = fixture-workspace
                test "$(stat -c %u /workspace)" = 1000
                test "$(stat -c %u /workspace/project.txt)" = 1000
                test "$(stat -c %a /workspace/project.txt)" = 600
            '''], mounts=[("volume", new_volumes["agent-home"], "/home", True),
                          ("volume", new_volumes["hindsight-auth"], "/auth", True),
                          ("volume", new_volumes["hindsight-data"], "/data", True),
                          ("bind", new_workspace, "/workspace", True)], shell=True, repository=False)
            assert not restored.running(), "Restore must not start destination services"
            cli(target, "restore", overrides=common, success=False)
            restored.helper(["-c", 'test "$(cat /workspace/project.txt)" = fixture-workspace'],
                            mounts=[("bind", new_workspace, "/workspace", True)], shell=True, repository=False)
            wrong = base / "wrong-key"
            wrong.write_text("fixture-wrong-key")
            wrong.chmod(0o600)
            cli(source, "snapshots", overrides={"BACKUP_PASSWORD_FILE": str(wrong)}, success=False)
            corrupt = base / "tampered-repository"
            shutil.copytree(job.repository, corrupt)
            packs = [path for path in (corrupt / "data").rglob("*") if path.is_file()]
            assert packs
            for pack in packs:
                pack.chmod(0o600)  # Restic pack files are deliberately read-only.
                with pack.open("r+b") as file:
                    file.seek(pack.stat().st_size // 2)
                    byte = file.read(1)
                    file.seek(-1, 1)
                    file.write(bytes([byte[0] ^ 0xFF]))
            broken = {"BACKUP_DIR": str(corrupt), "BACKUP_PASSWORD_FILE": str(recovery)}
            cli(tampered, "check", overrides=broken, success=False)
            cli(tampered, "restore", "--snapshot", identifier, overrides=broken, success=False)
            assert not (tampered / ".env").exists() and not (tampered / ".secrets").exists()
            assert not (tampered / "workspace").exists(), "Tampering must fail before activation"
            print("Restic roundtrip passed: cold restart, encrypted restore, UID/mode, config, wrong key, tamper and overwrite refusal.")
        finally:
            for root, project in zip((source, target, tampered), projects):
                command(["docker", "compose", "--project-name", project, "--profile", "memory", "down", "--volumes"], cwd=root, env=env)
            # Root-owned arbitrary source metadata is deliberately retained by restore.
            job.helper(["-c", 'chown -hR "$1:$2" /fixture', "sh", str(os.getuid()), str(os.getgid())],
                       mounts=[("bind", base, "/fixture")], shell=True, repository=False)
            os.environ.update(original_compose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
