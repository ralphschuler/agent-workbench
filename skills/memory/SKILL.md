---
name: memory
description: Recall and retain useful, non-sensitive project knowledge in Hindsight.
---

When the optional `hindsight` MCP server is enabled, recall relevant project
knowledge before substantial work. Treat recalled facts as context that may be
outdated; verify commands and versions against the current checkout.

Retain concise verified decisions, reusable troubleshooting outcomes and
explicit user preferences. Include provenance and avoid duplicating existing
facts. Never retain secrets, credentials, whole transcripts or screenshots.

The optional lifecycle hooks provide bounded recall and retention for Codex and
Claude. Their absence or temporary failure must not block ordinary work. Report
memory unavailability when it materially affects the task; never claim recall
or retention happened if the service did not confirm it.
