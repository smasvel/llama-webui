# llama-web

A self-hosted, authenticated workspace for local AI chat with the llama.cpp engine.

## Features

- **Chat** — JavaScript streaming, saved conversations, regenerate/edit/delete, attachments, Markdown, code blocks.
- **Models** — Browse downloaded GGUF files, load a model, set context size, and see each model's max context on its label.
- **Agents** — OpenCode tasks with live progress, checkpoints, verification commands, schedules, and skills.
- **Tools** — Web search, browse history, cross-chat recall, voice, and generated OpenCode/Continue client configs.
- **Admin** — User accounts, roles, settings, service controls, and live logs.

## Fixed ports

Do not change these without updating the systemd units and Caddyfile together.

| Port | Service | Binding | Use |
|---|---|---|---|
| 18471 | Caddy | localhost | Reverse proxy to the app |
| 18472 | PostgreSQL | localhost | App database |
| 18473 | llama.cpp (`llama-server`) | localhost | Model inference |
| 18474 | llama-web | all interfaces | HTTPS web UI and API |

## Install

```bash
bash deploy/install-workspace.sh
```

Open `https://<your-host>:18474/`, create the administrator account, then download and load a model from **Downloads** and **Models**.

See [deploy/FIRST_TIME_INSTALL.md](deploy/FIRST_TIME_INSTALL.md) for full setup and operations.

## Development

```bash
.venv/bin/python tools/run_tests.py
.venv/bin/python tools/run_tests.py --browser
```

Code lives in `llama_web/`, entry point is `app.py`, deployment files are in `deploy/`.

## Safety

Use at your own risk. Model output may be wrong; verify important results. Not for medical, legal, or financial decisions.
