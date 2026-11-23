#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
dst="$HOME/.local/share/llama-web"
conf="$HOME/.config/llama-web"
units="$HOME/.config/systemd/user"
install -d "$dst" "$units"
install -d -m 700 "$conf"
test -s "$conf/server.conf" && test -s "$conf/postgres-password" || { echo "Configure PostgreSQL and server.conf first." >&2; exit 1; }
for key in engine-key session-key; do
  if [[ ! -s "$conf/$key" ]]; then
    (umask 077; set -C; openssl rand -hex 32 > "$conf/$key")
  fi
done
install -m644 app.py model_downloads.py schema.sql deploy/Caddyfile "$dst/"
install -d "$dst/templates" "$dst/static"
install -m644 templates/*.html "$dst/templates/"
install -m644 static/* "$dst/static/"
if [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version >/dev/null 2>&1; then
  python3 -m venv --without-pip "$dst/.venv"
  .venv/bin/python -m pip --python "$dst/.venv/bin/python" install -r requirements.txt
else
  python3 -m venv "$dst/.venv"
  "$dst/.venv/bin/python" -m pip install -r requirements.txt
fi
caddy validate --config "$dst/Caddyfile" --adapter caddyfile
install -m644 deploy/systemd/*.service "$units/"
systemctl --user daemon-reload
systemctl --user enable llama-web.service llama-web-engine.service llama-web-caddy.service
systemctl --user restart llama-web-engine.service llama-web.service llama-web-caddy.service
