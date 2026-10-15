#!/usr/bin/env python3
"""Register bundled tools without replacing user settings or persisting secrets.

The small ownership journal contains only settings generated here. On later
starts we update/remove a setting only if it still matches our previous value.
User edits and conflicting server names always win, with a visible warning.
"""

from collections.abc import MutableMapping
import copy
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
from urllib.parse import quote, urlsplit

import tomlkit


ROOT = Path("/opt/agent")
PYTHON = "/opt/agent-venv/bin/python"
MISSING = object()


class ConfigurationError(Exception):
    pass


def read_document(path, toml=False):
    if path.is_symlink():
        raise ConfigurationError(f"Refusing to replace symlinked settings: {path}")
    if not path.exists():
        return tomlkit.document() if toml else {}
    try:
        value = (tomlkit.parse(path.read_text()) if toml
                 else json.loads(path.read_text()))
    except (ValueError, OSError) as error:
        # Parser errors may contain user settings or credentials.
        raise ConfigurationError(f"Cannot parse settings; preserved file: {path}") from error
    if not isinstance(value, MutableMapping):
        raise ConfigurationError(f"Expected a settings object; preserved file: {path}")
    return value


def atomic_write(path, text):
    """Replace a regular file atomically while retaining its permission bits."""
    if path.is_symlink():
        raise ConfigurationError(f"Refusing to replace symlinked settings: {path}")
    if path.exists() and path.read_text() == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def plain(value):
    return value.unwrap() if hasattr(value, "unwrap") else value


class Settings:
    def __init__(self, path, previous, warnings, toml=False):
        self.path = path
        self.toml = toml
        self.document = read_document(path, toml)
        self.before = copy.deepcopy(self.document)
        self.previous = previous
        self.managed = {}
        self.warnings = warnings

    def parent(self, keys, create=False):
        node = self.document
        for key in keys[:-1]:
            if key not in node:
                if not create:
                    return None
                node[key] = {}
            node = node[key]
            if not isinstance(node, MutableMapping):
                return None
        return node

    def sync(self, keys, desired=MISSING):
        """Reconcile one whole entry, retaining conflicting user values."""
        identity = "/".join(keys)
        parent = self.parent(keys)
        current = parent.get(keys[-1], MISSING) if parent is not None else MISSING
        previous = self.previous.get(identity, MISSING)
        owned = previous is not MISSING and plain(current) == previous
        if desired is MISSING:
            if owned:
                del parent[keys[-1]]
            return
        if current is MISSING:
            parent = self.parent(keys, create=True)
            if parent is None:
                self.warnings.append(f"Preserved conflicting settings in {self.path}: {identity}")
                return
        elif not owned:
            if plain(current) != desired:
                self.warnings.append(f"Preserved user setting in {self.path}: {identity}")
            return
        if current is MISSING or plain(current) != desired:
            parent[keys[-1]] = copy.deepcopy(desired)
        self.managed[identity] = copy.deepcopy(desired)

    def sync_hook(self, event, command, enabled):
        """Append/remove our hook while preserving all unrelated hook entries."""
        identity = "hook/" + event
        previous = self.previous.get(identity)
        expected = {"hooks": [{"type": "command", "command": command, "timeout": 25}]}
        hooks = self.document.get("hooks", {})
        if not isinstance(hooks, MutableMapping):
            if enabled:
                self.warnings.append(f"Preserved conflicting hooks in {self.path}")
            return
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            if enabled:
                self.warnings.append(f"Preserved conflicting {event} hooks in {self.path}")
            return
        result = list(entries)
        if previous in result:
            result.remove(previous)
        if enabled:
            if expected in result:
                # An identical pre-existing hook is already sufficient, but is
                # not ours to remove when the user disables bundled hooks.
                return
            result.append(expected)
            self.managed[identity] = expected
        if result != entries:
            if "hooks" not in self.document:
                self.document["hooks"] = {}
            if result:
                self.document["hooks"][event] = result
            elif event in self.document["hooks"]:
                del self.document["hooks"][event]

    def save(self):
        if self.document != self.before:
            content = (tomlkit.dumps(self.document) if self.toml else
                       json.dumps(self.document, indent=2, ensure_ascii=False) + "\n")
            atomic_write(self.path, content)


def hindsight_url(env):
    base = env.get("HINDSIGHT_API_URL", "http://hindsight:8888").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ConfigurationError("HINDSIGHT_API_URL must be HTTP(S) without credentials, query, or fragment")
    bank = env.get("HINDSIGHT_BANK_ID", "agent")
    if not bank.strip():
        raise ConfigurationError("HINDSIGHT_BANK_ID must not be empty")
    return base + "/mcp/" + quote(bank, safe="") + "/"


def link_skills(home, root, warnings):
    source = root / "skills"
    if not source.is_dir():
        return
    for skill in sorted(source.iterdir()):
        if not skill.is_dir() or not (skill / "SKILL.md").is_file():
            continue
        for folder in (".agents", ".claude"):
            target = home / folder / "skills" / skill.name
            if target.is_symlink() and target.resolve() == skill.resolve():
                continue
            if target.exists() or target.is_symlink():
                warnings.append(f"Preserved existing skill: {target}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.symlink_to(skill, target_is_directory=True)


def configure(home=None, env=None, root=ROOT):
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path(env.get("HOME", str(Path.home())))
    state_path = home / ".config" / "agent-workbench" / "config-state.json"
    state = read_document(state_path)
    if state and state.get("version") != 1:
        raise ConfigurationError(f"Unsupported configuration journal; preserved file: {state_path}")
    prior = state.get("files", {})
    if not isinstance(prior, dict) or any(not isinstance(value, dict) for value in prior.values()):
        raise ConfigurationError(f"Invalid configuration journal; preserved file: {state_path}")
    warnings = []
    codex_home = Path(env.get("CODEX_HOME", str(home / ".codex")))
    paths = {
        "codex": (codex_home / "config.toml", True),
        "codex-hooks": (codex_home / "hooks.json", False),
        "claude": (home / ".claude.json", False),
        "claude-hooks": (home / ".claude" / "settings.json", False),
    }
    # Parse every existing document before making any changes.
    settings = {name: Settings(path, prior.get(str(path), {}), warnings, toml)
                for name, (path, toml) in paths.items()}
    memory = env.get("HINDSIGHT_ENABLED") == "1"
    hooks = env.get("HINDSIGHT_HOOKS_ENABLED") == "1"
    url = hindsight_url(env) if memory else None
    authenticated = bool(env.get("HINDSIGHT_API_KEY"))
    jev = {"command": PYTHON, "args": [str(root / "integrations" / "jev" / "server.py")]}
    cua = {"command": "cua-driver", "args": ["mcp"]}
    github = {"command": PYTHON, "args": [str(root / "scripts" / "github-mcp.py")]}
    for name in ("codex", "claude"):
        config = settings[name]
        section = "mcp_servers" if name == "codex" else "mcpServers"
        driver = copy.deepcopy(jev)
        desktop = copy.deepcopy(cua)
        github_server = copy.deepcopy(github)
        if name == "codex":
            # Codex clears most process variables for stdio MCP subprocesses.
            # Preserve the desktop bus/display and the integration's documented
            # runtime controls explicitly; secrets still remain environment-only.
            desktop["env_vars"] = ["DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "HOME"]
            driver["env_vars"] = ["TYPESAFE_API_KEY", "TYPESAFE_DEFAULT_MODEL",
                                  "JEV_TIMEOUT_SECONDS", "JEV_IMAGE_ROOTS"]
            github_server["env_vars"] = ["GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN",
                                        "HOME", "GH_CONFIG_DIR", "GITHUB_TOOLSETS"]
        else:
            driver["env"] = {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"}
        config.sync((section, "cua"), desktop)
        config.sync((section, "jev"), driver)
        config.sync((section, "github"), github_server)
        hindsight = MISSING
        if memory:
            hindsight = {"type": "http", "url": url} if name == "claude" else {"url": url}
            if authenticated:
                if name == "codex":
                    hindsight["bearer_token_env_var"] = "HINDSIGHT_API_KEY"
                else:
                    hindsight["headers"] = {"Authorization": "Bearer ${HINDSIGHT_API_KEY}"}
        config.sync((section, "hindsight"), hindsight)
    hook_command = shlex.join([PYTHON, str(root / "scripts" / "hindsight-hook.py")])
    for name in ("codex-hooks", "claude-hooks"):
        for event, action in (("UserPromptSubmit", "recall"), ("Stop", "retain")):
            settings[name].sync_hook(event, hook_command + " " + action, hooks)
    settings["codex"].sync(("features", "hooks"), True if hooks else MISSING)
    for config in settings.values():
        config.save()
    link_skills(home, root, warnings)
    new_state = {"version": 1, "files": {
        str(config.path): config.managed for config in settings.values() if config.managed
    }}
    atomic_write(state_path, json.dumps(new_state, indent=2, ensure_ascii=False) + "\n")
    return warnings


def main():
    try:
        for warning in configure():
            print("agent configuration: " + warning, file=sys.stderr)
    except (ConfigurationError, OSError):
        # Never print exception text that could include parsed settings or URLs.
        print("agent configuration failed; check settings syntax, file types and permissions. "
              "Existing user settings were preserved.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
