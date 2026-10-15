# Bundled agent skills

The image carries these skills under `/opt/agent/skills`:

| Skill | Purpose | Source |
| --- | --- | --- |
| `frontend-design` | Deliberate visual design for frontend work | Anthropic |
| `skill-creator` | Draft, evaluate, and improve skills | Anthropic |
| `desktop` | Browser actions with Playwright and desktop actions with Cua | This repository |
| `decisions` | Batched typed Jev decisions with uncertainty | This repository |
| `memory` | Hindsight recall and retention workflow | This repository |

`frontend-design` and `skill-creator` are vendored without content changes from
[anthropics/skills](https://github.com/anthropics/skills) at commit
[`dbd4588f9e1033efb41dad4bef2f7947c8993d44`](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44).
Each retains its upstream `LICENSE.txt` containing the Apache 2.0 terms. The
skill-creator copy includes its agents, reference schemas, validation scripts,
and evaluation viewer. `skills/anthropic-provenance.json` records the source
paths and SHA256 of every vendored file so updates can be reviewed precisely.

The upstream skills are instructions and local tools. Installing them does
not run frontend work, make model calls, or publish artifacts. Some optional
skill-creator scripts invoke an authenticated Claude CLI; use those only when
that runtime is configured. The repository does not add model credentials.

## Updating desktop and Jev guidance

The `desktop` and `decisions` skills keep their existing names so existing
discovery and links continue to work. Use the installed skill-creator workflow
to revise them:

1. Snapshot the original skills outside the checkout.
2. Describe the intended triggers and workflows, using the installed browser,
   desktop, and Jev tool schemas rather than assumed APIs.
3. Run the same fixture and prompt against the original and revised skill in
   independent, parallel agents.
4. Grade explicit expectations, generate a benchmark, and create the upstream
   static review viewer for human feedback.

Reusable scenarios live in each skill's `evals/evals.json` and
`evals/files/scenario.json`. The desktop scenario covers stale state, a batched
checkbox/export sequence, partial failure, background-only authorization, and
honest completion reporting. The Jev scenario covers choice ranking, batched
readiness questions, confident-but-insufficient evidence, mixed uncertainty,
missing credentials, and avoiding a model call for a directly observed Python
version. Both skills also include a browser-delivery scenario covering fresh
Playwright targets, batched Jev checks, uncertain evidence, an authorized option
selection, and verification without placing an order.

These evaluations are explicitly **planning simulations**. The agents read
stored tool observations and produce labelled proposed calls; they do not
operate a live desktop, call Jev, or establish model latency or task success.
Each skill has two tracked scenarios. The original comparisons used one
scenario per skill; the browser comparison applies both skills to their shared
scenario. These checks use one run per version, so passing them is a narrow
regression signal rather than evidence of general quality or speed gains.

Reusable prompts and fixtures are tracked in the repository. Keep generated
snapshots, run outputs, grades, benchmarks, and review pages local and untracked.
Mark token and duration data unavailable when no measurements are supplied.

## Validation and future updates

Run the repository's skill checks with the normal Python test suite. They
check vendored hashes, bundled resources, local frontmatter, and evaluation
fixture integrity. For upstream validation, install PyYAML in a development
virtual environment and run:

```sh
python skills/skill-creator/scripts/quick_validate.py skills/desktop
python skills/skill-creator/scripts/quick_validate.py skills/decisions
```

To update the external skills, select and review a new official commit, copy
each complete skill directory including its license, regenerate the provenance
hashes, and rerun validation. Do not edit vendored content to accommodate local
changes; keep repository-specific instructions in the local skills.

Before making a broad quality claim, expand the scenarios with real supported
desktop applications, changed screen states, model/API failures, and tasks
that should not trigger each skill. Measure real outcomes and latency
separately from these fixture-based planning checks.
