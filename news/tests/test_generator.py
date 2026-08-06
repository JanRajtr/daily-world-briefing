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

    def test_rejects_thin_google_index_and_local_program_items(self):
        source = generator.Source(
            "SIPRI", "https://news.google.com/rss/search?q=site%3Asipri.org", "geopolitics",
            "independent-analysis", "Europe",
        )
        payload = b'''<rss><channel><item><title>armstrade . sipri . org / armstrade / htm - SIPRI Arms Transfers Database</title>
        <link>https://news.google.com/rss/articles/example</link><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <description>SIPRI Arms Transfers Database</description></item></channel></rss>'''
        self.assertEqual(generator.parse_feed(source, payload, date(2026, 8, 5)), [])

        medical = generator.Source(
            "ESC", "https://news.google.com/rss/search?q=site%3Aescardio.org", "medicine",
            "professional-society", "Europe",
        )
        payload = b'''<rss><channel><item><title>Establishment of a cardio-oncology program in the Republic of Kazakhstan</title>
        <link>https://news.google.com/rss/articles/medical</link><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <description>Establishment of a cardio-oncology program in the Republic of Kazakhstan ESC 365</description></item></channel></rss>'''
        self.assertEqual(generator.parse_feed(medical, payload, date(2026, 8, 5)), [])

    def test_keeps_detailed_medical_advance(self):
        source = generator.Source("EMA", "https://example.test/feed", "medicine", "regulator", "Europe")
        payload = b'''<rss><channel><item><title>EMA recommends new glaucoma treatment</title>
        <link>https://example.test/glaucoma</link><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <description>A phase 3 trial in 700 patients found improved control compared with standard care.</description></item></channel></rss>'''
        items = generator.parse_feed(source, payload, date(2026, 8, 5))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].section, "medicine")

    def test_eye_scope_rejects_general_retinal_news_and_early_trials(self):
        source = generator.Source("Journal", "https://example.test/feed", "medicine", "university", "Europe")
        retinal = b'''<rss><channel><item><title>New retinal imaging method</title>
        <link>https://example.test/retina</link><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <description>A clinical trial describes a novel retinal imaging technique.</description></item></channel></rss>'''
        self.assertEqual(generator.parse_feed(source, retinal, date(2026, 8, 5)), [])
        early = b'''<rss><channel><item><title>Phase 1 cancer vaccine trial</title>
        <link>https://example.test/cancer</link><pubDate>Wed, 05 Aug 2026 10:00:00 GMT</pubDate>
        <description>A first-in-human cancer study reports preliminary safety findings.</description></item></channel></rss>'''
        self.assertEqual(generator.parse_feed(source, early, date(2026, 8, 5)), [])

    def test_medical_scope_accepts_ocular_trauma_recovery(self):
        self.assertTrue(generator.in_medical_scope("New surgical method for vision recovery after ocular trauma"))
        self.assertFalse(generator.in_medical_scope("New treatment for routine cataract care"))

    def test_parses_pubmed_abstract_and_evidence_type(self):
        payload = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID>
        <Article><Journal><Title>European Heart Journal</Title></Journal>
        <ArticleTitle>Randomized treatment for cardiovascular disease</ArticleTitle>
        <Abstract><AbstractText Label="RESULTS">The randomized trial enrolled 900 patients with cardiovascular disease and found fewer clinical events after treatment compared with standard care during two years of follow-up.</AbstractText></Abstract>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
        </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'''
        items = generator.parse_pubmed(payload, date(2026, 8, 5))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_type, "peer-reviewed")
        self.assertIn("Randomized Controlled Trial", items[0].tags)
        self.assertIn("pubmed.ncbi.nlm.nih.gov/12345", items[0].url)

    def test_deduplicate_similar_titles(self):
        base = dict(url="https://example.test", published="2026-08-04", source="A", section="economy", source_type="official", region="Europe")
        first = generator.Item("1", "Central bank holds interest rates unchanged", **base, score=100)
        second = generator.Item("2", "Central bank holds its interest rates unchanged", **base, score=90)
        self.assertEqual([item.id for item in generator.deduplicate([second, first])], ["1"])

    def test_ai_cannot_cite_unknown_item(self):
        item = generator.Item("known", "Title", "https://example.test", "2026-08-04", "Source", "economy", "official", "Europe")
        briefing = {"overview": [], "sections": {"economy": [{"title": "Story", "summary": "Text", "item_ids": ["invented"]}]}}
        clean = generator.validate_briefing(briefing, [item])
        self.assertEqual(clean["sections"]["economy"], [])

    def test_ai_prompt_trims_long_source_text(self):
        item = generator.Item(
            "known", "Cancer trial", "https://example.test", "2026-08-04", "Journal",
            "medicine", "peer-reviewed", "Europe", summary="x" * 5000,
        )
        prompt = generator.ai_prompt([item], date(2026, 8, 5))
        self.assertNotIn("x" * 901, prompt)
        self.assertIn("x" * 900, prompt)

    def test_default_editorial_selection_is_bounded(self):
        items = []
        for section, count in (("economy", 20), ("geopolitics", 20), ("medicine", 20)):
            for index in range(count):
                items.append(generator.Item(str(index) + section, f"Title {index}", "https://example.test", "2026-08-04", "Source", section, "official", "Europe", score=index))
        selected = generator.select_items(items)
        self.assertEqual(sum(item.section == "economy" for item in selected), 8)
        self.assertEqual(sum(item.section == "geopolitics" for item in selected), 0)
        self.assertEqual(sum(item.section == "medicine" for item in selected), 0)

    def test_company_source_is_visibly_labelled(self):
        item = generator.Item("company", "Company result", "https://example.test", "2026-08-04", "Novartis", "economy", "company", "Europe", watchlist=["NVS"])
        briefing = {"overview": ["Brief"], "sections": {"economy": [{"title": "Story", "summary": "Text", "why_it_matters": "Relevant", "evidence": "Company report", "item_ids": ["company"]}]}}
        page = generator.render_report(date(2026, 8, 5), briefing, [item], [], "now", True)
        self.assertIn("interested-party source", page)
        self.assertIn("Sledované nástroje:</strong> NVS", page)
        self.assertNotIn("<table", page)
        self.assertNotIn("<script", page)

    def test_fixture_end_to_end_without_ai(self):
        fixture = Path(__file__).parent / "fixture.json"
        with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", ["generate_report.py", "--date", "2026-08-05", "--output", directory, "--fixture", str(fixture), "--no-ai"]):
            generator.main()
            page = (Path(directory) / "index.html").read_text()
            metadata = json.loads((Path(directory) / "report.json").read_text())
        self.assertNotIn("Global geopolitics", page)
        self.assertNotIn("Medical progress, care and longevity", page)
        self.assertFalse(metadata["ai_used"])
        self.assertEqual(metadata["selected_items"], 3)

    def test_empty_fixture_publishes_transparent_empty_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "empty.json"
            fixture.write_text("[]")
            output = Path(directory) / "output"
            with patch.object(sys, "argv", ["generate_report.py", "--date", "2026-08-05", "--output", str(output), "--fixture", str(fixture), "--no-ai"]):
                generator.main()
            page = (output / "index.html").read_text()
            metadata = json.loads((output / "report.json").read_text())
        self.assertIn("V nastavených zdrojích nebyly dostatečně relevantní nedávné ekonomické zprávy", page)
        self.assertIn("V nastavených zdrojích nebyly dostatečně relevantní zprávy", page)
        self.assertEqual(metadata["selected_items"], 0)


if __name__ == "__main__":
    unittest.main()
