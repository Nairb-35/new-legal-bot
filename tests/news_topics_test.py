import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# The production workflow installs these integrations. Unit tests exercise only
# topic routing, so lightweight modules keep the test independent of the runner.
sys.modules.setdefault("feedparser", types.SimpleNamespace(parse=lambda _url: None))
requests_module = types.ModuleType("requests")
requests_module.post = lambda *_args, **_kwargs: None
requests_module.get = lambda *_args, **_kwargs: None
sys.modules.setdefault("requests", requests_module)
translator_module = types.ModuleType("deep_translator")
translator_module.GoogleTranslator = object
sys.modules.setdefault("deep_translator", translator_module)

import bilingual_news as bot


class NewsTopicRoutingTests(unittest.TestCase):
    def test_local_coverage_uses_multiple_bilingual_feeds(self):
        self.assertGreaterEqual(len(bot.LOCAL_FEED_URLS), 3)
        combined = " ".join(bot.LOCAL_FEED_URLS).lower()
        self.assertIn("bernama.com", combined)
        self.assertIn("malaymail.com", combined)
        self.assertIn("mahkamah", combined)

    def test_expanded_legal_terms_are_kept(self):
        examples = [
            "Federal Court allows appeal in constitutional lawsuit",
            "SPRM siasatan rasuah diteruskan",
            "Coroner opens inquest after custody death",
            "Regulator gazettes new consumer rights rules",
        ]
        for title in examples:
            with self.subTest(title=title):
                self.assertTrue(bot.is_genuinely_legal_or_political(title, ""))

    def test_missing_config_falls_back_to_general(self):
        with mock.patch.object(bot, "NEWS_CONFIG_FILE", os.path.join(tempfile.gettempdir(), "missing-newscfg.json")):
            self.assertIsNone(bot.news_thread_id("local"))

    def test_saved_topic_is_used(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            json.dump({"local_thread_id": 101, "international_thread_id": 202}, fh)
            path = fh.name
        try:
            with mock.patch.object(bot, "NEWS_CONFIG_FILE", path):
                self.assertEqual(bot.news_thread_id("local"), 101)
                self.assertEqual(bot.news_thread_id("international"), 202)
        finally:
            os.unlink(path)

    def test_send_message_includes_selected_thread(self):
        response = mock.Mock(status_code=200)
        with mock.patch.object(bot, "news_thread_id", return_value=202), mock.patch.object(bot, "tg", return_value=response) as send:
            bot.send_news_message("World court ruling", "Keputusan mahkamah dunia", "Today", "***", "https://notion.so/page", "https://example.com", section="international")
        self.assertEqual(send.call_args.args[1]["message_thread_id"], 202)


if __name__ == "__main__":
    unittest.main()
