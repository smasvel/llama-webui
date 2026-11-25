#!/usr/bin/env bash
# Install the PrismML llama.cpp fork, which adds PTQ1_0/PQ2_0 ternary quant
# support (used by Ternary-Bonsai GGUFs) while still serving ordinary quants.
# The upstream engine cannot load tensor type 143 (PTQ1_0); this fork can.
set -euo pipefail

# Pinned release: changing this requires re-verifying the GGUFs in the library.
PRISM_TAG="prism-b12178-03ec600"
PRISM_RELEASE="https://github.com/PrismML-Eng/llama.cpp/releases/download/${PRISM_TAG}"

target="${1:-${LLAMA_WEB_PRISM_DIR:-$HOME/.local/share/llama-web/engine-prism}}"
machine="$(uname -m)"
case "$machine" in
  x86_64|amd64) arch="x64" ;;
  aarch64|arm64) arch="arm64" ;;
  *) echo "Prism Vulkan engine is not available for $machine." >&2; exit 1 ;;
esac
asset="llama-${PRISM_TAG}-bin-ubuntu-vulkan-${arch}.tar.gz"

# Idempotent: keep an already working engine untouched.
if [[ -x "$target/llama-server" ]] && "$target/llama-server" --version >/dev/null 2>&1; then
  echo "Prism engine already installed at $target."
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "Downloading $asset ..."
curl -fL --retry 3 -o "$work/engine.tar.gz" "${PRISM_RELEASE}/${asset}"
mkdir -p "$work/extracted" "$target"
tar -xzf "$work/engine.tar.gz" -C "$work/extracted" --strip-components=1
# The bundle is self-contained: keep every .so alongside llama-server so its
# $ORIGIN runpath resolves without touching LD_LIBRARY_PATH in the service unit.
cp -a "$work/extracted/." "$target/"
chmod +x "$target/llama-server"
if ! "$target/llama-server" --version >/dev/null 2>&1; then
  echo "Prism engine at $target failed its version check." >&2
  exit 1
fi
echo "Prism engine installed at $target ($("$target/llama-server" --version 2>&1 | head -1))."
