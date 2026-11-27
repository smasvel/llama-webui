"""Gateway requests from remote networks appear in the Live logs UI."""
import contextlib
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

NAME = "live-logs-gateway-ui-test"
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == NAME:
        app.users_store.delete_user(existing["id"])
user = app.users_store.create_user(NAME, "test-password", "admin")

class Response:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}
    def iter_content(self, chunk_size=4096):
        return iter([b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"])

with contextlib.ExitStack() as stack:
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)

    client = app.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, user_id=user["id"], username=NAME, role="admin",
                       auth_version=3, last_activity=time.time(), login_csrf_token="fixture")
    key = "fixture-key"
    with patch.object(app.llamacpp, "api_key", return_value=key), \
         patch.object(app.requests, "request", return_value=Response()):
        client.environ_base["REMOTE_ADDR"] = "203.0.113.9"
        response = client.post(
            "/v1/chat/completions",
            json={"model": "llamacpp/live-log.gguf", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "X-llama_web-Client": "opencode", "X-CSRF-Token": "fixture"})
        response.get_data()

    with patch.object(app.users_store, "get_user_by_id", return_value={**user, "role": "admin"}):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            page.context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                       "domain": "127.0.0.1", "path": "/"}])
            page.goto(f"http://127.0.0.1:{server.server_port}/live-logs")
            expect(page.locator("#live-feed")).to_contain_text("tailscale")
            expect(page.locator("#live-feed")).to_contain_text("llamacpp/live-log.gguf")
            browser.close()
            print("PASS Live logs show remote gateway traffic with source tailscale.")
