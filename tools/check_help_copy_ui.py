"""Check Help panel code blocks render inline copy buttons with tooltips."""
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

NAME = "help-copy-ui-test"
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
            context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
            page = context.new_page()
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                  "domain": "127.0.0.1", "path": "/"}])
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#help-toggle").click()
            pre = page.locator("#help-opencode-config")
            button = page.locator("#copy-opencode-config")
            expect(pre).to_be_visible()
            # Trust commands and certificate download are available again.
            expect(page.locator("#help-linux-trust")).to_be_visible()
            expect(page.locator("#help-tailscale-ca_cert-link")).to_be_visible()
            tailscale_link_text = page.locator("#help-tailscale-chat-link").inner_text()
            assert "://" not in tailscale_link_text, tailscale_link_text
            expect(page.get_by_text("issued by a public CA", exact=False)).to_be_visible()
            wrap = page.locator("#copy-opencode-config").locator("xpath=..")
            expect(wrap).to_have_class("code-copy-wrap")
            pre_box = pre.bounding_box()
            btn_box = button.bounding_box()
            assert pre_box and btn_box
            assert btn_box["x"] >= pre_box["x"] and btn_box["y"] >= pre_box["y"], (pre_box, btn_box)
            assert btn_box["x"] + btn_box["width"] <= pre_box["x"] + pre_box["width"] + 1
            button.hover()
            content = button.evaluate("e => getComputedStyle(e, '::after').content")
            assert "Copy local config" in content, content
            button.click()
            copied = page.evaluate("navigator.clipboard.readText()")
            assert copied == pre.inner_text()
            browser.close()
            print("PASS Help code blocks have aligned copy buttons with tooltips.")
