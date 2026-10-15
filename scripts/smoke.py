#!/usr/bin/env python3
"""Exercise installed CLIs, desktop CUA, and official Playwright browser tools."""
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def running_group_members(group):
    """Ignore zombies: they cannot keep writing a temporary browser profile."""
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # comm may contain spaces or parentheses; fields after its final
            # ')' begin with state, parent PID, process group and session.
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == group and fields[0] not in {'Z', 'X'}:
            return True
    return False


def stop_process_group(process, timeout=5):
    """Stop a group we started with start_new_session, then reap its leader."""
    group = process.pid
    if group <= 1 or group == os.getpgrp():
        raise ValueError('Refusing to signal the smoke runner process group')
    for termination_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, termination_signal)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + timeout
        while running_group_members(group):
            process.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        else:
            break
    if running_group_members(group):
        raise RuntimeError(f'Smoke process group {group} did not stop after TERM and KILL')
    process.wait(timeout=timeout)


def tool_failed(result):
    # SDK 2 uses Python-style names; SDK 1 exposes the protocol's camelCase.
    return result.is_error if hasattr(result, 'is_error') else result.isError


def configured_server(name, extra_environment=None):
    # Match Codex's restricted inherited environment; using the entire process
    # environment here would conceal missing DISPLAY / session-bus config.
    config = tomllib.loads((Path.home() / '.codex/config.toml').read_text())['mcp_servers'][name]
    defaults = {'HOME', 'LOGNAME', 'PATH', 'SHELL', 'USER', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR', 'TZ'}
    keys = defaults | set(config.get('env_vars', []))
    environment = {key: value for key, value in os.environ.items() if key in keys}
    environment.update(config.get('env', {}))
    environment.update(extra_environment or {})
    return StdioServerParameters(command=config['command'], args=config['args'], env=environment)


async def check_cua(pid: int, marker: Path):
    async with stdio_client(configured_server('cua')) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {'list_windows', 'get_window_state', 'run_steps'} <= tools, tools
            windows = await session.call_tool('list_windows', {'pid': pid})
            assert not tool_failed(windows), 'CUA could not enumerate terminal windows'
            result = await session.call_tool('get_window_state', {'pid': pid, 'max_elements': 30, 'timeout_ms': 5000})
            assert not tool_failed(result), 'CUA could not observe the terminal'
            assert any(item.type == 'image' for item in result.content), 'CUA returned no screenshot'
            typed = await session.call_tool('run_steps', {
                'pid': pid,
                'delivery_mode': 'foreground',
                'steps': [
                    {'tool': 'type_text', 'args': {'text': 'printf agent-cua-ok > ' + shlex.quote(str(marker))}},
                    {'tool': 'press_key', 'args': {'key': 'enter'}},
                ],
            })
            assert not tool_failed(typed), 'CUA could not type into the terminal'
            for _ in range(50):
                if marker.exists() and marker.read_text() == 'agent-cua-ok':
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError('CUA input did not execute in the terminal')
            print('CUA MCP: discovery, window observation, screenshot and terminal input passed')


async def check_playwright(url: str, directory: Path):
    parameters = configured_server('playwright', {
        'PLAYWRIGHT_MCP_USER_DATA_DIR': str(directory / 'profile'),
        'PLAYWRIGHT_MCP_OUTPUT_DIR': str(directory / 'artifacts'),
    })
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {'browser_navigate', 'browser_snapshot', 'browser_fill_form',
                    'browser_click', 'browser_take_screenshot'} <= tools, tools

            async def invoke(name, arguments):
                result = await session.call_tool(name, arguments)
                text = '\n'.join(item.text for item in result.content if item.type == 'text')
                assert not tool_failed(result), f'{name} failed: {text[:2000]}'
                return text

            def reference(snapshot, role):
                match = re.search(role + r'[^\n]*?\[ref=([^\]]+)\]', snapshot)
                assert match, f'No fresh {role} reference in browser snapshot: {snapshot[:2000]}'
                return match.group(1)

            await invoke('browser_navigate', {'url': url})
            snapshot = await invoke('browser_snapshot', {})
            field = reference(snapshot, 'textbox "Message"')
            await invoke('browser_fill_form', {'fields': [
                {'target': field, 'name': 'Message', 'type': 'textbox', 'value': 'agent-playwright-ok'},
            ]})
            snapshot = await invoke('browser_snapshot', {})
            await invoke('browser_click', {'target': reference(snapshot, 'button "Save"')})
            snapshot = await invoke('browser_snapshot', {})
            assert 'Saved agent-playwright-ok' in snapshot, 'Browser form action did not update the page'
            screenshot = directory / 'artifacts' / 'smoke.png'
            await invoke('browser_take_screenshot', {
                'filename': str(screenshot), 'type': 'png', 'scale': 'css',
            })
            assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), 'Playwright screenshot was not saved'
            spec = importlib.util.spec_from_file_location('smoke_jev', '/opt/agent/integrations/jev/server.py')
            jev = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(jev)
            assert jev.load_image(str(screenshot)) is not None, 'Jev could not load the browser screenshot'
            await invoke('browser_close', {})
            print('Playwright MCP: navigation, snapshot, form fill, click, screenshot and offline Jev image adapter passed')


def check_browser_workflow():
    # Serve a local HTTP fixture; Playwright MCP blocks file:// navigation by
    # default, and this test does not need unrestricted filesystem access.
    with tempfile.TemporaryDirectory(prefix='.playwright-smoke-', dir='/workspace') as temporary:
        directory = Path(temporary)
        (directory / 'index.html').write_text(
            '<!doctype html><title>Playwright smoke</title><label>Message <input id="message"></label>'
            '<button onclick="document.querySelector(\'#result\').textContent = '
            '\'Saved \' + document.querySelector(\'#message\').value">Save</button>'
            '<p id="result" role="status"></p>'
        )
        handler = partial(SimpleHTTPRequestHandler, directory=temporary)
        with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/index.html'
                asyncio.run(asyncio.wait_for(check_playwright(url, directory), timeout=90))
            finally:
                server.shutdown()
                worker.join(timeout=5)


def main():
    assert os.getuid() != 0, 'Desktop must run as a regular user'
    subprocess.run(['python', '/opt/agent/scripts/healthcheck.py'], check=True, timeout=15)
    for command in ('codex', 'claude', 'gh', 'github-mcp-server', 'google-chrome'):
        version = subprocess.check_output([command, '--version'], text=True, timeout=45).strip()
        assert version, f'{command} has no version output'
        print(f'{command}: {version}')
    check_browser_workflow()
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
            terminal = None
            try:
                terminal = subprocess.Popen(
                    ['xterm', '-title', 'Agent smoke terminal', '-e', 'bash', '--noprofile', '--norc'],
                    start_new_session=True,
                )
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
                try:
                    if terminal is not None:
                        stop_process_group(terminal)
                finally:
                    stop_process_group(chrome)


if __name__ == '__main__':
    main()
