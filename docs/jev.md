# Jev decisions with Playwright and Cua

The image connects agents to Playwright MCP for browser pages, Cua Driver for
the desktop, and Jev for typed questions about supplied evidence. The agent
gathers current observations, asks Jev when decision support is useful, then
acts and verifies through the appropriate browser or desktop tool. Jev runs
on TypeSafe's hosted API. No Jev weights or GPU runtime are installed locally.

The official integration uses `typesafe-sdk==0.7.5` and its default
`jev-latest` model. The separate PyPI package named `jev` requires Python 3.14
and discards probabilities in its high-level decorators. Using the official
SDK directly preserves the uncertainty needed for routing decisions and works
with the image's Python 3.12.

## Configuration

Set `TYPESAFE_API_KEY` in the environment settings or local Compose `.env` file.
Keep the file out of Git. The key is read at runtime and never included in the
image or MCP configuration files. Without a key, MCP discovery still works;
calls return `status: unconfigured`, `abstain: true`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | unset | Authenticate requests to TypeSafe |
| `TYPESAFE_DEFAULT_MODEL` | `jev-latest` | An available model name or alias |
| `JEV_TIMEOUT_SECONDS` | `8` | Total deadline for each API call; maximum 60 seconds |
| `JEV_IMAGE_ROOTS` | `/workspace:/tmp` | Colon-separated directories allowed for explicit screenshot uploads |

Outbound HTTPS to `api.typesafe.ai` is required for model calls. The SDK also
supports `TYPESAFE_BASE_URL`; leave its official default unless intentionally
using a trusted compatible service. A key by itself does not establish model
availability or endpoint access. No API request is made during image builds.

The stdio server command inside the image is:

```sh
/opt/agent-venv/bin/python /opt/agent/integrations/jev/server.py
```

Use the MCP server's `jev_decide` and `jev_assess` tools from Codex or Claude
Code. TypeSafe receives the supplied context and any explicitly selected
image. Supply only the relevant evidence. The integration does not upload
screenshots automatically or persist prompts, screenshots, or decisions.

## Use Jev to answer bounded questions

`jev_decide` selects among explicit alternatives. It asks a choice question and
a question about evidence sufficiency together, in one API request. It returns
the complete distribution and a separate confidence value. A selection is
accepted only when its probability, confidence, and evidence sufficiency all
meet the threshold, 0.85 by default. `need_more_information` is always available
as an alternative. A timeout, API failure, missing key, invalid response, or
uncertain result produces abstention.

Example tool arguments:

```json
{
  "context": "A checkout snapshot lists Standard delivery in 3–5 business days for 4 euros and Express next business day for 8 euros, subject to an ordering cutoff that is not shown. Delivery by Friday is required, within a 10 euro shipping budget.",
  "question": "Which delivery option is supported by the available evidence?",
  "candidates": {
    "standard": "Select Standard only if its delivery window meets the required date.",
    "express": "Select Express only if its cutoff and delivery promise meet the required date."
  }
}
```

`jev_assess` answers up to 32 independent yes/no questions in one request. Each
answer contains `probability_yes` and `outcome: yes | no | uncertain`. With the
default threshold, 0.85 or above means yes, 0.15 or below means no, and the
middle range stays uncertain. For example:

```json
{
  "context": "A fresh desktop observation shows a download completion notice and the expected file in the download list.",
  "questions": {
    "download_complete": "Does this observation indicate that the download completed?",
    "error_visible": "Does the observation show a download failure?"
  },
  "image_path": "/tmp/current-window.png"
}
```

Images must be PNG/JPEG files, at most 10 MiB, inside `JEV_IMAGE_ROOTS`. The
path is resolved before reading, so a symlink cannot bypass the directory
boundary. A screenshot is evidence for a decision; it does not create a Cua
capture or authorize a pixel action.

Jev does not generate a prose explanation, fetch external facts, or establish
that an action succeeded. The main agent gathers evidence, uses the typed
result if helpful, and explains its answer from that evidence. For open-ended
research or missing information, gather facts first. A high confidence score
does not establish truth or override user instructions.

## Browser decisions with Playwright

The official `@playwright/mcp` 0.0.83 server is registered as `playwright`.
Use its accessibility/DOM snapshot for browser page content. Cua remains
available for native application controls, browser chrome, and desktop surfaces.
Keep the requested interaction method and the authorized task scope intact.

The normal loop is:

1. Obtain a fresh `browser_snapshot({})` for the intended page or tab. Read the
   relevant text and current element refs before choosing an action.
2. For an ambiguous choice or several checks sharing that observation, send
   the relevant snapshot text in one `jev_decide` or `jev_assess` request.
   A directly observed price, clear label, or trivial navigation step usually
   needs no Jev call. Ask for missing evidence when the result abstains or a
   required check remains uncertain.
3. Perform the authorized Playwright action using a current target. Version
   0.0.83 uses `target`, not an argument named `ref`: for a returned ref `e23`,
   a click is `browser_click({"target":"e23","element":"Express shipping"})`.
   The example ref must be replaced by the actual current one. Refresh after
   navigation, a changed DOM, a stale-target error, or a Cua action that changes
   the page. Jev does not return browser refs or make old ones current.
4. Verify the action's returned page state or take another snapshot. Selecting
   a shipping option and placing an order are separate actions; a confident
   Jev decision neither authorizes the purchase nor proves it happened.

Use `browser_take_screenshot({"scale":"css","type":"png"})` only when visual
evidence adds something the snapshot lacks. This version requires `scale`.
For an intended Jev upload, use the screenshot's returned local path; the
default artifact directory `/workspace/.browser-artifacts` is inside the
default image roots. Do not invent a filename or upload a screenshot merely
because a browser is present. The Playwright launcher uses the installed Chrome
with its own persistent profile; page refs belong to that browser context.

## Reduce browser and desktop overhead

```mermaid
flowchart LR
  A[User task] --> B[Agent gathers current evidence]
  B --> C[Playwright page snapshot]
  B --> D[Cua desktop state]
  B --> M[Relevant Hindsight recall]
  C --> E[Jev batches typed questions]
  D --> E
  M --> E
  E --> F{Enough evidence and confidence?}
  F -->|Yes| G[Agent chooses permitted next step]
  F -->|No| H[Gather missing evidence or reason further]
  G --> I[Browser or desktop action and verification]
```

Useful improvements already present:

- Batch questions sharing the same observation. `jev_decide` makes one call for
  both ranking and evidence sufficiency; `jev_assess` sends all named questions
  together. This avoids one model round trip per question.
- Reuse the asynchronous TypeSafe client and its connection pool for the MCP
  process lifetime. An eight-second deadline and no automatic SDK retries keep
  a failed optional decision from delaying the main agent repeatedly.
- Retain probabilities and abstention. Ambiguous decisions return to the main
  agent instead of silently choosing an action at a 0.5 cutoff.
- Start with exact shell, browser, or accessibility evidence when available.
  Send a screenshot when visual context contributes to the question.
- Use Playwright for page actions and state verification. Keep Cua for desktop
  interaction when needed; changing tools does not supply missing evidence or
  extend the authorized task. Skip Jev when the answer is already explicit.

Cua Driver 0.35.4 supplies additional useful mechanisms:

- Use fresh `element_token` values for semantic controls. Re-read a known
  window with `since: "latest"` for changes rather than repeating a large tree.
- Use `run_steps` for a known sequence, and `run_script` for bounded local
  loops or branches. Inspect the installed tool schema before using either.
- Use `get_window_state` for the exact window; its screenshot coordinates may
  be scaled. Use the returned scale and capture identity for later actions.
  Jev rankings do not replace the driver's targeting requirements.
- Keep one controller for a shared desktop. Multiple agent sessions do not
  isolate keyboard focus or application state.
- Run Cua under the same desktop user, `DISPLAY`, and
  `DBUS_SESSION_BUS_ADDRESS` as XFCE. AT-SPI needs the desktop session's D-Bus
  and `at-spi2-core`; successful MCP discovery alone does not prove capture,
  accessibility, or input delivery.

Do not reuse a decision after its UI evidence changes. No persistent response
cache is enabled. If caching is added later, key it by the exact immutable
state, questions, image digest, and model version, with a short expiry. Never
cache action authorization or reuse Cua element handles across snapshots.

## Further experiments

Measure a representative set of desktop tasks before making speed claims.
Compare the baseline agent with Jev routing using task success rate, total
wall-clock time, API latency p50/p95, main-model token usage, cost, and
abstention/error rates. Include wrong-but-confident decisions and changed UI
states. Tune thresholds on held-out tasks instead of lowering them merely to
increase the acceptance rate.

The official SDK also exposes `Box` questions for visual grounding, with an
example using `jev-preview`. This integration does not enable them: model
availability, localization accuracy, coordinate transforms, and fresh capture
identity need validation before connecting a predicted box to a click.

Hindsight can supply relevant prior outcomes to the context. Retain verified
results after completion, and evaluate whether retrieval plus typed ranking
reduces repeated exploration. The integration makes no automatic memory
writes and treats recalled content as evidence requiring current validation.

## Validation and sources

The repository tests exercise the official SDK's wire format, batched
questions, confidence gates, explicit abstention, malformed answers, deadlines,
and screenshot file boundaries without a paid API call. A stdio MCP smoke
check verifies tool discovery and graceful operation without a key. Real model
quality and latency require a configured TypeSafe account and endpoint access.

Verified source references:

- [TypeSafe introduction](https://docs.typesafe.ai/introduction)
- [Official Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python)
- [Official SDK package and license](https://pypi.org/project/typesafe-sdk/0.7.5/)
- [Official Playwright MCP](https://github.com/microsoft/playwright-mcp)
- [Pinned Playwright MCP package](https://www.npmjs.com/package/@playwright/mcp/v/0.0.83)
- [Cua Driver source](https://github.com/trycua/cua/tree/main/libs/cua-driver)
- [Cua Driver Linux guide](https://github.com/trycua/cua/blob/main/libs/cua-driver/rust/Skills/cua-driver/LINUX.md)
- [Cua MCP protocol and bundled skills](https://github.com/trycua/cua/blob/main/libs/cua-driver/docs/mcp-protocol-and-skills.md)
- [Cua Driver package](https://pypi.org/project/cua-driver/0.35.4/)

The SDK and Cua Driver are MIT licensed. The optional `cua-perception`
extension has a separate license, including an AGPL icon detector, and is not
installed by this image.
