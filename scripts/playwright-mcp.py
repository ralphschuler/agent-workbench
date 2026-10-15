#!/usr/bin/env python3
"""Run Microsoft's stdio MCP server against this container's desktop Chrome."""

import os
from pathlib import Path
import sys


BINARY = "/opt/agent-tools/node_modules/.bin/playwright-mcp"
CONFIG = "/opt/agent/docker/playwright-mcp.json"


def launch(environ=None):
    environment = dict(os.environ if environ is None else environ)
    home = Path(environment["HOME"])
    profile = Path(environment.get("PLAYWRIGHT_MCP_USER_DATA_DIR") or
                   home / ".local/share/agent-workbench/playwright").expanduser()
    output = Path(environment.get("PLAYWRIGHT_MCP_OUTPUT_DIR") or
                  "/workspace/.browser-artifacts").expanduser()
    if not profile.is_absolute() or not output.is_absolute():
        raise ValueError("Playwright profile and output paths must be absolute")
    sandbox = environment.get("CHROME_DISABLE_SANDBOX", "0")
    if sandbox not in {"0", "1"}:
        raise ValueError("CHROME_DISABLE_SANDBOX must be 0 or 1")
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep this launcher on stdio and the container browser. Inherited upstream
    # switches must not turn it into an HTTP server or attach it to a host CDP
    # endpoint. Only the two documented path overrides above are supported.
    environment = {name: value for name, value in environment.items()
                   if not name.startswith("PLAYWRIGHT_MCP_")}
    environment["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    arguments = [
        BINARY, "--config", CONFIG,
        "--browser", "chrome", "--executable-path", "/usr/bin/google-chrome-stable",
        "--user-data-dir", str(profile), "--output-dir", str(output),
        "--file-paths", "absolute", "--codegen", "none",
        "--no-sandbox" if sandbox == "1" else "--sandbox",
    ]
    if sandbox == "1":
        print("Playwright Chrome sandbox explicitly disabled for this isolated container.", file=sys.stderr)
    return arguments, environment


def main():
    try:
        if len(sys.argv) != 1:
            raise ValueError("The Playwright MCP launcher does not accept command arguments")
        arguments, environment = launch()
        os.execve(BINARY, arguments, environment)
    except (KeyError, OSError, ValueError) as error:
        print(f"Playwright MCP could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
