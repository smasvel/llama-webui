"""llama_web integration for the local llama.cpp engine and its model library.

The engine is a single-model ``llama-server`` managed by
``llama-web-engine.service``. Its configuration lives in
``~/.config/llama-web/server.conf`` and GGUF files live in the llama-web model
library (``~/.local/share/llama-web/models`` by default). This module keeps the
Models page pointed at those llama-web-owned paths rather than any Hearth model
directory.
"""

from __future__ import annotations

import os
import re
import shlex
import struct
import subprocess
import time
from pathlib import Path

import psutil
import requests

SERVICE_NAME = "llama-web-engine.service"
_DEFAULT_BASE_URL = "http://127.0.0.1:18473"
_MODEL_PREFIX = "llamacpp/"
_CONFIG_DIR = Path(os.environ.get("LLAMA_WEB_CONFIG", Path.home() / ".config/llama-web"))
SERVER_CONF = _CONFIG_DIR / "server.conf"
ENGINE_KEY = _CONFIG_DIR / "engine-key"
LIBRARY = Path(os.environ.get("LLAMA_MODEL_LIBRARY", Path.home() / ".local/share/llama-web/models"))


class LlamaCppError(RuntimeError):
    pass


def base_url() -> str:
    return (os.environ.get("LLAMA_ENGINE_URL") or _DEFAULT_BASE_URL).rstrip("/")


def model_library() -> Path:
    return LIBRARY


def _headers() -> dict[str, str]:
    try:
        key = ENGINE_KEY.read_text().strip()
    except OSError:
        return {}
    return {"Authorization": f"Bearer {key}"} if key else {}


def api_key() -> str | None:
    try:
        return ENGINE_KEY.read_text().strip() or None
    except OSError:
        return None


def served_model() -> dict | None:
    """Return the engine's currently served model entry, or None when unavailable."""
    try:
        response = requests.get(f"{base_url()}/v1/models", headers=_headers(), timeout=4)
        response.raise_for_status()
        entries = response.json().get("data") or []
        return entries[0] if entries else None
    except (requests.RequestException, ValueError, TypeError, IndexError):
        return None


def api_key() -> str | None:
    try:
        return ENGINE_KEY.read_text().strip() or None
    except OSError:
        return None


def request_model_id() -> str:
    return str((served_model() or {}).get("id") or "local-model")


def library_entries() -> list[Path]:
    if not LIBRARY.is_dir():
        return []
    return sorted(
        path for path in LIBRARY.rglob("*.gguf")
        if path.is_file() and not path.name.startswith(".")
    )


def model_id(path: Path) -> str:
    return _MODEL_PREFIX + path.relative_to(LIBRARY).as_posix()


def sync_library_metadata() -> int:
    """Import the GGUF library into the database once so every runtime read uses DB
    metadata instead of parsing files. Safe to re-run: it refreshes rows in place and
    removes rows whose GGUF has been deleted from the library."""
    from llama_web.storage import model_metadata_store as store

    ids = set()
    for path in library_entries():
        identifier = model_id(path)
        ids.add(identifier)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            continue
        store.upsert_local_model(
            identifier, str(path), path.stem,
            path.parent.name if path.parent != LIBRARY else "llama-web",
            _quantization(path.name), size_bytes, gguf_max_context(path), None,
        )
    for row in store.local_models():
        if row["model_id"] not in ids:
            store.delete_local_model(row["model_id"])
    return len(ids)


def model_path(identifier: str) -> Path | None:
    """Resolve a model id to the GGUF path stored in the database."""
    from llama_web.storage import model_metadata_store as store

    name = str(identifier or "").strip().strip("/")
    if name.startswith(_MODEL_PREFIX):
        name = name[len(_MODEL_PREFIX):]
    if not name or name in (".", ".."):
        return None
    try:
        rows = store.local_models()
    except Exception:
        return None
    for row in rows:
        if row["model_id"] == _MODEL_PREFIX + name or Path(row["path"]).name == Path(name).name:
            candidate = Path(row["path"])
            return candidate if candidate.is_file() else None
    return None


def owns_model(identifier: str) -> bool:
    return model_path(identifier) is not None


def safe_context_limit(identifier: str | None = None) -> int:
    """Largest served window this Vulkan engine can sustain for the 27B NVFP4 GGUF.

    The model itself supports 262,144 tokens, but FP16 KV beyond ~16K exhausts the
    device on this integrated GPU and the engine crashes on load.
    """
    return 16384


def configured_context() -> int | None:
    """Context the engine is configured to load with (server.conf LLAMA_ARGS)."""
    try:
        text = SERVER_CONF.read_text()
    except OSError:
        return None
    match = re.search(r"(?m)^LLAMA_ARG_CTX_SIZE=(\d+)$", text)
    if not match:
        match = re.search(r"(?:^|\s)(?:-c|--ctx-size)\s+(\d+)(?=\s|$)", text)
    value = int(match.group(1)) if match else None
    return value if value and value >= 512 else None


def _quantization(filename: str) -> str:
    match = re.search(r"(Q\d+_[A-Z0-9_]+|IQ\d+_[A-Z0-9_]+|NVFP4|F16|F32|BF16)", filename, re.I)
    return match.group(1).upper() if match else "GGUF"


def gguf_max_context(path: Path) -> int | None:
    """Read `<architecture>.context_length` from a GGUF header without loading weights."""
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
            if len(header) < 24 or header[:4] != b"GGUF":
                return None
            _, _, _, count = struct.unpack("<4sIQQ", header)

            def read(fmt: str):
                size = struct.calcsize(fmt)
                data = handle.read(size)
                return struct.unpack(fmt, data)[0] if len(data) == size else None

            def read_string() -> str | None:
                length = read("<Q")
                if not length:
                    return ""
                data = handle.read(length)
                return data.decode("utf-8", "replace") if len(data) == length else None

            def skip_value(value_type: int) -> bool:
                if value_type == 8:  # string
                    return read_string() is not None
                if value_type in (0, 1):  # int8 / uint8
                    return read("<B") is not None
                if value_type in (2, 3):  # int16 / uint16
                    return read("<H") is not None
                if value_type in (4, 5):  # int32 / uint32
                    return read("<I") is not None
                if value_type in (6, 12):  # float / double
                    return read("<f" if value_type == 6 else "<d") is not None
                if value_type == 7:  # bool
                    return read("<?") is not None
                if value_type in (10, 11):  # uint64 / int64
                    return read("<Q") is not None
                return False

            for _ in range(count):
                key = read_string()
                value_type = read("<I")
                if key is None or value_type is None:
                    return None
                if value_type == 9:  # array: element type + count + values
                    element_type = read("<I")
                    elements = read("<Q")
                    if elements is None or element_type is None or elements > 1_000_000:
                        return None
                    for _ in range(elements):
                        if not skip_value(element_type):
                            return None
                    continue
                if value_type in (4, 10):  # uint32 / uint64 context length
                    value = read("<I" if value_type == 4 else "<Q")
                    if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                        return value
                elif not skip_value(value_type):
                    return None
    except (OSError, ValueError, struct.error):
        return None
    return None


_SPLIT_SHARD_RE = re.compile(r"^(?P<base>.+)-(?P<index>\d{5})-of-(?P<count>\d{5})\.gguf$", re.I)


def _split_shard_status(path: Path) -> tuple[bool, str]:
    """A split GGUF needs every shard; a partial download is not loadable."""
    match = _SPLIT_SHARD_RE.match(path.name)
    if not match:
        return True, ""
    count = int(match.group("count"))
    if count <= 1:
        return True, ""
    missing = []
    for index in range(1, count + 1):
        shard = path.with_name(
            f"{match.group('base')}-{index:05d}-of-{count:05d}.gguf")
        if not shard.is_file():
            missing.append(shard.name)
    if not missing:
        return True, ""
    return False, (f"Incomplete split GGUF: {len(missing)} of {count} shard files are "
                   f"missing (starting with {missing[0]}). Download the full set "
                   f"before loading this model.")


def list_models() -> list[dict]:
    """List llama-web GGUFs using database metadata, marking the served one from the
    engine. No GGUF headers or library directory scans happen here."""
    from llama_web.storage import model_metadata_store as store

    try:
        db_models = store.local_models()
    except Exception:
        db_models = []
    served = served_model()
    served_name = Path(str((served or {}).get("id") or "")).name
    meta = (served or {}).get("meta") or {}
    served_context = meta.get("n_ctx")
    served_max = meta.get("n_ctx_train")
    if not isinstance(served_max, int) or isinstance(served_max, bool) or served_max <= 0:
        served_max = None
    default_context = configured_context()
    models = []
    for row in db_models:
        path = Path(row["path"])
        loaded = bool(served_name) and served_name == path.name
        context = served_context if loaded else None
        if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
            context = default_context
        loadable, shard_hint = _split_shard_status(path)
        models.append({
            "id": row["model_id"],
            "name": row["name"],
            "type": "llm",
            "publisher": row["publisher"] or "llama-web",
            "state": "loaded" if loaded else "not-loaded",
            "size_gb": round((row["size_bytes"] or 0) / 1e9, 2),
            "loaded_context_length": served_context if loaded and isinstance(served_context, int) and served_context > 0 else None,
            "configured_context_length": context,
            "max_context_length": (served_max or row["max_context"]) if loaded else row["max_context"],
            "safe_context_length": safe_context_limit(),
            "server_model_id": (served or {}).get("id") if loaded else None,
            "compatibility_type": "gguf",
            "quantization": row["quantization"] or "GGUF",
            "deletable": True,
            "lan_compatible": True,
            "context_editable": True,
            "loadable": loadable,
            "management_hint": shard_hint,
        })
    # The engine may serve a GGUF outside the managed library. Keep it visible and
    # unloadable; its only metadata comes from the engine itself, not from a file.
    if served_name and not any(model["state"] == "loaded" for model in models):
        models.insert(0, {
            "id": _MODEL_PREFIX + served_name,
            "name": Path(served_name).stem,
            "type": "llm",
            "publisher": "llama.cpp engine",
            "state": "loaded",
            "size_gb": None,
            "loaded_context_length": served_context if isinstance(served_context, int) and served_context > 0 else None,
            "configured_context_length": default_context,
            "max_context_length": served_max,
            "safe_context_length": safe_context_limit(),
            "server_model_id": served.get("id"),
            "compatibility_type": "gguf",
            "deletable": False,
            "lan_compatible": True,
            "context_editable": True,
            "management_hint": "This model is served from the engine's configured path; download it into the llama-web library to manage it here.",
        })
    return models


def _systemctl(*args: str, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args, SERVICE_NAME],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise LlamaCppError(f"Could not control {SERVICE_NAME}: {exc}") from exc
    if result.returncode and "is-active" not in args:
        raise LlamaCppError((result.stderr or result.stdout or "systemctl failed").strip())
    return result.stdout


def _update_server_conf(path: Path, context_length: int | None) -> None:
    try:
        text = SERVER_CONF.read_text()
    except OSError as exc:
        raise LlamaCppError(f"Could not read {SERVER_CONF}: {exc}") from exc
    updates = {"LLAMA_MODEL": str(path)}
    if context_length:
        updates["LLAMA_ARG_CTX_SIZE"] = str(context_length)
        # The service passes LLAMA_ARGS on the command line, so the saved window has
        # to be updated there as well or a Load would keep using the old -c value.
        args_match = re.search(r"(?m)^LLAMA_ARGS=(.*)$", text)
        if args_match:
            args = args_match.group(1)
            args, changed = re.subn(
                r"(?<!\S)(?:-c|--ctx-size)\s+\d+", f"-c {context_length}", args, count=1)
            if not changed:
                stripped = args.rstrip()
                if stripped.endswith(('"', "'")):
                    quote = stripped[-1]
                    args = stripped[:-1].rstrip() + f" -c {context_length}" + quote
                else:
                    args = stripped + f" -c {context_length}"
            text = text[:args_match.start()] + f"LLAMA_ARGS={args}" + text[args_match.end():]
    for key, value in updates.items():
        pattern = rf"(?m)^{re.escape(key)}=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, f"{key}={value}", text, count=1)
        else:
            text = text.rstrip() + f"\n{key}={value}\n"
    try:
        SERVER_CONF.write_text(text)
    except OSError as exc:
        raise LlamaCppError(f"Could not write {SERVER_CONF}: {exc}") from exc


def load_model(identifier: str, context_length: int | None = None) -> None:
    path = model_path(identifier)
    if path is None:
        raise LlamaCppError(f"Unknown llama.cpp model: {identifier}")
    try:
        original_conf = SERVER_CONF.read_text()
    except OSError:
        original_conf = None
    _update_server_conf(path, context_length)
    try:
        restart_service()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if served_model():
                return
            time.sleep(2)
        raise LlamaCppError("llama.cpp engine did not become ready; check the engine configuration and logs.")
    except Exception:
        # A bad window (or a model that cannot fit) must not leave the configured
        # engine dead: restore the previous config and bring it back up.
        if original_conf is not None:
            try:
                SERVER_CONF.write_text(original_conf)
                restart_service()
            except Exception:
                pass
        raise


def _engine_processes() -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "llama-server" in command and ("--port 18473" in command or "--port=18473" in command):
            found.append(proc)
    return found


def _stop_engine_processes() -> None:
    processes = _engine_processes()
    for proc in processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if processes:
        time.sleep(2)
    for proc in _engine_processes():
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _conf_value(key: str) -> str | None:
    try:
        text = SERVER_CONF.read_text()
    except OSError:
        return None
    match = re.search(rf"(?m)^{key}=(.*)$", text)
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def _start_engine_directly() -> None:
    server = _conf_value("LLAMA_SERVER")
    model = _conf_value("LLAMA_MODEL")
    if not server or not model:
        raise LlamaCppError(f"llama.cpp engine is not configured in {SERVER_CONF}.")
    raw_args = _conf_value("LLAMA_ARGS") or ""
    # Windows paths contain backslashes that POSIX shlex would consume.
    args = shlex.split(raw_args, posix=(os.name != "nt")) if raw_args else []
    if os.name == "nt":
        args = [arg.strip('"') for arg in args]
    command = [server, "--model", model, "--host", "127.0.0.1", "--port", "18473",
               "--api-key-file", str(ENGINE_KEY), "--metrics", "--no-webui", *args]
    try:
        popen_kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        raise LlamaCppError(f"Could not start llama.cpp engine: {exc}") from exc


def unload_model(identifier: str | None = None) -> None:
    try:
        _systemctl("stop", timeout=60)
    except LlamaCppError:
        pass  # the user manager may be unavailable; stop the process directly below
    time.sleep(1)
    if _engine_processes():
        _stop_engine_processes()
    if served_model():
        raise LlamaCppError("llama.cpp engine is stopped but its endpoint still serves a model.")


def restart_service() -> None:
    old_pids = {proc.pid for proc in _engine_processes()}
    try:
        _systemctl("restart", timeout=120)
    except LlamaCppError:
        _stop_engine_processes()
        _start_engine_directly()
        return
    time.sleep(2)
    if not _engine_processes() or {proc.pid for proc in _engine_processes()} == old_pids:
        # systemctl reported success but did nothing (no user manager running).
        _stop_engine_processes()
        _start_engine_directly()


def delete_model(identifier: str) -> None:
    path = model_path(identifier)
    if path is None:
        raise LlamaCppError(f"Unknown llama.cpp model: {identifier}")
    served_name = Path(str((served_model() or {}).get("id") or "")).name
    if served_name == path.name:
        unload_model(identifier)
    try:
        path.unlink()
        from llama_web.storage import model_metadata_store as store
        store.delete_local_model(identifier)
        parent = path.parent
        while parent != LIBRARY and parent != parent.parent:
            if any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
    except OSError as exc:
        raise LlamaCppError(f"Could not delete {path}: {exc}") from exc


def chat_stream(messages, model, temperature=0.7, max_tokens=None, frequency_penalty=None,
                tools=None, **options):
    """Stream an OpenAI-compatible chat completion from the local llama.cpp engine.

    Per-model reasoning controls are passed through: llama-server's Jinja template
    reads ``chat_template_kwargs.enable_thinking`` for Qwen-style thinking models.
    """
    payload = {
        "model": request_model_id(),
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty
    if tools:
        payload["tools"] = tools
    for key in (
        "tool_choice", "parallel_tool_calls", "enable_thinking", "chat_template_kwargs",
        "response_format", "stop", "top_p", "presence_penalty", "seed",
        "reasoning_effort", "reasoning_budget_tokens", "repeat_penalty",
    ):
        if options.get(key) is not None:
            payload[key] = options[key]
    try:
        with requests.post(
            f"{base_url()}/v1/chat/completions", json=payload,
            headers=_headers(), stream=True, timeout=(10, 900),
        ) as response:
            if response.status_code >= 400:
                detail = (response.text or "").strip()
                raise LlamaCppError(
                    f"llama.cpp engine returned HTTP {response.status_code}: {detail[:500]}"
                )
            for line in response.iter_lines(chunk_size=1):
                if line:
                    yield line.decode("utf-8", errors="replace")
    except requests.RequestException as exc:
        raise LlamaCppError(f"llama.cpp chat request failed: {exc}") from exc
