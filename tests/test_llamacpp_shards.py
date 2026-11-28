"""Split GGUF sets must be complete before they can be loaded."""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app
from llama_web.backends import llamacpp_client as llamacpp


class SplitShardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def shard(self, index, count=3):
        path = self.directory / f"model-{index:05d}-of-{count:05d}.gguf"
        path.write_bytes(b"gguf")
        return path

    def test_missing_shards_are_not_loadable(self):
        first = self.shard(1)
        loadable, hint = llamacpp._split_shard_status(first)
        self.assertFalse(loadable)
        self.assertIn("2 of 3 shard files are missing", hint)

    def test_complete_set_is_loadable(self):
        first = self.shard(1)
        self.shard(2)
        self.shard(3)
        self.assertEqual(llamacpp._split_shard_status(first), (True, ""))

    def test_single_file_gguf_is_unaffected(self):
        single = self.directory / "single.gguf"
        single.write_bytes(b"gguf")
        self.assertEqual(llamacpp._split_shard_status(single), (True, ""))

    def test_load_route_explains_an_incomplete_split_model(self):
        client = app.app.test_client()
        user = {"id": "shard-probe", "username": "shard-probe", "role": "admin"}
        with client.session_transaction() as session:
            session.update(logged_in=True, user_id=user["id"], username=user["username"],
                           role="admin", last_activity=time.time(), login_csrf_token="t")
        with patch.object(app.users_store, "get_user_by_id", return_value=user), \
             patch.object(llamacpp, "owns_model", return_value=True), \
             patch.object(llamacpp, "list_models", return_value=[{
                 "id": "llamacpp/model-00001-of-00003.gguf",
                 "loadable": False,
                 "management_hint": "Incomplete split GGUF: 2 of 3 shard files are missing.",
             }]), \
             patch.object(llamacpp, "load_model") as load:
            response = client.post(
                "/api/models/load",
                json={"model": "llamacpp/model-00001-of-00003.gguf"},
                headers={"X-CSRF-Token": "t"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Incomplete split GGUF", response.get_json()["error"])
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
