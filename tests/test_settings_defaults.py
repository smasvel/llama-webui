import unittest

from llama_web.storage import database, settings_store


class DefaultSettingsTests(unittest.TestCase):
    def test_chat_tools_and_live_search_default_to_enabled(self):
        expected = {
            "chat_tools_enabled": "true",
            "python_tools_enabled": "true",
            "web_search_enabled": "true",
            "web_search_mode": "automatic",
            "web_search_browser_enabled": "true",
            "web_search_direct_urls": "true",
        }
        for key, value in expected.items():
            self.assertEqual(settings_store.DEFAULT_SETTINGS.get(key), value)

    def test_seed_defaults_does_not_overwrite_saved_choices(self):
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('chat_tools_enabled', 'false') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
        try:
            settings_store.init_db()
            self.assertEqual(settings_store.get("chat_tools_enabled"), "false")
        finally:
            settings_store.set("chat_tools_enabled", "true")


if __name__ == "__main__":
    unittest.main()
