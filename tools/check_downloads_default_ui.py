"""Check that the Downloads page defaults to the requested HF trending catalog."""
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

NAME = "downloads-default-ui-test"
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == NAME:
        app.users_store.delete_user(existing["id"])
user = app.users_store.create_user(NAME, "test-password", "admin")
EXPECTED = "https://huggingface.co/models?apps=llama.cpp&other=function-calling&sort=trending"

with contextlib.ExitStack() as stack:
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)
    client = app.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, user_id=user["id"], username=NAME, role="admin",
                       auth_version=3, last_activity=time.time(), login_csrf_token="fixture")
    with patch.object(app.users_store, "get_user_by_id", return_value={**user, "role": "admin"}):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            page.context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                       "domain": "127.0.0.1", "path": "/"}])
            seen = []
            def fulfill_hf(route):
                seen.append(route.request.url)
                route.fulfill(json=[{
                    "id": "fixture/Trending-GGUF",
                    "tags": ["gguf", "function-calling"],
                    "likes": 10, "downloads": 100, "lastModified": "2026-11-26T00:00:00.000Z",
                    "safetensors": {"parameters": {}},
                }])
            page.route("https://huggingface.co/api/models**", fulfill_hf)
            page.route("**/api/downloads/files**", lambda route: route.fulfill(
                json=[{"name": "model.gguf", "size": 1000}]))
            page.goto(f"http://127.0.0.1:{server.server_port}/downloads")
            expect(page.locator("#catalog-all")).to_have_attribute("href", EXPECTED)
            expect(page.locator("#catalog-results .model-card")).to_have_count(1)
            assert seen, "HF API was not called"
            url = seen[0]
            assert "search=" not in url, url
            assert "apps=llama.cpp" in url, url
            assert "sort=trendingScore" in url, url
            assert "function-calling" in url, url
            browser.close()
            print("PASS Downloads defaults to the trending llama.cpp function-calling catalog.")
