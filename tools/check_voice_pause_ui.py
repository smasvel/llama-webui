"""Voice call must keep listening through normal pauses in speech."""
import contextlib
import io
import math
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

NAME = "voice-pause-ui-test"
for existing in app.users_store.list_users()["users"]:
    if existing["username"] == NAME:
        app.users_store.delete_user(existing["id"])
user = app.users_store.create_user(NAME, "test-password")

# Simulated mic: 0.5s speech, 1.0s pause, 0.5s speech, 1.0s pause, repeating.
rate = 16000
audio = io.BytesIO()
with wave.open(audio, "wb") as wav:
    wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
    frames = bytearray()
    for i in range(rate * 3):
        speaking = (i % rate) < 8000
        value = int(6000 * math.sin(2 * math.pi * 220 * i / rate)) if speaking else 0
        frames.extend(value.to_bytes(2, "little", signed=True))
    wav.writeframes(bytes(frames))
WAV_PATH = Path("/tmp/opencode/voice-pause-fixture.wav")
WAV_PATH.parent.mkdir(parents=True, exist_ok=True)
WAV_PATH.write_bytes(audio.getvalue())

speech_wav = io.BytesIO()
with wave.open(speech_wav, "wb") as wav:
    wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    wav.writeframes(b"\0\0" * 8000)
SPEECH = speech_wav.getvalue()
ANSWER = 'data: {"choices":[{"delta":{"content":"[emotion: neutral] Still listening."}}]}\n\ndata: [DONE]\n\n'

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
                "--no-sandbox", "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={WAV_PATH}"])
            context = browser.new_context(permissions=["microphone"])
            page = context.new_page()
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                  "domain": "127.0.0.1", "path": "/"}])
            page.route("**/api/voice/transcribe", lambda route: route.fulfill(json={"text": "hello"}))
            page.route("**/api/voice/ask", lambda route: route.fulfill(
                status=200, content_type="text/event-stream", body=ANSWER))
            page.route("**/api/voice/speak", lambda route: route.fulfill(
                status=200, content_type="audio/wav", body=SPEECH))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#conversation-btn").click()
            widget = page.locator(".voice-float")
            expect(widget).to_be_visible()
            page.wait_for_timeout(300)
            debug = page.evaluate("window.__voiceCallDebug()")
            assert debug["globe"], debug
            expect(widget.locator("button[aria-label='End voice call']")).to_be_visible()
            page.wait_for_timeout(2200)
            page.evaluate("window.__voiceCallTest.send('Hello there')")
            expect(widget.locator(".voice-float-caption")).to_contain_text(
                "Still listening.", timeout=20000)
            status = widget.locator("p.hint").last.inner_text()
            assert "Ready" in status, status
            browser.close()
            print("PASS voice call replies automatically with no on-screen buttons.")
