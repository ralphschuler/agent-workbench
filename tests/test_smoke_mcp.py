"""Ensure the real installed MCP SDK's result model keeps smoke failures visible."""

import importlib.util
from pathlib import Path
import unittest

from mcp.types import CallToolResult


SPEC = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).resolve().parents[1] / "scripts" / "smoke.py",
)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class MCPSmokeTests(unittest.TestCase):
    def test_tool_errors_remain_failures_with_installed_sdk_result_model(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                result = CallToolResult.model_validate({
                    "content": [{"type": "text", "text": "fixture response"}],
                    "isError": failed,
                })
                self.assertIs(SMOKE.tool_failed(result), failed)


if __name__ == "__main__":
    unittest.main()
