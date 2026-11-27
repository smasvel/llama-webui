# llama-web first-time installation

llama-web is a single-user web workspace around one local **llama.cpp engine**. It
runs on Ubuntu/Debian with systemd user services and Docker (PostgreSQL and the
isolated coding-agent container).

## Ports

These ports are part of the installation contract. Change them only together with
the service units, Caddyfile and application config.

| Port | Service | Binding | Purpose |
|---|---|---|---|
| 18471 | Caddy | `127.0.0.1` | Local gateway for OpenAI-compatible clients; forwards to 18474 |
| 18472 | PostgreSQL | `127.0.0.1` | Application database (`llama-web`) |
| 18473 | llama.cpp engine (`llama-server`) | `127.0.0.1` | Model inference, `/v1/models`, `/v1/chat/completions` |
| 18474 | llama-web app | `0.0.0.0` | HTTPS web UI and API; Tailscale access uses this port |

The app listens on 18474 through `PORT=18474` in `llama-web.service`. The session
cookie is named `llama_web_session_18474_v3`; the port and generation make logins
independent of other local installs and of cookies left by older builds.

## Install

Run as your regular user (never as root):

```bash
bash deploy/install-first-time.sh
```

The script installs missing Ubuntu packages, starts PostgreSQL from
`deploy/compose.postgres.yaml`, then runs `deploy/install-workspace.sh`, which:

- creates `~/.config/llama-web/` (engine key, session key, database password,
  `server.conf`),
- installs the app and the `llama_web` package into `~/.local/share/llama-web/`,
- installs the local Playwright MCP runtime and Chromium for browser tools,
- installs the local voice models (faster-whisper, Kokoro and Piper fallback) under
  `~/.local/share/llama-web/voice/`; `ffmpeg` must be available on `PATH`,
- installs and enables the `llama-web.service`, `llama-web-engine.service` and
  `llama-web-caddy.service` user units.

Skip Chromium download with `--skip-browser`. To install only host packages, use
`--no-service`.

### Windows and macOS

Release bundles use the platform installers instead of the systemd units:

- Windows: `powershell -ExecutionPolicy Bypass -File deploy\install-release.ps1`
- macOS: `bash deploy/install-release.sh`

These install the Python environment, PostgreSQL through Docker Compose and a
prebuilt `llama-server` for the host platform into
`~/.local/share/llama-web/engine/`, then write `~/.config/llama-web/server.conf`
and an engine key. Set `LLAMA_WEB_ENGINE_BINARY` to use an existing llama-server
instead of the downloaded binary. The app is started with
`deploy/start-llama-web.ps1` on Windows or `bash deploy/start-llama-web.sh` on
macOS/Linux; the installer prints the URL to open (default `https://localhost:5443`).
Docker is required for PostgreSQL and for the isolated coding-agent image on Linux.

After installation, open `https://localhost:18474` and accept the self-signed
certificate. The first visit asks you to create the administrator account.

## Engine and models

`~/.config/llama-web/server.conf` is the engine configuration:

```bash
LLAMA_SERVER="/home/<user>/.local/share/llama-web/engine-prism/llama-server"
LLAMA_MODEL="/home/<user>/.local/share/llama-web/models/<repo>/<file>.gguf"
LLAMA_ARGS="-dev Vulkan0 -ngl 99 --flash-attn off -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 -ctk f16 -ctv f16 --cont-batching --kv-unified --parallel 1"
LLAMA_ARG_CTX_SIZE=16384
```

On a fresh install `LLAMA_MODEL` is a placeholder. Download a GGUF from the
**Downloads** page (it lands in `~/.local/share/llama-web/models/`). The default list
shows trending llama.cpp function-calling GGUFs from Hugging Face; the "Browse the full
tool-calling GGUF catalog" link opens the same filtered view on huggingface.co. Set
`LLAMA_MODEL` to that file, and restart:

```bash
systemctl --user restart llama-web-engine.service
```

Models can also be loaded from the Models page, which writes the selected GGUF
and context size into `server.conf` and restarts the engine. The engine is a
single-model server: loading one model replaces the currently served one.

Account credentials are stored in PostgreSQL in `user_credentials`, encrypted with a
key derived from the database password and bound to each user id and credential type.
Each account has its own opencode.ai API key and personal Caddy LAN token; other users
cannot read them. On upgrade, legacy plaintext tokens and the old global opencode.ai key
are migrated into that table once and removed from their old locations.

Model metadata (name, publisher, size, quantisation and native context) is stored
in PostgreSQL in `local_model_metadata`, not read from GGUF files at request time.
On first start after upgrading, existing GGUFs are imported into that table once;
downloads and uploads refresh it automatically. This same table feeds the Chat
and agent model pickers.

Linux installs use the PrismML llama.cpp fork from
`~/.local/share/llama-web/engine-prism/` (installed by
`deploy/install-prism-engine.sh`). It serves ordinary quants and also loads
PTQ1_0/PQ2_0 ternary GGUFs such as `Ternary-Bonsai-2-27B-PTQ1_0`; the upstream
engine cannot load tensor type 143 (PTQ1_0) and fails with "failed to read tensor
info". The Prism engine does not accept `--no-mmap` (mmap is its default), so the
flags above omit it.

On the Radeon 890M Vulkan runtime, 65,536-token loading works with the smaller
MTP IQ3Floor (~11 GB), Q3_K_M (~13 GB) and Ternary-Bonsai PTQ1_0 (~6 GB) GGUFs
using the flags above. The 16 GB NVFP4 build can only be served at 16,384 tokens:
larger FP16-KV windows crash the device, and q8_0 KV produces corrupt output.
Each model's native maximum comes from its GGUF metadata and is shown on the
Models page; set a loading context smaller than that maximum.

## Chat tools

Chat can use local MCP tools (Playwright browser inspection and the Python REPL)
when the request clearly needs them. MCP browser tools, the Python REPL and live
web search are all enabled by default on a first-time install; the Settings page
has separate **Enable MCP browser tools** and **Enable Python tool** switches to
turn them off independently. The Python tool runs in the application virtualenv
(the `mcp` package is in `requirements.txt`) and does not depend on web search.
The installer sets up Node,
`@playwright/mcp` and Chromium under `~/.local/share/llama-web/` unless
`--skip-browser` was passed. The runtime paths are resolved in
`llama_web/integrations/mcp_tools_client.py`; override them with
`LLAMA_WEB_NODE_BIN`, `LLAMA_WEB_PLAYWRIGHT_MCP_CLI`, `LLAMA_WEB_CHROMIUM_PATH`
and `PLAYWRIGHT_BROWSERS_PATH` if your tools live elsewhere.

Browser chat tools require a working Chromium; the Playwright MCP server is
started in headless mode. The Python REPL uses the application virtualenv.

Live search respects a site's response: pages that return 401/403/404/410/429/451
or serve a captcha/access-denied page are remembered as unavailable for 30 minutes,
never retried or worked around, and skipped in favour of the next search result.

## Voice call

Click the phone button in the Chat composer (or open `/voice` directly). The avatar
appears as a draggable floating panel above the chat window; click the phone button
again or the panel's × to end the call. The picker uses the current chat model and
supports both local llama.cpp and opencode.ai models.

- Speech recognition: local faster-whisper; synthesis: local Piper voice.
- The call uses the model currently selected in Chat; change it there before calling.
  If that model stalls or returns nothing, the call retries once with the other
  loaded model before showing an error.
- The call shows a ChatGPT-style round globe built from inline SVG; it spins gently
  while listening, faster while the model is thinking or speaking. Voice questions
  use the same live-search and MCP tool routing as chat: Playwright browser read tools
  are available on every turn when web search is enabled, calculation/script requests
  get the local Python tool, and results are fed back before the spoken answer.
  Detection, transcription and replies run
  automatically; the only in-panel control is the ×
  button that ends the call (the Chat phone button toggles it too). While the call is
  open, the chat's spoken-response mode is muted so only the globe speaks. Status shows
  "Listening…" only while speech is being captured and "Ready — start speaking"
  once the reply finishes. Each complete sentence is spoken as it arrives rather
  than after the full reply. Hosted replies time out after 45 s, local ones after 90 s.
  No CDN or external
  image service is used. Override
  the voice with `LLAMA_WEB_TTS_VOICE` if needed; the default is Kokoro `af_heart`,
  with local Piper as a fallback.
  Words appear live as you speak where the browser supports live transcription;
  the server transcription is the fallback. The status shows "Listening…" rather than "Recording…".
- Each spoken turn is saved in the current chat conversation and appears in the
  transcript like a typed message.
- No LM Studio or Lemonade is involved.
- A busy lock and cooldown prevent the voice detector from restarting recording
  while a reply is being processed; speaking interrupts playback.
- Models are cached under `~/.local/share/llama-web/voice/`. The installer prefetches
  them; otherwise the first use downloads them. `ffmpeg` and the `piper` executable
  must be available.

## Coding agents

Assignments run in an isolated Docker image built from `agent_container/` on
first use (`llama_web-agent-opencode:<version>`). PostgreSQL stores task state,
workspace files and checkpoints. New assignments default to automatic shell
permissions and DuckDuckGo online research; both are editable per task.

The `/v1` gateway routes local GGUF ids to llama.cpp and `opencode/...` ids to
opencode.ai, so hosted assignments run at hosted speed. Online tasks use shorter
timeouts than local GPU tasks. Gateway requests, including Tailscale clients, are
shown in **Live logs** with the source label `tailscale`. Generated OpenCode configs
are shown in the Help panel; the desktop OpenCode config is never rewritten automatically.

The app starts a persistent supervisor. If a new assignment reports that it is not
running, restart llama-web:

```bash
systemctl --user restart llama-web.service
```

Finished or removed assignments may leave stopped containers; remove them with
`docker rm -f <container>` if they appear in `docker ps -a`.

## Backups and maintenance

- Back up PostgreSQL and `~/.config/llama-web/` (secrets and engine config).
- Model weights in `~/.local/share/llama-web/models/` can be re-downloaded; voice
  models in `~/.local/share/llama-web/voice/` are also cacheable.
- Service logs: `journalctl --user -u llama-web.service -f` and
  `journalctl --user -u llama-web-engine.service -f`.
- Restart everything: `systemctl --user restart llama-web-engine.service llama-web.service llama-web-caddy.service`.

## Safety

Use at your own risk. llama-web is not a source of financial, medical or legal
advice, and model output can be wrong. Check model licences and hardware limits
before downloading weights. Keep the web UI and the OpenAI-compatible gateway on
loopback unless you have configured a trusted private network or Tailscale.
