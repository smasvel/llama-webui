# llama-web

A self-hosted, authenticated workspace for local AI chat, model management and
coding agents. Chat runs on one local **llama.cpp engine**; hosted opencode.ai
models are available as an optional online backend.

- [Features](#features)
- [Fixed ports](#fixed-ports)
- [Installation](#installation)
- [Using llama-web](#using-llama-web)
- [Configuration](#configuration)
- [Development](#development)
- [Safety](#safety)

## Features

### Chat

- Streaming answers with saved conversations: regenerate, edit, truncate, delete.
- Rich replies: headings, lists, GFM tables, fenced code, sanitised HTML, images,
  emoji and shortcodes.
- **Voice call** from the composer's phone button: an animated globe listens,
  transcribes and replies automatically. Local faster-whisper is the primary
  speech-to-text engine, with browser transcription only as a fallback; English uses
  local Kokoro, Tamil uses the neural Piper Rasa Female voice, and Hindi uses Kokoro's
  Hindi voice. eSpeak NG is only a last-resort fallback for Indic scripts. The call uses the model selected in Chat, speaks short replies and
  saves each exchange to the transcript. It can research live topics or run local
  Python work through the same MCP tools as typed chat, then speak a short summary.
- Live web search with citations, plus Playwright browser and Python REPL MCP tools.
  The assistant chooses internally between answering from knowledge or supplied live
  evidence, browsing an authoritative source, and using Python for calculation or
  data work; it never narrates that decision. Temporary files created by these tools
  go in the git-ignored `chat-temp/` working directory.
- Attachments, cross-chat recall and browse history.
- User model preference, request limits and thinking controls.

### Models and downloads

- Models page with load, unload, delete, context-size and thinking controls.
- Native GGUF context maximum shown for each model; stored in PostgreSQL.
- Downloads default to trending llama.cpp function-calling GGUFs on Hugging Face.
- Local GGUF upload and Hugging Face downloads with progress and file sizes.
- Online tab lists opencode.ai models when a per-user API key is configured.

### Help panel tools

- The **Help** panel generates copyable OpenCode config for the local network and,
  when Tailscale is enabled, for the Tailscale network. It includes the app's model
  inventory with each model's real context and output limits, a placeholder for your
  opencode.ai key, the MCP tool block, and compaction budgets sized from the largest
  online model window (buffer as a percentage of each model's own window).
- Agent containers use that generated config. The desktop
  `~/.config/opencode/opencode.jsonc` is never rewritten automatically: copy the
  config from Help, or sync it yourself, when you want the desktop client updated.
- Help also shows the CA-trust commands for clients that do not yet trust the local
  HTTPS certificate.

### Coding agents

- Isolated Docker assignments with live progress, workspace files, checkpoints,
  verification commands and saved results.
- Automatic shell permissions and DuckDuckGo research by default, configurable
  per assignment.
- Local and hosted models routed through the app's `/v1` gateway.
- Schedules, reusable skills, private-by-default assignments and explicit sharing.
- Persistent supervisor with rerun and edit-and-rerun.

### Administration

- User accounts, roles, per-user agent access and auth-event history.
- Per-user opencode.ai keys and Caddy tokens encrypted in PostgreSQL.
- Settings for chat limits, web search, MCP tools, gateway identities and TLS.
- Live request feed with traces and cancellation; request metrics API.
- Quizzes with saved decks and scenarios.
- Local HTTPS certificate management and copyable OpenCode configs in Help.

## Fixed ports

| Port | Service | Binding | Use |
|---|---|---|---|
| 18471 | Caddy | `127.0.0.1` | Local gateway for OpenAI-compatible clients; forwards to 18474 |
| 18472 | PostgreSQL | `127.0.0.1` | Application database (`llama-web`) |
| 18473 | llama.cpp (`llama-server`) | `127.0.0.1` | Model inference, `/v1/models`, `/v1/chat/completions` |
| 18474 | llama-web | `0.0.0.0` | HTTPS web UI and API; Tailscale access uses this port |

The session cookie is named `llama_web_session_18474_v3`.

## Installation

### Linux (systemd)

Run as your regular user (sudo is used only for missing packages):

```bash
bash deploy/install-first-time.sh
```

This installs host packages, PostgreSQL, the app and Python environment, the local
MCP runtime and Chromium, the local voice models, and the `llama-web-*` systemd
user units. Use `--skip-browser` to skip Chromium or `--no-service` to install
without starting services.

### Windows and macOS

Release bundles ship `deploy/install-release.ps1` and `deploy/install-release.sh`.
They install the Python environment, PostgreSQL via Docker Compose and a prebuilt
`llama-server`. Start with `deploy/start-llama-web.ps1` (Windows) or
`bash deploy/start-llama-web.sh` (macOS/Linux), then open the HTTPS URL printed by
the installer. On first visit, create the administrator account.

See [deploy/FIRST_TIME_INSTALL.md](deploy/FIRST_TIME_INSTALL.md) for full details.

## Using llama-web

1. Sign in and open **Downloads** to fetch a GGUF, or **Models** to upload one.
2. In **Models**, load the GGUF and optionally set its context size.
3. Pick the loaded model in **Chat** and start a conversation, or click the phone
   button for a voice call. For research questions or small local tasks, the voice
   agent can use live search, the browser or the Python tool and speak the result.
4. **Agents** is optional: create an assignment, watch its progress and collect the
   result. Private by default; share explicitly when needed.
5. Open **Help** to copy an OpenCode config for your network and the CA-trust command
   for new clients.
6. **Settings** and **Users** are admin-only. **Live logs** streams diagnostics.

## Configuration

Linux systemd installs use:

- `~/.config/llama-web/server.conf` — engine path, model and arguments;
- `~/.config/llama-web/engine-key` — local engine bearer token;
- `~/.config/llama-web/postgres-password` — shared database secret;
- `~/.local/share/llama-web/models/` — GGUF library;
- `~/.local/share/llama-web/voice/` — local STT/TTS models;
- `chat-temp/` — git-ignored working directory for chat/tool scratch files;
- PostgreSQL tables `user_credentials` and `local_model_metadata` for credentials
  and model metadata.

Override paths with `LLAMA_WEB_CONFIG`, `LLAMA_MODEL_LIBRARY`,
`LLAMA_WEB_ENGINE_BINARY`, `LLAMA_WEB_NODE_BIN`, `LLAMA_WEB_PLAYWRIGHT_MCP_CLI`,
`LLAMA_WEB_CHROMIUM_PATH`, `PLAYWRIGHT_BROWSERS_PATH` and `LLAMA_WEB_TTS_VOICE`.
Speech recognition uses `LLAMA_WEB_STT_MODEL` (default `small`) and optionally
`LLAMA_WEB_STT_LANGUAGE` (auto-detect when unset).

On the Radeon 890M Vulkan runtime, a 65,536-token window needs a smaller GGUF:
`Ternary-Bonsai-2-27B-PTQ1_0`, `Qwen3.8-27B-UD-KQVQ4-IQ3Floor-10G-MTP` or
`Qwen3.8-27B-Q3_K_M`. The 16 GB NVFP4 build is limited to 16,384 tokens.

## Development

Code lives in `llama_web/` by domain; `app.py` is the web entry point and
`deploy/` holds installers and service definitions.

```bash
.venv/bin/python tools/run_tests.py
.venv/bin/python tools/run_tests.py --browser
```

## Safety

Use at your own risk. Model output may be wrong; verify important results and check
model licences. This project is not a source of medical, legal or financial advice.
Keep the web UI and gateway on loopback or a trusted private network/Tailscale;
protect the database password, engine key and TLS key as secrets.
