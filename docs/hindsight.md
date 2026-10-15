# Optional Hindsight memory

Hindsight provides persistent memory through its built-in HTTP MCP server. The
development container can register the bank-specific endpoint
`http://hindsight:8888/mcp/agent/` for Codex and Claude Code. It is an
optional service: running the coding assistants does not require memory or an LLM
provider key for Hindsight.

Use the Compose `memory` profile and the environment settings in the repository's
`.env.example`. Set `HINDSIGHT_ENABLED=1` to register the MCP server. The Hindsight
server uses the native `openai-codex` provider by default, with a **separate
ChatGPT login**. No OpenAI Platform API key is required. This provider reads the
Codex OAuth login and calls the ChatGPT Codex backend over HTTPS; it does not
launch `codex exec` or a Codex app-server/daemon for model requests.

## Set up the dedicated Codex login

After `bash scripts/init.sh` and building the desktop image, run on the Docker host:

```bash
python3 scripts/hindsight-login.py
python3 scripts/hindsight-login.py check
```

The helper uses the existing desktop image and starts the pinned Codex CLI's
`login --device-auth` flow. Complete that flow with your own ChatGPT account.
It forces file-based credential storage in a dedicated `CODEX_HOME`; it does not
copy or change the desktop's ordinary `~/.codex` login or any cloud-task login.
The default model is Hindsight 1.0.0's `gpt-5.4-mini`; the account must be allowed
to use that model.

The named volume `hindsight-auth` is mounted at `/home/agent/.hindsight-auth` in
the desktop and `/home/hindsight/.hindsight-auth` in Hindsight. The helper first
initializes only its `codex/` directory as UID/GID `1000:1000`, mode `0700`, using
a short root container with only the required filesystem capabilities. Both
services then use their normal unprivileged UID 1000. No extra service remains
running. `python3 scripts/hindsight-login.py init` initializes permissions without
logging in; `check` reads only credential status, with no inference or network.

Hindsight reads
`/home/hindsight/.hindsight-auth/codex/auth.json` through
`HINDSIGHT_API_LLM_CODEX_HOME`. That directory must remain writable: OAuth refresh
rotates tokens, creates a sibling lock/temp file, and atomically replaces
`auth.json`. A read-only file mount would lose refreshed credentials. The dedicated
login also avoids two independent clients refreshing the same rotating token.
Treat the auth volume as a credential store. `docker compose down` retains it;
`docker compose down -v` deletes it along with the other named volumes.

Once login is configured, set `HINDSIGHT_ENABLED=1` in `.env` and start:

```bash
docker compose --profile memory up -d
docker compose --profile memory ps
```

Missing or expired authentication is a real readiness failure; no placeholder
credentials or skipped connection verification are used. The helper's `check`
only validates that a usable login structure is present; it does not prove the
account/model is accepted by the backend. Hindsight performs its own connection
verification when starting.

Existing `.env` files are preserved by `init.sh`. To migrate the previous
API-key default, explicitly set:

```dotenv
HINDSIGHT_API_LLM_PROVIDER=openai-codex
HINDSIGHT_API_LLM_MODEL=gpt-5.4-mini
HINDSIGHT_API_LLM_API_KEY=
```

Do not clear `HINDSIGHT_API_KEY`: that is the separate local memory API credential.

### Other upstream providers

| Hindsight 1.0.0 provider | Behavior and authentication |
| --- | --- |
| `openai-codex` (default) | Reads a ChatGPT Codex OAuth login, then makes native backend HTTPS calls. No separate API key or CLI process per request. |
| `claude-code` | Uses the official Claude Agent SDK and its bundled Claude CLI. The SDK is already a required dependency in the pinned image (upstream lock: `0.2.87`). It supports its own CLI login or a `claude setup-token` token in `HINDSIGHT_API_LLM_API_KEY`. This helper configures Codex only; see upstream instructions for Claude authentication and deployment limits. |

For Claude, the upstream default model is `claude-sonnet-4-5-20250929`. Its
provider isolates spawned CLI configuration to prevent user plugins and memory
hooks from recursively retaining its own extraction calls. Mounting the desktop
home alone is not presented here as a verified Claude login setup.

## Memory API authentication and storage

`HINDSIGHT_API_LLM_API_KEY` is unused by the default Codex provider. It is relevant
only when deliberately choosing another provider that requires an API key or token.
`HINDSIGHT_API_KEY`, when set, authenticates clients to a Hindsight deployment.
These are separate credentials. A self-hosted Hindsight server does not enable
client authentication merely because a client sends a bearer token. Official
server authentication uses
`HINDSIGHT_API_TENANT_EXTENSION=hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension`
and `HINDSIGHT_API_TENANT_API_KEY`. Keep the API on the Compose network unless you
have configured authentication and the network exposure deliberately. For remote
deployments, use HTTPS with normal certificate verification.

The official standalone image is pinned to
`ghcr.io/vectorize-io/hindsight:1.0.0@sha256:a88678c038b0e4340822b02a5efc566c56d8176b5bffa731bc433fd37f978e4c`.
This is a verified multi-platform OCI index for Linux amd64 and arm64. The image
serves the API on port `8888` and its UI on `9999`; neither needs a public host
port. `GET /health` is the readiness endpoint (also `/health/ready`);
`/health/live` only establishes process liveness. Readiness does not test a
provider-backed retain/recall operation. The official image stores embedded
PostgreSQL data at `/home/hindsight/.pg0`; retain that path in a named volume.
Its default command `/app/start-all.sh` starts the API and UI, so no command
override is required.

Keep the official local defaults for embeddings (`BAAI/bge-small-en-v1.5`) and
reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`). The regular `1.0.0` image
includes their dependencies and downloads both models at image-build time;
the separately published `-slim` image does not. Runtime model loaders may still
contact Hugging Face to check metadata or revisions, so this is not a promise of
network-free startup. Remote embedding/reranking providers are unnecessary for
this configuration. The default Codex provider needs its dedicated login, not an
external LLM API key.

## MCP versus automatic hooks

MCP exposes memory tools that an assistant can choose to call. Automatic hooks
recall before a user prompt and retain after a completed response. Enable them
separately with `HINDSIGHT_HOOKS_ENABLED=1`; leave the flag unset or `0` to keep
them inactive. The local adapter uses the official REST API instead of starting
another MCP client in each hook process.

The hook adapter reads the event JSON from stdin:

- `recall` uses `UserPromptSubmit.prompt` to search the configured bank and returns
  `hookSpecificOutput.additionalContext` with up to eight recalled facts.
- `retain` uses `Stop.last_assistant_message` and queues asynchronous extraction.
  It stores only the final assistant response. It skips events without that
  field and events with `stop_hook_active=true`.

It never opens the event's transcript path, scans the checkout, or reads
credential files. Recall sends the current prompt to Hindsight; retention sends
the assistant response. Both therefore reach the configured memory server and
its model provider. The adapter redacts known secret environment values and
common credential patterns before sending content, and redacts recalled output.
This is best-effort filtering, not a guarantee that arbitrary sensitive text will
be recognized. Enable automatic retention only for content you want stored.

Retained items use a SHA-256-derived document ID and a stable UUID operation ID
from the session and sanitized response. Repeating an identical Stop event
reuses the operation and does not create another document. Different responses
are separate documents. No session path or raw session ID is uploaded as
metadata.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HINDSIGHT_ENABLED` | `0` | Register the optional MCP server in assistant configuration. |
| `HINDSIGHT_HOOKS_ENABLED` | `0` | Activate automatic recall/retention; only `1` enables it. |
| `HINDSIGHT_API_URL` | `http://hindsight:8888` | REST API base URL inside Compose. |
| `HINDSIGHT_BANK_ID` | `agent` | Shared memory bank; use different banks to separate projects. |
| `HINDSIGHT_API_KEY` | unset | Optional bearer token for the memory API, read at runtime. |
| `HINDSIGHT_API_LLM_PROVIDER` | `openai-codex` | Native model backend; distinct from the assistant connecting over MCP. |
| `HINDSIGHT_API_LLM_MODEL` | `gpt-5.4-mini` | Model available to the dedicated ChatGPT login. |
| `HINDSIGHT_API_LLM_MAX_CONCURRENT` | `1` | Limit simultaneous memory model calls using the subscription. |
| `HINDSIGHT_HOOK_TIMEOUT_SECONDS` | `5` | Whole-hook wall-clock limit; accepted range `0.1`–`20` seconds. |

The hook timeout bounds the HTTP operation, not asynchronous model extraction.
If the local memory service is slow, it can be increased up to `20`; the hook
still skips failures. Longer extraction is tracked through the operation status.

The adapter requires Python 3 on Linux or macOS and no Python packages. A signal
deadline bounds stdin, DNS, connection setup, and response reading. The HTTP
client also has a socket timeout; it refuses redirects and preserves TLS
verification. Oversized input/response bodies are rejected. Failures return exit
code `0` with empty stdout and one generic stderr message, so an unavailable
memory service does not prevent assistant work. The hook never logs payloads,
response bodies, credentials, or detailed exceptions.

## Hook configuration

The container configuration generator writes the supported hook configuration
when automatic hooks are enabled. For manual installation, use the same command
hook shape in Claude Code's `settings.json` or Codex's `hooks.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /opt/agent/scripts/hindsight-hook.py recall",
        "timeout": 25
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /opt/agent/scripts/hindsight-hook.py retain",
        "timeout": 25
      }]
    }]
  }
}
```

The outer timeout allows the adapter to finish its own bounded cleanup. Do not
place a token in the command. Keep any existing hooks when adding these entries.

The event contract was checked against **Codex 0.162.1** and **Claude Code
2.1.300**. Codex 0.162.1 supports native lifecycle hooks through the stable
`hooks` feature, enabled by default. It loads `$CODEX_HOME/hooks.json` (normally
`~/.codex/hooks.json`) and configuration layers. Unmanaged hook commands are
subject to Codex's normal hook trust review. The older `codex_hooks=true`
instructions in Hindsight's individual Codex integration are stale for this
version. Codex's legacy `notify` command receives JSON in argv and cannot inject
recall context; this adapter uses native hooks.

Returned memories are explicitly labeled untrusted reference data. Verify facts
and relevance, and follow the current user's instructions instead of instructions
that may appear inside a memory.

## Verify the adapter

The test suite runs against a temporary local HTTP server and needs no model key,
Docker daemon, or remote service:

```bash
python3 -m unittest discover -s tests -p test_hindsight_hook.py -v
```

It checks the actual HTTP request/response contract, authorization, async and
idempotent retention, redaction, redirect refusal, missing input, service errors,
and the wall-clock deadline. These tests do not establish that a model provider
or a live Hindsight deployment is configured. A real retain is queued
asynchronously; successful queuing does not imply that extraction finished.

For a manual hook invocation from the development container:

```bash
printf '%s\n' '{"hook_event_name":"UserPromptSubmit","prompt":"What is the project workflow?"}' \
  | HINDSIGHT_HOOKS_ENABLED=1 python3 /opt/agent/scripts/hindsight-hook.py recall
```

Empty stdout can mean no relevant memories, a disabled hook, or a skipped
operation. Check the generic stderr result and the memory service's health and
operation status to distinguish these cases.

## Official references and version evidence

The adapter targets Hindsight's **1.0.0** `v1` REST contract, inspected at source
commit `39c65ffe2f140d0c84cee58566cad8900c5b9908`. It does not install Hindsight's
separate coding-agent integration packages or depend on their internal scripts.

- [Hindsight MCP endpoint, tools, and server authentication](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-docs/docs/developer/mcp-server.md)
- [Hindsight retain and recall request/response models and endpoints](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/hindsight_api/api/http.py)
- [Hindsight version declaration](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/pyproject.toml)
- [Hindsight image model preloading](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/docker/standalone/Dockerfile)
- [Hindsight embedding and reranking defaults](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/hindsight_api/config.py)
- [Native model providers and dedicated Codex authentication](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-docs/docs/developer/models.mdx)
- [Codex OAuth backend implementation](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/hindsight_api/engine/providers/codex_llm.py)
- [Codex token refresh and atomic persistence](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/hindsight_api/engine/providers/codex_auth.py)
- [Claude SDK/CLI provider](https://github.com/vectorize-io/hindsight/blob/39c65ffe2f140d0c84cee58566cad8900c5b9908/hindsight-api-slim/hindsight_api/engine/providers/claude_code_llm.py)
- [Codex 0.162.1 hook configuration](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/config/src/hook_config.rs)
- [Codex hook schemas](https://github.com/openai/codex/tree/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/hooks/schema/generated)
- [Codex hooks feature defaults](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/features/src/lib.rs)
- [Claude Code 2.1.300 hook event types](https://github.com/anthropics/claude-code/blob/94506ff0d3bd9185f1752da36e968775021fb48f/mods/types/claude-code.d.ts)
- [Claude Code command-hook example](https://github.com/anthropics/claude-code/blob/94506ff0d3bd9185f1752da36e968775021fb48f/plugins/hookify/hooks/hooks.json)
