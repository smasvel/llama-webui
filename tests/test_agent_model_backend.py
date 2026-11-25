import unittest
from unittest.mock import patch

import app
from llama_web.storage import model_metadata_store as store


class AgentModelBackendTests(unittest.TestCase):
    def test_gateway_provider_maps_to_owning_backend(self):
        self.assertEqual(app._agent_model_backend("llama-web", "llamacpp/foo.gguf"), "llamacpp")
        self.assertEqual(app._agent_model_backend("llama-web", "opencode/gpt-6"), "opencode")
        self.assertEqual(app._agent_model_backend("lmstudio", "qwen"), "lmstudio")
        self.assertEqual(app._agent_model_backend("opencode", "glm-5.3"), "opencode")

    def test_agent_prepare_uses_owning_backend_for_handoff(self):
        rows = store.local_models()
        if not rows:
            self.skipTest("No local GGUFs imported into the database yet.")
        target = {"model": {"providerID": "llama-web", "id": rows[0]["model_id"]}}
        captured = {}
        with patch.object(app, "_test_load_model", side_effect=lambda value: captured.update(value)):
            app._prepare_agent_model(target)
        if captured:
            self.assertEqual(captured["backend"], "llamacpp")
            self.assertEqual(captured["id"], rows[0]["model_id"])


if __name__ == "__main__":
    unittest.main()
