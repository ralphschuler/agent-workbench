"""Exercise the optional adapter against a local HTTP server, without credentials."""

from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hindsight-hook.py"
SPEC = importlib.util.spec_from_file_location("hindsight_hook", SCRIPT)
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, dict(self.headers), json.loads(body)))
        mode = self.server.mode
        if mode == "slow":
            time.sleep(0.35)
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.end_headers()
            return
        self.send_response(503 if mode == "error" else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if mode == "error":
            body = b'{"detail":"private-server-error-and-api-key"}'
        elif mode == "invalid":
            body = b"not json, private-server-error-and-api-key"
        elif self.path.endswith("/recall"):
            body = json.dumps({"results": self.server.memories}).encode()
        else:
            body = b'{"success":true,"operation_id":"queued"}'
        try:
            if mode == "drip":
                # Every chunk arrives before a socket timeout, but their total
                # duration exceeds the whole-hook deadline.
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.04)
            elif mode == "oversize":
                self.wfile.write(b"x" * (hook.MAX_RESPONSE + 1))
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@contextmanager
def service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.mode = "ok"
    server.requests = []
    server.memories = [{"text": "Use Python 3.12 for this project."}]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class HindsightHookTests(unittest.TestCase):
    def setUp(self):
        self.proxy_patch = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1"})
        self.proxy_patch.start()
        self.addCleanup(self.proxy_patch.stop)

    def run_hook(self, action, event, server=None, extra_env=None):
        env = {"HINDSIGHT_HOOKS_ENABLED": "1"}
        if server:
            env["HINDSIGHT_API_URL"] = f"http://127.0.0.1:{server.server_port}"
        env.update(extra_env or {})
        stdout, stderr = io.StringIO(), io.StringIO()
        status = hook.main([action], io.StringIO(json.dumps(event)), stdout, stderr, env)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_disabled_hook_never_reads_input_or_contacts_service(self):
        with service() as server:
            stream = mock.Mock()
            status = hook.main(["recall"], stream, env={})
            self.assertEqual(status, 0)
            stream.read.assert_not_called()
            self.assertEqual(server.requests, [])

    def test_recall_sends_official_schema_and_returns_agent_context(self):
        with service() as server:
            status, stdout, stderr = self.run_hook("recall", {
                "hook_event_name": "UserPromptSubmit", "prompt": "Which Python version?",
            }, server, {"HINDSIGHT_BANK_ID": "my bank/one", "HINDSIGHT_API_KEY": "test-private-key"})
            self.assertEqual((status, stderr), (0, ""))
            path, headers, payload = server.requests[0]
            self.assertEqual(path, "/v1/default/banks/my%20bank%2Fone/memories/recall")
            self.assertEqual(headers["Authorization"], "Bearer test-private-key")
            self.assertEqual(payload, {"query": "Which Python version?", "budget": "low", "max_tokens": 1024})
            output = json.loads(stdout)["hookSpecificOutput"]
            self.assertEqual(output["hookEventName"], "UserPromptSubmit")
            self.assertIn("Python 3.12", output["additionalContext"])
            self.assertIn("untrusted reference data", output["additionalContext"])
            self.assertNotIn("test-private-key", stdout)

    def test_retain_is_async_idempotent_and_does_not_read_transcripts(self):
        with service() as server:
            event = {
                "session_id": "session-one", "last_assistant_message": "Use unittest for the adapter.",
                "transcript_path": "/path/that/must/not/be/read", "stop_hook_active": False,
            }
            self.assertEqual(self.run_hook("retain", event, server), (0, "", ""))
            self.assertEqual(self.run_hook("retain", event, server), (0, "", ""))
            path, headers, payload = server.requests[0]
            self.assertEqual(path, "/v1/default/banks/agent/memories")
            self.assertTrue(payload["async"])
            self.assertEqual(payload, server.requests[1][2])
            self.assertEqual(payload["items"][0]["content"], event["last_assistant_message"])
            self.assertNotIn("session-one", json.dumps(payload))
            self.assertNotIn("transcript_path", json.dumps(payload))
            self.assertNotIn("Authorization", headers)
            event["last_assistant_message"] = "A new final response."
            self.run_hook("retain", event, server)
            self.assertNotEqual(payload["operation_id"], server.requests[-1][2]["operation_id"])

    def test_missing_messages_empty_memories_and_active_stop_are_noops(self):
        with service() as server:
            for action, event in [("recall", {}), ("retain", {}), ("retain", {
                    "last_assistant_message": "Do not retain this.", "stop_hook_active": True}),
                    ("recall", {"hook_event_name": "Stop", "prompt": "Wrong event"})]:
                self.assertEqual(self.run_hook(action, event, server), (0, "", ""))
            self.assertEqual(server.requests, [])
            server.memories = []
            self.assertEqual(self.run_hook("recall", {"prompt": "A question"}, server), (0, "", ""))

    def test_known_secrets_are_redacted_in_both_directions(self):
        with service() as server:
            secret = "injected-super-private-value"
            extra = {"OPENAI_API_KEY": secret}
            server.memories = [{"text": f"Never display {secret}. password=hunter2"}]
            status, stdout, stderr = self.run_hook("recall", {
                "prompt": f"Why did {secret} fail? api_key=other-secret-value",
            }, server, extra)
            self.assertEqual((status, stderr), (0, ""))
            exposed = json.dumps(server.requests[0][2]) + stdout
            for value in [secret, "hunter2", "other-secret-value"]:
                self.assertNotIn(value, exposed)
            self.assertIn("[REDACTED]", stdout)
            self.run_hook("retain", {"last_assistant_message": f"token: {secret}"}, server, extra)
            self.assertNotIn(secret, json.dumps(server.requests[-1][2]))

    def test_service_errors_and_invalid_responses_are_fail_open_without_details(self):
        with service() as server:
            for mode in ["error", "invalid"]:
                server.mode = mode
                status, stdout, stderr = self.run_hook("recall", {"prompt": "A question"}, server)
                self.assertEqual((status, stdout), (0, ""))
                self.assertIn("Hindsight hook skipped", stderr)
                self.assertNotIn("private-server-error", stderr)

    def test_redirects_do_not_forward_authentication_or_content(self):
        with service() as server:
            server.mode = "redirect"
            result = self.run_hook("recall", {"prompt": "A question"}, server,
                                   {"HINDSIGHT_API_KEY": "test-private-key"})
            self.assertEqual((result[0], result[1]), (0, ""))
            self.assertEqual(len(server.requests), 1)
            self.assertNotIn("test-private-key", result[2])

    def test_request_has_a_wall_clock_deadline(self):
        with service() as server:
            server.mode = "drip"
            start = time.monotonic()
            status, stdout, stderr = self.run_hook("recall", {"prompt": "A question"}, server,
                                                   {"HINDSIGHT_HOOK_TIMEOUT_SECONDS": "0.15"})
            self.assertLess(time.monotonic() - start, 0.45)
            self.assertEqual((status, stdout), (0, ""))
            self.assertIn("Hindsight hook skipped", stderr)

    def test_oversized_input_and_response_are_rejected(self):
        with service() as server:
            result = self.run_hook("recall", {"prompt": "x" * (hook.MAX_INPUT + 1)}, server)
            self.assertEqual((result[0], result[1]), (0, ""))
            self.assertIn("Hindsight hook skipped", result[2])
            self.assertEqual(server.requests, [])
            server.mode = "oversize"
            result = self.run_hook("recall", {"prompt": "A question"}, server)
            self.assertEqual((result[0], result[1]), (0, ""))
            self.assertIn("Hindsight hook skipped", result[2])

    def test_invalid_input_and_unsafe_urls_never_make_a_request(self):
        with service() as server:
            for url in ["file:///etc/passwd", "http://user:password@127.0.0.1", "https://example.org?secret=x"]:
                result = self.run_hook("recall", {"prompt": "A question"}, server,
                                       {"HINDSIGHT_API_URL": url})
                self.assertEqual((result[0], result[1]), (0, ""))
                self.assertIn("Hindsight hook skipped", result[2])
            for timeout in ["nan", "inf", "0", "21"]:
                result = self.run_hook("recall", {"prompt": "A question"}, server,
                                       {"HINDSIGHT_HOOK_TIMEOUT_SECONDS": timeout})
                self.assertEqual((result[0], result[1]), (0, ""))
            self.assertEqual(server.requests, [])
            stderr = io.StringIO()
            self.assertEqual(hook.main(["recall"], io.StringIO("not json"),
                                       stderr=stderr, env={"HINDSIGHT_HOOKS_ENABLED": "1"}), 0)
            self.assertIn("Hindsight hook skipped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
