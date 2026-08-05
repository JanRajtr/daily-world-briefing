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
    def test_threshold_interpolation(self):
        self.assertEqual(generator.bands(0, (0, 10, 20, 30, 40)), 0)
        self.assertEqual(generator.bands(15, (0, 10, 20, 30, 40)), 37.5)
        self.assertEqual(generator.bands(99, (0, 10, 20, 30, 40)), 100)

    def test_risk_labels_cover_boundaries(self):
        self.assertEqual(generator.risk_label(24.9)[0], "Low")
        self.assertEqual(generator.risk_label(25)[0], "Guarded")
        self.assertEqual(generator.risk_label(80)[0], "Severe")

    def test_curve_uses_latest_shared_date(self):
        data = {
            "DGS10": [(date(2025, 1, 2), 4.5), (date(2025, 1, 3), 4.6)],
            "DGS2": [(date(2025, 1, 2), 4.0)],
        }
        self.assertEqual(generator.curve(data), (0.5, date(2025, 1, 2)))

    def test_drawdown_uses_trailing_peak(self):
        data = {"SP500": [(date(2025, 1, 1), 100), (date(2025, 1, 2), 120), (date(2025, 1, 3), 90)]}
        value, as_of = generator.drawdown(data)
        self.assertAlmostEqual(value, -25)
        self.assertEqual(as_of, date(2025, 1, 3))

    def test_change_and_pct_change(self):
        rows = [(date(2025, 1, day), value) for day, value in enumerate((100, 105, 110, 120), 1)]
        data = {"X": rows}
        self.assertEqual(generator.change(data, "X", 3)[0], 20)
        self.assertAlmostEqual(generator.pct_change(data, "X", 3)[0], 20)

    def test_yoy_uses_twelve_month_interval(self):
        rows = [(date(2024 + i // 12, i % 12 + 1, 1), 100 + i) for i in range(13)]
        value, as_of = generator.yoy({"CPI": rows}, "CPI")
        self.assertAlmostEqual(value, 12)
        self.assertEqual(as_of, date(2025, 1, 1))

    def test_render_report_links_every_source_series(self):
        result = {
            "name": "Curve", "weight": 100, "score": 50, "value": 0.1,
            "unit": " pp", "as_of": "2025-01-02", "note": "note",
            "source_label": "source", "series": ("DGS10", "DGS2"),
        }
        page, _, _ = generator.render_report("2025-01-02", [result], [], "2025-01-02T00:00:00Z")
        self.assertIn("/series/DGS10", page)
        self.assertIn("/series/DGS2", page)
        self.assertIn("Market watchlist", page)
        self.assertNotIn("<style", page)
        self.assertNotIn("<h1", page)
        self.assertIn("Latest: +0.10 pp. Risk: 50/100.", page)

    def test_existing_report_is_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "index.html"
            metadata = Path(directory) / "report.json"
            report.write_text("original")
            metadata.write_text(json.dumps({"date": "2025-01-02"}))
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(json.dumps({series: [["2025-01-02", 1]] for metric in generator.METRICS for series in metric.series}))
            simple_metric = generator.Metric(
                "simple", ("VIXCLS",), "Simple", "Test", 100, "", "Test",
                lambda data: generator.latest(data, "VIXCLS"), lambda value: 25, "Test",
            )
            with patch.object(generator, "METRICS", (simple_metric,)), patch.object(
                sys, "argv", ["generate_report.py", "--date", "2025-01-02", "--output", directory, "--fixture", str(fixture)]
            ):
                generator.main()
            self.assertNotEqual(report.read_text(), "original")
            self.assertIn("Market Summary", report.read_text())

    def test_stock_summary_calculates_euro_watchlist_fields(self):
        rows = [(date(2025, 1, day), 100 + day) for day in range(1, 29)]
        item = generator.stock_summary(generator.Instrument("TEST.IBIS2", "TEST.DE", "Test"), rows, "EUR")
        self.assertEqual(item["price"], 128)
        self.assertGreater(item["day"], 0)
        self.assertEqual(item["from_high"], 0)
        self.assertEqual(item["trend"], "Above 50-day avg.")

    def test_stock_summary_rejects_non_euro_quote(self):
        rows = [(date(2025, 1, 1), 100), (date(2025, 1, 2), 101)]
        with self.assertRaisesRegex(ValueError, "not EUR"):
            generator.stock_summary(generator.Instrument("TEST", "TEST", "Test"), rows, "USD")

    def test_watchlist_is_rendered_before_risk_drivers(self):
        result = {
            "name": "Risk", "weight": 100, "score": 25, "value": 1,
            "unit": "", "as_of": "2025-01-02", "note": "note",
            "source_label": "source", "series": ("TEST",),
        }
        stock = {
            "label": "SPYI.IBIS2", "name": "ETF", "price": 10, "day": 1,
            "month": 2, "from_high": -3, "trend": "Above 50-day avg.", "as_of": "2025-01-02",
        }
        page, _, _ = generator.render_report("2025-01-02", [result], [], "now", [stock])
        self.assertIn("€10.00", page)
        self.assertIn("One day: +1.0%. One month: +2.0%.", page)
        self.assertNotIn("Above 50-day avg. As of", page)
        self.assertNotIn("<table", page)
        self.assertLess(page.index("Composite risk"), page.index("Market situation"))
        self.assertLess(page.index("Market situation"), page.index("Market watchlist"))
        self.assertGreater(page.index("Report date"), page.index("Sources"))
        self.assertLess(page.index("Market watchlist"), page.index("Main risk drivers"))

    def test_market_situation_summarizes_watchlist_breadth_and_extremes(self):
        stocks = [
            {"label": "A", "day": 2, "month": 5, "from_high": -1, "trend": "Above 50-day avg."},
            {"label": "B", "day": -1, "month": -4, "from_high": -9, "trend": "Below 50-day avg."},
            {"label": "C", "day": 1, "month": 2, "from_high": -3, "trend": "Above 50-day avg."},
        ]
        summary = generator.render_market_situation(stocks)
        self.assertIn("2 of 3 instruments rose and 1 fell", summary)
        self.assertIn("the median move was +1.0%", summary)
        self.assertIn("2 of 3 are above their 50-day average", summary)
        self.assertIn("Strongest over one month: A (+5.0%)", summary)
        self.assertIn("Weakest: B (-4.0%)", summary)

    def test_requested_csg_and_crypto_symbols_are_configured(self):
        configured = {instrument.label: instrument.feed_symbol for instrument in generator.WATCHLIST}
        self.assertEqual(configured["NW0.IBIS2"], "NW0.DE")
        self.assertEqual(configured["BTC"], "BTC-EUR")
        self.assertEqual(configured["ADA"], "ADA-EUR")
        self.assertNotIn("N20.IBIS2", configured)

    def test_future_report_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "argv", ["generate_report.py", "--date", "2999-01-01", "--output", directory]):
                with self.assertRaisesRegex(SystemExit, "future"):
                    generator.main()


if __name__ == "__main__":
    unittest.main()
