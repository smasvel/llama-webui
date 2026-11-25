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


if __name__ == "__main__":
    unittest.main()
