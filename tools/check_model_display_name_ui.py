"""Check that local GGUF paths are never rendered in the Chat UI."""
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

MODEL_ID = ("llamacpp/cdiamond/Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF/"
            "Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf")
MODEL_NAME = "Qwen 3.8 27B NVFP4 MTP"
USERNAME = "display-name-browser-test"

# Re-running the check must not collide with a previous run's user.
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == USERNAME:
        app.users_store.delete_user(existing["id"])

with contextlib.ExitStack() as stack:
    user = app.users_store.create_user(USERNAME, "test-password")
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    for backend in (app.lemonade, app.kobold, app.vllm, app.q38rocm, app.zen):
        stack.enter_context(patch.object(backend, "owns_model", return_value=False))

    cid = app.chat_store.new_id()
    app.chat_store.ensure_conversation(cid, user["id"], MODEL_ID, "Display name check")
    app.chat_store.add_message(cid, "user", "hi", model=MODEL_ID, backend="llamacpp")
    app.chat_store.add_message(cid, "assistant", "Hello!", model=MODEL_ID, backend="llamacpp",
                               duration=1.0, total_tokens=12)

    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)

    client = app.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, user_id=user["id"], last_activity=time.time(),
                       login_csrf_token="fixture")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        cookie = app.app.config["SESSION_COOKIE_NAME"]
        page.context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                   "domain": "127.0.0.1", "path": "/"}])
        page.route("**/api/models", lambda route: route.fulfill(json={"models": [{
            "id": MODEL_ID, "name": MODEL_NAME, "backend": "llamacpp", "type": "llm",
            "state": "loaded", "quantization": "NVFP4", "max_context_length": 262144,
        }]}))
        page.goto(f"http://127.0.0.1:{server.server_port}/")

        # The chat model picker shows the model name, not its library-relative id.
        option = page.locator("#model-select option:checked")
        expect(option).to_contain_text(MODEL_NAME)
        assert "/" not in option.inner_text(), option.inner_text()
        assert ".gguf" not in option.inner_text(), option.inner_text()

        # Previously saved assistant messages store the raw id as their model; the
        # bubble metadata must resolve it back to a readable name.
        page.evaluate("id => window.ChatApp.loadConversation(id)", cid)
        meta = page.locator(".msg-meta").last
        expect(meta).to_contain_text(MODEL_NAME)
        assert "llamacpp/cdiamond" not in meta.inner_text(), meta.inner_text()
        assert ".gguf" not in meta.inner_text(), meta.inner_text()
        browser.close()
        print("PASS Chat picker and bubble metadata show model names instead of GGUF paths.")
