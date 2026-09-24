import unittest

from news_policy import canonical_url, classify_geography, is_duplicate, normalize_headline


class GeographyTests(unittest.TestCase):
    def test_malaysian_subject_overrides_international_feed_or_path(self):
        cases = (
            ("Malaysia tightens rules for foreign workers - Malay Mail", "", "https://example.com/world/story"),
            ("Sabah court sentences three men", "", ""),
            ("SPRM arrests company director", "", ""),
            ("MACC recovers funds from overseas accounts", "", ""),
            ("Dewan Rakyat debates tax bill", "", ""),
            ("Anwar Ibrahim meets US president in Washington", "", ""),
            ("Singapore and Malaysia sign new agreement", "", ""),
            ("Singapore court jails suspect", "The suspect is a Malaysian citizen.", ""),
            ("Thai tourist arrested over fatal crash", "The crash happened in Petaling Jaya, Selangor.", ""),
            ("Court sets trial date", "The case was heard in Kota Kinabalu, Sabah.", ""),
        )
        for args in cases:
            with self.subTest(title=args[0]):
                self.assertEqual(classify_geography(*args), "local")

    def test_foreign_subject_overrides_local_feed_and_publisher_brand(self):
        cases = (
            ("UK health minister confirms helicopter crash - The Star", "", "https://example.com/malaysia/story"),
            ("Court in London rejects appeal - Free Malaysia Today", "", ""),
            ("Indonesia probes gambling claims involving Dewan Rakyat - Bernama", "", ""),
            ("Brazil Supreme Court removes federal judge - Malay Mail", "", ""),
            ("US judge blocks new immigration rules - RTM", "", ""),
            ("Foreign minister rules out talks", "Cuba's foreign minister ruled out talks with the United States. Free Malaysia Today", ""),
            ("British court rejects minister's appeal", "KUALA LUMPUR: Malaysian observers followed the proceedings.", ""),
        )
        for args in cases:
            with self.subTest(title=args[0]):
                self.assertEqual(classify_geography(*args), "international")

    def test_ambiguous_stories_and_publisher_names_do_not_imply_malaysia(self):
        for title in ("Court hears appeal - Malay Mail", "Court hears appeal - Free Malaysia Today",
                      "Court hears appeal - TheStar", "Court hears appeal - Bernama",
                      "Court hears appeal - Berita RTM", "Minister asks us to be patient",
                      "Federal Court orders new hearing", "Police arrest a man"):
            with self.subTest(title=title):
                self.assertIsNone(classify_geography(title, "", "https://example.com.my/news/article"))
        self.assertIsNone(classify_geography("Court hears appeal", "Reported by Free Malaysia Today"))
        self.assertIsNone(classify_geography("Court hears appeal - malaysia.bernama.com"))
        self.assertIsNone(classify_geography("Court hears appeal", "Full article: https://malaysia.bernama.com/article"))
        self.assertEqual(classify_geography("British court hears appeal - majujohor.bernama.com"), "international")

    def test_url_section_is_only_a_weak_fallback(self):
        self.assertEqual(classify_geography("Court hears appeal", "", "https://example.com/global/story"), "international")
        self.assertEqual(classify_geography("Court hears appeal", "", "https://example.com/nasional/story"), "local")
        self.assertEqual(classify_geography("Court hears appeal", "The case concerns PDRM officers.", "https://example.com/world/story"), "local")
        self.assertIsNone(classify_geography("Court hears appeal", "", "https://world.example.com/article"))


class DuplicateTests(unittest.TestCase):
    def test_known_publisher_suffixes_removed_without_truncating_actual_headline(self):
        self.assertEqual(normalize_headline("Court overturns conviction – Free Malaysia Today"), "court overturns conviction")
        self.assertEqual(normalize_headline("Court overturns conviction | TheStar"), "court overturns conviction")
        self.assertEqual(normalize_headline("Court rejects appeal - new trial ordered"), "court rejects appeal new trial ordered")
        self.assertEqual(normalize_headline("Stars shine at The Star awards"), "stars shine at the star awards")
        self.assertEqual(normalize_headline("Court rejects appeal - Unknown Publisher"), "court rejects appeal unknown publisher")
        self.assertEqual(normalize_headline("Court hears appeal - majujohor.bernama.com"), "court hears appeal")
        self.assertEqual(normalize_headline("Court hears appeal - www.reuters.com"), "court hears appeal")
        self.assertEqual(normalize_headline("Court hears appeal - malaysia.unknown.example"), "court hears appeal malaysia unknown example")

    def test_tracking_links_match_but_article_ids_remain_distinct(self):
        first = "http://WWW.Example.com:80/article/?id=42&utm_source=rss&fbclid=abc#comments"
        self.assertEqual(canonical_url(first), "https://example.com/article?id=42")
        self.assertEqual(canonical_url("https://example.com/a?b=2&a=1&gclid=x"), "https://example.com/a?a=1&b=2")
        self.assertNotEqual(canonical_url("https://example.com/article?id=42"), canonical_url("https://example.com/article?id=43"))
        self.assertNotEqual(canonical_url("https://example.com/Story"), canonical_url("https://example.com/story"))
        self.assertEqual(canonical_url("https://example.com/%7euser/article"), "https://example.com/~user/article")
        self.assertNotEqual(canonical_url("https://example.com/a%2fb"), canonical_url("https://example.com/a/b"))

    def test_invalid_urls_do_not_all_become_duplicates(self):
        for value in (None, "", "not a url", "file:///news", "https://example.com:bad/news"):
            self.assertEqual(canonical_url(value), "")
        self.assertFalse(is_duplicate("New story", "", [{"title": "Different story", "link": ""}]))

    def test_repeat_url_and_cross_publisher_headline(self):
        records = [{"title": "Court rejects man's appeal - Bernama", "link": "https://one.example/article"}]
        self.assertTrue(is_duplicate("Court rejects man’s appeal - The Star", "https://two.example/other", records))
        self.assertTrue(is_duplicate("Revised heading", "https://one.example/article?utm_medium=social", records))
        records = [{"title": "British court rejects minister's appeal - Reuters", "link": "https://reuters.com/one"}]
        self.assertTrue(is_duplicate("British court rejects minister’s appeal - The Star", "https://thestar.com.my/two", records))
        records = [{"title": "Johor police arrest suspect - majujohor.bernama.com", "link": "https://bernama.com/one"}]
        self.assertTrue(is_duplicate("Johor police arrest suspect - Malay Mail", "https://malaymail.com/two", records))

    def test_small_attribution_wording_difference_can_be_a_repeat(self):
        records = [{"title": "Police say three men arrested in Johor over fraud", "link": "https://one.example/a"}]
        self.assertTrue(is_duplicate("Police: three men arrested in Johor over fraud", "https://two.example/b", records))

    def test_numeric_changes_preserve_distinct_crimes_and_updates(self):
        records = [{"title": "Police arrest 3 men over RM50,000 fraud in Johor", "link": "https://example.com/first"}]
        for title in ("Police arrest 4 men over RM50,000 fraud in Johor",
                      "Police arrest 3 men over RM60,000 fraud in Johor",
                      "Police arrest 3 men over RM50,000 fraud in Perak"):
            with self.subTest(title=title):
                self.assertFalse(is_duplicate(title, "https://example.com/second", records))
        records = [{"title": "Police say three men arrested in Johor over fraud", "link": "https://one.example/a"}]
        self.assertFalse(is_duplicate("Police say four men arrested in Johor over fraud", "https://two.example/b", records))

    def test_new_legal_outcome_negation_name_and_date_are_not_suppressed(self):
        base = "Court says Ahmad convicted of fraud in Johor on 12 September"
        records = [{"title": base, "link": "https://example.com/first"}]
        for title in (base.replace("convicted", "acquitted"), base.replace("convicted", "not convicted"),
                      base.replace("Ahmad", "Ali"), base.replace("12", "13")):
            with self.subTest(title=title):
                self.assertFalse(is_duplicate(title, "https://example.com/second", records))


if __name__ == "__main__":
    unittest.main()
