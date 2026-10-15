#!/usr/bin/env python3
"""Run the official GitHub MCP stdio server using runtime credentials or gh login."""

import os
import subprocess
import sys


BINARY = "/usr/local/bin/github-mcp-server"
TOKEN_NAMES = ("GITHUB_PERSONAL_ACCESS_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
DEFAULT_TOOLSETS = "context,repos,issues,pull_requests,users,actions"


def clean_token(value):
    token = value.strip()
    if token and any(char.isspace() or ord(char) < 32 for char in token):
        raise ValueError("invalid token")
    return token


def resolve_token(env):
    for name in TOKEN_NAMES:
        token = clean_token(env.get(name, ""))
        if token:
            return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            env=env, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return clean_token(result.stdout) if result.returncode == 0 else ""


def main(environ=None):
    env = dict(os.environ if environ is None else environ)
    try:
        token = resolve_token(env)
    except ValueError:
        print("GitHub MCP received an invalid runtime token. Check the configured token source.",
              file=sys.stderr)
        return 1
    if not token:
        print("GitHub MCP needs authentication: run gh auth login --hostname github.com, "
              "or set GITHUB_PERSONAL_ACCESS_TOKEN, GH_TOKEN or GITHUB_TOKEN.", file=sys.stderr)
        return 1
    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    # The gh fallback is explicitly for github.com; never send that token to an
    # inherited GITHUB_HOST pointing at a different service.
    env["GITHUB_HOST"] = "https://github.com"
    env["GITHUB_TOOLSETS"] = env.get("GITHUB_TOOLSETS", "").strip() or DEFAULT_TOOLSETS
    try:
        os.execvpe(BINARY, [BINARY, "stdio"], env)
    except OSError:
        print("The official GitHub MCP executable could not be started.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
