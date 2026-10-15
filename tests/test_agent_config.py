"""Behavioral checks for startup reconciliation of persistent agent settings."""

import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

import tomlkit


SPEC = importlib.util.spec_from_file_location(
    "configure_agent", Path(__file__).resolve().parents[1] / "scripts" / "configure-agent.py",
)
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class AgentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.root = Path(self.temporary.name) / "image"
        self.home.mkdir()
        skill = self.root / "skills" / "desktop"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Desktop\n")
        self.env = {"HOME": str(self.home)}

    def configure(self, **env):
        return CONFIG.configure(self.home, {**self.env, **env}, self.root)

    def write(self, relative, value):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return path

    def read(self, relative):
        path = self.home / relative
        return (tomlkit.parse(path.read_text()).unwrap() if path.suffix == ".toml"
                else json.loads(path.read_text()))

    def snapshot(self):
        return {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.home.rglob("*") if path.is_file() and not path.is_symlink()}

    def test_registers_valid_native_transports_and_runtime_env_references(self):
        self.assertEqual(self.configure(), [])
        codex = self.read(".codex/config.toml")["mcp_servers"]
        claude = self.read(".claude.json")["mcpServers"]
        self.assertEqual(codex["cua"], {
            "command": "cua-driver", "args": ["mcp"],
            "env_vars": ["DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "HOME"],
        })
        self.assertEqual(codex["jev"]["env_vars"], [
            "TYPESAFE_API_KEY", "TYPESAFE_DEFAULT_MODEL", "JEV_TIMEOUT_SECONDS", "JEV_IMAGE_ROOTS",
        ])
        self.assertEqual(claude["jev"]["env"], {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"})
        self.assertEqual(codex["jev"]["command"], "/opt/agent-venv/bin/python")
        self.assertNotIn("hindsight", codex)
        self.assertFalse((self.home / ".codex/hooks.json").exists())
        self.assertFalse((self.home / ".gemini").exists())

    def test_repeated_start_is_byte_and_mtime_idempotent(self):
        self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_HOOKS_ENABLED="1")
        before = self.snapshot()
        self.assertEqual(self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_HOOKS_ENABLED="1"), [])
        self.assertEqual(self.snapshot(), before)

    def test_preserves_custom_settings_comments_and_permissions(self):
        path = self.write(".codex/config.toml", '# Keep me\nmodel = "chosen"\n[features]\ncustom = true\n')
        path.chmod(0o640)
        self.write(".claude.json", {"theme": "dark", "mcpServers": {"custom": {"command": "mine"}}})
        self.write(".claude/settings.json", {"permissions": {"allow": ["Read"]}})
        self.configure()
        self.assertIn("# Keep me", path.read_text())
        self.assertEqual(self.read(".codex/config.toml")["model"], "chosen")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(self.read(".claude.json")["mcpServers"]["custom"], {"command": "mine"})
        self.assertEqual(self.read(".claude/settings.json")["permissions"], {"allow": ["Read"]})

    def test_conflicting_server_names_are_preserved_and_reported(self):
        self.write(".claude.json", {"mcpServers": {"cua": {"command": "my-driver"}}})
        warnings = self.configure()
        self.assertTrue(any("mcpServers/cua" in warning for warning in warnings))
        self.assertEqual(self.read(".claude.json")["mcpServers"]["cua"], {"command": "my-driver"})

    def test_updates_managed_endpoint_but_preserves_user_edits(self):
        self.configure(HINDSIGHT_ENABLED="1")
        self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_BANK_ID="new bank")
        self.assertEqual(self.read(".claude.json")["mcpServers"]["hindsight"]["url"],
                         "http://hindsight:8888/mcp/new%20bank/")
        data = self.read(".claude.json")
        data["mcpServers"]["hindsight"]["url"] = "https://my.example/mcp"
        self.write(".claude.json", data)
        warnings = self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_BANK_ID="another")
        self.assertTrue(any("mcpServers/hindsight" in warning for warning in warnings))
        self.assertEqual(self.read(".claude.json")["mcpServers"]["hindsight"]["url"],
                         "https://my.example/mcp")

    def test_disabling_removes_only_managed_memory_and_hooks(self):
        custom = {"hooks": [{"type": "command", "command": "my-hook"}]}
        self.write(".claude/settings.json", {"hooks": {"Stop": [custom]}})
        self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_HOOKS_ENABLED="1")
        self.configure()
        self.assertNotIn("hindsight", self.read(".claude.json")["mcpServers"])
        self.assertEqual(self.read(".claude/settings.json")["hooks"]["Stop"], [custom])
        self.assertNotIn("hooks", self.read(".codex/config.toml").get("features", {}))
        self.assertNotIn("Stop", self.read(".codex/hooks.json")["hooks"])

    def test_authenticated_memory_never_serializes_secret_values(self):
        secret = "test-secret-never-save-1234"
        self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_API_KEY=secret, TYPESAFE_API_KEY=secret)
        for path in self.home.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(), str(path))
        codex = self.read(".codex/config.toml")["mcp_servers"]["hindsight"]
        claude = self.read(".claude.json")["mcpServers"]["hindsight"]
        self.assertEqual(codex["bearer_token_env_var"], "HINDSIGHT_API_KEY")
        self.assertEqual(claude["type"], "http")
        self.assertEqual(claude["headers"]["Authorization"], "Bearer ${HINDSIGHT_API_KEY}")

    def test_existing_unmanaged_client_files_remain_untouched(self):
        path = self.write(".gemini/settings.json", "{user-owned config; do not parse or replace}")
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_HOOKS_ENABLED="1")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse((path.parent / "skills").exists())

    def test_skill_links_preserve_existing_skills(self):
        existing = self.home / ".claude/skills/desktop"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("My skill")
        warnings = self.configure()
        self.assertTrue(any("Preserved existing skill" in warning for warning in warnings))
        self.assertEqual((existing / "SKILL.md").read_text(), "My skill")
        self.assertEqual((self.home / ".agents/skills/desktop").resolve(), self.root / "skills/desktop")

    def test_invalid_json_is_reported_before_any_settings_are_written(self):
        path = self.write(".claude.json", "{broken")
        with self.assertRaises(CONFIG.ConfigurationError):
            self.configure()
        self.assertEqual(path.read_text(), "{broken")
        self.assertFalse((self.home / ".codex/config.toml").exists())

    def test_symlinked_settings_are_never_replaced(self):
        target = self.write("elsewhere.json", {"my": "config"})
        path = self.home / ".claude.json"
        path.symlink_to(target)
        with self.assertRaises(CONFIG.ConfigurationError):
            self.configure()
        self.assertTrue(path.is_symlink())
        self.assertEqual(json.loads(target.read_text()), {"my": "config"})

    def test_rejects_urls_that_could_persist_credentials(self):
        for url in ("http://user:secret@host", "https://host?key=secret", "https://host/#secret", "file:///tmp/x"):
            with self.subTest(url=url), self.assertRaises(CONFIG.ConfigurationError):
                self.configure(HINDSIGHT_ENABLED="1", HINDSIGHT_API_URL=url)
        self.assertFalse((self.home / ".codex/config.toml").exists())

    def test_honors_custom_codex_home(self):
        custom = self.home / "custom-codex"
        self.configure(CODEX_HOME=str(custom), HINDSIGHT_HOOKS_ENABLED="1")
        self.assertTrue((custom / "config.toml").is_file())
        self.assertTrue((custom / "hooks.json").is_file())
        self.assertFalse((self.home / ".codex/config.toml").exists())


if __name__ == "__main__":
    unittest.main()
