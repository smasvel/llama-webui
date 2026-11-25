"""Machine-specific data for the chat Help panel and config generation.

The panel shows live model inventory, local addresses, Tailscale links and the
local CA certificate. Generated Continue/OpenCode configuration remains available
for the optional config-sync tool. All values are derived at request time so the
help does not go stale by hand.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

import requests

from llama_web.backends import opencode_client as zen
from llama_web.storage import settings_store
from llama_web.storage import model_metadata_store

CADDYFILE = "/etc/caddy/Caddyfile"
CADDY_ADMIN = "http://localhost:2019"
CERT_FILENAME = "llama_web-ca.crt"

# Sensible fallbacks if a source is briefly unreachable, so the help still renders.
_DEFAULT_PORTS = {"llamacpp": 18471, "app": 18474, "engine": 18473}
_UPSTREAM_BACKEND = {
    "18474": "llamacpp",
    "18473": "llamacpp",
}
_LABELS = {"llamacpp": "Local llama.cpp engine", "opencode": "opencode.ai"}
_CTX = {"llamacpp": 16384, "opencode": 262144}
_OUT = {"llamacpp": 4096, "opencode": 8192}
_BACKENDS = ("llamacpp", "opencode")
_NON_CHAT = ("embedding", "embeddings", "transcription", "tts", "image", "video")

def compaction_for_window(window: int) -> dict:
    """keep/buffer for an OpenCode config, derived purely from a model's context window.

    keep + buffer + the compaction prompt itself must fit inside the window: keep equal
    to the whole window makes OpenCode's own summarization request impossible and the
    session fails with "The compaction request cannot be reduced further without losing
    the latest exchange or checkpoint". The buffer is one twelfth of the loaded window
    (about 5.3K tokens at 65,536). Keep uses the remaining capacity after the buffer
    and a one-sixty-fourth prompt reserve, so a 65,536 window retains about 59K tokens. Both
    values scale with context rather than using model-name-specific settings.
    """
    window = max(1, int(window))
    buffer = max(1, window // 12)
    prompt_reserve = max(1, window // 64)
    keep = max(0, window - buffer - prompt_reserve)
    return {"keep": keep, "buffer": buffer}


def configured_compaction_for_window(window: int, keep_override: int | None = None,
                                     buffer_override: int | None = None) -> dict:
    """Apply optional admin overrides to formula-derived budgets for one context window.

    Keep overrides may lower the formula's retained history. Buffer overrides may raise
    the formula's safety headroom, bounded to one quarter of the loaded window.
    """
    window = max(1, int(window))
    budgets = compaction_for_window(window)
    keep = budgets['keep'] if keep_override is None else min(max(0, int(keep_override)), budgets['keep'])
    buffer = budgets['buffer']
    if buffer_override is not None:
        buffer = min(max(buffer, int(buffer_override)), window // 4)
    return {'keep': keep, 'buffer': buffer}


def compaction_settings() -> dict:
    """Effective compaction settings, re-read on each config build so an admin's Settings change
    is picked up the next time the Help page regenerates the OpenCode config."""
    raw_keep = settings_store.get("opencode_compaction_keep_tokens")
    try:
        keep = int(raw_keep) if raw_keep not in (None, "") else None
    except (TypeError, ValueError):
        keep = None
    buffer: int | None = None
    raw_buffer = settings_store.get("opencode_compaction_buffer")
    if raw_buffer not in (None, ""):
        try:
            buffer = int(raw_buffer)
        except (TypeError, ValueError):
            buffer = None
    # Generated configs compact before the loaded model window fills. Admins can
    # still turn this off explicitly when they prefer a fully preserved transcript.
    auto = (settings_store.get("opencode_compaction_auto") or "true") == "true"
    return {"auto": auto, "keep_tokens": max(0, keep) if keep is not None else None,
            "buffer": buffer}


def opencode_context_settings() -> dict:
    """Admin controls for how much context the generated OpenCode config advertises.

    advertise_loaded (default true): use each model's *real* loaded context when the backend
    reports one (LM Studio's loaded_context_length, Lemonade's recipe_options.ctx_size, vLLM's
    registry max_model_len, KoboldCPP's KOBOLD_CONTEXT). This is what fixed a real report:
    LM Studio had `qwen3.8-2b-distill` loaded at 8192, but the config hardcoded 32768 for every
    LM Studio model, so OpenCode packed ~30k tokens into a request the server rejected with
    "request (29899 tokens) exceeds the available context size (8192 tokens)".

    fallback: optional single override for models whose real context can't be determined
    (unloaded, or a backend that doesn't report one). Blank keeps each backend's tuned _CTX
    default, which is deliberate — the backends genuinely differ (FastFlowLM/Lemonade serve
    65536, vLLM serves a small CPU context), so one global number would be wrong for most."""
    advertise = (settings_store.get("opencode_advertise_loaded_context") or "true") == "true"
    fallback: int | None = None
    raw = settings_store.get("opencode_context_fallback")
    if raw not in (None, ""):
        try:
            fallback = int(raw)
        except (TypeError, ValueError):
            fallback = None
        if fallback is not None and fallback <= 0:
            fallback = None
    return {"advertise_loaded": advertise, "fallback": fallback}


def _advertised_context(be: str, model_id: str, ctx_by_model: dict, fallback: int | None) -> int:
    """Context to advertise: loaded/configured context, reported model maximum, admin
    fallback, or backend default."""
    detected = ctx_by_model.get((be, model_id))
    context = detected if isinstance(detected, int) and detected > 0 else fallback or _CTX[be]
    return context


def _configured_or_loaded_context(model: dict) -> int | None:
    """Advertise only a loaded model's real window; saved values apply on next load."""
    loaded = model.get("state") == "loaded"
    context = model.get("loaded_context_length") if loaded else model.get("configured_context_length")
    if type(context) is int and context > 0:
        return context
    if loaded:
        return None
    try:
        context = model_metadata_store.get_model_configured_context(
            model.get("backend"), model.get("id"))
    except Exception:
        return None
    return context if type(context) is int and context > 0 else None


def _advertised_output(be: str, context: int, model_id: str | None = None) -> int:
    """Output cap, never more than half the (possibly tiny) context. Without this a model
    whose real context is 8192 would keep the old fixed output of 8192 — an output budget
    equal to the entire window, leaving no room for the prompt (and making compaction
    impossible). Half is a safe reserve; unchanged for every backend that was already
    narrower (e.g. 32768 context / 8192 output)."""
    out_cap = _OUT[be]
    return max(1, min(out_cap, context // 2))


def lan_ip() -> str:
    """This machine's LAN address, as another PC on the network would reach it. Opening a
    UDP socket 'to' a public IP just makes the OS pick the right outbound interface — no
    traffic is sent."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def tailscale_ip() -> str | None:
    """This machine's Tailscale IP (100.x.x.x), if the `tailscale` CLI is installed and the
    node is logged in — None otherwise (not installed, logged out, daemon down), so the Help
    panel can just omit the Tailscale section rather than show a broken one. Deliberately a
    plain CLI call, not a new Caddy site parse: added 2026-07-26 as a second reachability path
    alongside the LAN IP (same ports, same tokens, same CA — only the address differs), after a
    real "Connect Timeout Error" report from a LAN client turned out to be a network-path issue
    between the two machines rather than anything wrong on this one; the client (a laptop) was
    already on the same tailnet, so exposing the existing Caddy ports there too gave it a second
    path that doesn't depend on the LAN/Wi-Fi being cooperative. See parse_caddyfile()'s ports —
    Tailscale reuses them unchanged, it's the same Caddy site blocks with one more address each."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3
        )
        ip = out.stdout.strip().splitlines()[0].strip() if out.returncode == 0 else ""
        return ip or None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def tailscale_dns_name() -> str | None:
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                                text=True, timeout=3)
        if result.returncode:
            return None
        name = json.loads(result.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
        return name if re.fullmatch(r"[a-zA-Z0-9.-]+\.ts\.net", name) else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None


def tailscale_app_url(dns_name: str | None, app_port: int) -> str | None:
    """Find llama_web's configured HTTPS Serve route without guessing the port."""
    if not dns_name:
        return None
    try:
        result = subprocess.run(["tailscale", "serve", "status", "--json"], capture_output=True,
                                text=True, timeout=3)
        if result.returncode:
            return None
        config = json.loads(result.stdout)
        for authority, site in config.get("Web", {}).items():
            address = urlsplit("https://" + authority)
            target = urlsplit(site.get("Handlers", {}).get("/", {}).get("Proxy", ""))
            if (address.hostname == dns_name and target.port == app_port
                    and target.hostname in {"localhost", "127.0.0.1", "::1"}
                    and config.get("TCP", {}).get(str(address.port or 443), {}).get("HTTPS")):
                port = address.port or 443
                return f"https://{dns_name}" + (f":{port}" if port != 443 else "")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        pass
    return None


def parse_caddyfile(path: str = CADDYFILE) -> dict:
    """Pull the per-client bearer tokens and the external→backend port mapping out of the
    Caddyfile. Three distinct tokens (opencode/vscode/lan), not one shared secret (see the
    Caddyfile's `client_auth` snippet) - keyed by the Caddy matcher label attached to each
    ("@opencode header Authorization ...") so a client is identified by the credential it
    actually presents, not a spoofable/absent User-Agent. caddy_metrics.py parses this same
    file the same way to attribute LAN metrics rows by token."""
    result = {"tokens": {}, "ports": {}}
    try:
        text = open(path).read()
    except OSError:
        return result
    for label, token in re.findall(r'@(\w+)\s+header\s+Authorization\s+"Bearer\s+([A-Za-z0-9]+)"', text):
        result["tokens"][label] = token
    current_ext = None
    for raw in text.splitlines():
        line = raw.strip()
        header = re.search(r":(\d+)", line) if "{" in line and "reverse_proxy" not in line else None
        if header:
            current_ext = header.group(1)
        rp = re.search(r"reverse_proxy.*?:(\d+)", line)
        if rp and current_ext:
            backend = _UPSTREAM_BACKEND.get(rp.group(1))
            if backend:
                result["ports"][backend] = int(current_ext)
    return result


def ca_certificate() -> str | None:
    """Caddy's local CA root certificate (the one that must be trusted on the client),
    fetched from Caddy's admin API — it isn't sent in the served TLS chain."""
    try:
        r = requests.get(f"{CADDY_ADMIN}/pki/ca/local", timeout=4)
        r.raise_for_status()
        return (r.json().get("root_certificate") or "").strip() or None
    except (requests.RequestException, ValueError):
        return None


def ca_trust_commands(ip: str, app_port: int, *, base_url: str | None = None) -> dict[str, str]:
    """One-line copy/paste commands that fetch + trust the CA cert in a single step, per OS.

    A public CA (Let's Encrypt/ZeroSSL) can't issue certs for a private IP or LAN-only
    hostname like this machine's — that's a hard protocol constraint (domain validation needs
    public DNS/HTTP reachability), not a config choice. Caddy's `tls internal` already *is*
    its automatic-HTTPS behavior for exactly this case: certs are auto-issued and auto-renewed
    from Caddy's own local CA with zero server-side cert management. The one irreducible manual
    step is trusting that CA on each client once - these commands collapse what used to be
    "download this file, then follow several OS-specific GUI steps" into one line, they don't
    (and can't) remove the trust step itself.

    Fetches from THIS app's own /ca-cert route (deliberately unauthenticated - see its
    docstring) rather than Caddy directly, so one URL works before a client has any other
    reason to reach this machine yet."""
    url = f"{base_url}/ca-cert" if base_url else f"https://{ip}:{app_port}/ca-cert"
    curl = "curl -fsS" if base_url else "curl -sk"
    windows_tls = "" if base_url else " -SkipCertificateCheck"
    # Saved to the home dir, not /tmp - it needs to still be there later when a tool's config
    # (e.g. Continue's caBundlePath) points at it, and /tmp can get cleared on reboot.
    return {
        "linux": (
            f"{curl} {url} -o ~/{CERT_FILENAME} && "
            f"sudo cp ~/{CERT_FILENAME} /usr/local/share/ca-certificates/{CERT_FILENAME} && "
            "sudo update-ca-certificates"
        ),
        "macos": (
            f"{curl} {url} -o ~/{CERT_FILENAME} && "
            "sudo security add-trusted-cert -d -r trustRoot "
            f"-k /Library/Keychains/System.keychain ~/{CERT_FILENAME}"
        ),
        "windows": (
            f"Invoke-WebRequest -Uri {url} -OutFile $env:USERPROFILE\\{CERT_FILENAME}{windows_tls}; "
            f"Import-Certificate -FilePath $env:USERPROFILE\\{CERT_FILENAME} -CertStoreLocation Cert:\\LocalMachine\\Root"
        ),
    }


def _all_models() -> list[dict]:
    """Live model inventory across backends, each tagged with its backend — fail-soft, so a
    backend being down just omits it (same contract as /api/models)."""
    out: list[dict] = []
    # Broad excepts on purpose: this feeds a page render, so a backend being down (or a
    # client raising something unexpected) must degrade to "that backend's models are
    # absent", never a 500 on the whole page.
    from llama_web.backends import llamacpp_client
    for client, name in ((llamacpp_client, "llamacpp"), (zen, "opencode")):
        try:
            for m in client.list_models():

                m["backend"] = name
                out.append(m)
        except Exception:
            pass
    return out


def _pick_default(chat_by_be: dict, loaded_by_be: dict, override: str | None = None) -> str | None:
    """Default model for generated client configs. An explicit override wins while it
    still exists; otherwise prefer the loaded llama.cpp model so the first request is warm."""
    if override:
        be, _, model_id = override.partition("/")
        if model_id in chat_by_be.get(be, []):
            return override
    for be in ("llamacpp", "opencode"):
        if loaded_by_be.get(be):
            return f"{be}/{loaded_by_be[be][0]}"
    for be in ("llamacpp", "opencode"):
        if chat_by_be.get(be):
            return f"{be}/{chat_by_be[be][0]}"
    return None


def build_continue_yaml(ip, token, ports, chat_by_be, embed_models, network_label="LAN", ctx_by_model=None) -> str:
    # network_label is purely cosmetic (model display names + the config's own title) — lets
    # the same builder produce a LAN-flavored or Tailscale-flavored config from the same live
    # model list without duplicating this whole function; the actual connectivity is entirely
    # determined by `ip`.
    lines = [
        f"name: {network_label} Assistant (via Caddy)",
        "version: 1.0.0",
        "schema: v1",
        "",
        f"# Auto-generated from the live model list, Caddy config, and this machine's {network_label} IP.",
        "# Re-copy after downloading/deleting a model or if the IP changes.",
        "# Set caBundlePath to wherever you saved the CA cert (Step 1).",
        "models:",
    ]

    def entry(name, model, port, ctx, roles, timeout):
        b = [f"  - name: {name}", "    provider: openai", f"    model: {model}",
             f"    apiBase: https://{ip}:{port}/v1", f"    apiKey: {token}"]
        if ctx:
            b += ["    defaultCompletionOptions:", f"      contextLength: {ctx}"]
        b += [f"    roles: [{', '.join(roles)}]", "    requestOptions:",
              f"      caBundlePath: /home/YOUR_USERNAME/{CERT_FILENAME}", f"      timeout: {timeout}",
              # Caddy redacts Authorization from its own access log (a deliberate secret-
              # scrubbing default), so the token itself can't be used to attribute metrics rows
              # to "vscode_continue" after the fact - this header isn't a secret, just a label, and
              # Caddy logs client-sent headers it doesn't recognize as sensitive verbatim (see
              # caddy_metrics.py's _source_from_headers).
              "      headers:", "        X-llama_web-Client: vscode_continue"]
        return b

    any_chat = False
    ctx_by_model = dict(ctx_by_model or {})
    for be in ("llamacpp",):
        if be not in ports:
            continue
        for mid in chat_by_be.get(be, []):
            any_chat = True
            lines += entry(f"{mid} ({_LABELS[be]}, {network_label})", mid, ports[be],
                           _advertised_context(be, mid, ctx_by_model, None),
                           ["chat", "edit", "apply"], 120000)
    for em in embed_models:
        be = em["backend"]
        lines += entry(f"{em['id']} ({_LABELS.get(be, be)}, {network_label}) — embeddings for @codebase",
                       em["id"], ports.get(be, next(iter(ports.values()), 18471)), None,
                       ["embed"], 30000)
    if not any_chat:
        lines.append("  # (no chat models downloaded yet — get one from the Downloads panel)")
    lines += ["", "context:", "  - provider: code", "  - provider: docs", "  - provider: diff",
              "  - provider: terminal", "  - provider: problems", "  - provider: folder",
              "  - provider: codebase"]
    return "\n".join(lines)


def build_opencode_config(ip, token, ports, chat_by_be, loaded_by_be, default_override=None, network_label="LAN", ctx_by_model=None) -> str:
    # network_label: see build_continue_yaml's docstring comment — same reasoning, cosmetic only.
    # ctx_by_model: {(backend, model_id): context_to_advertise} resolved by build() from the
    # admin's saved per-model ctx-size (preferred) or the live loaded context. This keeps a
    # generated client config current even before a running model has been reloaded.
    ctx_by_model = dict(ctx_by_model or {})
    context_settings = opencode_context_settings()
    if not context_settings["advertise_loaded"]:
        ctx_by_model = {}
    provider = {}
    for be in ("llamacpp",):
        models = {}
        for mid in chat_by_be.get(be, []):
            ctx = _advertised_context(be, mid, ctx_by_model, context_settings["fallback"])
            models[mid] = {
                "name": f"{mid} ({_LABELS[be]}, {network_label})",
                "limit": {"context": ctx, "output": _advertised_output(be, ctx, mid)},
            }
        options = {
            "baseURL": f"https://{ip}:{ports[be]}/v1", "apiKey": token,
            "headers": {"X-llama_web-Client": "opencode"},
        }
        provider[be] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": f"{_LABELS[be]} ({network_label} via Caddy)",
            # headers.X-llama_web-Client isn't a secret, just a label - Caddy redacts Authorization
            # from its own access log (deliberate secret-scrubbing default), so the actual token
            # can't be used to attribute metrics rows to "opencode" after the fact. Caddy logs
            # client-sent headers it doesn't recognize as sensitive verbatim - see
            # caddy_metrics.py's _source_from_headers.
            "options": options,
            "models": models,
        }
    # OpenCode Console (opencode.ai) is remote and paid per request — it is deliberately
    # NOT routed through Caddy like the LAN backends and never auto-selected as the
    # default model. The admin's Console key goes directly into the provider block so a
    # generated config works as-is on the desktop and inside agent containers; the agentic
    # framework re-points the baseURL at its own per-task relay with the same key.
    console_key = zen.api_key()
    if console_key:
        console_models = {}
        for item in zen.list_models():
            context = item.get("configured_context_length") or item.get("max_context_length")
            if not (isinstance(context, int) and context > 0):
                # Conservative floor: Console models are typically >>65K; the model-list
                # endpoint normally advertises the real limit (used verbatim when present).
                context = 65536
            output = item.get("output_tokens_limit")
            entry = {
                "name": f"{item.get('name') or item['id']} (opencode.ai)",
                "limit": {"context": context, "output": max(2048, min(output or 16384, context // 4))},
            }
            console_models[item["id"].removeprefix("opencode/")] = entry
        if console_models:
            provider["opencode"] = {
                "npm": "@ai-sdk/openai-compatible",
                "name": "opencode.ai (hosted models)",
                "options": {
                    "baseURL": zen.base_url().rstrip("/") + "/v1",
                    "apiKey": console_key,
                },
                "models": console_models,
            }
    # Bound the administrator's compaction settings to the configured model windows
    # below; OpenCode's default retained history can exceed an entire small window.
    compaction = compaction_settings()
    compaction_block: dict = {"auto": compaction["auto"]}
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": _pick_default(chat_by_be, loaded_by_be, default_override) or "",
        "provider": provider,
        "compaction": compaction_block,
    }
    # Compaction is global for every backend.
    windows = [item['limit']['context'] for p in provider.values()
               for item in p['models'].values()]
    if windows:
        default_model = _pick_default(chat_by_be, loaded_by_be, default_override) or ""
        be_, model_id = default_model.partition("/")[0], default_model.partition("/")[2]
        default_window = None
        if be_ in provider and model_id in provider.get(be_, {}).get('models', {}):
            default_window = provider[be_]['models'][model_id]['limit']['context']
        limit = default_window or min(windows)
        # keep/buffer are derived from the selected model's window: keep + buffer +
        # the compaction prompt must fit, so keep never equals the window.
        budgets = configured_compaction_for_window(
            limit, keep_override=compaction['keep_tokens'],
            buffer_override=compaction['buffer'])
        compaction_block['keep'] = {'tokens': budgets['keep']}
        compaction_block['buffer'] = budgets['buffer']
    return json.dumps(cfg, indent=2)


def strip_jsonc(text: str) -> str:
    """Strip comments and trailing commas from a JSONC string without reading files."""
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/',
                  lambda match: match.group(1) or '', text, flags=re.S)
    return re.sub(r'("(?:\\.|[^"\\])*")|,([\s]*[}\]])',
                  lambda match: match.group(1) or match.group(2), text)


def opencode_config_object() -> dict:
    """The generated OpenCode config as a dict, derived from the live DB-backed inventory."""
    return json.loads(strip_jsonc(build()['opencode_config']))


def build_opencode_jsonc(host: str, app_port: int, api_key: str,
                         network_label: str = "local") -> str:
    """Return a copyable OpenCode JSONC config for llama-web's /v1 gateway."""
    models = _all_models()
    chat = [m for m in models if m.get("type") not in _NON_CHAT]
    entries = {}
    for m in chat:
        backend = m.get("backend") or "llamacpp"
        model_id = m["id"]
        context = (_configured_or_loaded_context(m)
                   or m.get("max_context_length")
                   or _advertised_context(backend, model_id, {}, None))
        label = m.get("name") or model_id
        entries[model_id] = {
            "name": f"{label} ({_LABELS.get(backend, backend)}, {network_label})",
            "limit": {"context": context,
                      "output": _advertised_output(backend, context, model_id)},
        }
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "llama-web": {
                "npm": "@ai-sdk/openai-compatible",
                "name": f"llama-web llama.cpp ({network_label})",
                "options": {
                    "baseURL": f"https://{host}:{app_port}/v1",
                    "apiKey": api_key,
                    "headers": {"X-llama_web-Client": "opencode"},
                },
                "models": entries,
            }
        },
    }
    header = (
        "// Generated by llama-web for the " + network_label + " network.\n"
        "// Replace apiKey if you rotate the engine key in ~/.config/llama-web/engine-key.\n"
        "// The apiKey is a credential — treat this file as sensitive.\n"
    )
    return header + json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def build(app_port: int = 5443) -> dict:
    """Assemble all runtime help data for the template / the /api/help endpoint.

    Wrapped in a stale-while-revalidate cache: every live source here has a cost (the
    Tailscale CLI, the Caddy admin API, and five backends' model lists — each with a
    multi-second timeout), so building this synchronously on `/` right after a sign-in
    made the post-login page sit blank for seconds — and indefinitely when a backend or
    the Tailscale CLI stalled ("signed in, clicked the button, nothing happens").
    Returning the last-good snapshot immediately and refreshing it in the background keeps
    the login->index redirect instant; the cache is warmed at startup (see warm_cache).
    """
    now = time.monotonic()
    stale = None
    with _help_cache_lock:
        cached, ts = _help_cache.get("data"), _help_cache.get("ts", 0)
        if cached is not None and now - ts < HELP_DATA_TTL:
            return cached
        if cached is not None:
            stale = cached
            if _help_cache.get("refreshing"):
                return stale  # a background refresh is already running from stale data
            _help_cache["refreshing"] = True
    if stale is not None:
        threading.Thread(target=_refresh, args=(app_port,), daemon=True).start()
        return stale
    data = _build_uncached(app_port)
    with _help_cache_lock:
        _help_cache["data"], _help_cache["ts"] = data, time.monotonic()
    return data


HELP_DATA_TTL = 12.0  # seconds a snapshot is served before a background rebuild starts
_help_cache: dict = {}
_help_cache_lock = threading.Lock()


def _refresh(app_port: int) -> None:
    """Background helper for stale-while-revalidate and startup warm-up; never raises
    and always clears the refreshing flag under the lock."""
    try:
        data = _build_uncached(app_port)
    except Exception:
        with _help_cache_lock:
            _help_cache["refreshing"] = False
        return
    with _help_cache_lock:
        _help_cache["data"] = data
        _help_cache["ts"] = time.monotonic()
        _help_cache["refreshing"] = False


def warm_cache(app_port: int = 5443) -> None:
    """Build the help snapshot in a background thread at startup, so the very first
    post-login render of `/` never pays the multi-second live-build cost."""
    threading.Thread(target=_refresh, args=(app_port,), daemon=True).start()


def _build_uncached(app_port: int = 5443) -> dict:
    ip = lan_ip()
    caddy = parse_caddyfile()
    from llama_web.backends import llamacpp_client
    fallback = "SET_A_BEARER_TOKEN_IN_CADDYFILE"
    lan_token = caddy["tokens"].get("lan") or fallback
    engine_key = llamacpp_client.api_key() or "SET_THE_LLAMA_CPP_ENGINE_KEY"
    ports = {**_DEFAULT_PORTS, **caddy["ports"]}
    cert = ca_certificate()

    models = _all_models()
    chat_by_be = {
        be: [m["id"] for m in models
             if m.get("backend") == be and m.get("type") not in _NON_CHAT
             and m.get("loadable") is not False]
        for be in ("llamacpp",)
    }
    # Which chat models are already loaded, per backend — used to default a LAN tool to a
    # warm model so its first request doesn't pay the cold-load (evict+load) cost.
    loaded_by_be = {
        be: [m["id"] for m in models
             if m.get("backend") == be and m.get("type") not in _NON_CHAT
             and m.get("loadable") is not False and m.get("state") == "loaded"]
        for be in ("llamacpp",)
    }
    # A loaded model's active window is authoritative. A saved larger value only takes
    # effect on the next Load; advertising it early makes OpenCode send oversized turns.
    ctx_by_model = {}
    for m in models:
        context = _configured_or_loaded_context(m)
        # An unloaded model can safely use its reported native maximum when no saved
        # context exists. For a loaded model, never advertise the maximum in place of its
        # active runtime window: doing so can make clients send requests the server rejects.
        if not context and m.get("state") != "loaded":
            reported_max = m.get("max_context_length")
            if type(reported_max) is int and reported_max > 0:
                context = reported_max
        if context:
            ctx_by_model[(m.get("backend"), m["id"])] = context
    embed_models = [
        {"id": m["id"], "backend": m.get("backend")}
        for m in models if m.get("type") == "embeddings"
    ]
    # Tailscale: "enabled" gates whether the Help panel shows the Tailscale section at all —
    # an admin who doesn't use Tailscale can turn it off outright rather than just ignoring
    # it. The manual override exists because tailscale_ip() shells out to the `tailscale` CLI
    # as this app's own process, which can fail in a container/restricted-service setup even
    # though Tailscale itself is reachable; an admin who already knows the IP can just type it
    # in instead of debugging CLI access.
    #
    # Deliberately NOT a "prefer Tailscale over LAN" switch (an earlier version of this had
    # one, swapping Step 2's config to the Tailscale IP) — real feedback was that some users
    # want the LAN config even with Tailscale enabled (e.g. a laptop that's usually on the
    # same LAN and only needs Tailscale occasionally, off-network). So Step 2 below always
    # stays LAN-only regardless of any Tailscale setting, and Tailscale gets its own always-
    # present, independently-generated set of configs instead of replacing anything — both
    # are shown side by side, pick whichever fits the device being configured right now.
    ts_enabled = (settings_store.get("tailscale_enabled") or "true") == "true"
    ts_ip = (settings_store.get("tailscale_ip_override") or tailscale_ip()) if ts_enabled else None
    ts_dns = tailscale_dns_name() if ts_enabled else None
    ts_app = tailscale_app_url(ts_dns, app_port)
    ts_links = {name: ts_app + path for name, path in {
        "chat": "/", "models": "/models", "downloads": "/downloads",
        "settings": "/settings", "ca_cert": "/ca-cert",
    }.items()} if ts_app else {}

    # Tailscale devices need their own copy of the CA-trust command: the app listens on
    # 0.0.0.0, so /ca-cert answers on the Tailscale address too.
    ts_ca_trust_commands = ca_trust_commands(ts_ip, app_port, base_url=ts_app) if ts_ip else None

    return {
        "lan_ip": ip,
        "tailscale_ip": ts_ip,
        "tailscale_dns_name": ts_dns,
        "tailscale_app_url": ts_app,
        "tailscale_links": ts_links,
        "tailscale_ca_trust_commands": ts_ca_trust_commands,
        "lan_token": lan_token,
        "ports": ports,
        "cert_filename": CERT_FILENAME,
        "ca_cert": cert or "(could not read the CA certificate — is Caddy running? "
                           "check the admin API at localhost:2019)",
        "ca_cert_ok": bool(cert),
        "ca_trust_commands": ca_trust_commands(ip, app_port),
        "opencode_config": build_opencode_jsonc(ip, app_port, engine_key, "local"),
        "tailscale_opencode_config": (
            build_opencode_jsonc(ts_ip or ip, app_port, engine_key, "Tailscale")
            if ts_ip else None
        ),
    }
