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
- Rich responses: headings, lists, GFM tables, fenced code, sanitised inline HTML,
  images, Unicode emoji and shortcodes.
- **Voice call**: click the phone button in the composer for a draggable ChatGPT-style
  **round globe**; listening, transcribing and replying are automatic, and × ends the
  call. Live web tools are available on every chat and voice turn when web access is
  enabled, so the model can look things up without a keyword trigger. Live speech
  appears as you talk; speech recognition uses faster-whisper
  with browser live transcription, speech uses a natural local Kokoro voice (Piper
  fallback), and the model is the one selected in Chat. Each complete sentence is
  spoken as soon as it streams in. Voice questions use the same live-search and MCP
  tool routing as typed chat (Playwright browser or local Python tool), execute tool
  calls and answer from their results. Replies use the real chat transcript as context,
  skip long local reasoning preambles, and retry the selected model on a silent reply
  rather than switching models. Voice turns are saved in chat. No LM Studio or Lemonade.
- Voice input and spoken replies from the local audio models.
- Live web search with citations; blocked or captcha pages are skipped, not retried.
- Local MCP tools for browser inspection and a persistent Python REPL, enabled by
  default with independent Settings switches.
- Attachments, cross-chat recall using local embeddings, and browse history.

### Models and downloads

- Models page lists GGUFs with load, unload, delete, context-size and thinking
  controls. Metadata comes from PostgreSQL, not from parsing files at request time.
- Native maximum context from each GGUF is shown; the loading context must be
  smaller than that maximum.
- Downloads default to trending llama.cpp function-calling GGUFs from Hugging Face.
- Upload a local GGUF or download one from Hugging Face with real file sizes and
  completion progress.
- Online tab lists opencode.ai models when a per-user API key is configured.

### Coding agents

- OpenCode assignments run in isolated Docker containers with live progress,
  workspace files, checkpoints, verification commands and saved results.
- Shell commands run automatically and DuckDuckGo online research is enabled by
  default; both are editable per assignment.
- Local llama.cpp and hosted opencode.ai models are routed correctly through the
  app's `/v1` gateway, with model-appropriate timeouts.
- Schedules, reusable skills, private-by-default assignments and explicit sharing.
- A persistent supervisor picks up queued work; assignments can be rerun or edited.

### Administration and operations

- User accounts, roles, per-user agent access and auth-event history.
- Per-user opencode.ai keys and personal Caddy tokens stored encrypted in PostgreSQL.
- Settings for chat limits, web search, MCP tools, gateway identities and TLS.
- Live request feed with traces and cancellation; request metrics API.
- Quizzes with saved decks, generated quizzes and saved scenarios.
- Local HTTPS certificate management and generated OpenCode/Continue configs from
  the Help panel.

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
MCP tools and Chromium, the local voice models (faster-whisper and Piper), and the
`llama-web-*` systemd user units. Use `--skip-browser` to skip Chromium or
`--no-service` to install without starting services.

### Windows and macOS

Release bundles ship `deploy/install-release.ps1` and `deploy/install-release.sh`.
They install the Python environment, PostgreSQL via Docker Compose and a prebuilt
`llama-server`. Start with `deploy/start-llama-web.ps1` (Windows) or
`bash deploy/start-llama-web.sh` (macOS/Linux), then open the HTTPS URL printed by
the installer. On Linux the engine is the PrismML fork so PTQ1_0 ternary GGUFs work.
On first visit, create the administrator account.

See [deploy/FIRST_TIME_INSTALL.md](deploy/FIRST_TIME_INSTALL.md) for full details.

## Using llama-web

1. Sign in and open **Downloads** to fetch a GGUF, or **Models** to upload one.
2. In **Models**, load the GGUF and optionally set its context size.
3. Pick the loaded model in **Chat** and start a conversation, or click the phone
   button for a voice call with an animated assistant.
4. **Agents** is optional: create an assignment, watch its progress and collect the
   result. Private by default; share explicitly when needed.
5. **Settings** and **Users** are admin-only. **Live logs** streams diagnostics.

## Configuration

Linux systemd installs use:

- `~/.config/llama-web/server.conf` — engine path, model and arguments;
- `~/.config/llama-web/engine-key` — local engine bearer token;
- `~/.config/llama-web/postgres-password` — shared database secret;
- `~/.local/share/llama-web/models/` — GGUF library;
- `~/.local/share/llama-web/voice/` — local STT/TTS models;
- PostgreSQL tables `user_credentials` and `local_model_metadata` for credentials
  and model metadata.

Override paths with `LLAMA_WEB_CONFIG`, `LLAMA_MODEL_LIBRARY`,
`LLAMA_WEB_ENGINE_BINARY`, `LLAMA_WEB_NODE_BIN`, `LLAMA_WEB_PLAYWRIGHT_MCP_CLI`,
`LLAMA_WEB_CHROMIUM_PATH` and `PLAYWRIGHT_BROWSERS_PATH`.

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

The browser runner discovers `tools/check_*_ui.py` suites and runs desktop and
mobile checks.

## Safety

Use at your own risk. Model output may be wrong; verify important results and check
model licences. This project is not a source of medical, legal or financial advice.
Keep the web UI and gateway on loopback or a trusted private network/Tailscale;
protect the database password, engine key and TLS key as secrets.
