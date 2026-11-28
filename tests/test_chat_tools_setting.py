import json
import time
import unittest
import uuid
from unittest.mock import patch

import app
from llama_web.storage import users_store


class ChatToolsSettingTests(unittest.TestCase):
    def _chattools_offered(self, message, overrides):
        user = users_store.create_user("chat-tools-setting-test", "test-password")
        client = app.app.test_client()
        captured = {}

        def fake_stream(messages, model, **kwargs):
            captured["tools"] = [t["function"]["name"] for t in kwargs.get("tools") or []]
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}) + "\n\n"
            yield "data: [DONE]\n\n"

        with client.session_transaction() as session:
            session.update(logged_in=True, user_id=user["id"], last_activity=time.time(),
                           login_csrf_token="t")
        body = {
            "messages": [{"role": "user", "content": message}],
            "model": "llamacpp/test.gguf",
            "conversation_id": uuid.uuid4().hex,
            "max_tokens": 20,
        }

        def get_setting(key, default=None):
            if key in overrides:
                return overrides[key]
            return default

        try:
            with patch.object(app.users_store, "get_user_by_id", return_value=user), \
                 patch.object(app.llamacpp, "chat_stream", side_effect=fake_stream), \
                 patch.object(app, "_recall_memory", return_value=None), \
                 patch.object(app, "_remember_exchange"), \
                 patch.object(app.web_search, "should_search_web", return_value=False), \
                 patch.object(app.llamacpp, "owns_model", return_value=True), \
                 patch.object(app.chat_jobs.registry, "get", return_value=None), \
                 patch.object(app.settings_store, "get", side_effect=get_setting):
                response = client.post("/api/chat", json=body, headers={"X-CSRF-Token": "t"})
            self.assertEqual(response.status_code, 200)
            return captured.get("tools") or []
        finally:
            users_store.delete_user(user["id"])

    def test_browser_tools_offered_when_enabled(self):
        tools = self._chattools_offered(
            "Browse https://example.org and inspect the page", {"chat_tools_enabled": "true"})
        self.assertTrue(any(name.startswith("playwright__") for name in tools), tools)

    def test_browser_tools_withheld_when_disabled(self):
        tools = self._chattools_offered(
            "Browse https://example.org and inspect the page", {"chat_tools_enabled": "false"})
        self.assertFalse(any(name.startswith("playwright__") for name in tools), tools)

    def test_python_tool_offered_when_enabled(self):
        tools = self._chattools_offered("Run Python to add 2 and 2", {"python_tools_enabled": "true"})
        self.assertIn("python_repl__execute_python", tools)

    def test_python_tool_withheld_when_disabled(self):
        tools = self._chattools_offered("Run Python to add 2 and 2", {"python_tools_enabled": "false"})
        self.assertNotIn("python_repl__execute_python", tools)

    def test_python_tool_does_not_require_web_search(self):
        # The Python REPL runs locally; disabling web search must not disable it.
        tools = self._chattools_offered("Run Python to add 2 and 2", {"web_search_enabled": "false"})
        self.assertIn("python_repl__execute_python", tools)

    def test_tool_prompt_asks_for_an_internal_route_decision(self):
        user = users_store.create_user("chat-tool-decision-" + uuid.uuid4().hex, "test-password")
        client = app.app.test_client()
        seen = {}

        def fake_stream(messages, model, **kwargs):
            seen["system"] = "\n".join(m.get("content", "") for m in messages
                                       if m.get("role") == "system")
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}) + "\n\n"
            yield "data: [DONE]\n\n"

        with client.session_transaction() as session:
            session.update(logged_in=True, user_id=user["id"], last_activity=time.time(),
                           login_csrf_token="t")
        body = {
            "messages": [{"role": "user", "content": "Browse https://example.org and inspect the page"}],
            "model": "llamacpp/test.gguf",
            "conversation_id": uuid.uuid4().hex,
            "max_tokens": 20,
        }
        try:
            with patch.object(app.users_store, "get_user_by_id", return_value=user), \
                 patch.object(app.llamacpp, "chat_stream", side_effect=fake_stream), \
                 patch.object(app, "_recall_memory", return_value=None), \
                 patch.object(app, "_remember_exchange"), \
                 patch.object(app.web_search, "should_search_web", return_value=False), \
                 patch.object(app.llamacpp, "owns_model", return_value=True), \
                 patch.object(app.chat_jobs.registry, "get", return_value=None):
                client.post("/api/chat", json=body, headers={"X-CSRF-Token": "t"})
            self.assertIn("Before calling any tool", seen["system"])
            self.assertIn("decide internally", seen["system"])
            self.assertIn("never narrate", seen["system"])
        finally:
            users_store.delete_user(user["id"])

    def test_silent_model_answer_after_tools_is_requested_again(self):
        user = users_store.create_user("chat-tool-followup-" + uuid.uuid4().hex, "test-password")
        client = app.app.test_client()
        rounds = []

        def sse(delta, finish=None):
            choice = {"delta": delta}
            if finish:
                choice["finish_reason"] = finish
            return "data: " + json.dumps({"choices": [choice]}) + "\n\n"

        def fake_stream(messages, model, **kwargs):
            rounds.append(json.dumps(messages))
            step = len(rounds)
            if step == 1:
                yield sse({"tool_calls": [{
                    "index": 0, "id": "call_1", "type": "function",
                    "function": {"name": "playwright__browser_navigate",
                                 "arguments": '{"url": "https://example.org"}'}}]}, "tool_calls")
            elif step == 2:
                # The model stops right after the tool result without saying anything.
                yield sse({}, "stop")
            else:
                yield sse({"content": "Here are the deals."}, "stop")
            yield "data: [DONE]\n\n"

        with client.session_transaction() as session:
            session.update(logged_in=True, user_id=user["id"], last_activity=time.time(),
                           login_csrf_token="t")
        body = {
            "messages": [{"role": "user", "content": "Browse https://example.org and inspect the page"}],
            "model": "llamacpp/test.gguf",
            "conversation_id": uuid.uuid4().hex,
            "max_tokens": 20,
        }
        try:
            with patch.object(app.users_store, "get_user_by_id", return_value=user), \
                 patch.object(app.llamacpp, "chat_stream", side_effect=fake_stream), \
                 patch.object(app.llamacpp, "owns_model", return_value=True), \
                 patch.object(app.mcp_tools_client, "ensure_started", return_value=[
                     {"type": "function", "function": {
                         "name": "playwright__browser_navigate", "parameters": {}}}]), \
                 patch.object(app.mcp_tools_client, "call_tool", return_value="Verified page content."), \
                 patch.object(app, "_recall_memory", return_value=None), \
                 patch.object(app, "_remember_exchange"), \
                 patch.object(app.web_search, "should_search_web", return_value=False), \
                 patch.object(app.chat_jobs.registry, "get", return_value=None):
                response = client.post("/api/chat", json=body, headers={"X-CSRF-Token": "t"})
            text = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Here are the deals.", text)
            self.assertGreaterEqual(len(rounds), 3)
            self.assertIn("You already ran the requested tools", rounds[1])
        finally:
            users_store.delete_user(user["id"])


if __name__ == "__main__":
    unittest.main()
