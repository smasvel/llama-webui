"""Check the in-chat voice call: floating draggable avatar and working mic turn."""
import contextlib
import io
import sys
import threading
import time
import wave
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

NAME = "chat-voice-call-ui-test"
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == NAME:
        app.users_store.delete_user(existing["id"])
user = app.users_store.create_user(NAME, "test-password")

audio = io.BytesIO()
with wave.open(audio, "wb") as wav:
    wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    wav.writeframes(b"\0\0" * 8000)
WAV = audio.getvalue()
ANSWER = "data: {\"choices\":[{\"delta\":{\"content\":\"[emotion: happy] Hello from the call.\"}}]}\n\n" \
         "data: [DONE]\n\n"

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
            browser = playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            context = browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 900})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                  "domain": "127.0.0.1", "path": "/"}])
            page.route("**/api/models", lambda route: route.fulfill(json={"models": [
                {"id": "opencode/hy4-flash-free", "backend": "opencode", "type": "llm",
                 "state": "loaded", "name": "Hy4 flash Free"},
                {"id": "llamacpp/local.gguf", "backend": "llamacpp", "type": "llm",
                 "state": "loaded", "name": "Local model"},
            ]}))
            page.route("**/api/voice/transcribe", lambda route: route.fulfill(json={"text": "Hello there"}))
            page.route("**/api/voice/ask", lambda route: route.fulfill(
                status=200, content_type="text/event-stream", body=ANSWER))
            page.route("**/api/voice/speak", lambda route: route.fulfill(
                status=200, content_type="audio/wav", body=WAV))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.wait_for_timeout(1000)

            page.locator("#conversation-btn").click()
            widget = page.locator(".voice-float")
            expect(widget).to_be_visible()
            expect(widget.locator("canvas")).to_be_visible()
            expect(page.locator("#conversation-btn")).to_have_attribute("aria-pressed", "true")

            before = widget.bounding_box()
            head = widget.locator(".voice-float-head")
            head.hover()
            page.mouse.down()
            page.mouse.move(before["x"] - 180, before["y"] - 120, steps=5)
            page.mouse.up()
            after = widget.bounding_box()
            assert abs(after["x"] - before["x"]) > 50, (before, after)

            widget.locator("button", has_text="Hold to talk").click()
            widget.locator("button", has_text="Stop and send").click()
            expect(widget.locator(".voice-float-caption")).to_contain_text("Hello from the call.")
            status = widget.locator("p.hint").last.inner_text()
            assert "Error" not in status, status
            assert not errors, errors

            widget.locator("button[aria-label='End voice call']").click()
            expect(widget).to_have_count(0)
            browser.close()
            print("PASS in-chat voice call opens a draggable avatar and completes a mic turn.")
