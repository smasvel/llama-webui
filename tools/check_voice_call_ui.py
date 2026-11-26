"""Check that the llama-only voice call page renders and reports mic failures safely."""
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

NAME = "voice-call-ui-test"
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == NAME:
        app.users_store.delete_user(existing["id"])
user = app.users_store.create_user(NAME, "test-password")

with contextlib.ExitStack() as stack:
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)
    client = app.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, user_id=user["id"], username=NAME, role="user",
                       auth_version=3, last_activity=time.time(), login_csrf_token="fixture")
    with patch.object(app.users_store, "get_user_by_id", return_value=user):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            page.context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                       "domain": "127.0.0.1", "path": "/"}])
            page.goto(f"http://127.0.0.1:{server.server_port}/voice")
            expect(page.locator("#voice-avatar")).to_be_visible()
            expect(page.locator("#voice-start")).to_be_enabled()
            expect(page.locator("#voice-model-select")).to_be_visible()
            expect(page.locator("#voice-mic")).to_be_disabled()
            expect(page.locator("#voice-status")).to_contain_text("microphone")
            assert not errors, errors
            browser.close()
            print("PASS llama-only voice call page renders with animated avatar canvas.")
