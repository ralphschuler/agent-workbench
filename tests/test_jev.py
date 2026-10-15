"""Decision quality boundaries, request batching, and network-free MCP discovery."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from integrations.jev.server import ABSTAIN, JevRuntime, load_image


def response(choice="terminal", confidence=0.98, evidence=0.97, probabilities=None):
    return SimpleNamespace(
        choices={"decision": SimpleNamespace(choice=choice, confidence=confidence, probabilities=probabilities or {"terminal": 0.97, ABSTAIN: 0.03})},
        nouls={"enough_evidence": SimpleNamespace(noul=evidence)},
        model="jev-latest",
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 2}),
    )


class JevTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = JevRuntime()
        self.runtime.client = SimpleNamespace(system_one=AsyncMock(return_value=response()))

    async def decide(self, **kwargs):
        return await self.runtime.decide("A shell command must be run.", "Which tool fits?", {"terminal": "Run the command in the terminal."}, **kwargs)

    async def test_batches_choice_and_evidence_and_preserves_probabilities(self):
        result = await self.decide()
        self.assertEqual(result["selected"], "terminal")
        self.assertFalse(result["abstain"])
        self.assertEqual(result["probabilities"]["terminal"], 0.97)
        self.runtime.client.system_one.assert_awaited_once()
        questions = self.runtime.client.system_one.call_args.kwargs["questions"]
        self.assertEqual(set(questions), {"decision", "enough_evidence"})

    async def test_uncertain_or_insufficient_decision_abstains(self):
        for fixture in [response(confidence=0.6), response(evidence=0.6), response(probabilities={"terminal": 0.6, ABSTAIN: 0.4})]:
            self.runtime.client.system_one.return_value = fixture
            result = await self.decide()
            self.assertTrue(result["abstain"])
            self.assertIsNone(result["selected"])

    async def test_explicit_abstention_survives_high_confidence(self):
        self.runtime.client.system_one.return_value = response(choice=ABSTAIN, probabilities={"terminal": 0.01, ABSTAIN: 0.99})
        self.assertTrue((await self.decide())["abstain"])

    async def test_invalid_or_incomplete_responses_do_not_authorize_selection(self):
        fixtures = [response(probabilities={"unknown": 1}), response(confidence=float("nan")), response(probabilities={"terminal": 0.9, ABSTAIN: 0.9}), response(choice="invented")]
        for fixture in fixtures:
            self.runtime.client.system_one.return_value = fixture
            result = await self.decide()
            self.assertEqual(result["status"], "invalid_response")
            self.assertIsNone(result["selected"])

    async def test_missing_key_has_no_network_call(self):
        self.runtime.client = None
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}):
            self.assertEqual((await self.decide())["status"], "unconfigured")

    async def test_timeout_abstains(self):
        async def slow(**_kwargs):
            await asyncio.sleep(1)
        self.runtime.timeout = 0.01
        self.runtime.client.system_one.side_effect = slow
        self.assertEqual((await self.decide())["status"], "timeout")

    async def test_assess_batches_and_retains_uncertainty(self):
        fixture = response()
        fixture.nouls = {"ready": SimpleNamespace(noul=0.99), "complete": SimpleNamespace(noul=0.1), "ambiguous": SimpleNamespace(noul=0.5)}
        self.runtime.client.system_one.return_value = fixture
        result = await self.runtime.assess("Current application state", {"ready": "Ready?", "complete": "Complete?", "ambiguous": "Certain?"})
        self.assertEqual({name: item["outcome"] for name, item in result["answers"].items()}, {"ready": "yes", "complete": "no", "ambiguous": "uncertain"})
        self.runtime.client.system_one.assert_awaited_once()

    async def test_rejects_ambiguous_thresholds(self):
        for threshold in [0.5, 0, 1.1, float("nan")]:
            with self.assertRaises(ValueError):
                await self.decide(confidence_threshold=threshold)

    async def test_official_sdk_wire_format_with_mock_transport(self):
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={
                "model": "jev-latest",
                "usage": {"input_tokens": 10, "output_tokens": 2},
                "answers": {
                    "decision": {"type": "choice", "choice": "terminal", "confidence": 0.98, "probabilities": {"terminal": 0.97, ABSTAIN: 0.03}},
                    "enough_evidence": {"type": "noul", "noul": 0.99},
                },
            })

        async with AsyncTypeSafeClient(api_key="test-not-a-real-key", transport=httpx2.MockTransport(handler), retry=RetryPolicy(max_retries=0), compression=False) as client:
            self.runtime.client = client
            result = await self.decide()
        self.assertEqual(result["selected"], "terminal")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/v1/systemone")
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["model"], "jev-latest")
        self.assertEqual(payload["questions"]["decision"]["type"], "choice")
        self.assertEqual(payload["questions"]["enough_evidence"]["type"], "noul")

    async def test_stdio_mcp_discovery_and_unconfigured_response(self):
        server = Path(__file__).resolve().parents[1] / "integrations" / "jev" / "server.py"
        environment = os.environ.copy()
        environment["TYPESAFE_API_KEY"] = ""
        params = StdioServerParameters(command=sys.executable, args=[str(server)], env=environment)
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                tools = await client.list_tools()
                self.assertEqual({tool.name for tool in tools.tools}, {"jev_decide", "jev_assess"})
                result = await client.call_tool("jev_decide", {"context": "A shell command is needed", "question": "Which tool?", "candidates": {"terminal": "Run a command"}})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["status"], "unconfigured")


class JevImageTests(unittest.TestCase):
    def test_rejects_non_images_and_files_outside_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "screen.png"
            image.write_bytes(b"not an image")
            with self.assertRaises(ValueError):
                load_image(str(image))
            image.write_bytes(b"\x89PNG\r\n\x1a\nexample")
            with patch.dict("os.environ", {"JEV_IMAGE_ROOTS": "/workspace"}):
                with self.assertRaises(ValueError):
                    load_image(str(image))


if __name__ == "__main__":
    unittest.main()
