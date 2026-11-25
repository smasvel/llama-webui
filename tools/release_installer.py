"""Interactive installer used by the Windows, macOS, and Linux release bundles.

It installs llama_web and its database, then saves the selected inference endpoint.
Optional local runtime installation is available; model weights are separate downloads.
"""
from __future__ import annotations

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

BACKENDS = {
    "llamacpp": ("llama.cpp engine", "http://127.0.0.1:18473", {"Windows", "Darwin", "Linux"}),
}

# Prebuilt llama-server binaries for hosts without a local build. Stable llama.cpp
# releases only point at a nightly tag, so pin one release tag for reproducibility.
ENGINE_TAG = "b12359"
ENGINE_RELEASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_TAG}"

# PrismML's fork adds PTQ1_0/PQ2_0 ternary quant support and is the default on
# Linux; it still serves ordinary quants. Pin the release for reproducibility.
PRISM_TAG = "prism-b12178-03ec600"
PRISM_RELEASE = f"https://github.com/PrismML-Eng/llama.cpp/releases/download/{PRISM_TAG}"



def choose_backend(system=None, input_fn=input):
    system = system or platform.system()
    options = [(key, *value[:2]) for key, value in BACKENDS.items() if system in value[2]]
    print("Choose the backend llama_web should connect to:")
    for index, (key, label, url) in enumerate(options, 1):
        print(f"  {index}. {label} ({url})")
    while True:
        answer = input_fn(f"Backend [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            key, label, url = options[int(answer) - 1]
            break
        print("Enter one of the listed numbers.")
    endpoint = input_fn(f"{label} URL [{url}]: ").strip() or url
    return key, endpoint


def _run(command, *, cwd=ROOT):
    subprocess.run(command, cwd=cwd, check=True)


def _engine_asset(system, machine):
    """Return the binary asset for this platform (Prism Vulkan on Linux)."""
    arm = "arm64" in machine.lower() or "aarch64" in machine.lower()
    if system == "Windows":
        return f"llama-{ENGINE_TAG}-bin-win-cpu-{'arm64' if arm else 'x64'}.zip", ".zip"
    if system == "Darwin":
        return f"llama-{ENGINE_TAG}-bin-macos-{'arm64' if arm else 'x64'}.tar.gz", ".tar.gz"
    return f"llama-{PRISM_TAG}-bin-ubuntu-vulkan-{'arm64' if arm else 'x64'}.tar.gz", ".tar.gz"


def _find_engine(directory):
    for path in Path(directory).rglob("llama-server*"):
        if path.is_file() and ("exe" in path.suffix.lower() or path.suffix == ""):
            return path
    return None


def configure_engine(system, backend_url):
    """Create the config, engine key and model library used by the local engine."""
    import platform
    import secrets
    import stat
    import tarfile
    import zipfile

    config = Path.home() / ".config" / "llama-web"
    data = Path.home() / ".local" / "share" / "llama-web"
    engine_dir = data / "engine"
    library = data / "models"
    config.mkdir(parents=True, exist_ok=True)
    engine_dir.mkdir(parents=True, exist_ok=True)
    library.mkdir(parents=True, exist_ok=True)
    key_file = config / "engine-key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))

    bundled = ROOT / "data" / "q38rocm" / "engine" / "bin" / "llama-server"
    prism = data / "engine-prism" / "llama-server"
    if system == "Linux" and prism.exists():
        bundled = prism
    server = os.environ.get("LLAMA_WEB_ENGINE_BINARY")
    if server and Path(server).exists():
        server = Path(server)
    else:
        server = shutil.which("llama-server") or (bundled if bundled.exists() else None)
    if server is None:
        asset, suffix = _engine_asset(system, platform.machine())
        release = PRISM_RELEASE if system == "Linux" else ENGINE_RELEASE
        archive = engine_dir / asset
        print(f"Downloading llama.cpp engine ({asset})...")
        with requests.get(f"{release}/{asset}", stream=True, timeout=120) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        extract_to = engine_dir / asset.rsplit(".", 1)[0]
        extract_to.mkdir(exist_ok=True)
        if suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extract_to)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(extract_to)
        archive.unlink()
        server = _find_engine(extract_to)
    if server is None:
        raise SystemExit("Could not install llama-server. Set LLAMA_WEB_ENGINE_BINARY to a llama-server executable and retry.")
    server = Path(server)
    if system != "Windows":
        server.chmod(server.stat().st_mode | stat.S_IXUSR)

    if system == "Windows":
        args = "-c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 --parallel 1"
    else:
        gpu_layers = 99 if system == "Darwin" else 0
        args = f"-ngl {gpu_layers} -c 16384 -b 1024 -ub 64 -t 16 --repeat-penalty 1.05 --parallel 1"
    server_conf = config / "server.conf"
    if not server_conf.exists():
        server_conf.write_text(
            f'LLAMA_SERVER="{server}"\n'
            f'LLAMA_MODEL="{library / "model.gguf"}"\n'
            f'LLAMA_ARGS="{args}"\n'
            'LLAMA_ARG_CTX_SIZE=16384\n'
        )
    print(f"llama.cpp engine installed at {server}; download a GGUF from the Downloads page.")


def install(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=BACKENDS, help="Backend key to configure instead of asking")
    parser.add_argument("--backend-url", help="Backend URL; defaults to its local endpoint")
    parser.add_argument("--postgres-port", type=int, default=55432)
    parser.add_argument("--project-name", help="Separate Docker Compose project name")
    parser.add_argument("--no-api-key", action="store_true", help="Skip the API key prompt without changing an existing key")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright Chromium installation")
    parser.add_argument("--skip-agent-image", action="store_true", help="Skip the Docker image used by coding agents")
    sys.path.insert(0, str(ROOT))
    from tools import backend_installer
    backend_installer.add_arguments(parser)
    options = parser.parse_args(args)
    if not 1 <= options.postgres_port <= 65535:
        parser.error("--postgres-port must be between 1 and 65535")
    system = platform.system()
    if system not in {"Windows", "Darwin", "Linux"}:
        parser.error(f"Unsupported operating system: {system}")
    if sys.version_info < (3, 12):
        raise SystemExit("llama_web needs Python 3.12 or newer. Install Python, then run this installer again.")
    if not shutil.which("docker"):
        raise SystemExit("Docker is required. Install Docker Desktop or Docker Engine, start it, and retry.")
    _run(["docker", "info"], cwd=ROOT)
    _run(["docker", "compose", "version"], cwd=ROOT)

    backend = options.backend
    backend_url = options.backend_url
    if backend is None:
        backend, prompted_url = choose_backend(system)
        backend_url = backend_url or prompted_url
    else:
        supported = BACKENDS.get(backend)
        if not supported or system not in supported[2]:
            raise SystemExit(f"{backend} is not offered for {system} in this installer.")
        backend_url = backend_url or supported[1]

    venv_python = ROOT / (".venv/Scripts/python.exe" if system == "Windows" else ".venv/bin/python")
    if os.environ.get("LLAMA_WEB_RELEASE_INSTALL_IN_VENV") != "1":
        if not venv_python.exists():
            _run([sys.executable, "-m", "venv", ".venv"])
        _run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
        _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"])
        _run([str(venv_python), "-c", "import pypdf"])
        if not options.skip_browser:
            browser_command = [str(venv_python), "-m", "playwright", "install"]
            if system == "Linux":
                browser_command.append("--with-deps")
            _run(browser_command + ["chromium"])
        child_env = os.environ.copy()
        child_env["LLAMA_WEB_RELEASE_INSTALL_IN_VENV"] = "1"
        forwarded = list(sys.argv[1:] if args is None else args)
        # Forward the resolved choice so the child never asks twice.
        forwarded = [arg for arg in forwarded if arg != "--skip-backend-install"]
        # The only supported runtime is llama.cpp; optional legacy runtimes are no
        # longer installed by the release bundle.
        forwarded.append("--skip-backend-install")
        if not any(arg == "--backend" or arg.startswith("--backend=") for arg in forwarded):
            forwarded += ["--backend", backend]
        if not any(arg == "--backend-url" or arg.startswith("--backend-url=") for arg in forwarded):
            forwarded += ["--backend-url", backend_url]
        subprocess.run([str(venv_python), str(Path(__file__).resolve()), *forwarded],
                       cwd=ROOT, env=child_env, check=True)
        return

    sys.path.insert(0, str(ROOT))
    from tools import first_time_setup as setup

    setup.validate_existing_installation()
    if not options.skip_agent_image and system == "Linux":
        setup.install_agent_runtime()
    elif system != "Linux":
        print("The isolated coding-agent image is only supported on Linux; agent execution is unavailable here.")
    values = setup.prepare_environment(options.postgres_port)
    os.environ["DATABASE_URL"] = values["DATABASE_URL"]
    compose = ["docker", "compose", "--env-file", str(ROOT / ".env.postgres"), "-f", str(ROOT / "compose.yaml")]
    if options.project_name:
        compose += ["--project-name", options.project_name]
    _run(compose + ["up", "-d", "--wait", "postgres"])
    setup.initialize_application_storage()
    configure_engine(system, backend_url)
    from llama_web.core.local_tls import ensure_certificate
    ensure_certificate(ROOT / 'certs')

    from llama_web.storage import settings_store
    print(f"\nllama_web is installed with its local {BACKENDS[backend][0]} at {backend_url}.")
    print("Download a GGUF from the Downloads page and set LLAMA_MODEL in ~/.config/llama-web/server.conf.")
    print("Start llama_web with deploy/start-llama_web.ps1 on Windows, or bash deploy/start-llama_web.sh on macOS/Linux.")
    print("Then open https://localhost:5443 and create the administrator account on first use.")


if __name__ == "__main__":
    install()
