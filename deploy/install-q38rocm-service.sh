#!/usr/bin/env bash
# Install a user systemd service for llama-web's local q38rocm backend.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repo_dir="$project_dir/data/q38rocm"
model_library=${LLAMA_MODEL_LIBRARY:-$HOME/.local/share/llama-web/models}
model_path=${Q38ROCM_MODEL_PATH:-$model_library/cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF/Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf}
context_tokens=${Q38ROCM_CONTEXT:-16384}
if [[ ! "$context_tokens" =~ ^[0-9]+$ ]] || ((context_tokens < 1024 || context_tokens > 262144)); then
  echo 'Q38ROCM_CONTEXT must be between 1024 and 262144 tokens.' >&2
  exit 1
fi
server_path="$repo_dir/engine/bin/llama-server"

[[ -x "$server_path" ]] || { echo "Build q38rocm first: bash deploy/build-q38rocm-vulkan.sh" >&2; exit 1; }
[[ -f "$model_path" ]] || { echo "Model file not found: $model_path" >&2; exit 1; }

unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
escaped_repo=${repo_dir//%/%%}
escaped_model=${model_path//%/%%}
# This host's Radeon 890M build is Vulkan-only. Upstream reports ~0% MTP
# acceptance on Vulkan0, so use the safe profile with MTP and prompt caching off.
# The pinned engine produces garbled output for this NVFP4 GGUF when Flash
# Attention is on (with q8_0 KV), so keep Flash Attention off with FP16 KV.
# FP16 KV needs more device memory: 16K context is the stable bounded window here.
cat > "$unit_dir/q38rocm.service" <<UNIT
[Unit]
Description=q38rocm Qwen 3.8 27B Vulkan backend for llama-web
After=network.target

[Service]
Type=simple
WorkingDirectory=${escaped_repo}
Environment=SKIP_ROCM_CHECK=1
Environment=DEVICE=Vulkan0
Environment=PROFILE=safe
Environment=CTX=${context_tokens}
Environment=CACHE_RAM_MIB=0
ExecStart=/bin/bash ${escaped_repo}/run_server.sh --port 8008 --host 127.0.0.1 --device Vulkan0 --profile safe --no-mtp --no-reasoning --kv-k f16 --kv-v f16 --repeat-penalty 1.05 --ubatch 64 --ctx ${context_tokens} ${escaped_model} --flash-attn off
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
echo "Installed q38rocm.service. llama-web starts it on demand from Models; it won't load at login."
