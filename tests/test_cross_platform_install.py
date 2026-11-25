import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import release_installer


class EngineAssetTests(unittest.TestCase):
    def test_asset_names_match_each_platform(self):
        self.assertEqual(release_installer._engine_asset("Windows", "AMD64")[0],
                         f"llama-{release_installer.ENGINE_TAG}-bin-win-cpu-x64.zip")
        self.assertEqual(release_installer._engine_asset("Windows", "ARM64")[0],
                         f"llama-{release_installer.ENGINE_TAG}-bin-win-cpu-arm64.zip")
        self.assertEqual(release_installer._engine_asset("Darwin", "arm64")[0],
                         f"llama-{release_installer.ENGINE_TAG}-bin-macos-arm64.tar.gz")
        self.assertEqual(release_installer._engine_asset("Linux", "x86_64")[0],
                         f"llama-{release_installer.PRISM_TAG}-bin-ubuntu-vulkan-x64.tar.gz")


class EngineConfigurationTests(unittest.TestCase):
    def test_configure_engine_creates_config_without_downloading_when_binary_is_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "llama-server"
            binary.write_text("engine")
            with patch.object(Path, "home", return_value=root), \
                 patch.dict(os.environ, {"LLAMA_WEB_ENGINE_BINARY": str(binary)}):
                release_installer.configure_engine("Linux", "http://127.0.0.1:18473")
            conf = root / ".config" / "llama-web" / "server.conf"
            text = conf.read_text()
            self.assertIn("LLAMA_SERVER=", text)
            self.assertIn("LLAMA_MODEL=", text)
            self.assertIn("LLAMA_ARGS=", text)
            self.assertTrue((root / ".config" / "llama-web" / "engine-key").is_file())
            self.assertTrue((root / ".local" / "share" / "llama-web" / "models").is_dir())


class StartScriptTests(unittest.TestCase):
    def test_installer_start_scripts_exist(self):
        root = Path(release_installer.ROOT)
        self.assertTrue((root / "deploy" / "start-llama-web.sh").is_file())
        self.assertTrue((root / "deploy" / "start-llama-web.ps1").is_file())
        self.assertTrue((root / "deploy" / "install-prism-engine.sh").is_file())


if __name__ == "__main__":
    unittest.main()
