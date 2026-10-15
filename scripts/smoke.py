#!/usr/bin/env python3
"""Exercise installed CLIs, actual Chrome/xterm windows, and the CUA MCP link."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import tomllib
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_cua(pid: int, marker: Path):
    # Match Codex's restricted inherited environment; using the entire process
    # environment here would conceal missing DISPLAY / session-bus config.
    config = tomllib.loads((Path.home() / '.codex/config.toml').read_text())['mcp_servers']['cua']
    defaults = {'HOME', 'LOGNAME', 'PATH', 'SHELL', 'USER', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR', 'TZ'}
    keys = defaults | set(config.get('env_vars', []))
    environment = {key: value for key, value in os.environ.items() if key in keys}
    environment.update(config.get('env', {}))
    parameters = StdioServerParameters(command=config['command'], args=config['args'], env=environment)
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {'list_windows', 'get_window_state', 'run_steps'} <= tools, tools
            windows = await session.call_tool('list_windows', {'pid': pid})
            assert not windows.isError, 'CUA could not enumerate terminal windows'
            result = await session.call_tool('get_window_state', {'pid': pid, 'max_elements': 30, 'timeout_ms': 5000})
            assert not result.isError, 'CUA could not observe the terminal'
            assert any(item.type == 'image' for item in result.content), 'CUA returned no screenshot'
            typed = await session.call_tool('run_steps', {
                'pid': pid,
                'delivery_mode': 'foreground',
                'steps': [
                    {'tool': 'type_text', 'args': {'text': 'printf agent-cua-ok > ' + shlex.quote(str(marker))}},
                    {'tool': 'press_key', 'args': {'key': 'enter'}},
                ],
            })
            assert not typed.isError, 'CUA could not type into the terminal'
            for _ in range(50):
                if marker.exists() and marker.read_text() == 'agent-cua-ok':
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('CUA input did not execute in the terminal')
            print('CUA MCP: discovery, window observation, screenshot and terminal input passed')


def main():
    assert os.getuid() != 0, 'Desktop must run as a regular user'
    subprocess.run(['python', '/opt/agent/scripts/healthcheck.py'], check=True, timeout=15)
    for command in ('codex', 'claude', 'google-chrome'):
        version = subprocess.check_output([command, '--version'], text=True, timeout=45).strip()
        assert version, f'{command} has no version output'
        print(f'{command}: {version}')
    # The local page requires no provider key, external website or agent account.
    with tempfile.TemporaryDirectory(prefix='agent-smoke-') as temporary:
        page = Path(temporary) / 'smoke.html'
        page.write_text('<!doctype html><title>Agent smoke Chrome</title><h1>Agent desktop smoke</h1>')
        with open(Path(temporary) / 'chrome.log', 'w+') as chrome_log:
            chrome = subprocess.Popen([
                'agent-browser', '--no-first-run', '--no-default-browser-check',
                f'--user-data-dir={temporary}/profile', '--remote-debugging-port=9222',
                '--remote-debugging-address=127.0.0.1', page.as_uri(),
            ], stdout=chrome_log, stderr=chrome_log, start_new_session=True)
            terminal = subprocess.Popen(['xterm', '-title', 'Agent smoke terminal', '-e', 'bash', '--noprofile', '--norc'])
            try:
                for _ in range(60):
                    if chrome.poll() is not None:
                        chrome_log.seek(0)
                        raise RuntimeError('Chrome exited before rendering: ' + chrome_log.read(2000))
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:9222/json', timeout=1) as response:
                            pages = json.load(response)
                        if any(item.get('title') == 'Agent smoke Chrome' for item in pages):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError('Chrome did not render the local smoke page')
                print('Chrome: local page rendered in the desktop')
                asyncio.run(asyncio.wait_for(check_cua(terminal.pid, Path(temporary) / 'cua-result'), timeout=40))
            finally:
                terminal.terminate()
                chrome.terminate()
                for process in (terminal, chrome):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    main()
