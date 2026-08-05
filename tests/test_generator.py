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
        self.assertNotIn("<table", page)
        self.assertIn('class="indicators"', page)
        self.assertIn("Latest: +0.10 pp · Risk: 50/100", page)

    def test_existing_report_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / "reports"
            reports.mkdir()
            report = reports / "2025-01-02.html"
            metadata = reports / "2025-01-02.json"
            report.write_text("original")
            metadata.write_text(json.dumps({"date": "2025-01-02"}))
            with patch.object(sys, "argv", ["generate_report.py", "--date", "2025-01-02", "--output", directory]):
                generator.main()
            self.assertEqual(report.read_text(), "original")

    def test_future_report_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "argv", ["generate_report.py", "--date", "2999-01-01", "--output", directory]):
                with self.assertRaisesRegex(SystemExit, "future"):
                    generator.main()


if __name__ == "__main__":
    unittest.main()
