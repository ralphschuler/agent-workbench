"""Backup safety contracts; no real accounts, containers or project data."""

from contextlib import nullcontext, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("workbench_backup", Path(__file__).resolve().parents[1] / "scripts/backup.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = backup.Backup(self.root, {})

    def key(self):
        self.job.password.parent.mkdir(parents=True, exist_ok=True)
        self.job.password.write_text("fixture-recovery-key\n")
        self.job.password.chmod(0o600)

    def test_init_creates_private_key_without_overwriting(self):
        with mock.patch.object(self.job, "helper") as helper:
            self.job.initialize()
        original = self.job.password.read_bytes()
        self.assertEqual(self.job.password.stat().st_mode & 0o777, 0o600)
        self.assertGreater(len(original), 48)
        helper.assert_called_once_with(["init", "--repository-version", "2"])
        (self.job.repository / "config").write_text("encrypted")
        with mock.patch.object(backup.secrets, "token_urlsafe") as generate:
            with self.assertRaises(backup.BackupError):
                self.job.initialize()
            generate.assert_not_called()
        self.assertEqual(self.job.password.read_bytes(), original)

    def test_existing_external_key_is_preserved(self):
        self.key()
        with mock.patch.object(self.job, "helper"), mock.patch.object(backup.secrets, "token_urlsafe") as generate:
            self.job.initialize()
        generate.assert_not_called()
        self.assertEqual(self.job.password.read_text(), "fixture-recovery-key\n")

    def test_missing_existing_repository_key_never_generated(self):
        self.job.repository.mkdir(parents=True)
        (self.job.repository / "config").touch()
        with self.assertRaises(backup.BackupError):
            self.job.initialize()
        self.assertFalse(self.job.password.exists())

    def test_key_and_repository_cannot_be_copied_into_each_other(self):
        for env in ({"BACKUP_PASSWORD_FILE": ".backups/restic/key"}, {"BACKUP_DIR": ".secrets/repository"}):
            with self.subTest(env=env), self.assertRaises(backup.BackupError):
                backup.Backup(self.root, env)

    def test_key_excluded_from_config_including_hardlink_alias(self):
        self.key()
        secret_dir = self.root / ".secrets"
        (secret_dir / "vnc_password").write_text("fixture-private-value")
        os.link(self.job.password, secret_dir / "key-alias")
        (secret_dir / "key-link").symlink_to(self.job.password)
        (self.root / ".env").write_text("PRIVATE=fixture-secret\n")
        target = self.root / "stage"
        self.job.stage_config(target)
        self.assertEqual(sorted(path.name for path in (target / ".secrets").iterdir()), ["vnc_password"])
        self.assertEqual((target / ".env").read_bytes(), (self.root / ".env").read_bytes())
        os.unlink(self.root / ".env")
        os.link(self.job.password, self.root / ".env")
        with self.assertRaises(backup.BackupError):
            self.job.stage_config(self.root / "stage2")

    def test_backup_always_restarts_exact_original_ids(self):
        for failure in (backup.BackupError("fixture failure"), KeyboardInterrupt()):
            calls = []
            def fake(command, **kwargs):
                calls.append(command)
                return mock.Mock(stdout=json.dumps({"Running": False, "ExitCode": 0}) + "\n")
            with self.subTest(failure=type(failure).__name__), mock.patch.object(self.job, "running", return_value=["a" * 64]), \
                    mock.patch.object(backup, "run", side_effect=fake):
                with self.assertRaises(type(failure)):
                    with self.job.stopped():
                        raise failure
            self.assertEqual(calls[0], ["docker", "stop", "--time", "60", "a" * 64])
            self.assertEqual(calls[-1], ["docker", "start", "a" * 64])

    def test_stop_failure_still_restarts_original_ids_and_never_yields(self):
        with mock.patch.object(self.job, "running", return_value=["b" * 64]), \
                mock.patch.object(backup, "run", side_effect=[backup.BackupError("stop failed"), mock.Mock()]) as run:
            with self.assertRaises(backup.BackupError):
                with self.job.stopped():
                    self.fail("Backup must not run after stop failed")
            self.assertEqual(run.call_args_list[-1].args[0], ["docker", "start", "b" * 64])

    def test_forced_kill_refuses_snapshot_but_restarts(self):
        with mock.patch.object(self.job, "running", return_value=["c" * 64]), \
                mock.patch.object(backup, "run", side_effect=[mock.Mock(), mock.Mock(stdout=json.dumps(
                    {"Running": False, "ExitCode": 137})), mock.Mock()]) as run:
            with self.assertRaisesRegex(backup.BackupError, "cleanly"):
                with self.job.stopped():
                    self.fail("Unclean database stop cannot create a cold snapshot")
            self.assertEqual(run.call_args_list[-1].args[0][1], "start")

    def test_helper_has_no_network_socket_or_password_argument_and_cleans_interrupt(self):
        self.key()
        self.job.repository.mkdir(parents=True)
        with mock.patch.object(backup, "run", side_effect=[KeyboardInterrupt(), mock.Mock()]) as run:
            with self.assertRaises(KeyboardInterrupt):
                self.job.helper(["snapshots", "--json"])
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn(backup.IMAGE, command)
        self.assertNotIn("docker.sock", " ".join(command))
        self.assertNotIn("fixture-recovery-key", " ".join(command))
        name = command[command.index("--name") + 1]
        self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "--force", name])

    def test_restic_exit_three_is_failure_even_with_snapshot_summary(self):
        result = mock.Mock(returncode=3, stdout=json.dumps({"message_type": "summary", "snapshot_id": "e" * 64}),
                           stderr="private-fixture-value")
        with mock.patch.object(backup.subprocess, "run", return_value=result):
            with self.assertRaises(backup.BackupError) as error:
                backup.run(["restic", "backup"])
        self.assertNotIn("private-fixture-value", str(error.exception))

    def test_incomplete_backup_never_promoted_to_restorable_tag(self):
        self.key()
        workspace = self.root / "workspace"
        workspace.mkdir()
        with mock.patch.object(self.job, "configuration", return_value=("fixture", {}, workspace)), \
                mock.patch.object(self.job, "stopped", return_value=nullcontext()), \
                mock.patch.object(self.job, "idle_volumes"), \
                mock.patch.object(self.job, "helper", side_effect=backup.BackupError("incomplete")) as helper:
            with self.assertRaises(backup.BackupError):
                self.job.backup()
        command = helper.call_args.args[0]
        self.assertIn(backup.PENDING_TAG, command)
        self.assertNotIn(backup.TAG, command)
        self.assertEqual(helper.call_count, 1)

    def test_completed_backup_promotes_and_returns_rewritten_snapshot_id(self):
        self.key()
        workspace = self.root / "workspace"
        workspace.mkdir()
        outputs = [json.dumps({"message_type": "summary", "snapshot_id": "a" * 64}),
                   json.dumps({"message_type": "changed", "old_snapshot_id": "a" * 64, "new_snapshot_id": "b" * 64}) +
                   "\n" + json.dumps({"message_type": "summary", "changed_snapshots": 1})]
        with mock.patch.object(self.job, "configuration", return_value=("fixture", {}, workspace)), \
                mock.patch.object(self.job, "stopped", return_value=nullcontext()), \
                mock.patch.object(self.job, "idle_volumes"), \
                mock.patch.object(self.job, "helper", side_effect=outputs) as helper, \
                mock.patch("builtins.print") as output:
            self.job.backup()
        self.assertEqual(helper.call_args.args[0], ["tag", "--json", "--add", backup.TAG,
                                                  "--remove", backup.PENDING_TAG, "a" * 64])
        self.assertIn("b" * 64, output.call_args.args[0])

    def test_pending_snapshot_cannot_be_listed_or_selected_as_latest(self):
        self.key()
        snapshots = [{"id": "a" * 64, "tags": [backup.TAG], "time": "2026-01-01T00:00:00Z"},
                     {"id": "b" * 64, "tags": [backup.TAG, backup.PENDING_TAG], "time": "2026-02-01T00:00:00Z"}]
        with mock.patch.object(self.job, "helper", return_value=json.dumps(snapshots)):
            self.assertEqual([item["id"] for item in self.job.snapshots()], ["a" * 64])

    def test_tag_exit_zero_without_changed_record_is_not_success(self):
        self.key()
        workspace = self.root / "workspace"
        workspace.mkdir()
        outputs = [json.dumps({"message_type": "summary", "snapshot_id": "a" * 64}),
                   json.dumps({"message_type": "summary", "changed_snapshots": 0})]
        with mock.patch.object(self.job, "configuration", return_value=("fixture", {}, workspace)), \
                mock.patch.object(self.job, "stopped", return_value=nullcontext()), \
                mock.patch.object(self.job, "idle_volumes"), mock.patch.object(self.job, "helper", side_effect=outputs):
            with self.assertRaisesRegex(backup.BackupError, "completion"):
                self.job.backup()

    def test_recovery_key_inside_workspace_refused_even_with_glob_name(self):
        self.job = backup.Backup(self.root, {"BACKUP_PASSWORD_FILE": "workspace/key[1].txt"})
        self.key()
        with mock.patch.object(self.job, "configuration", return_value=("fixture", {}, self.root / "workspace")), \
                mock.patch.object(self.job, "stopped") as stopped:
            with self.assertRaisesRegex(backup.BackupError, "outside"):
                self.job.backup()
            stopped.assert_not_called()

    def test_restore_refuses_hidden_workspace_data_config_and_volume_data(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        with mock.patch.object(self.job, "running", return_value=[]), \
                mock.patch.object(self.job, "idle_volumes"), \
                mock.patch.object(self.job, "empty_volume", return_value=True):
            (workspace / ".hidden").touch()
            with self.assertRaisesRegex(backup.BackupError, "workspace"):
                self.job.restore_preflight({"agent-home": "fixture-home"}, workspace)
            (workspace / ".hidden").unlink()
            (self.root / ".env").touch()
            with self.assertRaisesRegex(backup.BackupError, "absent"):
                self.job.restore_preflight({}, workspace)
            (self.root / ".env").unlink()
            with mock.patch.object(self.job, "empty_volume", return_value=False):
                with self.assertRaisesRegex(backup.BackupError, "volume data"):
                    self.job.restore_preflight({"agent-home": "fixture-home"}, workspace)

    def test_restore_refuses_running_destination_or_symlink(self):
        workspace = self.root / "workspace"
        workspace.symlink_to(self.root / "missing")
        with mock.patch.object(self.job, "running", return_value=[]), mock.patch.object(self.job, "idle_volumes"):
            with self.assertRaises(backup.BackupError):
                self.job.restore_preflight({}, workspace)
        workspace.unlink()
        with mock.patch.object(self.job, "running", return_value=["d" * 64]):
            with self.assertRaisesRegex(backup.BackupError, "stopped"):
                self.job.restore_preflight({}, workspace)

    def test_snapshot_failure_precedes_destination_activation(self):
        self.key()
        workspace = self.root / "workspace"
        with mock.patch.object(self.job, "snapshots", return_value=[{"id": "f" * 64, "time": "2026-01-01T00:00:00Z"}]), \
                mock.patch.object(self.job, "configuration", return_value=("fixture", {}, workspace)), \
                mock.patch.object(self.job, "restore_preflight") as preflight, \
                mock.patch.object(self.job, "helper", side_effect=[backup.BackupError("wrong key"), ""]) as helper:
            with self.assertRaises(backup.BackupError):
                self.job.restore("latest")
        self.assertEqual(preflight.call_count, 1)
        self.assertFalse(workspace.exists())
        self.assertEqual(helper.call_args_list[0].args[0], ["restore", "f" * 64, "--target", "/staging", "--verify"])

    def test_errors_never_dump_compose_credentials(self):
        stderr = io.StringIO()
        with mock.patch.object(backup, "Backup") as factory, redirect_stderr(stderr):
            factory.side_effect = subprocess.CalledProcessError(1, "docker", output="PRIVATE=do-not-log-this")
            self.assertEqual(backup.main(["backup"]), 1)
        self.assertNotIn("do-not-log-this", stderr.getvalue())

    def test_mount_parser_rejects_comma_injection(self):
        with self.assertRaises(backup.BackupError):
            backup.mount("bind", "/tmp/a,readonly=false", "/destination", True)


if __name__ == "__main__":
    unittest.main()
