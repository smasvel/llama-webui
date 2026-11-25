"""Encrypted per-user credentials (opencode.ai key, personal Caddy token).

Values live only in the ``user_credentials`` table and are encrypted with an
AES-GCM key derived from the database secret, with the user id and credential
kind bound into the AAD so a row cannot be moved between accounts. Nothing is
written to files, and the plaintext is never logged.
"""
from __future__ import annotations

import secrets
import time

from llama_web.storage import database, secret_store

OPENCODE_KEY = 'opencode_api_key'
CADDY_TOKEN = 'caddy_token'


def init_db() -> None:
    with database.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_credentials (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (user_id, kind)
            )
        """)


def get(user_id: str, kind: str) -> str | None:
    if not user_id:
        return None
    with database.connect() as conn:
        row = conn.execute(
            "SELECT encrypted_value FROM user_credentials WHERE user_id = %s AND kind = %s",
            (user_id, kind),
        ).fetchone()
    if row is None:
        return None
    try:
        return secret_store.decrypt_user_secret(user_id, kind, row['encrypted_value'])
    except (ValueError, UnicodeDecodeError):
        return None


def set(user_id: str, kind: str, value: str | None) -> None:
    """Store an encrypted credential, or delete it when value is empty."""
    if not value:
        delete(user_id, kind)
        return
    encrypted = secret_store.encrypt_user_secret(user_id, kind, value)
    with database.connect() as conn:
        conn.execute(
            """INSERT INTO user_credentials (user_id, kind, encrypted_value, updated_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, kind) DO UPDATE SET
                 encrypted_value = EXCLUDED.encrypted_value,
                 updated_at = EXCLUDED.updated_at""",
            (user_id, kind, encrypted, time.time()),
        )


def ensure_caddy_token(user_id: str) -> str:
    """Every account gets one personal, revocable LAN token."""
    token = get(user_id, CADDY_TOKEN)
    if not token:
        token = secrets.token_hex(32)
        set(user_id, CADDY_TOKEN, token)
    return token


def delete(user_id: str, kind: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "DELETE FROM user_credentials WHERE user_id = %s AND kind = %s", (user_id, kind))


def migrate_legacy_secrets() -> None:
    """One-time move of legacy plaintext/global credentials into per-user rows."""
    from llama_web.storage import settings_store, users_store

    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id, username, role, caddy_token FROM users"
        ).fetchall()
    for row in rows:
        legacy_token = row['caddy_token']
        if not get(row['id'], CADDY_TOKEN):
            set(row['id'], CADDY_TOKEN, legacy_token or secrets.token_hex(32))
        if legacy_token:
            with database.connect() as conn:
                conn.execute("UPDATE users SET caddy_token = NULL WHERE id = %s", (row['id'],))

    legacy_key = settings_store.get('opencode_api_key')
    if legacy_key:
        for row in rows:
            if row['role'] == 'admin' and not get(row['id'], OPENCODE_KEY):
                set(row['id'], OPENCODE_KEY, legacy_key)
    # The global row must not survive even if its ciphertext is unreadable:
    # it can no longer be used and would keep raising on every settings read.
    settings_store.set('opencode_api_key', None)
