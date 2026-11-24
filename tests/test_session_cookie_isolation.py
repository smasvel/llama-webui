import re
import unittest

from llama_web.core.session_cookie import session_cookie_name


class SessionCookieIsolationTests(unittest.TestCase):
    def test_each_port_gets_its_own_cookie_name(self):
        """Browsers ignore ports when scoping cookies, so the name must carry the isolation."""
        self.assertEqual(session_cookie_name(5443), "llama_web_session_5443_v3")
        self.assertEqual(session_cookie_name(5444), "llama_web_session_5444_v3")
        self.assertNotEqual(session_cookie_name(5443), session_cookie_name(5444))

    def test_name_is_a_valid_stable_cookie_token(self):
        for port in (5443, 5444, 10443):
            name = session_cookie_name(port)
            self.assertRegex(name, r"^[A-Za-z0-9_]+$")
            self.assertEqual(name, session_cookie_name(port))

    def test_non_numeric_input_cannot_produce_an_empty_or_unsafe_name(self):
        self.assertEqual(session_cookie_name("5443"), "llama_web_session_5443_v3")
        self.assertRegex(session_cookie_name(""), r"^llama_web_session_\d+_v3$")
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_]+", session_cookie_name("54;43")))

    def test_two_flask_apps_with_different_ports_do_not_share_a_cookie(self):
        from flask import Flask

        def client_for(port):
            application = Flask(f"llama_web-{port}")
            application.secret_key = "test-key"
            application.config["SESSION_COOKIE_NAME"] = session_cookie_name(port)
            client = application.test_client()
            with client.session_transaction() as session:
                session["logged_in"] = True
            return client

        first = client_for(5443)
        second = client_for(5444)
        self.assertIsNotNone(first.get_cookie("llama_web_session_5443_v3"))
        self.assertIsNone(first.get_cookie("llama_web_session_5444_v3"))
        self.assertIsNotNone(second.get_cookie("llama_web_session_5444_v3"))
        self.assertIsNone(second.get_cookie("llama_web_session_5443_v3"))


class StaleSessionTests(unittest.TestCase):
    def test_session_without_current_auth_version_is_not_authenticated(self):
        import time
        from unittest.mock import patch

        import app

        client = app.app.test_client()
        with client.session_transaction() as session:
            # Shape of a session from an older build: signed correctly, old version.
            session.update(logged_in=True, user_id="old-user", username="old",
                           role="admin", login_csrf_token="old", last_activity=time.time(),
                           auth_version=2)
        with patch.object(app.users_store, "get_user_by_id",
                          return_value={"id": "old-user", "username": "old", "role": "admin"}):
            response = client.get("/api/system-stats")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
