"""Chat/MCP scratch data must stay in the git-ignored chat-temp folder."""
import unittest

from llama_web.integrations import mcp_tools_client as mcp


class ChatTempDirTests(unittest.TestCase):
    def test_all_mcp_servers_run_inside_chat_temp(self):
        self.assertEqual(mcp.CHAT_TEMP_DIR.name, "chat-temp")
        for name, server in mcp._SERVERS.items():
            with self.subTest(server=name):
                self.assertEqual(str(server.cwd), str(mcp.CHAT_TEMP_DIR))

    def test_chat_temp_is_ignored_by_git(self):
        from pathlib import Path
        root = Path(mcp.__file__).resolve().parents[2]
        ignore = (root / ".gitignore").read_text().splitlines()
        self.assertIn("/chat-temp/", ignore)


if __name__ == "__main__":
    unittest.main()
