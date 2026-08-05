import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("generator", Path(__file__).parents[1] / "scripts" / "generate_report.py")
generator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generator
SPEC.loader.exec_module(generator)


class GeneratorTests(unittest.TestCase):
    def test_parse_rss_classifies_and_maps_watchlist(self):
        source = generator.Source("Test", "https://example.test/rss", "economy", "official", "Europe")
        payload = b'''<rss><channel><item><title>ASML faces new chip export controls</title>
        <link>https://example.test/a</link><pubDate>Tue, 04 Aug 2026 10:00:00 GMT</pubDate>
        <description>Semiconductor equipment rules affect trade with China.</description></item></channel></rss>'''
        items = generator.parse_feed(source, payload, date(2026, 8, 5))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].section, "geopolitics")
        self.assertIn("ASME", items[0].watchlist)
        self.assertIn("SEC0", items[0].watchlist)

    def test_medical_terms_override_default_section(self):
        source = generator.Source("University", "x", "economy", "university", "Europe")
        self.assertEqual(generator.classify(source, "New glaucoma treatment", "Phase 3 trial"), "medicine")

    def test_deduplicate_similar_titles(self):
        base = dict(url="https://example.test", published="2026-08-04", source="A", section="economy", source_type="official", region="Europe")
        first = generator.Item("1", "Central bank holds interest rates unchanged", **base, score=100)
        second = generator.Item("2", "Central bank holds its interest rates unchanged", **base, score=90)
        self.assertEqual([item.id for item in generator.deduplicate([second, first])], ["1"])

    def test_ai_cannot_cite_unknown_item(self):
        item = generator.Item("known", "Title", "https://example.test", "2026-08-04", "Source", "economy", "official", "Europe")
        briefing = {"overview": [], "sections": {"economy": [{"title": "Story", "summary": "Text", "item_ids": ["invented"]}], "geopolitics": [], "medicine": []}}
        clean = generator.validate_briefing(briefing, [item])
        self.assertEqual(clean["sections"]["economy"], [])

    def test_company_source_is_visibly_labelled(self):
        item = generator.Item("company", "Company result", "https://example.test", "2026-08-04", "Novartis", "economy", "company", "Europe", watchlist=["NVS"])
        briefing = {"overview": ["Brief"], "sections": {"economy": [{"title": "Story", "summary": "Text", "why_it_matters": "Relevant", "evidence": "Company report", "item_ids": ["company"]}], "geopolitics": [], "medicine": []}}
        page = generator.render_report(date(2026, 8, 5), briefing, [item], [], "now", True)
        self.assertIn("interested-party source", page)
        self.assertIn("Watchlist:</strong> NVS", page)
        self.assertNotIn("<table", page)
        self.assertNotIn("<script", page)

    def test_fixture_end_to_end_without_ai(self):
        fixture = Path(__file__).parent / "fixture.json"
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", ["generate_report.py", "--date", "2026-08-05", "--output", directory, "--fixture", str(fixture), "--no-ai"]):
            generator.main()
            page = (Path(directory) / "index.html").read_text()
            metadata = json.loads((Path(directory) / "report.json").read_text())
        self.assertIn("Global geopolitics", page)
        self.assertIn("Medical progress, care and longevity", page)
        self.assertFalse(metadata["ai_used"])
        self.assertEqual(metadata["selected_items"], 10)


if __name__ == "__main__":
    unittest.main()
