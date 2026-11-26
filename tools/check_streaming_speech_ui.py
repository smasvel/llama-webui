"""Verify that spoken replies stream from llama.cpp through the local Piper voice."""
import contextlib
import io
import json
import os
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
from llama_web.voice import tts as local_tts

FIRST_PHRASE = "The first words are ready now"
LONG_FIRST = "Here is the complete report for today."
LIVE_TTS = os.environ.get("LLAMA_WEB_TEST_LIVE_TTS") == "1"
live_synthesize = local_tts.synthesize
speech_requests = []
audio = io.BytesIO()
with wave.open(audio, "wb") as wav:
    wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    wav.writeframes(b"\0\0" * 9600)


def stream_chat(*args, **kwargs):
    def delta(text):
        return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})

    if "complete report" in args[0][-1]["content"]:
        yield delta(LONG_FIRST + " " + ("The rest of this fixture should follow the first spoken phrase. " * 9))
    else:
        yield delta(FIRST_PHRASE + " ")
        yield delta("\n```python\nDO_NOT_SPEAK_THIS_CODE\n```\n")
    yield "data: [DONE]"


def synthesize(text, **kwargs):
    speech_requests.append(text)
    if LIVE_TTS:
        return live_synthesize(text, **kwargs)
    return audio.getvalue()


with contextlib.ExitStack() as stack:
    user = app.users_store.create_user("speech-browser-test", "fixture-password", "user")
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    stack.enter_context(patch.object(app.llamacpp, "chat_stream", side_effect=stream_chat))
    stack.enter_context(patch.object(app.llamacpp, "owns_model", return_value=True))
    stack.enter_context(patch.object(local_tts, "synthesize", side_effect=synthesize))
    stack.enter_context(patch.object(app, "_recall_memory", return_value=None))
    stack.enter_context(patch.object(app, "_remember_exchange"))
    for backend in (app.lms, app.lemonade, app.kobold, app.vllm, app.q38rocm, app.zen):
        stack.enter_context(patch.object(backend, "owns_model", return_value=False))
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
        name = app.app.config["SESSION_COOKIE_NAME"]
        page.context.add_cookies([{"name": name, "value": client.get_cookie(name).value,
                                   "domain": "127.0.0.1", "path": "/"}])
        page.add_init_script("""(() => {
            window.spokenClips = 0;
            window.stoppedClips = 0;
            new MutationObserver(() => {
                if (!window.firstPhraseShown &&
                    document.body?.textContent.includes("The first words are ready now")) {
                    window.firstPhraseShown = performance.now();
                }
            }).observe(document, {childList: true, subtree: true, characterData: true});
            const start = AudioBufferSourceNode.prototype.start;
            AudioBufferSourceNode.prototype.start = function (...args) {
                const result = start.apply(this, args);
                if (this.buffer?.duration > 0.1) {
                    window.spokenClips++;
                    window.firstClipStart ??= performance.now();
                }
                return result;
            };
            const stop = AudioBufferSourceNode.prototype.stop;
            AudioBufferSourceNode.prototype.stop = function (...args) {
                if (this.buffer?.duration > 0.1) window.stoppedClips++;
                return stop.apply(this, args);
            };
        })()""")
        page.route("**/api/models", lambda route: route.fulfill(json={"models": [{
            "id": "speech-fixture", "backend": "llamacpp", "type": "llm", "state": "loaded",
        }]}))
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        expect(page.locator("#model-select option[value='speech-fixture']")).to_have_count(1)
        page.locator("#model-select").select_option("speech-fixture")
        page.locator("#speak-toggle").click()
        page.locator("#chat-input").fill("Explain this fixture")
        page.locator("#send-btn").click()
        expect(page.get_by_text(FIRST_PHRASE, exact=False)).to_be_visible()
        page.wait_for_function("window.spokenClips >= 1", timeout=6000)
        elapsed = page.evaluate("(window.firstClipStart - window.firstPhraseShown) / 1000")
        assert 0 <= elapsed < (5 if LIVE_TTS else 2), elapsed
        expect(page.locator("#send-btn")).to_have_text("Send")
        assert speech_requests == [FIRST_PHRASE], speech_requests

        page.locator("#chat-input").fill("Show the complete report")
        clip_started = time.monotonic()
        page.locator("#send-btn").click()
        expect(page.get_by_text(LONG_FIRST, exact=False)).to_be_visible()
        page.wait_for_function("window.spokenClips >= 2", timeout=7000)
        assert speech_requests[1] == LONG_FIRST, speech_requests[2:]
        print(f"PASS llama.cpp streaming speech began within {time.monotonic() - clip_started:.2f}s of Send.")
        browser.close()
