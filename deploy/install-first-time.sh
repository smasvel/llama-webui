#!/usr/bin/env bash
# Standard first-time installation entry point. Installs host dependencies,
# starts PostgreSQL, then installs the llama-web workspace and its services.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"

if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: bash deploy/install-first-time.sh [--skip-browser] [--no-service]

Runs as your regular user (uses sudo only to install missing Ubuntu packages).
Installs the web app, the local llama.cpp engine, the Caddy gateway, PostgreSQL,
and the Playwright/Python tools used by chat. Model weights are downloaded
separately from the Downloads page after installation.
EOF
  exit 0
fi
if [[ $EUID -eq 0 ]]; then
  echo 'Run as your regular user, without sudo.' >&2
  exit 1
fi

install_browser=1
install_service=1
for argument in "$@"; do
  case "$argument" in
    --skip-browser) install_browser=0 ;;
    --no-service) install_service=0 ;;
    --skip-backend-broker|--skip-lmstudio-server|--skip-model-download|--rebuild-agent-image)
      echo "Ignoring retired option: $argument" ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done
if [[ $install_service -eq 1 && ! -d /run/systemd/system ]]; then
  echo 'systemd is not running. Rerun with --no-service.' >&2
  exit 1
fi

needs_host_dependencies=0
for command_name in python3 docker curl ffmpeg; do
  command -v "$command_name" >/dev/null || needs_host_dependencies=1
done
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  needs_host_dependencies=1
fi
if [[ $needs_host_dependencies -eq 1 ]]; then
  command -v sudo >/dev/null || { echo 'sudo is required to install missing Ubuntu packages.' >&2; exit 1; }
  command -v apt-get >/dev/null || { echo 'Automatic host setup requires Ubuntu or Debian.' >&2; exit 1; }
  sudo -v
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv docker.io docker-compose-v2 curl ffmpeg
  if ! docker info >/dev/null 2>&1; then
    sudo systemctl enable --now docker.service
    sudo usermod -aG docker "$(id -un)"
    sudo setfacl -m "u:$(id -un):rw" /var/run/docker.sock 2>/dev/null || true
  fi
fi
docker info >/dev/null
docker compose version >/dev/null
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo 'Python 3.12 or newer is required.' >&2
  exit 1
}

# PostgreSQL for llama-web (port 18472). install-workspace.sh creates the shared
# password file on a fresh install, so start it before the workspace installer.
if [[ $install_service -eq 1 ]]; then
  install -d -m 700 "$HOME/.config/llama-web"
  [[ -s "$HOME/.config/llama-web/postgres-password" ]] || \
    (umask 077; openssl rand -hex 24 > "$HOME/.config/llama-web/postgres-password")
  docker compose -f deploy/compose.postgres.yaml up -d
  bash deploy/install-workspace.sh $([[ $install_browser -eq 0 ]] && echo --skip-browser)
else
  echo 'Host dependencies are ready. Rerun without --no-service to install llama-web.'
fi
