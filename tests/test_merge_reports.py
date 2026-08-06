import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("merger", Path(__file__).parents[1] / "scripts" / "merge_reports.py")
merger = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merger
SPEC.loader.exec_module(merger)


class MergeTests(unittest.TestCase):
    def sourced_content(self):
        return {
            "quote": {"text": "Ověřený citát", "author": "Autor", "work": "Dílo", "url": "https://example.test/quote"},
            "buddhist_teaching": {"text": "Ověřené učení", "author": "Lama", "work": "Výklad", "url": "https://example.test/teaching"},
            "meals": [
                {"meal": meal, "name": f"Fresh {meal}", "recipe": "Smíchejte přesně odměřené suroviny, dvacet minut je pečlivě vařte, dochuťte a podávejte teplé se zeleninou.", "source_title": "Recept", "source_url": f"https://example.test/{meal.lower()}"}
                for meal in ("Breakfast", "Lunch", "Snack", "Dinner")
            ],
            "longevity_tip": {"text": "Ověřený praktický tip.", "author": "WHO", "work": "Doporučení", "url": "https://example.test/tip"},
        }

    def test_combines_both_articles_into_one_semantic_article(self):
        market = "<html><body><article><p>Market component</p></article></body></html>"
        news = "<html><body><article><p><strong>Daily World Briefing — 2026-08-05.</strong> Intro.</p><h2>Today in brief</h2></article></body></html>"
        weather = [{"name": "Horoměřice", "condition": "Clear", "minimum_c": 15, "maximum_c": 25, "rain_probability": 10, "rain_mm": 0.4, "wind_kmh": 12, "sunrise": "05:32", "sunset": "20:36"}]
        page = merger.merge_pages(market, news, "2026-08-05", weather, self.sourced_content())
        self.assertEqual(page.count("<article>"), 1)
        self.assertIn("Market component", page)
        self.assertIn("<h2>Myšlenka dne</h2>", page)
        self.assertIn("Ověřené učení", page)
        self.assertIn("Český překlad", page)
        self.assertIn("example.test/quote", page)
        self.assertLess(page.index("Myšlenka dne"), page.index("Market component"))
        self.assertIn("<h2>Ekonomické zprávy</h2>", page)
        self.assertIn("Today in brief", page)
        self.assertNotIn("Daily World Briefing — 2026-08-05.</strong> Intro", page)
        self.assertIn("Dnešní počasí", page)
        self.assertIn("pravděpodobnost 10 %, očekávaný úhrn 0.4 mm", page)
        self.assertIn("Horoměřice", page)
        self.assertIn("Zdravý a chutný jídelníček", page)
        self.assertIn("Tip pro zdravé stárnutí", page)
        self.assertLess(page.index("Myšlenka dne"), page.index("Dnešní počasí"))
        self.assertLess(page.index("Dnešní počasí"), page.index("Zdravý a chutný jídelníček"))
        self.assertLess(page.index("Zdravý a chutný jídelníček"), page.index("Tip pro zdravé stárnutí"))
        self.assertLess(page.index("Tip pro zdravé stárnutí"), page.index("Market component"))
        self.assertTrue(page.index("Přeji hezký") > page.index("Tip pro zdravé stárnutí"))

    def test_rejects_page_without_article(self):
        with self.assertRaisesRegex(ValueError, "no <article>"):
            merger.article_body("<html></html>")

    def test_reflection_is_omitted_without_sourced_content(self):
        self.assertEqual(merger.daily_reflection({}), "")

    def test_dynamic_content_is_validated_and_rendered(self):
        content = self.sourced_content()
        page = merger.merge_pages("<article>Market</article>", "<article>News</article>", "2026-08-05", [], content)
        self.assertNotIn("A fresh reflection generated for today.", page)
        self.assertIn("example.test/teaching", page)
        self.assertIn("Fresh Breakfast", page)
        self.assertIn("Ověřený praktický tip.", page)

    def test_omits_incomplete_ai_recipe_section(self):
        content = self.sourced_content()
        content["meals"][0]["recipe"] = "Too short"
        self.assertNotIn("meals", merger.validate_daily_content(content))

    def test_omits_all_optional_sections_without_results(self):
        page = merger.merge_pages("<article>Market</article>", "<article>News</article>", "2026-08-05", [], {})
        self.assertNotIn("Myšlenka dne", page)
        self.assertNotIn("Zdravý a chutný jídelníček", page)
        self.assertNotIn("Tip pro zdravé stárnutí", page)
        self.assertNotIn("Dnešní počasí", page)

    def test_renders_calendar_fx_air_and_sourced_daily_extras(self):
        content = self.sourced_content()
        content.update({
            "portfolio_events": [{"title": "ČNB", "text": "Dnes zasedá bankovní rada.", "url": "https://example.test/event"}],
            "market_explanations": [{"title": "Výrazný pohyb", "text": "Zdroj popisuje příčinu pohybu.", "url": "https://example.test/move"}],
            "cultural_note": {"text": "Událost se stala tohoto dne.", "author": "Muzeum", "work": "Kalendárium", "url": "https://example.test/culture"},
        })
        weather = [{"name": "Praha", "condition": "Jasno", "minimum_c": 18, "maximum_c": 32, "rain_probability": 0, "rain_mm": 0, "wind_kmh": 10, "uv_index": 7, "sunrise": "05:30", "sunset": "20:30"}]
        calendar = {"dayInWeek": "čtvrtek", "name": "Oldřiška", "isHoliday": False, "holidayName": None, "tomorrow": {"name": "Lada"}}
        fx = {"date": "06.08.2026", "rates": {"EUR": 24.5, "USD": 21.1}}
        air = [{"name": "Praha", "aqi": 38, "pm25": 5.6, "pollen": {"pelyněk": 42.1}}]
        page = merger.merge_pages("<article>Market</article>", "<article>News</article>", "2026-08-06", weather, content, False, calendar, fx, air)
        for text in ("Oldřiška", "Lada", "Česká koruna", "1 EUR = 24.500 Kč", "Ovzduší a pyl", "Dnes vyžaduje pozornost", "Kalendář portfolia", "Co vysvětluje neobvyklé pohyby", "Česká kulturní a historická stopa"):
            self.assertIn(text, page)

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
