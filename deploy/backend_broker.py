"""Small host-side broker for the Docker llama_web app to manage host systemd units.

Docker llama_web runs the Flask app without systemd, so the vLLM / KoboldCPP / FastFlowLM
load/unload controls were unavailable from inside the container. This broker listens on
127.0.0.1 and executes a *restricted* set of ``systemctl --user`` commands on the llama_web
host, returning ``(returncode, stdout, stderr)``.

Defaults are about as locked down as this can be made short of not running at all:

- bound to 127.0.0.1 only,
- bearer-token auth (token file must have mode 600),
- an explicit allowlist of service names the broker will act on,
- an explicit allowlist of systemctl verbs — no ``kill``, no ``edit``, no ``run``,
  no ``daemon-reload`` and nothing else.

Run it with ``python3 deploy/backend_broker.py`` from the llama_web host, or install it as
a systemd *user* unit with ``bash deploy/install-backend-broker.sh``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = int(os.environ.get("LLAMA_WEB_BACKEND_BROKER_PORT", "8765"))
DEFAULT_TOKEN_FILE = os.path.expanduser(
    os.environ.get("LLAMA_WEB_BACKEND_BROKER_TOKEN_FILE", "~/.config/llama_web/backend-broker/token")
)
# Docker llama_web drives these host units. Keeping the list explicit keeps the blast
# radius of a leaked token very small.
_DEFAULT_SERVICES = "vllm.service,kobold.service,q38rocm.service"
_ALLOWED_VERBS = frozenset({"start", "stop", "restart", "daemon-reload", "is-active", "is-enabled"})
_ALLOWED_SCOPES = frozenset({"user", "system"})
_ALLOWED_UNITS = frozenset(
    service.strip() for service in os.environ.get(
        "LLAMA_WEB_BACKEND_BROKER_SERVICES", _DEFAULT_SERVICES
    ).split(",") if service.strip()
)


def _allowed_units() -> frozenset[str]:
    return _ALLOWED_UNITS


class BrokerError(RuntimeError):
    """Internal server error; surfaces as a JSON 500 to the caller."""


def load_token(token_file: str = DEFAULT_TOKEN_FILE) -> str | None:
    """Read the broker bearer token.

    Environment ``LLAMA_WEB_BACKEND_BROKER_TOKEN`` wins over the file. A file that is not
    mode 600 is refused, so an accidental world-readable token cannot silently weaken
    the broker."""
    env_token = os.environ.get("LLAMA_WEB_BACKEND_BROKER_TOKEN")
    if env_token:
        return env_token
    try:
        if os.stat(token_file).st_mode & 0o077:
            print(f"llama_web-backend-broker: {token_file} must not be group/world-accessible", file=sys.stderr)
            return None
        return open(token_file).read().strip() or None
    except OSError:
        return None


def run_systemctl(args: list[str], service: str, scope: str = "user", timeout: int = 120) -> tuple[int, str, str]:
    """Run ``systemctl [--user] <args...> <service>`` on the llama_web host.

    Returns ``(returncode, stdout, stderr)``. Verbs, scope and service names are validated
    so a leaked token still cannot run arbitrary commands."""
    verb = args[0] if args else ""
    if verb not in _ALLOWED_VERBS:
        raise BrokerError(f"unsupported systemctl verb '{verb}'")
    if scope not in _ALLOWED_SCOPES:
        raise BrokerError(f"unsupported systemctl scope '{scope}'")
    if service not in _allowed_units():
        raise BrokerError(f"service '{service}' is not enabled on the backend broker")
    command = ["systemctl"]
    if scope == "user":
        command.append("--user")
    command.extend([*args, service])
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BrokerError(f"systemctl {verb} {service} timed out") from exc
    except FileNotFoundError as exc:
        raise BrokerError("systemctl is not available on the broker host") from exc
    return proc.returncode, proc.stdout, proc.stderr


class _Handler(BaseHTTPRequestHandler):
    server_version = "llama_web-backend-broker/1"
    # Quiet down request logging; the token must never be logged.
    def log_message(self, fmt, *args) -> None:
        if "Bearer" in fmt % args:
            return
        super().log_message(fmt, *args)

    def _reject(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(message.encode())))
        self.end_headers()
        self.wfile.write(message.encode())

    def do_GET(self) -> None:
        self._reject(405, json.dumps({"error": "POST only"}))

    def do_HEAD(self) -> None:
        self._reject(405, json.dumps({"error": "POST only"}))

    def do_POST(self) -> None:
        token = load_token()
        if not token:
            self._reject(503, json.dumps({"error": "backend broker is not configured"}))
            return
        if self.path.rstrip("/") != "/systemd":
            self._reject(404, json.dumps({"error": "unknown endpoint"}))
            return
        auth = self.headers.get("Authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {token}"):
            self._reject(401, json.dumps({"error": "invalid broker token"}))
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
        except (ValueError, json.JSONDecodeError) as exc:
            self._reject(400, json.dumps({"error": f"invalid request body: {exc}"}))
            return
        if self.path.rstrip("/") == "/systemd":
            try:
                args = [str(x) for x in body["args"]]
                service = str(body["service"])
                scope = str(body.get("scope", "user"))
                returncode, stdout, stderr = run_systemctl(args, service, scope=scope)
                payload = {"returncode": returncode, "stdout": stdout, "stderr": stderr}
            except BrokerError as exc:
                self._reject(400, json.dumps({"error": str(exc)}))
                return
            except Exception as exc:  # pragma: no cover - unexpected failure path
                self._reject(500, json.dumps({"error": str(exc)}))
                return

def broker_main() -> None:
    port = DEFAULT_PORT
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"llama_web-backend-broker: listening on 127.0.0.1:{port} services={','.join(_allowed_units())}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    broker_main()
