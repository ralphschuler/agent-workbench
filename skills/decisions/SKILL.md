---
name: decisions
description: Use Jev to compare explicit alternatives or answer several evidence-based yes/no questions in one call, retaining probabilities and abstention. Use for ambiguous Playwright browser or CUA desktop choices, task routing, or multiple checks sharing an observation. Skip Jev for trivial facts, a clear DOM or control label, arithmetic, open-ended prose, or missing external evidence; gather evidence or use normal reasoning first.
---

The `jev` MCP server calls the official TypeSafe SDK, normally with
`jev-latest`. Jev provides typed decisions about supplied evidence; the main
agent gathers facts, chooses permitted actions, and explains the answer.

## Choose one bounded request

Inspect the tool schema when uncertain. The installed integration exposes:

| Tool | Required arguments | Result |
| --- | --- | --- |
| `jev_decide` | `context` string, `question` string, `candidates` mapping labels to descriptions | Ranked alternatives, full `probabilities`, `confidence`, evidence sufficiency, `selected`, and `abstain` |
| `jev_assess` | `context` string, `questions` mapping names to yes/no questions | Each answer's `probability_yes` and `outcome` of `yes`, `no`, or `uncertain` |

Both accept `image_path` and `confidence_threshold`, default 0.85. Batch up to
32 independent yes/no questions sharing one context with `jev_assess` rather
than making one call per question. `jev_decide` already batches ranking and
evidence sufficiency. Use concrete alternatives with distinct descriptions;
`need_more_information` is reserved and added by the server.

```json
{
  "context": "Fresh application observation: the downloaded report has a completed badge, and a separate upload is still running.",
  "questions": {
    "download_complete": "Does the observation indicate that the report download completed?",
    "upload_complete": "Does the observation indicate that the separate upload completed?"
  }
}
```

Use only evidence relevant to the question. An optional image is sent to
TypeSafe with the context. Use an explicitly selected PNG/JPEG no larger than
10 MiB inside `JEV_IMAGE_ROOTS`, default `/workspace:/tmp`; omit the image when
text or semantic state is sufficient. Do not send credentials or unrelated
desktop content. Start browser questions with fresh Playwright
`browser_snapshot` text; start native desktop questions with Cua state. Use the
path returned by `browser_take_screenshot` or Cua's `screenshot_out_file` only
when an image is useful. Keep browser refs and Cua capture identity with the
observation that produced them rather than expecting Jev to return them.

For a browser decision, the loop is fresh snapshot, one useful batched Jev
request, an authorized Playwright action with a current ref, and verification
of the new page state. Reobserve if the DOM changes while deciding. Do not send
a fact already explicit in the snapshot through Jev merely to restate it.

## Preserve uncertainty and action boundaries

For `jev_decide`, act on a selection only if `status` is `ok`, `abstain` is
false, and `selected` names an offered substantive alternative. The server
requires confidence, selected probability, and evidence sufficiency all to
meet the threshold. `proposed` alone is not an accepted selection. Do not lower
the threshold just to obtain a choice or reinterpret `need_more_information`.

For `jev_assess`, read each outcome separately. At the default threshold,
probability 0.85 or above yields `yes`, 0.15 or below yields `no`, and the middle
range remains `uncertain`. The top-level `abstain` may be true when only one
question is uncertain; retain the other answers without turning uncertainty
into a yes or no.

On abstention, gather a new relevant observation, revise an unclear question,
or continue normal reasoning. Do not repeat the same uncertain request without
new evidence. A confident answer cannot authorize an action, override user
intent, establish an external fact, or prove that an application changed.
Refresh changed UI evidence and verify the result through Playwright for page
actions or Cua for desktop actions.

If `status` is `unconfigured`, `timeout`, `unavailable`, or `invalid_response`,
continue with normal reasoning and available tools. Request a
`TYPESAFE_API_KEY` only if Jev is necessary and missing access is established;
use secure environment settings, never ask for the value in chat. Do not claim
that Jev ran when no inference occurred.

Consult the repository's `docs/jev.md` when available for configuration, source
links, performance tradeoffs, and evaluation limits.
