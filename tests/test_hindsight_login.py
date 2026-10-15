"""Check dedicated authentication boundaries without any account or model call."""

from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "hindsight_login", Path(__file__).resolve().parents[1] / "scripts" / "hindsight-login.py")
login = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(login)


class HindsightLoginTests(unittest.TestCase):
    def test_login_uses_dedicated_file_store(self):
        command = login.login_command()
        self.assertIn("CODEX_HOME=/home/agent/.hindsight-auth/codex", command)
        self.assertEqual(command[-4:], ["-c", 'cli_auth_credentials_store="file"', "login", "--device-auth"])
        self.assertNotIn("--with-api-key", command)
        self.assertNotIn("app-server", command)

    def test_initialization_and_check_mount_only_dedicated_volume(self):
        initialize = login.volume_command("agent:test", "project_hindsight-auth", "init")
        check = login.volume_command("agent:test", "project_hindsight-auth", "check")
        self.assertIn("type=volume,source=project_hindsight-auth,target=/auth", initialize)
        self.assertIn("type=volume,source=project_hindsight-auth,target=/auth,readonly", check)
        self.assertIn("0:0", initialize)
        self.assertIn("1000:1000", check)
        self.assertNotIn("--cap-add", check)
        self.assertNotIn("/workspace", " ".join(initialize))
        self.assertNotIn("agent-home", " ".join(initialize))

    def test_new_volume_has_compose_labels(self):
        with mock.patch.object(login.subprocess, "run") as run:
            run.return_value.returncode = 1
            login.ensure_volume("project_hindsight-auth", "project")
            command = run.call_args_list[-1].args[0]
            self.assertIn("com.docker.compose.project=project", command)
            self.assertIn("com.docker.compose.volume=hindsight-auth", command)

    def test_timed_out_container_is_removed_by_its_own_unique_name(self):
        with mock.patch.object(login.subprocess, "run") as run:
            run.side_effect = [subprocess.TimeoutExpired("docker", 1), mock.Mock(returncode=0)]
            with self.assertRaises(subprocess.TimeoutExpired):
                login.run_container(["docker", "run", "--rm", "agent:test"], timeout=1)
            command = run.call_args_list[0].args[0]
            name = command[command.index("--name") + 1]
            self.assertTrue(name.startswith("agent-hindsight-auth-"))
            self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "--force", name])

    def test_missing_login_check_is_offline_and_reports_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "missing-auth.json")
            code = login.CHECK.replace("'/auth/codex/auth.json'", repr(path))
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("login is missing or incomplete", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_compose_errors_do_not_dump_runtime_environment(self):
        config = {"name": "project", "services": {"agent": {"image": "agent:test"}},
                  "volumes": {"hindsight-auth": {"name": "project_hindsight-auth"}},
                  "environment": {"PRIVATE_KEY": "must-never-appear"}}
        with mock.patch.object(login.subprocess, "run") as run:
            run.side_effect = [mock.Mock(stdout=json.dumps(config)),
                               subprocess.CalledProcessError(1, "inspect", output="must-never-appear")]
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(login.main(["check"]), 1)
            self.assertNotIn("must-never-appear", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
