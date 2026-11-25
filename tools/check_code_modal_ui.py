"""Check that the 'Insert into message' button is aligned and inserts code."""
import contextlib
import sys
import threading
import time
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

NAME = "code-modal-ui-test"
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
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            page.context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                       "domain": "127.0.0.1", "path": "/"}])
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#insert-code-btn").click()
            expect(page.locator(".code-modal-footer")).to_be_visible()

            cancel = page.locator("#code-modal-cancel").bounding_box()
            insert = page.locator("#code-modal-insert").bounding_box()
            assert cancel and insert
            assert abs(cancel["y"] - insert["y"]) < 1, (cancel, insert)
            assert abs(cancel["height"] - insert["height"]) < 1, (cancel, insert)
            assert page.locator("#code-modal-insert").evaluate(
                "e => e.scrollWidth <= e.clientWidth + 1"), "button label overflows"

            page.locator("#code-modal-lang").select_option("python")
            page.locator("#code-modal-textarea").fill("print('hi')")
            page.locator("#code-modal-insert").click()
            assert "```python\nprint('hi')\n```" in page.locator("#chat-input").input_value()
            browser.close()
            print("PASS Insert into message button is aligned and inserts code.")
app.users_store.delete_user(user["id"])
