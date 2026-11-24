#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run this script with sudo in your terminal.' >&2
  exit 1
fi
if [[ ${1:-} == --base-only ]]; then
  target_user=${SUDO_USER:-}
  if [[ -z $target_user || $target_user == root ]]; then
    echo 'Run with sudo from the account that will run Hearth.' >&2
    exit 1
  fi
  apt-get update
  apt-get install -y python3 python3-venv docker.io docker-compose-v2 acl curl ffmpeg
  systemctl enable --now docker.service
  usermod -aG docker "$target_user"
  echo "Base dependencies installed. Log out and back in before running deploy/install-first-time.sh."
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo 'Usage: sudo bash deploy/install-host-deps.sh [--base-only]' >&2
  exit 1
fi
# Resolve the invoking account and checkout for full-backend setup.
target_user=${SUDO_USER:-}
if [[ -z $target_user || $target_user == root ]]; then
  echo 'Run with sudo from the account that will run Hearth.' >&2
  exit 1
fi
target_home=$(getent passwd "$target_user" | cut -d: -f6)
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
render_template() {
  python3 - "$1" "$2" "$target_user" "$target_home" "$project_dir" <<'PYTHON'
from pathlib import Path
import sys
source, destination, user, home, project = sys.argv[1:]
text = Path(source).read_text()
for key, value in [('HEARTH_PROJECT_DIR', project), ('HEARTH_HOME', home), ('HEARTH_USER', user)]:
    text = text.replace(key, value)
Path(destination).write_text(text)
PYTHON
}
apt-get update
apt-get install -y python3 python3-venv docker.io docker-compose-v2 acl curl git ffmpeg lynis caddy build-essential cmake pkg-config libatomic1 libvulkan1 mesa-vulkan-drivers vulkan-tools libsndfile1 espeak-ng software-properties-common libtcmalloc-minimal4t64
systemctl enable --now docker.service
# llama-web installs its own Caddyfile for the local gateway.
usermod -aG render,video,docker "$target_user"
systemctl daemon-reload
# Ubuntu 26.04 includes Lemonade in its own package repository.
"${project_dir}/.venv/bin/python" -m playwright install-deps chromium
render_template "${project_dir}/deploy/hearth-lynis-sudoers" /etc/sudoers.d/hearth-lynis
chmod 0440 /etc/sudoers.d/hearth-lynis
visudo -cf /etc/sudoers.d/hearth-lynis
loginctl enable-linger "$target_user"
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  echo "Tailscale is available. Configure the gateway with deploy/Caddyfile if remote access is needed."
fi
