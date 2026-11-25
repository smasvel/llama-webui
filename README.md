# llama-web

A self-hosted, authenticated workspace for local AI chat, model management and
coding agents. Chat runs against a single local **llama.cpp engine**; optional
hosted models from opencode.ai appear in a separate online section.

- [Features](#features)
- [Fixed ports](#fixed-ports)
- [Installation](#installation)
- [Using llama-web](#using-llama-web)
- [Configuration](#configuration)
- [Development](#development)
- [Safety](#safety)

## Features

### Chat

- Streaming answers with saved conversations: regenerate, edit, truncate and delete chats.
- Rich responses: headings, bullet and numbered lists, GFM tables, fenced code,
  inline HTML (sanitised), inline images, Unicode emoji and shortcode/emoticon conversion.
- Tool-enabled turns using local MCP servers: Playwright browser inspection and a
  persistent Python REPL, selected from the user's request. Web search supplies
  live context and citations.
- Attachments, cross-chat recall using local embeddings, and browse history.
- Voice input and spoken replies; image generation when a supported local image
  model is loaded.
- Per-user model preference, request limits and thinking toggle.

### Models and downloads

- Models page lists GGUFs in the llama-web model library, grouped by service, with
  load, unload, delete, context-size and thinking controls.
- Upload a local GGUF or download one from Hugging Face. Downloads show real file
  sizes, tool-calling metadata and completion progress, and land in the same library.
- Native end-of-turn tool-call markup and raw JSON payloads are rendered as readable
  tool calls, tables or bullet lists instead of raw text.
- Online tab lists opencode.ai models when an API key is configured.

### Coding agents

- OpenCode assignments run in an isolated Docker environment with live progress,
  workspace files, checkpoints, verification commands and saved results.
- Schedules, reusable skills, private-by-default assignments and explicit sharing.
- A persistent supervisor picks up queued work; assignments can be rerun or edited and rerun.

### Administration and operations

- User accounts, roles, per-user agent access and auth-event history.
- Settings for chat limits, web search, gateway identities, TLS and service controls.
- Live request feed with per-request traces and cancellation; request metrics API
  and recent-request filtering.
- Quizzes with saved decks, generated quizzes and quizzes with saved scenarios.
- Local HTTPS certificate management and generated OpenCode/Continue client configs
  from the Help panel. No automatic rewrite of the desktop OpenCode config.

## Fixed ports

These ports are part of the installation contract; change them only together with
the service units, Caddyfile and application configuration.

| Port | Service | Binding | Use |
|---|---|---|---|
| 18471 | Caddy | `127.0.0.1` | Local gateway for OpenAI-compatible clients; forwards to 18474 |
| 18472 | PostgreSQL | `127.0.0.1` | Application database (`llama-web`) |
| 18473 | llama.cpp (`llama-server`) | `127.0.0.1` | Model inference, `/v1/models`, `/v1/chat/completions` |
| 18474 | llama-web | `0.0.0.0` | HTTPS web UI and API; Tailscale access uses this port |

The session cookie is named `llama_web_session_18474_v3`; the port and generation
keep logins independent of other installs and of stale cookies from older builds.

## Installation

### Linux (systemd)

Run as your regular user (sudo is used only for missing packages):

```bash
bash deploy/install-first-time.sh
```

This installs host packages, starts PostgreSQL, installs the app and Python
environment, provisions the local Playwright/Python MCP tools, and enables the
three `llama-web-*` systemd user units. To skip the Chromium download, add
`--skip-browser`; to install dependencies without services, use `--no-service`.

You can also run `bash deploy/install-workspace.sh` on a host that already has
PostgreSQL and Caddy.

### Windows and macOS (release bundles)

Release bundles ship `deploy/install-release.ps1` (PowerShell) and
`deploy/install-release.sh` (macOS/Linux shell). They install the Python
environment, PostgreSQL via Docker Compose, the agent image on Linux, and a
llama.cpp engine binary for the host platform — the PrismML fork on Linux (so
PTQ1_0 ternary GGUFs work) and the upstream build on Windows/macOS. Models are
downloaded from the Downloads page after first sign-in.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File deploy\install-release.ps1
```

```bash
# macOS
bash deploy/install-release.sh
```

Start the app with `deploy/start-llama-web.ps1` (Windows) or
`bash deploy/start-llama-web.sh` (macOS/Linux), then open the HTTPS URL printed
by the installer. On first visit, create the administrator account.

Open `https://localhost:18474/` on Linux systemd installs; release installs
use the port printed by their installer (default 5443).

See [deploy/FIRST_TIME_INSTALL.md](deploy/FIRST_TIME_INSTALL.md) for full setup
and operational details.

## Using llama-web

1. Sign in and open **Downloads** to fetch a GGUF, or **Models** to upload one.
2. In **Models**, load the GGUF and optionally set its context size. On Linux the
   app writes the choice into `~/.config/llama-web/server.conf` and restarts the engine.
3. Pick the loaded model in **Chat** and start a conversation. Ask for a table,
   headings or bullets when you want formatted output; ask for a live lookup to
   enable browser tools.
4. **Agents** is optional: create an assignment, watch its progress and collect the
   result. Private by default; share explicitly when needed.
5. **Settings** and **Users** are admin-only. **Live logs** streams diagnostics.

## Configuration

Linux systemd installs use:

- Per-user credentials (opencode.ai API key and personal Caddy LAN token) live encrypted
  in the `user_credentials` table, scoped to each account and never shown to others;
- `~/.config/llama-web/server.conf` — `LLAMA_SERVER`, `LLAMA_MODEL`, `LLAMA_ARGS`,
  `LLAMA_ARG_CTX_SIZE`;
- `~/.config/llama-web/engine-key` — local engine bearer token;
- `~/.config/llama-web/postgres-password` — shared database secret;
- `~/.local/share/llama-web/models/` — downloaded GGUF library; its metadata is
  imported once into PostgreSQL and served from there afterwards.

Override paths with `LLAMA_WEB_CONFIG`, `LLAMA_MODEL_LIBRARY`,
`LLAMA_WEB_ENGINE_BINARY`, `LLAMA_WEB_NODE_BIN`, `LLAMA_WEB_PLAYWRIGHT_MCP_CLI`,
`LLAMA_WEB_CHROMIUM_PATH` and `PLAYWRIGHT_BROWSERS_PATH`.

On Linux the engine is the PrismML fork under
`~/.local/share/llama-web/engine-prism/` (installed by
`deploy/install-prism-engine.sh`). Besides ordinary quants it loads PTQ1_0 ternary
GGUFs such as `Ternary-Bonsai-2-27B-PTQ1_0`, which the upstream engine cannot read.

On the Radeon 890M Vulkan runtime, a 65,536-token served window needs a smaller
GGUF: the verified choices are `Ternary-Bonsai-2-27B-PTQ1_0` (~6 GB),
`Qwen3.8-27B-UD-KQVQ4-IQ3Floor-10G-MTP` (~11 GB) and `Qwen3.8-27B-Q3_K_M`
(~13 GB). The 16 GB NVFP4 build is limited to 16,384 tokens. Every model's native
maximum is read from its GGUF metadata and shown on the Models page; the loading
context must be smaller than that maximum.

MCP chat tools (Playwright browser and Python REPL) and live web search are
enabled by default; the Settings page has independent switches for the browser
tools and the Python REPL and can change web search behaviour. Chat tool routing
is described in `deploy/FIRST_TIME_INSTALL.md`.

## Development

Code lives in `llama_web/` by domain; `app.py` is the web entry point and
`deploy/` holds installers and service definitions.

```bash
.venv/bin/python tools/run_tests.py
.venv/bin/python tools/run_tests.py --browser
```

The browser runner discovers `tools/check_*_ui.py` suites and runs desktop and
mobile checks. Use `tests/run_playwright_mcp_chat_regression.py` for a live chat
regression against a running instance.

## Safety

Use at your own risk. Model output may be wrong; verify important results and
check model licences. This project is not a source of medical, legal or financial
advice, including trading or investment recommendations. Keep the web UI and
gateway on loopback or a trusted private network/Tailscale; protect the database
password, engine key and TLS key as secrets.
