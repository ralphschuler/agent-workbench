---
name: desktop
description: Operate and inspect the shared Linux desktop with Cua Driver. Use for Chrome or terminal GUI tasks, native application controls, screenshots, stale element handles, and verifying visible UI changes, even when the user does not mention CUA. Prefer direct shell or file tools for tasks whose outcome does not require a GUI.
---

Use the `cua` MCP server. It shares the desktop user's `DISPLAY` and
`DBUS_SESSION_BUS_ADDRESS`; the session label organizes calls but does not
isolate focus or authorize a target. Keep one controller for this desktop.

## Observe the exact target

1. Inspect the advertised tools before using unfamiliar parameters. The examples
   below follow the installed Cua Driver 0.35.4 Linux schema; rediscover after
   upgrades. Find the application with `list_windows` or `list_apps` and retain
   the returned integer `pid` and `window_id`.
2. Call `get_window_state` on that exact window. Prefer a bounded accessibility
   tree and its current screenshot. Use `query` for a relevant control rather
   than fetching a huge tree. On later reads pass `since: "latest"` to receive
   changes and a new `snapshot_id`.
3. Use the `element_token` returned by the latest snapshot for semantic actions.
   A new snapshot replaces old handles even when the visible row number stays
   the same. Refresh after a stale-token error; an unknown action outcome needs
   observation before retrying.

```json
{"pid": 241, "window_id": 4194318, "query": "Downloads", "session": "task"}
```

These are illustrative IDs, never substitutes for discovery. Screenshots use
the delivered image's coordinate frame, which may be scaled. Use pixels only
for a current target absent from the accessibility tree; preserve the returned
capture identity, scale, and exact window. Reobserve if the UI moved, another
window appeared, the image is stale, or capture failed. Missing pixels are not
evidence of a location. `screenshot` is not a standalone tool in this version.

## Act and verify

Use `run_steps` for the next known action or short sequence, with `observe: {}`
to obtain the resulting window changes in the same call. Each step uses the
actual action schema. Put actions needing existing element tokens before a
step that replaces the screen. When the next action depends on the result,
stop the batch there and inspect the observation.

```json
{
  "pid": 241,
  "window_id": 4194318,
  "session": "task",
  "steps": [{"tool": "click", "args": {"element_token": "s0000002a:11"}}],
  "observe": {}
}
```

The example token is illustrative. Replace it with the current returned token.
Use `get_window_state` with `since: "latest"` when another observation is
needed. `run_steps` stops at the first failed step; inspect partial results
before retrying. A tool's successful delivery is not proof of completion:
verify the requested application state at meaningful checkpoints and before
reporting success. Do not claim completion from a planned or simulated call.

`background_unavailable` is evidence of a route limitation. Keep
`foreground_fallback` false unless visible foreground control is already
authorized; neither a tool failure nor a Jev decision grants additional scope.
Respect the user's stated interaction method, login and confirmation boundaries,
and explicit grants for existing browser profiles.

## Use visual decisions selectively

For an ambiguous finite choice, consult the `decisions` skill and use Jev with
the relevant fresh evidence. A clear control label or trivial fact usually
needs no model call. `get_window_state` supports
`screenshot_out_file: "/tmp/current-window.png"` for an explicitly intended
Jev image upload. Read the returned `screenshot_file_path`; do not invent a
path or treat a saved file as a fresh Cua action capture. Jev abstention means
gather evidence or use normal reasoning within the authorized scope.

If all accessibility trees are empty or degraded, check that Cua and the app
share the desktop user, display, and D-Bus session and that the accessibility
bus is running. Inspect `cua-driver doctor` diagnostics rather than repeatedly
clicking or silently switching input routes.
