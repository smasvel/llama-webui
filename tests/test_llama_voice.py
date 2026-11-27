"""The dedicated voice call must use llama.cpp + local STT/TTS only."""
import io
import time
import unittest
from unittest.mock import patch

import app
from llama_web.voice import stt as local_stt, tts as local_tts


class LlamaVoiceTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.user = {"id": "voice-user", "username": "voice-user", "role": "admin"}
        self.user_patch = patch.object(app.users_store, "get_user_by_id", return_value=self.user)
        self.user_patch.start()
        self.addCleanup(self.user_patch.stop)
        with self.client.session_transaction() as session:
            session.update(logged_in=True, user_id=self.user["id"], username=self.user["username"],
                           role="admin", auth_version=3, last_activity=time.time(),
                           login_csrf_token="voice-token")
        self.headers = {"X-CSRF-Token": "voice-token"}

    def test_voice_page_renders(self):
        response = self.client.get("/voice")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="voice-avatar"', response.data)
        self.assertIn(b'id="voice-start"', response.data)

    def test_ask_streams_only_from_llamacpp(self):
        def stream(messages, model, **kwargs):
            yield 'data: {"choices": [{"delta": {"content": "[emotion: happy]Hi"}}]}\n\n'
            yield 'data: [DONE]\n\n'
        with patch.object(app.llamacpp, "chat_stream", side_effect=stream) as chat, \
             patch.object(app.lemonade, "chat_stream", side_effect=AssertionError("lemonade used")), \
             patch.object(app.lms, "chat_stream", side_effect=AssertionError("lms used")):
            response = self.client.post(
                "/api/voice/ask", json={"message": "hello"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"[emotion: happy]Hi", response.data)
        chat.assert_called_once()

    def test_ask_can_use_an_online_model(self):
        def stream(messages, model, **kwargs):
            yield 'data: {"choices": [{"delta": {"content": "[emotion: happy]Hi there"}}]}\n\n'
            yield 'data: [DONE]\n\n'
        with patch.object(app.zen, "chat_stream", side_effect=stream) as chat, \
             patch.object(app.llamacpp, "chat_stream", side_effect=AssertionError("local model used")):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "hello", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"[emotion: happy]Hi there", response.data)
        chat.assert_called_once()

    def test_ask_offers_mcp_tools_when_the_request_needs_them(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["tools"] = kwargs.get("tools")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: happy] Done."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.mcp_tools_client, "chat_tool_mode", return_value="python"), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "python_repl__execute_python"}}]), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "Run Python to add 2 and 2", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
            response.get_data()
        self.assertEqual(response.status_code, 200)
        names = [tool["function"]["name"] for tool in seen.get("tools") or []]
        self.assertIn("python_repl__execute_python", names)

    def test_ask_always_offers_browser_tools_when_web_is_enabled(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["tools"] = kwargs.get("tools")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Hello."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        original_mode = app.mcp_tools_client.chat_tool_mode
        with patch.object(app.mcp_tools_client, "chat_tool_mode",
                          side_effect=lambda text, **kw: None if text == "hello there"
                          else original_mode(text, **kw)), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_snapshot"}},
                 {"function": {"name": "python_repl__execute_python"}},
             ]), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "hello there", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
            response.get_data()
        self.assertEqual(response.status_code, 200)
        names = [tool["function"]["name"] for tool in seen.get("tools") or []]
        self.assertIn("playwright__browser_snapshot", names)
        self.assertNotIn("python_repl__execute_python", names)

    def test_ask_persists_both_turns_in_the_chat_conversation(self):
        from llama_web.storage import chat_store
        cid = chat_store.new_id()
        chat_store.ensure_conversation(cid, self.user["id"], "opencode/hy4-flash-free", "hello")

        def stream(messages, model, **kwargs):
            yield 'data: {"choices": [{"delta": {"content": "[emotion: happy] Persisted reply."}}]}\n\n'
            yield 'data: [DONE]\n\n'
        with patch.object(app.zen, "chat_stream", side_effect=stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "hello", "model": "opencode/hy4-flash-free",
                      "conversation_id": cid},
                headers=self.headers)
        self.assertEqual(response.status_code, 200)
        response.get_data()  # drain the SSE stream so the generator finalizer runs
        messages = chat_store.get_conversation(cid, self.user["id"])["messages"]
        roles = [message["role"] for message in messages]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[1]["content"], "Persisted reply.")

    def test_speak_uses_local_tts_and_wav(self):
        wav = io.BytesIO()
        wav.write(b"RIFFfake-wave")
        with patch.object(local_tts, "synthesize", return_value=b"RIFFfake-wave") as synth:
            response = self.client.post(
                "/api/voice/speak", json={"text": "hello"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(response.data, b"RIFFfake-wave")
        synth.assert_called_once()

    def test_voice_error_is_actionable(self):
        with patch.object(local_stt, "transcribe", side_effect=local_stt.VoiceError("no local model")):
            response = self.client.post(
                "/api/voice/transcribe", data={"audio": (io.BytesIO(b"audio"), "a.webm")},
                headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertIn("no local model", response.json["error"])


if __name__ == "__main__":
    unittest.main()
