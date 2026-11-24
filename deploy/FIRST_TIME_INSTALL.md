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
LLAMA_SERVER="/home/<user>/.local/share/llama-web/engine/bin/llama-server"
LLAMA_MODEL="/home/<user>/.local/share/llama-web/models/<repo>/<file>.gguf"
LLAMA_ARGS="-dev Vulkan0 -ngl 99 --flash-attn off -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 -ctk f16 -ctv f16 --no-mmap --cont-batching --kv-unified --parallel 1"
LLAMA_ARG_CTX_SIZE=16384
```

On a fresh install `LLAMA_MODEL` is a placeholder. Download a GGUF from the
**Downloads** page (it lands in `~/.local/share/llama-web/models/`), set
`LLAMA_MODEL` to that file, and restart:

```bash
systemctl --user restart llama-web-engine.service
```

Models can also be loaded from the Models page, which writes the selected GGUF
and context size into `server.conf` and restarts the engine. The engine is a
single-model server: loading one model replaces the currently served one.

The 27B NVFP4 Qwen model can only be served at 16,384 tokens on the Radeon 890M
Vulkan runtime; larger FP16-KV windows crash the device, and q8_0 KV produces
corrupt output. The app caps that model's served window accordingly even though
the model file advertises a much larger native maximum.

## Chat tools

Chat can use local MCP tools (Playwright browser inspection and the Python REPL)
when the request clearly needs them. The runtime paths are resolved in
`llama_web/integrations/mcp_tools_client.py`; override them with
`LLAMA_WEB_NODE_BIN`, `LLAMA_WEB_PLAYWRIGHT_MCP_CLI`, `LLAMA_WEB_CHROMIUM_PATH`
and `PLAYWRIGHT_BROWSERS_PATH` if your tools live elsewhere.

Browser chat tools require a working Chromium and an X display. The Python REPL
uses the application virtualenv.

## Coding agents

Assignments run in an isolated Docker image built from `agent_container/` on
first use (`llama_web-agent-opencode:<version>`). PostgreSQL stores task state,
workspace files and checkpoints. The app starts a persistent supervisor; if a
new assignment reports that the supervisor is not running, restart llama-web:

```bash
systemctl --user restart llama-web.service
```

Generated OpenCode/Continue configurations and their authentication tokens are
shown in the Help panel. No automatic rewrite of the desktop OpenCode config is
performed.

## Backups and maintenance

- Back up PostgreSQL and `~/.config/llama-web/` (secrets and engine config).
- Model weights in `~/.local/share/llama-web/models/` can be re-downloaded.
- Service logs: `journalctl --user -u llama-web.service -f` and
  `journalctl --user -u llama-web-engine.service -f`.
- Restart everything: `systemctl --user restart llama-web-engine.service llama-web.service llama-web-caddy.service`.

## Safety

Use at your own risk. llama-web is not a source of financial, medical or legal
advice, and model output can be wrong. Check model licences and hardware limits
before downloading weights. Keep the web UI and the OpenAI-compatible gateway on
loopback unless you have configured a trusted private network or Tailscale.
