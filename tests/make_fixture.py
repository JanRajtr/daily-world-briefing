#!/usr/bin/env python3
"""Create deterministic synthetic FRED-shaped observations for an offline smoke test."""
import json
from datetime import date, timedelta
from pathlib import Path

start = date(2023, 1, 1)
days = [(start + timedelta(days=i)).isoformat() for i in range(420)]
months = [date(2022 + i // 12, i % 12 + 1, 1).isoformat() for i in range(38)]
fixture = {
    "VIXCLS": [[d, 18 + (i % 7) / 10] for i, d in enumerate(days)],
    "SP500": [[d, 4200 + i * 3 - max(0, i - 390) * 8] for i, d in enumerate(days)],
    "BAMLH0A0HYM2": [[d, 3.4 + (i % 5) / 20] for i, d in enumerate(days)],
    "NFCI": [[days[i], -0.35 + i / 5000] for i in range(0, 420, 7)],
    "DGS10": [[d, 4.25] for d in days], "DGS2": [[d, 4.05] for d in days],
    "UNRATE": [[d, 3.8 + i * 0.02] for i, d in enumerate(months)],
    "CPIAUCSL": [[d, 300 * (1.0025 ** i)] for i, d in enumerate(months)],
    "DCOILBRENTEU": [[d, 78 + (i % 30) * 0.15] for i, d in enumerate(days)],
}
Path("work/fixture.json").parent.mkdir(exist_ok=True)
Path("work/fixture.json").write_text(json.dumps(fixture))
