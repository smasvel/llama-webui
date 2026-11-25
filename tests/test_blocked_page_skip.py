import unittest
from unittest.mock import patch

from llama_web.integrations import page_scraper, web_search_client as web


class BlockedPageSkipTests(unittest.TestCase):
    def test_looks_blocked_detects_refusals_without_bypass(self):
        self.assertTrue(page_scraper.looks_blocked(403, "", ""))
        self.assertTrue(page_scraper.looks_blocked(200, "Access Denied", ""))
        self.assertTrue(page_scraper.looks_blocked(200, "", "Please verify you are human"))
        self.assertFalse(page_scraper.looks_blocked(200, "Example", "Ordinary article text"))

    def test_blocked_url_is_remembered_and_not_retried(self):
        url = "https://blocked.example/page"
        page_scraper.mark_blocked(url)
        try:
            self.assertTrue(page_scraper.is_blocked(url))
            with patch.object(page_scraper, "sync_playwright", side_effect=AssertionError("browser launched for blocked URL")):
                self.assertEqual(page_scraper.fetch_pages([url]), {})
        finally:
            page_scraper._BLOCKED_URLS.pop(url, None)

    def test_refused_page_is_skipped_and_next_result_still_fetched(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        blocked = "https://denied.example/a"
        good = "https://ok.example/b"
        counter = {"n": 0}

        def make_page():
            url = blocked if counter["n"] == 0 else good
            counter["n"] += 1
            page = MagicMock()
            page.goto.return_value = SimpleNamespace(status=403 if url == blocked else 200)
            page.url = url
            page.title.return_value = "Denied" if url == blocked else "Article"
            page.evaluate.return_value = "blocked" if url == blocked else "Article text"
            page.content.return_value = "<html></html>"
            return page

        playwright = MagicMock()
        playwright.__enter__.return_value = playwright
        playwright.chromium.launch.return_value.new_context.return_value.new_page.side_effect = make_page
        playwright.chromium.launch.return_value.new_context.return_value.route = MagicMock()
        with patch.object(page_scraper, "sync_playwright", return_value=playwright), \
             patch.object(page_scraper.browse_history_store, "add_visit"):
            try:
                result = page_scraper.fetch_pages([blocked, good])
                self.assertTrue(page_scraper.is_blocked(blocked))
                self.assertIn(good, result)
                self.assertNotIn(blocked, result)
            finally:
                page_scraper._BLOCKED_URLS.pop(blocked, None)

    def test_navigation_filter_drops_blocked_results(self):
        results = [
            {"href": "https://blocked.example/a", "title": "news topic", "body": "news topic details"},
            {"href": "https://ok.example/b", "title": "news topic", "body": "news topic details"},
        ]
        page_scraper.mark_blocked(results[0]["href"])
        try:
            kept = web.filter_navigation_results("news topic", results, limit=2)
            self.assertEqual([r["href"] for r in kept], ["https://ok.example/b"])
        finally:
            page_scraper._BLOCKED_URLS.pop(results[0]["href"], None)

    def test_format_context_skips_blocked_results(self):
        results = [
            {"href": "https://blocked.example/a", "title": "A", "body": "secret", "blocked": True},
            {"href": "https://ok.example/b", "title": "B", "body": "usable"},
        ]
        note = web.format_for_context("topic", results)
        self.assertIn("usable", note)
        self.assertNotIn("secret", note)


if __name__ == "__main__":
    unittest.main()
