import unittest
from unittest.mock import patch

from llama_web.backends import llamacpp_client
from llama_web.storage import model_metadata_store as store


class LocalModelMetadataTests(unittest.TestCase):
    def test_db_rows_are_the_source_of_list_models(self):
        rows = store.local_models()
        if not rows:
            self.skipTest("No local GGUFs have been imported into the database yet.")
        with patch.object(llamacpp_client, "library_entries", side_effect=AssertionError("file scan at runtime")), \
             patch.object(llamacpp_client, "gguf_max_context", side_effect=AssertionError("GGUF read at runtime")), \
             patch.object(llamacpp_client, "served_model", return_value=None):
            models = llamacpp_client.list_models()
        self.assertEqual(len(models), len(rows))
        by_id = {model["id"]: model for model in models}
        for row in rows[:3]:
            model = by_id[row["model_id"]]
            self.assertEqual(model["name"], row["name"])
            self.assertEqual(model["max_context_length"], row["max_context"])
            self.assertEqual(model["size_gb"], round(row["size_bytes"] / 1e9, 2))

    def test_model_path_uses_db_path(self):
        rows = store.local_models()
        if not rows:
            self.skipTest("No local GGUFs have been imported into the database yet.")
        row = rows[0]
        self.assertEqual(llamacpp_client.model_path(row["model_id"]), __import__("pathlib").Path(row["path"]))

    def test_agent_inventory_is_json_object_from_db_backed_data(self):
        from llama_web.core import help_data
        config = help_data.opencode_config_object()
        provider = config["provider"]["llama-web"]
        names = {entry["name"] for entry in provider["models"].values()}
        for row in store.local_models()[:3]:
            self.assertTrue(any(row["name"] in name for name in names), row["name"])


if __name__ == "__main__":
    unittest.main()
