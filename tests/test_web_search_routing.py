import unittest
from unittest.mock import patch

import app
from llama_web.integrations import page_scraper, web_search_client as web_search


class WebSearchRoutingTests(unittest.TestCase):
    def test_weather_always_triggers_live_lookup(self):
        self.assertTrue(web_search.should_search_web("What will the weather be in Boston tomorrow?"))

    def test_weather_location_does_not_treat_contraction_as_city(self):
        self.assertIsNone(web_search.extract_weather_location("How's the weather?"))
        self.assertIsNone(web_search.extract_weather_location("How’s weather?"))

    def test_news_always_triggers_live_lookup(self):
        self.assertTrue(web_search.should_search_web("Give me the news about semiconductor exports"))

    def test_local_event_question_uses_web_search_not_news(self):
        query = "What's happening in Zinc House Durham this weekend"
        self.assertTrue(web_search.looks_like_event_query(query))
        self.assertFalse(web_search.looks_like_news_query(query))
        self.assertEqual(web_search.event_search_query(query), "Zinc House Durham events")
        self.assertEqual(web_search.event_search_query("What's on at Zinc House this weekend"),
                         "Zinc House events")

    def test_event_site_candidates_include_likely_official_domain(self):
        candidates = web_search.event_site_candidates(
            "What's happening in Zinc House Durham this weekend")
        self.assertIn("https://zinchouse.com/events/", candidates)

    def test_event_result_ranking_prefers_official_calendar(self):
        query = "What's happening in Zinc House Durham this weekend"
        official = {"title": "Events for November 2026 – Zinc House Winery & Brewery",
                    "href": "https://www.zinchouse.com/events/", "body": "Calendar"}
        social = {"title": "Zinc House Durham Wedding | TikTok",
                  "href": "https://www.tiktok.com/discover/zinc-house-durham-wedding",
                  "body": "wedding"}
        self.assertGreater(web_search.event_result_score(official, query),
                           web_search.event_result_score(social, query))

    def test_event_context_fetches_the_calendar_even_for_voice_snippets(self):
        query = "What's happening in Zinc House Durham this weekend"
        result = {"title": "Events", "href": "https://example.org/events", "body": "stub"}
        fetched = {"title": "Events", "href": "https://example.org/events",
                   "full_text": "Sat Nov 28 Food Trucks 5pm"}
        with patch.object(app.settings_store, "get", return_value=None), \
             patch.object(web_search, "looks_like_event_query", return_value=True), \
             patch.object(web_search, "event_search_query", return_value="Zinc House Durham events"), \
             patch.object(web_search, "search", return_value=[result]) as search, \
             patch.object(web_search, "fetch_event_urls", return_value=[fetched]) as fetch:
            note, _ = app._web_search_context(query, snippets_only=True)
        search.assert_called_once_with("Zinc House Durham events", max_results=10)
        fetch.assert_called_once()
        self.assertIn("Sat Nov 28 Food Trucks 5pm", note)

    def test_online_topic_research_triggers_live_lookup(self):
        self.assertTrue(web_search.should_search_web("Research solid-state battery progress online"))
        self.assertTrue(web_search.is_explicit_search_request("Research solid-state battery progress online"))

    def test_playwright_method_reuses_topic_and_preserves_actual_playwright_queries(self):
        prior = "Find the James Webb Space Telescope mission launch date"
        followup = "Search online using Playwright"
        self.assertEqual(web_search.contextualize_search_query(followup, [
            {"role": "user", "content": prior},
            {"role": "assistant", "content": "Which sources should I check?"},
            {"role": "user", "content": followup},
        ]), prior)
        self.assertEqual(web_search.contextualize_search_query(followup, []), "")
        self.assertEqual(web_search.contextualize_search_query("Invoke Playwright to find Webb's launch date", []),
                         "find Webb's launch date")
        self.assertEqual(web_search.contextualize_search_query("Research Playwright test fixtures online", []),
                         "Research Playwright test fixtures online")

    def test_event_fetch_uses_http_before_browser(self):
        with patch.object(web_search, "_fetch_text_http", return_value="Saturday 5pm"), \
             patch.object(web_search, "fetch_urls_directly") as browser:
            rows = web_search.fetch_event_urls(["https://example.org/events/"])
        self.assertEqual(rows[0]["full_text"], "Saturday 5pm")
        browser.assert_not_called()

    def test_direct_page_fetch_falls_back_to_http_when_browser_text_is_empty(self):
        from unittest.mock import Mock
        response = Mock(status_code=200, text="<html><body><h1>Events</h1><p>Saturday 5pm</p></body></html>")
        with patch.object(page_scraper, "available", return_value=False), \
             patch.object(web_search.requests, "get", return_value=response):
            rows = web_search.fetch_urls_directly(["https://example.org/events/"])
        self.assertIn("Events Saturday 5pm", rows[0]["full_text"])

    def test_playwright_reports_launch_failure_without_claiming_pages_were_read(self):
        progress = []
        token = page_scraper.playwright_progress.set(progress.append)
        try:
            with (
                patch.object(page_scraper, "_PLAYWRIGHT_AVAILABLE", True),
                patch.object(page_scraper, "sync_playwright", side_effect=RuntimeError("test launch failure")),
            ):
                self.assertEqual(page_scraper.fetch_pages(["https://example.org/"]), {})
        finally:
            page_scraper.playwright_progress.reset(token)
        self.assertEqual(progress, ["Starting Playwright…", "Playwright could not start."])

    def test_social_greetings_do_not_trigger_live_lookup(self):
        for message in (
            "How are you?", "how are you today", "How's it going?", "Hello there",
            "Good morning", "Are you okay?", "hi there", "What's up?",
        ):
            with self.subTest(message=message):
                self.assertFalse(web_search.should_search_web(message))

    def test_social_messages_still_search_when_user_asks_for_lookup(self):
        self.assertTrue(web_search.should_search_web("How are you feeling about batter today? search for current scores online"))

    def test_greeting_plus_topical_follow_up_still_triggers_lookup(self):
        self.assertTrue(web_search.should_search_web("How are you? and what is the latest version of Firefox?"))

    def test_greeting_follow_up_without_topic_is_not_searched(self):
        self.assertFalse(web_search.should_search_web("How are you? please tell me more about that."))

    def test_news_context_prefers_playwright_article_text(self):
        note = web_search.format_news_for_context("chip news", [{
            "title": "A headline",
            "url": "https://example.com/story",
            "date": "2026-09-04T10:00:00Z",
            "body": "short snippet",
            "full_text": "Detailed article text loaded in Playwright.",
        }])
        self.assertIn("Detailed article text loaded in Playwright.", note)
        self.assertNotIn("short snippet", note)


if __name__ == "__main__":
    unittest.main()


class WeatherHorizonTests(unittest.TestCase):
    def test_forecast_horizon_is_not_treated_as_a_location(self):
        from llama_web.integrations import web_search_client as weather
        self.assertIsNone(weather.extract_weather_location("10-day forecast"))
        self.assertEqual(weather.extract_weather_location("Forecast for the next 10 days in Durham, NC"),
                         "Durham NC")
        self.assertEqual(weather.weather_forecast_days("10-day forecast"), 10)
        self.assertEqual(weather.weather_forecast_days("30-day forecast"), 16)
