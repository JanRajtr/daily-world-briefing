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
        weather = [{"name": "Horoměřice", "condition": "Clear", "minimum_c": 15, "maximum_c": 25, "rain_probability": 10, "rain_mm": 0.4, "wind_kmh": 12, "sunrise": "05:32", "sunset": "20:36"}]
        page = merger.merge_pages(market, news, "2026-08-05", weather)
        self.assertEqual(page.count("<article>"), 1)
        self.assertIn("Market component", page)
        self.assertIn("<h2>Daily reflection</h2>", page)
        self.assertIn("A Buddhist perspective:", page)
        self.assertIn("gutenberg.org", page)
        self.assertLess(page.index("Daily reflection"), page.index("Market component"))
        self.assertIn("<h2>Economy news</h2>", page)
        self.assertIn("Today in brief", page)
        self.assertNotIn("Daily World Briefing — 2026-08-05.</strong> Intro", page)
        self.assertIn("Today's weather", page)
        self.assertIn("Rain forecast:</strong> 10% chance, 0.4 mm expected", page)
        self.assertIn("Horoměřice", page)
        self.assertIn("Healthy and tasty menu", page)
        self.assertIn("Longevity tip of the day", page)
        self.assertLess(page.index("Daily reflection"), page.index("Today's weather"))
        self.assertLess(page.index("Today's weather"), page.index("Healthy and tasty menu"))
        self.assertLess(page.index("Healthy and tasty menu"), page.index("Longevity tip of the day"))
        self.assertLess(page.index("Longevity tip of the day"), page.index("Market component"))
        self.assertTrue(page.index("Have a nice, calm, happy and safe day!") > page.index("Longevity tip of the day"))

    def test_rejects_page_without_article(self):
        with self.assertRaisesRegex(ValueError, "no <article>"):
            merger.article_body("<html></html>")

    def test_reflection_is_stable_for_a_given_date_and_rotates(self):
        first = merger.daily_reflection("2026-08-05")
        self.assertEqual(first, merger.daily_reflection("2026-08-05"))
        self.assertNotEqual(first, merger.daily_reflection("2026-08-06"))

    def test_dynamic_content_is_validated_and_rendered(self):
        content = {
            "reflection": "A fresh reflection generated for today.",
            "meals": [
                {"meal": meal, "name": f"Fresh {meal}", "recipe": "Combine measured wholesome ingredients, cook them carefully for twenty minutes, season to taste and serve warm with vegetables."}
                for meal in ("Breakfast", "Lunch", "Snack", "Dinner")
            ],
            "longevity_tip": "A fresh, cautious tip for today.",
        }
        page = merger.merge_pages("<article>Market</article>", "<article>News</article>", "2026-08-05", [], content)
        self.assertIn("A fresh reflection generated for today.", page)
        self.assertIn("Fresh Breakfast", page)
        self.assertIn("A fresh, cautious tip for today.", page)

    def test_rejects_incomplete_ai_recipe(self):
        content = merger.fallback_daily_content("2026-08-05")
        content["meals"][0]["recipe"] = "Too short"
        with self.assertRaisesRegex(ValueError, "recipe is incomplete"):
            merger.validate_daily_content(content)

    def test_omits_economy_component_when_it_has_no_items(self):
        page = merger.merge_pages(
            "<article><h2>Market situation</h2></article>",
            "<article><p><strong>Daily Economy Briefing — 2026-08-05.</strong></p><h2>Global economy and portfolio</h2></article>",
            "2026-08-05", [], None, False,
        )
        self.assertNotIn("Economy news", page)
        self.assertNotIn("Global economy and portfolio", page)
        self.assertIn("Market situation", page)


if __name__ == "__main__":
    unittest.main()
