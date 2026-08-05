import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("merger", Path(__file__).parents[1] / "scripts" / "merge_reports.py")
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


class MergeTests(unittest.TestCase):
    def test_combines_both_articles_into_one_semantic_article(self):
        market = "<html><body><article><p>Market component</p></article></body></html>"
        news = "<html><body><article><p><strong>Daily World Briefing — 2026-08-05.</strong> Intro.</p><h2>Today in brief</h2></article></body></html>"
        page = merger.merge_pages(market, news, "2026-08-05")
        self.assertEqual(page.count("<article>"), 1)
        self.assertIn("Market component", page)
        self.assertIn("<h2>Daily reflection</h2>", page)
        self.assertIn("A Buddhist perspective:", page)
        self.assertIn("gutenberg.org", page)
        self.assertLess(page.index("Daily reflection"), page.index("Market component"))
        self.assertIn("<h2>World news</h2>", page)
        self.assertIn("Today in brief", page)
        self.assertNotIn("Daily World Briefing — 2026-08-05.</strong> Intro", page)

    def test_rejects_page_without_article(self):
        with self.assertRaisesRegex(ValueError, "no <article>"):
            merger.article_body("<html></html>")

    def test_reflection_is_stable_for_a_given_date_and_rotates(self):
        first = merger.daily_reflection("2026-08-05")
        self.assertEqual(first, merger.daily_reflection("2026-08-05"))
        self.assertNotEqual(first, merger.daily_reflection("2026-08-06"))


if __name__ == "__main__":
    unittest.main()
