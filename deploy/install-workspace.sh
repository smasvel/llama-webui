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
  install -d "$dst/models"
  if [[ "$(uname -s)" == "Linux" ]]; then
    # PrismML's fork serves PTQ1_0 ternary GGUFs (e.g. Ternary-Bonsai) as well as
    # ordinary quants; the upstream engine cannot load tensor type 143.
    LLAMA_WEB_PRISM_DIR="$dst/engine-prism" bash "$(dirname "$0")/install-prism-engine.sh"
    engine="$dst/engine-prism/llama-server"
    # Prism does not accept --no-mmap; mmap is the default there.
    engine_args="-dev Vulkan0 -ngl 99 --flash-attn off -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 -ctk f16 -ctv f16 --cont-batching --kv-unified --parallel 1"
  else
    engine="$dst/engine/bin/llama-server"
    install -d "$dst/engine/bin"
    if [[ -x data/q38rocm/engine/bin/llama-server ]]; then
      install -m 755 data/q38rocm/engine/bin/llama-server "$engine"
    else
      echo "llama.cpp engine binary is missing from data/q38rocm/engine/bin/llama-server." >&2
      exit 1
    fi
    engine_args="-dev Vulkan0 -ngl 99 --flash-attn off -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 -ctk f16 -ctv f16 --no-mmap --cont-batching --kv-unified --parallel 1"
  fi
  cat > "$conf/server.conf" <<EOF
LLAMA_SERVER="$engine"
LLAMA_MODEL="$dst/models/PLACEHOLDER.gguf"
LLAMA_ARGS="$engine_args"
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

# Local MCP tool runtime (browser + Python REPL used by chat), enabled by default.
# PATH often has no node (desktop sessions don't source nvm), so also accept the
# Hearth/managed installs before giving up. Without node the browser tools would
# silently never be offered in chat on a first-time install.
node_bin="$(command -v node || true)"
npm_bin="$(command -v npm || true)"
if [[ -z "$node_bin" && -x "$HOME/.local/share/hearth/node/bin/node" ]]; then
  node_bin="$HOME/.local/share/hearth/node/bin/node"
fi
if [[ -z "$npm_bin" && -x "$HOME/.local/share/hearth/node/bin/npm" ]]; then
  npm_bin="$HOME/.local/share/hearth/node/bin/npm"
fi
if [[ -n "$node_bin" && ! -x "$dst/node/bin/node" ]]; then
  install -d "$dst/node/bin"
  ln -sf "$node_bin" "$dst/node/bin/node"
fi
# Managed npm is a symlink to a JS file with an `env node` shebang; desktop PATHs
# often lack node, so invoke it through the node binary we just located.
npm_cmd=()
if [[ -n "$npm_bin" ]]; then
  npm_cli="$(readlink -f "$npm_bin" 2>/dev/null || true)"
  if [[ "$npm_cli" == *.js && -n "$node_bin" ]]; then
    npm_cmd=("$node_bin" "$npm_cli")
  else
    npm_cmd=("$npm_bin")
  fi
fi
if [[ ${#npm_cmd[@]} -gt 0 && ! -f "$dst/mcp/node_modules/@playwright/mcp/cli.js" ]]; then
  install -d "$dst/mcp"
  (cd "$dst/mcp" && "${npm_cmd[@]}" install --no-audit --no-fund @playwright/mcp >/dev/null 2>&1) || \
    echo "Could not install @playwright/mcp; browser chat tools will be unavailable." >&2
fi
if [[ ! -f "$dst/mcp/node_modules/@playwright/mcp/cli.js" ]]; then
  echo "Node/npm not found; MCP browser tools will use any host install or stay unavailable." >&2
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
