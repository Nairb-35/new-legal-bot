import sys
import types
import unittest
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from unittest import mock

# Keep these focused routing tests independent of network integrations.
sys.modules.setdefault("feedparser", types.SimpleNamespace(parse=lambda _url: None))
requests_module = types.ModuleType("requests")
requests_module.post = lambda *_args, **_kwargs: None
requests_module.get = lambda *_args, **_kwargs: None
sys.modules.setdefault("requests", requests_module)
translator_module = types.ModuleType("deep_translator")
translator_module.GoogleTranslator = object
sys.modules.setdefault("deep_translator", translator_module)

import bilingual_news as bot


def entry(title, summary="", age_days=0, official=False):
    published = datetime.now(timezone.utc) - timedelta(days=age_days)
    return types.SimpleNamespace(title=title, summary=summary,
                                 link=("https://berita.rtm.gov.my/" if official else "https://example.com/") + title.replace(" ", "_"),
                                 published_parsed=published.utctimetuple())


class ParliamentNewsTests(unittest.TestCase):
    topics = {"local_thread_id": 101, "international_thread_id": 202,
              "dewan_negara_thread_id": 303, "dewan_rakyat_thread_id": 404}

    def run_pipeline(self, entries_by_url, topics=None, **limits):
        configured = self.topics if topics is None else topics
        requested_urls = []

        def parse(url):
            requested_urls.append(url)
            return types.SimpleNamespace(entries=entries_by_url.get(url, []))

        with mock.patch.object(bot, "load_news_topics", return_value=configured), \
                mock.patch.object(bot, "NOTION_TOKEN", "test-only"), \
                mock.patch.object(bot, "load_recent_news_history", return_value=[]), \
                mock.patch.object(bot, "parliament_sources", return_value=bot.PARLIAMENT_DEFAULT_SOURCES), \
                mock.patch.object(bot, "fetch_news_feed", side_effect=parse), \
                mock.patch.object(bot, "bilingual_titles", side_effect=lambda title: ("English " + title, title)) as translate, \
                mock.patch.object(bot, "already_in_notion", return_value=False), \
                mock.patch.object(bot, "ai_lens", return_value={}) as analyse, \
                mock.patch.object(bot, "push_to_notion", return_value="https://notion.so/article"), \
                mock.patch.object(bot, "push_parliament_to_notion", return_value="https://notion.so/source") as archive, \
                mock.patch.object(bot, "push_video_to_notion", return_value="https://notion.so/video") as video, \
                mock.patch.object(bot, "send_news_message") as send, \
                mock.patch.object(bot.time, "sleep"):
            count = bot.fetch_and_post_news(**limits)
        self.translation_calls = translate.call_args_list
        self.video_calls = video.call_args_list
        self.archive_calls = archive.call_args_list
        return count, send.call_args_list, requested_urls, analyse.call_args_list

    def test_separate_saved_thread_ids_and_rtm_buttons(self):
        for section, thread_id in (("dewan_negara", 303), ("dewan_rakyat", 404)):
            with self.subTest(section=section), \
                    mock.patch.object(bot, "load_news_topics", return_value=self.topics), \
                    mock.patch.object(bot, "tg", return_value=mock.Mock(status_code=200)) as send:
                bot.send_news_message("English headline", "Tajuk Bahasa Melayu", "Today", "***",
                                      "https://notion.so/article", "https://berita.rtm.gov.my/news", section=section,
                                      source_title="Tajuk asal RTM")
            payload = send.call_args.args[1]
            self.assertEqual(payload["message_thread_id"], thread_id)
            self.assertIn(section.replace("_", " ").title(), payload["text"])
            self.assertTrue(any("RTM" in button["text"]
                                for row in payload["reply_markup"]["inline_keyboard"] for button in row))

    def test_chamber_name_alone_never_promotes_unofficial_news(self):
        configured = set(bot.PARLIAMENT_SECTIONS)
        examples = [
            ("Dewan Negara approves funding", "", "local"),
            ("Education minister answers questions", "Debated in Dewan Rakyat", "local"),
            ("Parliament approves bill", "", "local"),
            ("Dewan Rakyat and Dewan Negara receive bill", "", "local"),
            ("Senate passes bill", "", "local"),
        ]
        for title, summary, expected in examples:
            with self.subTest(title=title):
                self.assertEqual(bot.route_news_section("local", title, summary, configured), expected)
        self.assertEqual(bot.route_news_section("international", "Dewan Rakyat", "", configured), "international")
        self.assertEqual(bot.route_news_section("local", "Dewan Rakyat", "", set()), "local")

    def test_rakyat_in_repurposed_general_omits_thread_id_without_changing_other_routes(self):
        topics = dict(self.topics, dewan_rakyat_thread_id=1)
        for section, expected_thread_id in (("dewan_rakyat", None), ("dewan_negara", 303),
                                            ("local", 101), ("international", 202)):
            with self.subTest(section=section), \
                    mock.patch.object(bot, "load_news_topics", return_value=topics), \
                    mock.patch.object(bot, "tg", return_value=mock.Mock(status_code=200)) as send:
                bot.send_news_message("English headline", "Tajuk Bahasa Melayu", "Today", "***",
                                      "https://notion.so/article", "https://berita.rtm.gov.my/news", section=section,
                                      source_title="Tajuk asal RTM")
            send.assert_called_once()
            payload = send.call_args.args[1]
            if expected_thread_id is None:
                self.assertNotIn("message_thread_id", payload)
                self.assertIn("Dewan Rakyat", payload["text"])
            else:
                self.assertEqual(payload["message_thread_id"], expected_thread_id)

    def test_general_topic_id_one_counts_as_configured_rakyat(self):
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"]
        count, sent, requested, _ = self.run_pipeline(
            {rtm: [entry("Dana pendidikan baharu", official=True)]}, topics={"dewan_rakyat_thread_id": 1})
        self.assertEqual(count, 1)
        self.assertIn(rtm, requested)
        self.assertEqual(sent[0].kwargs["section"], "dewan_rakyat")

    def test_authoritative_rtm_chamber_item_without_chamber_in_title_survives(self):
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        count, sent, _, analyses = self.run_pipeline({rtm: [
            entry("Dana baharu bagi pembangunan sukan", "<p>Sports budget &amp; education&nbsp;fund.</p>", official=True)
        ]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].kwargs["section"], "dewan_negara")
        self.assertEqual(sent[0].kwargs["source_excerpt"], "Sports budget & education fund.")
        self.assertEqual(analyses, [])
        self.assertEqual(self.translation_calls, [])
        self.assertEqual(self.video_calls, [])
        self.assertEqual(self.archive_calls[0].args[0], "Dana baharu bagi pembangunan sukan")

    def test_html_entities_and_markup_become_plain_text(self):
        value = '&lt;p&gt;Dewan&nbsp;Rakyat &amp;amp; bill&lt;/p&gt;<script>sports</script>'
        self.assertEqual(bot.article_plain_text(value), "Dewan Rakyat & bill")

    def test_fair_default_quotas_keep_existing_local_and_world_capacity(self):
        feeds = {
            bot.LOCAL_FEED_URLS[0]: [entry(f"Malaysia police investigation {i}") for i in range(15)],
            bot.INTERNATIONAL_FEED_URL: [entry(f"UK court ruling {i}") for i in range(10)],
            bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]:
                [entry(f"Dana pendidikan negara {i}", official=True) for i in range(10)],
            bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"]:
                [entry(f"Pembiayaan rakyat {i}", official=True) for i in range(10)],
        }
        count, sent, _, _ = self.run_pipeline(feeds)
        self.assertEqual(count, 20)
        self.assertEqual(Counter(call.kwargs["section"] for call in sent),
                         {"local": 8, "international": 4, "dewan_negara": 4, "dewan_rakyat": 4})
        # A reduced global cap still gives each destination an early chance.
        count, sent, _, _ = self.run_pipeline(feeds, max_posts=4)
        self.assertEqual(count, 4)
        self.assertEqual(len({call.kwargs["section"] for call in sent}), 4)

    def test_no_config_skips_new_feeds_and_preserves_existing_coverage(self):
        count, _, requested, _ = self.run_pipeline({}, topics={})
        self.assertEqual(count, 0)
        self.assertEqual(requested, bot.LOCAL_FEED_URLS + [bot.INTERNATIONAL_FEED_URL])
        with mock.patch.object(bot, "load_news_topics", return_value={}), mock.patch.object(bot, "tg") as send:
            bot.send_news_message("Bill", "RUU", "Today", "***", "https://notion.so/page",
                                  "https://example.com/article", section="dewan_rakyat")
        send.assert_not_called()

    def test_only_configured_chamber_feed_is_loaded(self):
        _, _, requested, _ = self.run_pipeline({}, topics={"dewan_negara_thread_id": 303})
        self.assertIn(bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"], requested)
        self.assertNotIn(bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"], requested)

    def test_unofficial_chamber_item_does_not_bypass_local_quota(self):
        count, sent, _, _ = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [
            entry("Dewan Rakyat discusses sports budget"),
            entry("Police investigation local story")
        ]}, local_max_posts=0)
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])

    def test_old_parliament_news_stays_outside_existing_daily_window(self):
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"]
        count, _, _, _ = self.run_pipeline({rtm: [entry("Funding update", age_days=2, official=True)]})
        self.assertEqual(count, 0)

    def test_parliament_fetches_only_the_two_official_rtm_feeds(self):
        _, _, requested, _ = self.run_pipeline({})
        self.assertEqual(requested, bot.LOCAL_FEED_URLS + [bot.INTERNATIONAL_FEED_URL] +
                         [bot.PARLIAMENT_DEFAULT_SOURCES[section]["feed_url"]
                          for section in bot.PARLIAMENT_SECTIONS])

    def test_general_news_malay_headlines_can_still_be_auto_translated(self):
        source = "Dewan Negara lulus rang undang-undang"
        expected = {"en": "Dewan Negara passes bill", "ms": source}

        def translator(**kwargs):
            self.assertEqual(kwargs["source"], "auto")
            return types.SimpleNamespace(translate=lambda title: expected[kwargs["target"]])

        with mock.patch.object(bot, "GoogleTranslator", side_effect=translator):
            self.assertEqual(bot.bilingual_titles(source), (expected["en"], source))

    def test_failed_english_translation_has_no_false_english_flag(self):
        source = "Dewan Negara lulus rang undang-undang"
        with mock.patch.object(bot, "GoogleTranslator", side_effect=RuntimeError("offline")):
            title_en, title_bm = bot.bilingual_titles(source)
        self.assertEqual((title_en, title_bm), ("", ""))
        with mock.patch.object(bot, "load_news_topics", return_value=self.topics), \
                mock.patch.object(bot, "tg", return_value=mock.Mock(status_code=200)) as send:
            bot.send_news_message(title_en, title_bm, "Today", "***", "https://notion.so/page",
                                  "https://berita.rtm.gov.my/article", section="dewan_negara", source_title=source)
        text = send.call_args.args[1]["text"]
        self.assertIn(source, text)
        self.assertNotIn("🇬🇧", text)
        self.assertNotIn("🇲🇾", text)

    def test_same_article_in_local_and_parliament_feeds_posts_once(self):
        article = entry("Dewan Rakyat approves a new bill", official=True)
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"]
        count, sent, _, _ = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [article], rtm: [article]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].kwargs["section"], "dewan_rakyat")

    def test_rtm_tag_routes_local_duplicate_even_without_chamber_name(self):
        article = entry("Minister announces an education funding policy", official=True)
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        count, sent, _, _ = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [article], rtm: [article]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].kwargs["section"], "dewan_negara")

    def test_rtm_tag_routes_same_url_with_different_local_headline_once(self):
        local_article = entry("Minister announces an education funding policy", official=True)
        tagged_article = entry("Dana pendidikan baharu diumumkan", "Ayat asal RTM", official=True)
        tagged_article.link = local_article.link
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        count, sent, _, _ = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [local_article], rtm: [tagged_article]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].kwargs["section"], "dewan_negara")
        self.assertEqual(sent[0].kwargs["source_title"], tagged_article.title)
        self.assertEqual(sent[0].kwargs["source_excerpt"], tagged_article.summary)

    def test_conflicting_rtm_chamber_tags_stay_local(self):
        article = entry("Parliament announces education funding policy", official=True)
        negara = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        rakyat = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_rakyat"]["feed_url"]
        count, sent, _, _ = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [article], negara: [article], rakyat: [article]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].kwargs["section"], "local")

    def test_feed_fetch_uses_bounded_http_and_parses_response_bytes(self):
        response = mock.Mock(content=b"<rss/>")
        with mock.patch.object(bot.requests, "get", return_value=response) as get, \
                mock.patch.object(bot.feedparser, "parse", return_value="parsed") as parse:
            self.assertEqual(bot.fetch_news_feed("https://example.com/feed"), "parsed")
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 25))
        response.raise_for_status.assert_called_once_with()
        parse.assert_called_once_with(b"<rss/>")

    def test_rtm_allowlist_rejects_insecure_lookalike_and_userinfo_urls(self):
        self.assertTrue(bot.is_official_rtm_url("https://berita.rtm.gov.my/nasional/123"))
        self.assertTrue(bot.is_official_rtm_url("https://berita.rtm.gov.my:443/nasional/123"))
        for url in ("http://berita.rtm.gov.my/news", "https://berita.rtm.gov.my.evil.test/news",
                    "https://berita-rtm.gov.my/news", "https://berita.rtm.gov.my@evil.test/news",
                    "https://evil.test@berita.rtm.gov.my/news", "https://berita.rtm.gov.my:444/news",
                    "https://berita.rtm.gov.my/with space", None):
            with self.subTest(url=url):
                self.assertFalse(bot.is_official_rtm_url(url))

    def test_spoofed_links_inside_official_feed_are_never_posted(self):
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        spoof = entry("Dewan Negara approves policy", official=True)
        spoof.link = "https://berita.rtm.gov.my.evil.test/news"
        count, sent, _, analyses = self.run_pipeline({rtm: [spoof]})
        self.assertEqual(count, 0)
        self.assertEqual(sent, [])
        self.assertEqual(analyses, [])
        self.assertEqual(self.archive_calls, [])

    def test_local_title_match_uses_original_rtm_link_title_and_excerpt(self):
        rtm = bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"]
        local = entry("Dewan Negara lulus RUU - The Star", "Unverified interpretation")
        original = entry("Dewan Negara lulus RUU", "<p>Petikan <b>asal</b> &amp; tepat.</p>", official=True)
        count, sent, _, analyses = self.run_pipeline({bot.LOCAL_FEED_URLS[0]: [local], rtm: [original]})
        self.assertEqual(count, 1)
        self.assertEqual(sent[0].args[5], original.link)
        self.assertEqual(sent[0].kwargs["source_title"], original.title)
        self.assertEqual(sent[0].kwargs["source_excerpt"], "Petikan asal & tepat.")
        self.assertEqual(analyses, [])
        self.assertEqual(self.translation_calls, [])

    def test_parliament_message_contains_only_official_wording_and_reference_buttons(self):
        with mock.patch.object(bot, "load_news_topics", return_value=self.topics), \
                mock.patch.object(bot, "tg", return_value=mock.Mock(status_code=200)) as send:
            bot.send_news_message("AI English headline", "AI translation", "24 September 2026, 10:00 MYT",
                                  "Invented importance", "https://notion.so/page",
                                  "https://berita.rtm.gov.my/original-article",
                                  analysis={"brief": {"statute": "Invented law"}, "ratings": {"legal_impact": 5}},
                                  video_url="https://notion.so/fake-video", section="dewan_negara",
                                  source_title="Tajuk asal RTM", source_excerpt="<p>Ayat sumber &amp; butiran asal.</p>")
        payload = send.call_args.args[1]
        self.assertIn("Tajuk asal RTM", payload["text"])
        self.assertIn("Ayat sumber &amp; butiran asal.", payload["text"])
        self.assertIn("24 September 2026, 10:00 MYT", payload["text"])
        for unwanted in ("AI English", "AI translation", "Invented", "Ratings", "Statute", "Legal Lens", "🇬🇧"):
            self.assertNotIn(unwanted, payload["text"])
        buttons = [button for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        sources = bot.parliament_sources()["dewan_negara"]
        self.assertEqual([button["url"] for button in buttons],
                         ["https://berita.rtm.gov.my/original-article", sources["live_url"],
                          sources["agenda_url"], sources["hansard_url"]])
        self.assertTrue(all("callback_data" not in button for button in buttons))

    def test_parliament_archive_contains_only_original_source_data(self):
        with mock.patch.object(bot, "NOTION_TOKEN", "test-token"), \
                mock.patch.object(bot, "_create_page", return_value="https://notion.so/source") as create:
            bot.push_parliament_to_notion("Tajuk asal", "<p>Petikan asal</p>",
                                         "https://berita.rtm.gov.my/news", "24 September 2026", "2026-09-24", "dewan_negara")
        self.assertEqual(create.call_args.args[:3], ("Tajuk asal", "https://berita.rtm.gov.my/news", "2026-09-24"))
        saved_blocks = json.dumps(create.call_args.args[3])
        self.assertIn("Petikan asal", saved_blocks)
        self.assertNotIn("Legal Lens", saved_blocks)
        self.assertNotIn("Video Script", saved_blocks)

    def test_official_feed_redirect_to_other_host_is_rejected(self):
        response = mock.Mock(content=b"<rss/>", url="https://unofficial.example/feed")
        with mock.patch.object(bot.requests, "get", return_value=response), \
                mock.patch.object(bot.feedparser, "parse") as parse:
            with self.assertRaises(ValueError):
                bot.fetch_news_feed(bot.PARLIAMENT_DEFAULT_SOURCES["dewan_negara"]["feed_url"])
        parse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
