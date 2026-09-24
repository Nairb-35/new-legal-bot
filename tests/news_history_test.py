import unittest
from unittest.mock import Mock
from news_history import load_recent_news_history


def page(title, link):
    return {"properties": {"Name": {"title": [{"plain_text": title}]}, "Source Link": {"url": link}}}


class NewsHistoryTests(unittest.TestCase):
    def test_loads_all_pages_without_video_copies(self):
        post = Mock(side_effect=[
            Mock(status_code=200, json=lambda: {"results": [page("Malaysia bill", "https://source/a")],
                 "has_more": True, "next_cursor": "next"}),
            Mock(status_code=200, json=lambda: {"results": [page("🎬 Malaysia bill", "https://source/a"),
                 page("UK court ruling", "https://source/b")], "has_more": False}),
        ])
        self.assertEqual(len(load_recent_news_history(post, "database", {})), 2)
        self.assertEqual(post.call_args.kwargs["json"]["start_cursor"], "next")

    def test_history_failure_never_masquerades_as_empty_history(self):
        for response in [Mock(status_code=429), Mock(status_code=200, json=lambda: {}),
                         Mock(status_code=200, json=lambda: {"results": [], "has_more": True})]:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                load_recent_news_history(Mock(return_value=response), "database", {})
