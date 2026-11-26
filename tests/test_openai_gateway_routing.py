"""`/v1/chat/completions` must route hosted ids to opencode.ai, not the local engine."""
import json
import unittest
from unittest.mock import Mock, patch

import app


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def iter_content(self, chunk_size=4096):
        return iter([b'{"ok":true}'])


class OpenAIGatewayRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.headers = {"Authorization": "Bearer engine-key", "Content-Type": "application/json"}

    def _post(self, model):
        with patch.object(app.llamacpp, "api_key", return_value="engine-key"), \
             patch.object(app.zen, "api_key", return_value="zen-key"), \
             patch.object(app.requests, "request", return_value=_Response()) as request:
            response = self.client.post(
                "/v1/chat/completions", json={"model": model, "messages": []}, headers=self.headers)
        return response, request.call_args

    def test_hosted_model_goes_to_opencode(self):
        response, call = self._post("opencode/beam-free")
        self.assertEqual(response.status_code, 200)
        self.assertIn("opencode.ai", call.args[1])
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer zen-key")
        self.assertEqual(json.loads(call.kwargs["data"])["model"], "beam-free")

    def test_bare_hosted_model_goes_to_opencode(self):
        with patch.object(app.llamacpp, "api_key", return_value="engine-key"), \
             patch.object(app.zen, "api_key", return_value="zen-key"), \
             patch.object(app.zen, "list_models", return_value=[{"id": "opencode/beam-free"}]), \
             patch.object(app.requests, "request", return_value=_Response()) as request:
            response = self.client.post(
                "/v1/chat/completions", json={"model": "beam-free", "messages": []},
                headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("opencode.ai", request.call_args.args[1])
        self.assertEqual(json.loads(request.call_args.kwargs["data"])["model"], "beam-free")

    def test_local_model_goes_to_llamacpp(self):
        response, call = self._post("llamacpp/model.gguf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.args[1], f"{app.llamacpp.base_url()}/v1/chat/completions")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer engine-key")

    def test_bad_key_is_rejected(self):
        with patch.object(app.llamacpp, "api_key", return_value="engine-key"):
            response = self.client.post(
                "/v1/chat/completions", json={"model": "opencode/beam-free"},
                headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
