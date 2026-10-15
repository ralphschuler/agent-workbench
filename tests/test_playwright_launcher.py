"""Check browser routing and sandbox controls without launching Chrome."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "playwright_launcher", Path(__file__).resolve().parents[1] / "scripts/playwright-mcp.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class PlaywrightLauncherTests(unittest.TestCase):
    def test_inherited_endpoint_and_http_switches_cannot_replace_local_stdio_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                'HOME': temporary, 'DISPLAY': ':99',
                'PLAYWRIGHT_MCP_OUTPUT_DIR': str(Path(temporary) / 'artifacts'),
                'PLAYWRIGHT_MCP_CDP_ENDPOINT': 'http://host-browser:9222',
                'PLAYWRIGHT_MCP_PORT': '8931',
                'PLAYWRIGHT_MCP_NO_SANDBOX': '1',
            }
            arguments, child = launcher.launch(environment)
            self.assertIn('--sandbox', arguments)
            self.assertNotIn('--no-sandbox', arguments)
            self.assertNotIn('--port', arguments)
            self.assertNotIn('--cdp-endpoint', arguments)
            self.assertEqual(arguments[arguments.index('--executable-path') + 1],
                             '/usr/bin/google-chrome-stable')
            self.assertEqual(child['DISPLAY'], ':99')
            self.assertFalse(any(name.startswith('PLAYWRIGHT_MCP_') for name in child))
            self.assertEqual(child['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'], '1')
            self.assertEqual(environment['PLAYWRIGHT_MCP_PORT'], '8931')
            profile = Path(temporary) / '.local/share/agent-workbench/playwright'
            self.assertTrue(profile.is_dir())
            self.assertEqual(profile.stat().st_mode & 0o777, 0o700)

    def test_explicit_profile_and_sandbox_override_are_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / 'claude-profile'
            arguments, _ = launcher.launch({
                'HOME': temporary, 'CHROME_DISABLE_SANDBOX': '1',
                'PLAYWRIGHT_MCP_USER_DATA_DIR': str(profile),
                'PLAYWRIGHT_MCP_OUTPUT_DIR': str(Path(temporary) / 'output'),
            })
            self.assertEqual(arguments[arguments.index('--user-data-dir') + 1], str(profile))
            self.assertIn('--no-sandbox', arguments)
            self.assertNotIn('--sandbox', arguments)

    def test_invalid_sandbox_setting_fails_before_creating_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                launcher.launch({'HOME': temporary, 'CHROME_DISABLE_SANDBOX': 'true'})
            self.assertFalse((Path(temporary) / '.local').exists())
