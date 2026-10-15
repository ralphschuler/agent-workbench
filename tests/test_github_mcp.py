"""Verify runtime credential handling without GitHub requests or real tokens."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "github_mcp", Path(__file__).resolve().parents[1] / "scripts" / "github-mcp.py")
github = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(github)


class GitHubMCPTests(unittest.TestCase):
    def run_wrapper(self, env, result=None, error=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(github.subprocess, "run") as run, \
                mock.patch.object(github.os, "execvpe") as execute, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            run.return_value = result or mock.Mock(returncode=1, stdout="", stderr="")
            run.side_effect = error
            status = github.main(env)
        return status, stdout.getvalue(), stderr.getvalue(), run, execute

    def test_explicit_tokens_have_stable_precedence_and_never_enter_argv(self):
        for env, expected in [
            ({"GITHUB_PERSONAL_ACCESS_TOKEN": "explicit-pat", "GH_TOKEN": "gh-token",
              "GITHUB_TOKEN": "github-token"}, "explicit-pat"),
            ({"GH_TOKEN": "gh-token", "GITHUB_TOKEN": "github-token"}, "gh-token"),
            ({"GITHUB_TOKEN": "github-token"}, "github-token"),
        ]:
            original = dict(env)
            status, stdout, stderr, run, execute = self.run_wrapper(env)
            self.assertEqual((status, stdout, stderr), (0, "", ""))
            run.assert_not_called()
            binary, argv, child = execute.call_args.args
            self.assertEqual(binary, "/usr/local/bin/github-mcp-server")
            self.assertEqual(argv, [binary, "stdio"])
            self.assertEqual(child["GITHUB_PERSONAL_ACCESS_TOKEN"], expected)
            self.assertEqual(child["GITHUB_TOOLSETS"], "context,repos,issues,pull_requests,users,actions")
            self.assertNotIn(expected, " ".join(argv))
            self.assertEqual(env, original)

    def test_gh_login_is_reused_without_printing_token_or_cli_logs(self):
        env = {"HOME": "/home/agent", "GH_CONFIG_DIR": "/home/agent/.config/gh"}
        result = mock.Mock(returncode=0, stdout="stored-login-token\n", stderr="private CLI log")
        status, stdout, stderr, run, execute = self.run_wrapper(env, result=result)
        self.assertEqual((status, stdout, stderr), (0, "", ""))
        self.assertEqual(run.call_args.args[0], ["gh", "auth", "token", "--hostname", "github.com"])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(execute.call_args.args[2]["GITHUB_PERSONAL_ACCESS_TOKEN"], "stored-login-token")
        self.assertEqual(execute.call_args.args[2]["GH_CONFIG_DIR"], env["GH_CONFIG_DIR"])

    def test_github_com_login_cannot_be_sent_to_an_inherited_other_host(self):
        _, _, _, _, execute = self.run_wrapper({
            "GH_TOKEN": "runtime-token", "GITHUB_HOST": "https://other.example"})
        self.assertEqual(execute.call_args.args[2]["GITHUB_HOST"], "https://github.com")

    def test_explicit_toolsets_override_the_default(self):
        _, _, _, _, execute = self.run_wrapper({"GH_TOKEN": "runtime-token",
                                                "GITHUB_TOOLSETS": "repos,actions"})
        self.assertEqual(execute.call_args.args[2]["GITHUB_TOOLSETS"], "repos,actions")

    def test_missing_auth_has_clear_stderr_and_no_protocol_output(self):
        result = mock.Mock(returncode=1, stdout="unexpected-secret", stderr="private-login-detail")
        status, stdout, stderr, _, execute = self.run_wrapper({}, result=result)
        self.assertEqual((status, stdout), (1, ""))
        self.assertIn("gh auth login --hostname github.com", stderr)
        self.assertNotIn("unexpected-secret", stderr)
        self.assertNotIn("private-login-detail", stderr)
        execute.assert_not_called()

    def test_gh_timeout_is_bounded_and_does_not_dump_captured_output(self):
        error = subprocess.TimeoutExpired("gh", 5, output="private-token", stderr="private-detail")
        status, stdout, stderr, _, execute = self.run_wrapper({}, error=error)
        self.assertEqual((status, stdout), (1, ""))
        self.assertNotIn("private-token", stderr)
        self.assertNotIn("private-detail", stderr)
        execute.assert_not_called()

    def test_malformed_explicit_token_does_not_fall_back_to_another_account(self):
        status, stdout, stderr, run, execute = self.run_wrapper({"GH_TOKEN": "secret\nother-token"})
        self.assertEqual((status, stdout), (1, ""))
        self.assertIn("invalid runtime token", stderr)
        self.assertNotIn("secret", stderr)
        run.assert_not_called()
        execute.assert_not_called()

    def test_exec_errors_do_not_echo_sensitive_exception_details(self):
        stderr = io.StringIO()
        with mock.patch.object(github.os, "execvpe", side_effect=OSError("private-token")), \
                redirect_stderr(stderr):
            self.assertEqual(github.main({"GH_TOKEN": "private-token"}), 1)
        self.assertNotIn("private-token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
