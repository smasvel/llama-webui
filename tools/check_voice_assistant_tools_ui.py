"""Voice assistant must answer quickly by using web research or MCP tools.

The model and tool executor are stubbed, but the chat page, voice panel,
/api/voice/ask route, tool dispatch loop, sentence-level TTS and transcript save
are the real implementation.
"""
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
from llama_web.storage import chat_store, users_store
from llama_web.voice import tts as local_tts

NAME = "voice-assistant-tools-ui"
for existing in users_store.list_users()["users"]:
    if existing["username"] == NAME:
        users_store.delete_user(existing["id"])
user = users_store.create_user(NAME, "test-password")

audio = io.BytesIO()
with wave.open(audio, "wb") as wav:
    wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
    wav.writeframes(b"\0\0" * 8000)
WAV = audio.getvalue()

TOOLS = [
    {"type": "function", "function": {
        "name": "playwright__browser_navigate",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "python_repl__execute_python",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}}}},
]


def sse(content=None, tool_call=None, finish="stop"):
    choice = {"index": 0, "delta": {}}
    if tool_call:
        choice["delta"]["tool_calls"] = [tool_call]
    if content:
        choice["delta"]["content"] = content
    if finish:
        choice["finish_reason"] = finish
    return "data: " + __import__("json").dumps({"choices": [choice]}) + "\n\n"


def call(name, args):
    return {
        "id": "c1", "type": "function",
        "function": {"name": name, "arguments": __import__("json").dumps(args)},
    }


def script_for(kind):
    if kind == "browser":
        return [
            sse(tool_call=call("playwright__browser_navigate", {"url": "https://example.org"}),
                finish="tool_calls"),
            sse(content="[emotion: happy] Example Domain is reserved for documentation examples.",
                finish="stop"),
        ]
    return [
        sse(tool_call=call("python_repl__execute_python", {"code": "print(2+2)"}),
            finish="tool_calls"),
        sse(content="[emotion: happy] The Python tool says the answer is 4.", finish="stop"),
    ]


with contextlib.ExitStack() as stack:
    stack.enter_context(patch.dict(app.app.config, {"SESSION_COOKIE_SECURE": False}))
    stack.enter_context(patch.object(app.mcp_tools_client, "ensure_started", return_value=TOOLS))
    stack.enter_context(patch.object(app.web_search, "should_search_web", return_value=True))
    stack.enter_context(patch.object(app, "_web_search_context", return_value=(
        "Live results: Example Domain is reserved for documentation examples.", [])))
    stack.enter_context(patch.object(local_tts, "synthesize", return_value=WAV))

    executed = []
    stack.enter_context(patch.object(app.mcp_tools_client, "call_tool",
                                     side_effect=lambda name, args: (
                                         executed.append((name, args)) or
                                         ("Python output: 4" if "python" in name
                                          else "Page text: Example Domain is reserved for documentation examples."))))

    streams = {"kind": "browser", "round": 0}
    def fake_chat_stream(messages, model, **kwargs):
        streams["round"] += 1
        script = script_for(streams["kind"])
        if streams["round"] > 1:
            script = script[1:]  # after the tool result, answer only
        yield from script
        yield "data: [DONE]\n\n"
    stack.enter_context(patch.object(app.zen, "chat_stream", side_effect=fake_chat_stream))

    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stack.callback(server.shutdown)

    client = app.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, user_id=user["id"], username=NAME, role="admin",
                       auth_version=3, last_activity=time.time(), login_csrf_token="t")
    with patch.object(app.users_store, "get_user_by_id", return_value={**user, "role": "admin"}):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=[
                "--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            context = browser.new_context(permissions=["microphone"])
            page = context.new_page()
            page.add_init_script("""(() => {
                window.__audioStarts = 0;
                const start = AudioBufferSourceNode.prototype.start;
                AudioBufferSourceNode.prototype.start = function (...args) {
                    window.__audioStarts += 1;
                    return start.apply(this, args);
                };
            })()""")
            cookie = app.app.config["SESSION_COOKIE_NAME"]
            context.add_cookies([{"name": cookie, "value": client.get_cookie(cookie).value,
                                  "domain": "127.0.0.1", "path": "/"}])
            page.route("**/api/models", lambda route: route.fulfill(json={"models": [{
                "id": "opencode/hy4-flash-free", "backend": "opencode", "type": "llm",
                "state": "loaded", "name": "Hy4 flash Free"}]}))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.locator("#conversation-btn").click()
            widget = page.locator(".voice-float")
            expect(widget).to_be_visible()
            expect(widget.locator("button[aria-label='End voice call']")).to_be_visible()

            # 1. Live web research through Playwright MCP.
            started = time.monotonic()
            page.evaluate("window.__voiceCallTest.send('Find Black Friday deals for 8 quart Instant Pot')")
            expect(widget.locator(".voice-float-caption")).to_contain_text(
                "Example Domain is reserved for documentation", timeout=10000)
            elapsed = time.monotonic() - started
            assert elapsed < 8, elapsed
            assert any(name == "playwright__browser_navigate" for name, _ in executed), executed
            assert page.evaluate("window.__audioStarts") >= 1
            assert "<tool_calls" not in widget.locator(".voice-float-caption").inner_text()

            # 2. Python MCP tool execution.
            streams["kind"] = "python"
            streams["round"] = 0
            executed.clear()
            started = time.monotonic()
            page.evaluate("window.__voiceCallTest.send('Run Python to calculate 2+2')")
            expect(widget.locator(".voice-float-caption")).to_contain_text("answer is 4", timeout=10000)
            elapsed = time.monotonic() - started
            assert elapsed < 8, elapsed
            assert any(name == "python_repl__execute_python" for name, _ in executed), executed
            assert "<tool_calls" not in widget.locator(".voice-float-caption").inner_text()

            browser.close()
            print("PASS voice assistant uses live web and MCP tools with quick spoken replies.")
