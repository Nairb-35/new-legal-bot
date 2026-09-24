"""Network-free pipeline checks exercising real geography and duplicate policy."""

import io
import sys
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from unittest import mock


sys.modules.setdefault("feedparser", types.SimpleNamespace(parse=lambda _url: None))
requests_module = types.ModuleType("requests")
requests_module.post = lambda *_args, **_kwargs: None
requests_module.get = lambda *_args, **_kwargs: None
sys.modules.setdefault("requests", requests_module)
translator_module = types.ModuleType("deep_translator")
translator_module.GoogleTranslator = object
sys.modules.setdefault("deep_translator", translator_module)

import bilingual_news as bot


def article(title, link, summary=""):
    return types.SimpleNamespace(title=title, link=link, summary=summary,
                                 published_parsed=datetime.now(timezone.utc).utctimetuple())


class NewsIntegrityTests(unittest.TestCase):
    def run_pipeline(self, feeds, history=None, history_error=None, lookup_response=None):
        """Mock integrations only; classify_geography/is_duplicate stay real."""
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(bot, "NOTION_TOKEN", "unit-test-token"))
            stack.enter_context(mock.patch.object(bot, "load_news_topics", return_value={
                "local_thread_id": 101, "international_thread_id": 202,
            }))
            history_load = stack.enter_context(mock.patch.object(
                bot, "load_recent_news_history", return_value=list(history or []), side_effect=history_error))
            fetch = stack.enter_context(mock.patch.object(
                bot, "fetch_news_feed", side_effect=lambda url: types.SimpleNamespace(entries=feeds.get(url, []))))
            if lookup_response is None:
                lookup = stack.enter_context(mock.patch.object(bot, "already_in_notion", return_value=False))
                stack.enter_context(mock.patch.object(bot.requests, "post", side_effect=AssertionError("Unexpected network call")))
            else:
                lookup = stack.enter_context(mock.patch.object(bot.requests, "post", return_value=lookup_response))
            translation = stack.enter_context(mock.patch.object(bot, "bilingual_titles", side_effect=lambda title: (title, title)))
            analyse = stack.enter_context(mock.patch.object(bot, "ai_lens", return_value={}))
            archive = stack.enter_context(mock.patch.object(bot, "push_to_notion", return_value="https://notion.so/source"))
            stack.enter_context(mock.patch.object(bot, "push_parliament_to_notion", side_effect=AssertionError("Unexpected Parliament route")))
            video = stack.enter_context(mock.patch.object(bot, "push_video_to_notion", return_value="https://notion.so/video"))
            send = stack.enter_context(mock.patch.object(bot, "send_news_message"))
            stack.enter_context(mock.patch.object(bot.time, "sleep"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            count = bot.fetch_and_post_news()
        return {"count": count, "send": send, "fetch": fetch, "history_load": history_load,
                "lookup": lookup, "analyse": analyse, "archive": archive,
                "translation": translation, "video": video}

    def test_subject_geography_overrides_feed_labels_and_unknown_is_held_back(self):
        malaysia = article("Malaysia High Court orders a new corruption trial",
                           "https://international-publisher.example/news/malaysia-case")
        british = article("UK court rules on a British immigration policy",
                          "https://malaysian-publisher.example/news/british-case")
        unknown = article("Court announces a new hearing date", "https://publisher.example/news/hearing")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [british, unknown],
                                    bot.INTERNATIONAL_FEED_URL: [malaysia]})
        self.assertEqual(result["count"], 2)
        destinations = {call.kwargs["source_title"]: call.kwargs["section"]
                        for call in result["send"].call_args_list}
        self.assertEqual(destinations, {malaysia.title: "local", british.title: "international"})
        self.assertEqual(result["archive"].call_count, 2)

    def test_unknown_geography_cannot_fall_back_to_feed_or_publisher(self):
        unknown = article("Court orders police to reopen investigation - Bernama",
                          "https://bernama.com/news.php?id=12345")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [unknown], bot.INTERNATIONAL_FEED_URL: [unknown]})
        self.assertEqual(result["count"], 0)
        result["send"].assert_not_called()
        result["translation"].assert_not_called()
        result["archive"].assert_not_called()

    def test_global_history_blocks_same_story_from_another_publisher(self):
        historical = {"title": "Malaysia police arrest three men in Kuala Lumpur - The Star",
                      "link": "https://publisher-a.example/news/arrests"}
        repeated = article("MALAYSIA police arrest THREE men in Kuala Lumpur - Bernama",
                           "https://publisher-b.example/new-url/arrests")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [repeated]}, history=[historical])
        self.assertEqual(result["count"], 0)
        result["send"].assert_not_called()
        result["lookup"].assert_not_called()
        result["analyse"].assert_not_called()
        result["archive"].assert_not_called()

    def test_global_history_matches_tracking_url_even_if_headline_changes(self):
        historical = {"title": "Malaysia court hears civil lawsuit",
                      "link": "https://www.publisher.example/news/44?utm_source=email"}
        repeated = article("Malaysia judge opens court hearing today",
                           "https://publisher.example/news/44/?utm_source=social&fbclid=abc#top")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [repeated]}, history=[historical])
        self.assertEqual(result["count"], 0)
        result["send"].assert_not_called()

    def test_same_run_publisher_and_tracking_url_variants_post_once(self):
        first = article("Malaysia police arrest three men in Kuala Lumpur - The Star",
                        "https://publisher-a.example/news/arrests?utm_source=email")
        another_publisher = article("Malaysia police arrest three men in Kuala Lumpur - Bernama",
                                    "https://publisher-b.example/news/another-url")
        tracking_variant = article("Malaysia police issue update on the Kuala Lumpur arrests",
                                   "https://www.publisher-a.example/news/arrests/?utm_source=telegram&fbclid=abc")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [first],
                                    bot.LOCAL_FEED_URLS[1]: [another_publisher],
                                    bot.LOCAL_FEED_URLS[2]: [tracking_variant]})
        self.assertEqual(result["count"], 1)
        result["send"].assert_called_once()
        result["archive"].assert_called_once()
        self.assertEqual(result["send"].call_args.kwargs["source_title"], first.title)

    def test_changed_case_detail_is_not_suppressed_as_a_repeat(self):
        first = article("Malaysia police arrest three men in Kuala Lumpur",
                        "https://publisher.example/news/three-men")
        update = article("Malaysia police arrest four men in Kuala Lumpur",
                         "https://publisher.example/news/four-men")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [first, update]})
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["send"].call_count, 2)

    def test_history_load_failure_pauses_before_fetching_any_feed(self):
        result = self.run_pipeline({}, history_error=RuntimeError("Notion history unavailable"))
        self.assertEqual(result["count"], 0)
        result["fetch"].assert_not_called()
        result["send"].assert_not_called()
        result["archive"].assert_not_called()
        result["analyse"].assert_not_called()

    def test_live_duplicate_lookup_failure_never_sends_unverified_article(self):
        fresh = article("Malaysia High Court rules on a new policy", "https://publisher.example/news/fresh")
        result = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [fresh]},
                                    lookup_response=mock.Mock(status_code=429, text="rate limited"))
        self.assertEqual(result["count"], 0)
        result["send"].assert_not_called()
        result["archive"].assert_not_called()
        result["translation"].assert_not_called()

    def test_already_in_notion_raises_for_failed_or_malformed_lookup(self):
        cases = [mock.Mock(status_code=500, text="unavailable"),
                 mock.Mock(status_code=200, json=lambda: {}),
                 mock.Mock(status_code=200, json=lambda: {"results": None}),
                 mock.Mock(status_code=200, json=lambda: {"results": "invalid"})]
        for response in cases:
            with self.subTest(response=response), \
                    mock.patch.object(bot, "NOTION_TOKEN", "unit-test-token"), \
                    mock.patch.object(bot.requests, "post", return_value=response), \
                    redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                bot.already_in_notion("Malaysia court case", "https://publisher.example/news/case")
        with mock.patch.object(bot, "NOTION_TOKEN", "unit-test-token"), \
                mock.patch.object(bot.requests, "post", side_effect=OSError("offline")), \
                redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            bot.already_in_notion("Malaysia court case", "https://publisher.example/news/case")


if __name__ == "__main__":
    unittest.main()
