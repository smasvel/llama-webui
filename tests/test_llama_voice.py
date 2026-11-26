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
