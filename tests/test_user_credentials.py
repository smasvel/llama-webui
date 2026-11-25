"""Per-user credentials must be encrypted, isolated, and migrated from legacy storage."""
import time
import unittest

from llama_web.storage import database, secret_store, settings_store, user_credentials, users_store


class UserCredentialTests(unittest.TestCase):
    def setUp(self):
        scope = database.temporary_schema()
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)
        user_credentials.init_db()
        settings_store.init_db()
        self.alice = users_store.create_user('cred-alice', 'password123', 'admin')['id']
        self.bob = users_store.create_user('cred-bob', 'password123')['id']

    def _raw(self, user_id, kind):
        with database.connect() as conn:
            row = conn.execute(
                "SELECT encrypted_value, updated_at FROM user_credentials WHERE user_id = %s AND kind = %s",
                (user_id, kind),
            ).fetchone()
        return dict(row) if row else None

    def test_personal_caddy_tokens_are_encrypted_and_isolated(self):
        alice = user_credentials.get(self.alice, user_credentials.CADDY_TOKEN)
        bob = user_credentials.get(self.bob, user_credentials.CADDY_TOKEN)
        self.assertTrue(alice and bob)
        self.assertNotEqual(alice, bob)
        raw = self._raw(self.alice, user_credentials.CADDY_TOKEN)
        self.assertTrue(raw['encrypted_value'].startswith('v1:'))
        self.assertNotIn(alice, raw['encrypted_value'])
        self.assertIsInstance(raw['updated_at'], float)

    def test_ciphertext_is_bound_to_its_owner(self):
        raw = self._raw(self.alice, user_credentials.CADDY_TOKEN)['encrypted_value']
        with self.assertRaises(Exception):
            secret_store.decrypt_user_secret(self.bob, user_credentials.CADDY_TOKEN, raw)

    def test_opencode_key_is_per_user_and_not_global(self):
        user_credentials.set(self.alice, user_credentials.OPENCODE_KEY, 'alice-key')
        self.assertEqual(user_credentials.get(self.alice, user_credentials.OPENCODE_KEY), 'alice-key')
        self.assertIsNone(user_credentials.get(self.bob, user_credentials.OPENCODE_KEY))
        self.assertIsNone(settings_store.get('opencode_api_key'))
        raw = self._raw(self.alice, user_credentials.OPENCODE_KEY)['encrypted_value']
        self.assertNotIn('alice-key', raw)

    def test_legacy_secrets_migrate_into_user_rows(self):
        user_credentials.delete(self.alice, user_credentials.CADDY_TOKEN)
        with database.connect() as conn:
            conn.execute(
                "UPDATE users SET caddy_token = %s WHERE id = %s", ('legacy-caddy-token', self.alice))
        settings_store.set('opencode_api_key', 'legacy-zen-key')
        user_credentials.migrate_legacy_secrets()
        self.assertEqual(user_credentials.get(self.alice, user_credentials.CADDY_TOKEN),
                         'legacy-caddy-token')
        self.assertEqual(user_credentials.get(self.alice, user_credentials.OPENCODE_KEY),
                         'legacy-zen-key')
        self.assertIsNone(settings_store.get('opencode_api_key'))
        with database.connect() as conn:
            row = conn.execute("SELECT caddy_token FROM users WHERE id = %s", (self.alice,)).fetchone()
        self.assertIsNone(row['caddy_token'])


if __name__ == '__main__':
    unittest.main()
