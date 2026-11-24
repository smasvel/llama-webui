#!/usr/bin/env bash
# Install the current llama-web app: web app, llama.cpp engine, Caddy gateway,
# model library and the local agent/MCP runtime. Run as the regular user.
set -euo pipefail
cd "$(dirname "$0")/.."

dst="$HOME/.local/share/llama-web"
conf="$HOME/.config/llama-web"
units="$HOME/.config/systemd/user"
skip_browser=0
if [[ ${1:-} == "--skip-browser" ]]; then skip_browser=1; fi

install -d "$dst" "$units"
install -d -m 700 "$conf"

# PostgreSQL is provided by deploy/compose.postgres.yaml; its password file is the
# secret the app and that compose file share.
if [[ ! -s "$conf/postgres-password" ]]; then
  (umask 077; openssl rand -hex 24 > "$conf/postgres-password")
  echo "Generated $conf/postgres-password. Start PostgreSQL with: docker compose -f deploy/compose.postgres.yaml up -d"
fi

# Engine configuration. Keep an existing file untouched; create a working default
# for new installs. LLAMA_MODEL must point at a GGUF in the model library.
if [[ ! -s "$conf/server.conf" ]]; then
  engine="$dst/engine/bin/llama-server"
  install -d "$dst/engine/bin" "$dst/models"
  if [[ -x data/q38rocm/engine/bin/llama-server ]]; then
    install -m 755 data/q38rocm/engine/bin/llama-server "$engine"
  else
    echo "llama.cpp engine binary is missing from data/q38rocm/engine/bin/llama-server." >&2
    exit 1
  fi
  cat > "$conf/server.conf" <<EOF
LLAMA_SERVER="$engine"
LLAMA_MODEL="$dst/models/PLACEHOLDER.gguf"
LLAMA_ARGS="-dev Vulkan0 -ngl 99 --flash-attn off -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 -ctk f16 -ctv f16 --no-mmap --cont-batching --kv-unified --parallel 1"
LLAMA_ARG_CTX_SIZE=16384
EOF
  chmod 600 "$conf/server.conf"
  echo "Created $conf/server.conf with a placeholder model. Download a GGUF to $dst/models and set LLAMA_MODEL to its path."
fi
for key in engine-key session-key; do
  [[ -s "$conf/$key" ]] || (umask 077; set -C; openssl rand -hex 32 > "$conf/$key")
done

# The app reads DATABASE_URL from .env next to the installed package.
db_password=$(cat "$conf/postgres-password")
cat > "$dst/.env" <<EOF
DATABASE_URL=postgresql://postgres:${db_password}@127.0.0.1:18472/llama-web
EOF
chmod 600 "$dst/.env"

# Copy the application and everything it imports at runtime (the old installer
# copied only app.py and static files, so the installed app could not import
# llama_web.* or run the Python MCP tool).
install -m 644 app.py model_downloads.py schema.sql deploy/Caddyfile "$dst/"
for package in llama_web tools certs agent_container chat_templates; do
  [[ -d "$package" ]] && rm -rf "$dst/$package" && cp -a "$package" "$dst/$package"
done
install -d "$dst/templates" "$dst/static"
install -m 644 templates/*.html "$dst/templates/"
install -m 644 static/* "$dst/static/"

# Python environment.
if [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version >/dev/null 2>&1; then
  python3 -m venv --without-pip "$dst/.venv"
  .venv/bin/python -m pip --python "$dst/.venv/bin/python" install -r requirements.txt
else
  python3 -m venv "$dst/.venv"
  "$dst/.venv/bin/python" -m pip install -r requirements.txt
fi

# Local MCP tool runtime (browser + Python REPL used by chat). Reuse node from PATH
# when present; tools fall back to the host install locations otherwise.
if command -v node >/dev/null 2>&1 && [[ ! -x "$dst/node/bin/node" ]]; then
  install -d "$dst/node/bin"
  ln -sf "$(command -v node)" "$dst/node/bin/node"
fi
if command -v npm >/dev/null 2>&1 && [[ ! -f "$dst/mcp/node_modules/@playwright/mcp/cli.js" ]]; then
  install -d "$dst/mcp"
  (cd "$dst/mcp" && npm install --no-audit --no-fund @playwright/mcp >/dev/null 2>&1) || \
    echo "Could not install @playwright/mcp; browser chat tools will be unavailable." >&2
fi
if [[ $skip_browser -eq 0 ]]; then
  "$dst/.venv/bin/python" -m playwright install chromium >/dev/null 2>&1 || \
    echo "Could not install Playwright Chromium; browser chat tools may not work." >&2
fi

command -v caddy >/dev/null 2>&1 || echo "Caddy is not installed; the local gateway service will not start." >&2
command -v docker >/dev/null 2>&1 || echo "Docker is not installed; PostgreSQL and agent containers will not start." >&2
if command -v caddy >/dev/null 2>&1; then
  caddy validate --config "$dst/Caddyfile" --adapter caddyfile
fi

# Only the three current llama-web units; the old LM Studio shim units are retired.
install -m 644 deploy/systemd/llama-web.service deploy/systemd/llama-web-engine.service deploy/systemd/llama-web-caddy.service "$units/"
systemctl --user daemon-reload
systemctl --user enable llama-web.service llama-web-engine.service llama-web-caddy.service
systemctl --user restart llama-web-engine.service llama-web.service llama-web-caddy.service

echo "llama-web installed to $dst. Set LLAMA_MODEL in $conf/server.conf to a downloaded GGUF, then restart: systemctl --user restart llama-web-engine.service"
