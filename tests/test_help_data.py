import json
import unittest
from unittest.mock import Mock, patch

from llama_web.core import help_data


class CertificateHelpTests(unittest.TestCase):
    @patch.object(help_data.requests, "get", side_effect=help_data.requests.RequestException("admin off"))
    def test_certificate_falls_back_to_served_pem(self, _get):
        pem = help_data.ca_certificate()
        self.assertIn("-----BEGIN CERTIFICATE-----", pem)
        self.assertFalse(help_data.certificate_needs_trust())


class TailscaleHelpTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(help_data.zen, "api_key", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)


    @patch.object(help_data.model_metadata_store, "get_model_configured_context", return_value=131072)
    def test_loaded_context_wins_over_saved_maximum(self, saved):
        model = {"backend": "llamacpp", "id": "Muse", "state": "loaded",
                 "loaded_context_length": 12495, "configured_context_length": 131072}
        self.assertEqual(help_data._configured_or_loaded_context(model), 12495)
        saved.assert_not_called()
        model["state"] = "not-loaded"
        self.assertEqual(help_data._configured_or_loaded_context(model), 131072)

    @patch.object(help_data.settings_store, "get", side_effect=lambda key:
                  "true" if key == "opencode_compaction_auto" else None)
    def test_auto_compaction_defaults_on_and_can_be_disabled(self, _get):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["Muse"]}, {}))
        self.assertTrue(cfg["compaction"]["auto"])

    @patch.object(help_data.settings_store, "get", side_effect=lambda key:
                  "false" if key == "opencode_compaction_auto" else None)
    def test_auto_compaction_can_be_disabled_explicitly(self, _get):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["Muse"]}, {}))
        self.assertFalse(cfg["compaction"]["auto"])

    def test_help_context_uses_loaded_size_instead_of_supported_maximum(self):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["large", "small"]}, {},
            ctx_by_model={("llamacpp", "large"): 8192, ("llamacpp", "small"): 8192},
            ))
        entries = cfg["provider"]["llamacpp"]["models"]
        self.assertEqual(entries["large"]["limit"]["context"], 8192)
        self.assertEqual(entries["small"]["limit"]["context"], 8192)

    @patch.object(help_data.settings_store, "get", side_effect=lambda key: None)
    def test_compaction_budget_uses_loaded_context_for_any_model(self, _get):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["arbitrary-large-context-model"]}, {},
            ctx_by_model={("llamacpp", "arbitrary-large-context-model"): 32768}))
        entry = cfg["provider"]["llamacpp"]["models"]["arbitrary-large-context-model"]
        self.assertEqual(entry["limit"]["context"], 32768)
        self.assertEqual(cfg["compaction"], {
            "auto": True, "keep": {"tokens": 29526}, "buffer": "8.33%"})

    @patch.object(help_data.settings_store, "get", return_value=None)
    def test_compaction_budget_uses_selected_model_loaded_context(self, _get):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["large-model", "small-model", "tiny-model"]},
            {"llamacpp": ["large-model"]},
            ctx_by_model={("llamacpp", "large-model"): 65536,
                          ("llamacpp", "small-model"): 4096,
                          ("llamacpp", "tiny-model"): 2048}))
        self.assertEqual(cfg["model"], "llamacpp/large-model")
        self.assertEqual(cfg["compaction"], {
            "auto": True, "keep": {"tokens": 59051}, "buffer": "8.33%"})

    @patch.object(help_data, "_opencode_inventory", return_value=[{
        "id": "opencode/hy4-flash-free", "name": "Hy4 flash Free",
        "max_context_length": 1048576, "output_tokens_limit": 65536}])
    @patch.object(help_data.settings_store, "get", return_value=None)
    def test_compaction_scales_to_a_larger_hosted_model_window(self, _get, _inventory):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["local-65k"]}, {},
            ctx_by_model={("llamacpp", "local-65k"): 65536}))
        hosted = cfg["provider"]["opencode"]["models"]["hy4-flash-free"]["limit"]
        self.assertEqual(hosted, {"context": 1048576, "output": 65536})
        # Retained history is sized from the online model's max window, not the
        # local 65K default, but stops at half the window to leave room for new work.
        self.assertEqual(cfg["compaction"]["keep"]["tokens"], hosted["context"] // 2)
        buffer = cfg["compaction"]["buffer"]
        # A quarter of the online window is reserved so the compaction summary
        # request fits: a smaller share left no room to summarize ~958K tokens.
        self.assertEqual(buffer, "25%")
        reserved = hosted["context"] * float(buffer.rstrip("%")) / 100
        self.assertEqual(reserved, hosted["context"] // 4)
        self.assertLess(hosted["context"] - reserved,
                        hosted["context"] - hosted["output"])

    @patch.object(help_data.settings_store, "get", return_value=None)
    def test_small_lemonade_window_bounds_compaction_without_flm(self, _get):
        for model in ('Muse-Glimmer-30B-GGUF', 'GLM-4.7-Flash-GGUF'):
            with self.subTest(model=model):
                cfg = json.loads(help_data.build_opencode_config(
                    '203.0.113.10', 'test-key', help_data._DEFAULT_PORTS,
                    {'llamacpp': [model]}, {}, network_label='Tailscale',
                    ctx_by_model={('llamacpp', model): 4096}))
                self.assertEqual(cfg['provider']['llamacpp']['models'][model]['limit'],
                                 {'context': 4096, 'output': 2048})
                self.assertEqual(cfg['compaction'],
                                 {'auto': True, 'keep': {'tokens': 3691}, 'buffer': '8.33%'})

    @patch.object(help_data.settings_store, "get", side_effect=lambda key:
                  '32768' if key == 'opencode_compaction_buffer' else None)
    def test_mixed_backend_compaction_uses_selected_default_model_window(self, _get):
        cfg = json.loads(help_data.build_opencode_config(
            '203.0.113.10', 'test-key', help_data._DEFAULT_PORTS,
            {'llamacpp': ['Muse', 'small']}, {},
            ctx_by_model={('llamacpp', 'Muse'): 65536, ('llamacpp', 'small'): 4096}))
        self.assertEqual(cfg['compaction'],
                         {'auto': True, 'keep': {'tokens': 59051}, 'buffer': '25%'})

    def test_continue_uses_saved_context_over_backend_default(self):
        result = help_data.build_continue_yaml(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["Muse-Glimmer-30B-GGUF"]}, [], network_label="Tailscale",
            ctx_by_model={("llamacpp", "Muse-Glimmer-30B-GGUF"): 131072})
        self.assertIn("contextLength: 131072", result)
        self.assertNotIn("contextLength: 65536", result)

    @patch.object(help_data.subprocess, "run")
    def test_hardcoded_default_port_does_not_select_another_app(self, run):
        domain = "machine.example.ts.net"
        run.return_value = Mock(returncode=0, stdout=json.dumps({
            "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}, "10000": {"HTTPS": True}},
            "Web": {
                domain + ":443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5050"}}},
                domain + ":10000": {"Handlers": {"/": {"Proxy": "https+insecure://127.0.0.1:5000"}}},
                domain + ":8443": {"Handlers": {"/": {"Proxy": "https+insecure://127.0.0.1:5443"}}},
            },
        }))
        self.assertEqual(help_data.tailscale_app_url(domain, 5443), "https://" + domain + ":8443")

    @patch.object(help_data.subprocess, "run", return_value=Mock(returncode=1, stdout=""))
    def test_no_dns_link_is_invented_when_serve_is_unavailable(self, _run):
        self.assertIsNone(help_data.tailscale_app_url("machine.example.ts.net", 5443))


class HelpDataCacheTests(unittest.TestCase):
    def setUp(self):
        help_data._help_cache.clear()
        self.addCallback = self.addCleanup(help_data._help_cache.clear)

    @patch.object(help_data, "_build_uncached")
    def test_fresh_cache_returned_without_rebuild(self, build_mock):
        build_mock.return_value = {"lan_ip": "192.0.2.1"}
        first = help_data.build()
        second = help_data.build()
        self.assertEqual(build_mock.call_count, 1)
        self.assertIs(first, second)

    @patch.object(help_data, "_build_uncached")
    @patch.object(help_data.threading, "Thread")
    def test_stale_data_returned_while_background_refresh_runs(self, thread_mock, build_mock):
        cached = {"lan_ip": "192.0.2.1"}
        help_data._help_cache["data"] = cached
        help_data._help_cache["ts"] = __import__("time").monotonic() - help_data.HELP_DATA_TTL - 1
        result = help_data.build()
        self.assertIs(result, cached)  # served instantly, never blocking on a rebuild
        self.assertTrue(help_data._help_cache.get("refreshing"))
        self.assertTrue(thread_mock.return_value.start.called)

    @patch.object(help_data, "_build_uncached")
    def test_rebuild_failure_leaves_previous_snapshot_served(self, build_mock):
        cached = {"lan_ip": "192.0.2.1"}
        help_data._help_cache["data"] = cached
        help_data._help_cache["ts"] = __import__("time").monotonic() - help_data.HELP_DATA_TTL - 1
        build_mock.side_effect = RuntimeError("tailscake down")
        help_data._refresh(5443)  # _build_uncached raises
        self.assertFalse(help_data._help_cache.get("refreshing"))
        self.assertEqual(help_data._help_cache.get("data"), cached)


class OpenCodeConsoleConfigTests(unittest.TestCase):
    @patch.object(help_data.settings_store, "get", return_value=None)
    def test_generated_config_uses_the_current_gateway_port(self, _setting):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["local-model"]}, {}))
        self.assertEqual(cfg["provider"]["llamacpp"]["options"]["baseURL"],
                         "https://203.0.113.10:18471/v1")

    @patch.object(help_data.settings_store, "get", return_value=None)
    @patch.object(help_data.zen, "api_key", return_value="")
    def test_console_providers_absent_without_a_key(self, _key, _setting):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": ["Muse"]}, {},
            ctx_by_model={("llamacpp", "Muse"): 65536}))
        self.assertNotIn("opencode", cfg["provider"])

    @patch.object(help_data.settings_store, "get", return_value=None)
    @patch.object(help_data.zen, "list_models", return_value=[
        {"id": "opencode/glm-5.3", "name": "GLM 5.3",
         "configured_context_length": 200000, "output_tokens_limit": 32768}])
    @patch.object(help_data.zen, "api_key", return_value="test-key")
    def test_console_provider_uses_key_placeholder_windows_and_output(self, _key, _models, _setting):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS,
            {"llamacpp": []}, {}))
        provider = cfg["provider"]["opencode"]
        self.assertEqual(provider["options"]["baseURL"],
                         "https://opencode.ai/inference/openai/v1")
        self.assertEqual(provider["options"]["apiKey"], "<your opencode.ai API key>")
        self.assertEqual(provider["models"]["glm-5.3"]["limit"],
                         {"context": 200000, "output": 32768})

    @patch.object(help_data.settings_store, "get", return_value=None)
    @patch.object(help_data.zen, "list_models",
                  return_value=[{"id": "opencode/x", "configured_context_length": None}])
    @patch.object(help_data.zen, "api_key", return_value="test-key")
    def test_console_unknown_context_falls_back_to_safe_floor(self, _key, _models, _setting):
        cfg = json.loads(help_data.build_opencode_config(
            "203.0.113.10", "test-key", help_data._DEFAULT_PORTS, {}, {}))
        entry = cfg["provider"]["opencode"]["models"]["x"]
        self.assertEqual(entry["limit"]["context"], 65536)
        self.assertLessEqual(entry["limit"]["output"], 65536 // 4)


class GeneratedOpenCodeConfigTests(unittest.TestCase):
    @patch.object(help_data, "_all_models", return_value=[
        {"id": "llamacpp/qwen.gguf", "backend": "llamacpp", "type": "llm",
         "state": "loaded", "loaded_context_length": 16384},
    ])
    def test_config_contains_bearer_key_and_base_url(self, _models):
        text = help_data.build_opencode_jsonc("192.0.2.10", 18474, "test-key", "local")
        self.assertIn("https://192.0.2.10:18474/v1", text)
        self.assertIn('"apiKey": "test-key"', text)
        self.assertIn('"X-llama_web-Client": "opencode"', text)
        self.assertIn("llamacpp/qwen.gguf", text)

    @patch.object(help_data, "_all_models", return_value=[])
    def test_tailscale_config_uses_tailscale_label(self, _models):
        text = help_data.build_opencode_jsonc("remote.example", 18474, "key", "Tailscale")
        self.assertIn("remote.example", text)
        self.assertIn("Tailscale", text)

    @patch.object(help_data.settings_store, "get", return_value=None)
    @patch.object(help_data, "_all_models", return_value=[
        {"id": "llamacpp/qwen.gguf", "backend": "llamacpp", "type": "llm",
         "state": "loaded", "loaded_context_length": 65536},
        {"id": "opencode/hy4-flash-free", "backend": "opencode", "type": "llm",
         "max_context_length": 1048576, "output_tokens_limit": 65536},
    ])
    def test_generated_config_uses_online_compaction_budgets(self, _models, _settings):
        text = help_data.build_opencode_jsonc("192.0.2.10", 18474, "test-key")
        config = json.loads(help_data.strip_jsonc(text))
        self.assertEqual(config["compaction"], {
            "auto": True, "keep": {"tokens": 524288}, "buffer": "25%"})
        self.assertEqual(
            config["provider"]["llama-web"]["models"]["hy4-flash-free"]["limit"]["context"],
            1048576)


if __name__ == "__main__":
    unittest.main()
