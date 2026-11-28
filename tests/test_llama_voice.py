"""The dedicated voice call must use llama.cpp + local STT/TTS only."""
import json
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

    def test_ask_does_not_offer_browser_tools_for_casual_speech(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["tools"] = kwargs.get("tools")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Hello."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.mcp_tools_client, "chat_tool_mode", return_value=None), \
             patch.object(app.mcp_tools_client, "ensure_started", side_effect=AssertionError(
                 "MCP tools must not start for a spoken greeting")), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "hello there", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
            response.get_data()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(seen.get("tools"))

    def test_voice_prompt_asks_for_an_internal_tool_decision(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["system"] = messages[0]["content"]
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Hi."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.web_search, "should_search_web", return_value=True), \
             patch.object(app, "_web_search_context", return_value=("", [])), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_navigate"}}]), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            self.client.post(
                "/api/voice/ask",
                json={"message": "what is the weather in Durham",
                      "model": "opencode/hy4-flash-free"},
                headers=self.headers).get_data()
        self.assertIn("Before any tool call", seen["system"])
        self.assertIn("decide internally", seen["system"])
        self.assertIn("chat-temp/", seen["system"])

    def test_voice_disables_reasoning_and_uses_snippet_only_search(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["reasoning_effort"] = kwargs.get("reasoning_effort")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Hi."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.web_search, "should_search_web", return_value=True), \
             patch.object(app, "_web_search_context", return_value=("hint", [])) as search, \
             patch.object(app.mcp_tools_client, "chat_tool_mode", return_value=None), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[]), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            self.client.post(
                "/api/voice/ask",
                json={"message": "latest news", "model": "opencode/hy4-flash-free"},
                headers=self.headers).get_data()
        self.assertEqual(seen["reasoning_effort"], "none")
        search.assert_called_once_with("latest news", snippets_only=True)

    def test_ask_offers_browser_tools_for_a_live_lookup_without_explicit_mode(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["tools"] = kwargs.get("tools")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Sunny."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        original_mode = app.mcp_tools_client.chat_tool_mode
        with patch.object(app.web_search, "should_search_web", return_value=True), \
             patch.object(app, "_web_search_context", return_value=("some live hint", [])), \
             patch.object(app.mcp_tools_client, "chat_tool_mode",
                          side_effect=lambda text, **kw: None if text == "check the weather in Durham nc"
                          else original_mode(text, **kw)), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_navigate"}}]), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            self.client.post(
                "/api/voice/ask",
                json={"message": "check the weather in Durham nc",
                      "model": "opencode/hy4-flash-free"},
                headers=self.headers).get_data()
        names = [tool["function"]["name"] for tool in seen.get("tools") or []]
        self.assertIn("playwright__browser_navigate", names)

    def test_timeless_factual_question_skips_browser_tools_for_fast_answer(self):
        seen = {}

        def stream(messages, model, **kwargs):
            seen["tools"] = kwargs.get("tools")
            yield 'data: {"choices": [{"delta": {"content": "[emotion: neutral] Paris."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.mcp_tools_client, "ensure_started", side_effect=AssertionError(
                 "A timeless factual question must not start the browser")), \
             patch.object(app.zen, "chat_stream", side_effect=stream):
            self.client.post(
                "/api/voice/ask",
                json={"message": "What is the capital of France?",
                      "model": "opencode/hy4-flash-free"},
                headers=self.headers).get_data()
        self.assertFalse(seen.get("tools"))

    def test_refusal_escalates_to_browser_tools_when_none_were_offered(self):
        seen = []

        def first(messages, model, **kwargs):
            seen.append(kwargs.get("tools"))
            yield ('data: {"choices": [{"delta": {"content": '
                   '"[emotion: neutral] I don\'t know that one."}}]}\n\n')
            yield 'data: [DONE]\n\n'

        def second(messages, model, **kwargs):
            seen.append(kwargs.get("tools"))
            yield ('data: {"choices": [{"delta": {"content": '
                   '"[emotion: neutral] It is Paris."}}]}\n\n')
            yield 'data: [DONE]\n\n'

        streams = [first, second]

        def fake_chat(messages, model, **kwargs):
            return streams.pop(0)(messages, model, **kwargs)

        original_mode = app.mcp_tools_client.chat_tool_mode
        with patch.object(app, "_voice_needs_lookup", return_value=False), \
             patch.object(app.mcp_tools_client, "chat_tool_mode",
                          side_effect=lambda text, **kw: None if text == "What is the capital of France?"
                          else original_mode(text, **kw)), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_navigate"}}]), \
             patch.object(app.zen, "chat_stream", side_effect=fake_chat):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "What is the capital of France?",
                      "model": "opencode/hy4-flash-free"},
                headers=self.headers)
        self.assertIn(b"It is Paris.", response.data)
        self.assertFalse(seen[0])
        self.assertTrue(any((tool or {}).get("function", {}).get("name") ==
                            "playwright__browser_navigate" for tool in seen[1] or []))

    def test_tool_only_stream_still_produces_a_spoken_answer(self):
        def tool_stream(messages, model, **kwargs):
            yield ('data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", '
                   '"type": "function", "function": {"name": "playwright__browser_navigate", '
                   '"arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}\n\n')
            yield 'data: [DONE]\n\n'

        with patch.object(app.web_search, "should_search_web", return_value=True), \
             patch.object(app, "_web_search_context", return_value=("", [])), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_navigate"}}]), \
             patch.object(app.mcp_tools_client, "call_tool",
                          return_value="India won the final by 50 runs."), \
             patch.object(app.zen, "chat_stream", side_effect=tool_stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "who won the latest cricket world cup?",
                      "model": "opencode/hy4-flash-free"},
                headers=self.headers)
        self.assertIn(b"India won the final by 50 runs.", response.data)

    def test_weak_answer_detector_catches_leaked_planning_text(self):
        self.assertTrue(app._voice_is_weak_answer(
            "Need answer naturally out loud. Need parse user: the weather question."))
        self.assertTrue(app._voice_is_weak_answer(
            "Sorry, I couldn't verify the live results."))
        self.assertFalse(app._voice_is_weak_answer(
            "[emotion: neutral] It's sunny, 51F in Durham."))

    def test_ask_logs_thinking_and_answer_outcome(self):
        def stream(messages, model, **kwargs):
            yield 'data: {"choices": [{"delta": {"content": "[emotion: happy]Logged reply."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.llamacpp, "chat_stream", side_effect=stream), \
             self.assertLogs("app", level="INFO") as logs:
            self.client.post("/api/voice/ask", json={"message": "hello"},
                             headers=self.headers).get_data()
        self.assertTrue(any("VOICE_ASK" in line and "outcome=ok" in line
                            and "first_token_ms=" in line for line in logs.output))

    def test_voice_log_endpoint_records_spoken_vs_leftover(self):
        with self.assertLogs("app", level="INFO") as logs:
            response = self.client.post("/api/voice/log", json={
                "event": "coverage", "conversation_id": "conv-1",
                "model": "opencode/hy4-flash-free", "answer_chars": 100,
                "spoken_chars": 40, "left_chars": 60, "elapsed_ms": 4200,
                "preview": "the remaining sentence"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("VOICE_CLIENT" in line and "event=coverage" in line
                            and "left_chars=60" in line for line in logs.output))

    def test_ask_never_speaks_a_reasoning_only_stream(self):
        def stream(messages, model, **kwargs):
            yield 'data: {"choices": [{"delta": {"reasoning_content": "Just say hello back."}}]}\n\n'
            yield 'data: [DONE]\n\n'

        with patch.object(app.zen, "chat_stream", side_effect=stream):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "hello", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
        self.assertNotIn(b"Just say hello back.", response.data)
        self.assertIn(b"couldn't summarize it out loud", response.data)

    def test_ask_strips_thinking_tags_and_caps_long_replies(self):
        def stream(messages, model, **kwargs):
            yield ('data: {"choices": [{"delta": {"content": '
                   '"<think>private thoughts</think>A short spoken answer."}}]}\n\n')
            yield 'data: [DONE]\n\n'

        with patch.object(app.llamacpp, "chat_stream", side_effect=stream):
            response = self.client.post("/api/voice/ask", json={"message": "hello"},
                                        headers=self.headers)
        self.assertNotIn(b"private thoughts", response.data)
        self.assertIn(b"A short spoken answer.", response.data)

    def test_ask_summarizes_after_live_check_without_tool_round_text(self):
        calls = []

        def first(messages, model, **kwargs):
            yield ('data: {"choices": [{"delta": {"content": '
                   '"<think>Let me check the web.</think>I will look that up now."}}]}\n\n')
            yield ('data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", '
                   '"type": "function", "function": {"name": "playwright__browser_navigate", '
                   '"arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}\n\n')
            yield 'data: [DONE]\n\n'

        def second(messages, model, **kwargs):
            yield ('data: {"choices": [{"delta": {"content": '
                   '"The page says it is sunny."}}]}\n\n')
            yield 'data: [DONE]\n\n'

        streams = [first, second]

        def fake_chat(messages, model, **kwargs):
            calls.append(messages)
            return streams.pop(0)(messages, model, **kwargs)

        with patch.object(app.zen, "chat_stream", side_effect=fake_chat), \
             patch.object(app.mcp_tools_client, "chat_tool_mode", return_value="browser"), \
             patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                 {"function": {"name": "playwright__browser_navigate"}}]), \
             patch.object(app.mcp_tools_client, "call_tool", return_value="x" * 5000):
            response = self.client.post(
                "/api/voice/ask",
                json={"message": "check the weather", "model": "opencode/hy4-flash-free"},
                headers=self.headers)
        data = response.get_data(as_text=True)
        self.assertNotIn("Let me check the web", data)
        self.assertNotIn("I will look that up now", data)
        self.assertIn("The page says it is sunny.", data)
        self.assertIn("The live data check is complete", json.dumps(calls[1]))
        self.assertIn("omitted to fit model context", json.dumps(calls[1]))

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

    def test_tamil_speech_uses_neural_piper_not_english_kokoro(self):
        self.assertEqual(local_tts._espeak_voice_for_text("வணக்கம்"), "ta")
        self.assertEqual(local_tts._espeak_voice_for_text("नमस्ते"), "hi")
        self.assertIsNone(local_tts._espeak_voice_for_text("hello"))
        text = "வணக்கம், எப்படி இருக்கீங்க?"
        with patch.object(local_tts, "_synthesize_piper", return_value=b"ta-wav") as piper, \
             patch.object(local_tts, "_synthesize_espeak") as espeak, \
             patch.object(local_tts, "_kokoro_instance") as kokoro:
            audio = local_tts.synthesize(text)
        self.assertEqual(audio, b"ta-wav")
        piper.assert_called_once_with(text, "ta_IN-rasa_female-medium")
        espeak.assert_not_called()
        kokoro.assert_not_called()

    def test_tamil_piper_failure_falls_back_to_espeak(self):
        with patch.object(local_tts, "_synthesize_piper",
                          side_effect=local_tts.VoiceError("no model")), \
             patch.object(local_tts, "_synthesize_espeak", return_value=b"espeak-wav") as espeak:
            audio = local_tts.synthesize("வணக்கம்")
        self.assertEqual(audio, b"espeak-wav")
        espeak.assert_called_once_with("வணக்கம்", "ta")

    def test_hindi_speech_uses_kokoro_hindi_voice(self):
        with patch.object(local_tts, "_kokoro_instance", return_value=object()), \
             patch.object(local_tts, "_synthesize_kokoro", return_value=b"hi-wav") as kokoro, \
             patch.object(local_tts, "_synthesize_espeak") as espeak:
            audio = local_tts.synthesize("नमस्ते")
        self.assertEqual(audio, b"hi-wav")
        kokoro.assert_called_once_with("नमस्ते", "hf_alpha", speed=1.0)
        espeak.assert_not_called()

    def test_voice_error_is_actionable(self):
        with patch.object(local_stt, "transcribe", side_effect=local_stt.VoiceError("no local model")):
            response = self.client.post(
                "/api/voice/transcribe", data={"audio": (io.BytesIO(b"audio"), "a.webm")},
                headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertIn("no local model", response.json["error"])


if __name__ == "__main__":
    unittest.main()
