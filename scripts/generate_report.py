#!/usr/bin/env python3
"""Fetch public FRED data and generate a static daily market-risk briefing."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
USER_AGENT = "market-risk-briefing/1.0 (GitHub Actions; public-data reader)"


@dataclass(frozen=True)
class Metric:
    key: str
    series: tuple[str, ...]
    name: str
    section: str
    weight: float
    unit: str
    source_label: str
    transform: Callable[[dict[str, list[tuple[date, float]]]], tuple[float, date]]
    score: Callable[[float], float]
    note: str


def latest(data, series):
    d, v = data[series][-1]
    return v, d


def curve(data):
    shared = sorted(set(dict(data["DGS10"])) & set(dict(data["DGS2"])))
    if not shared:
        raise ValueError("No shared observation date for DGS10 and DGS2")
    d = shared[-1]
    return dict(data["DGS10"])[d] - dict(data["DGS2"])[d], d


def drawdown(data):
    values = data["SP500"][-252:]
    d, current = values[-1]
    peak = max(v for _, v in values)
    return 100 * (current / peak - 1), d


def volatility(data):
    values = data["SP500"][-31:]
    returns = [math.log(values[i][1] / values[i - 1][1]) for i in range(1, len(values))]
    return statistics.stdev(returns) * math.sqrt(252) * 100, values[-1][0]


def change(data, series, periods):
    values = data[series]
    if len(values) <= periods:
        raise ValueError(f"Not enough observations for {series}")
    return values[-1][1] - values[-1 - periods][1], values[-1][0]


def pct_change(data, series, periods):
    values = data[series]
    if len(values) <= periods:
        raise ValueError(f"Not enough observations for {series}")
    return 100 * (values[-1][1] / values[-1 - periods][1] - 1), values[-1][0]


def yoy(data, series):
    values = data[series]
    if len(values) < 13:
        raise ValueError(f"Not enough observations for {series}")
    return 100 * (values[-1][1] / values[-13][1] - 1), values[-1][0]


def bands(value, boundaries):
    """Linearly interpolate a 0–100 risk score through five threshold points."""
    points = [(boundaries[i], i * 25.0) for i in range(5)]
    if value <= points[0][0]:
        return 0.0
    if value >= points[-1][0]:
        return 100.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            return y0 + (value - x0) * (y1 - y0) / (x1 - x0)
    raise AssertionError("unreachable")


METRICS = (
    Metric("vix", ("VIXCLS",), "VIX", "Market stress", 18, "", "CBOE via FRED", lambda d: latest(d, "VIXCLS"), lambda v: bands(v, (12, 17, 22, 30, 45)), "Equity-implied volatility"),
    Metric("drawdown", ("SP500",), "S&P 500 drawdown", "Market stress", 14, "%", "S&P Dow Jones Indices via FRED", drawdown, lambda v: bands(-v, (0, 5, 10, 20, 35)), "From trailing 252-session high"),
    Metric("volatility", ("SP500",), "S&P 500 realized vol.", "Market stress", 10, "%", "S&P Dow Jones Indices via FRED", volatility, lambda v: bands(v, (8, 13, 18, 25, 40)), "30-session annualized volatility"),
    Metric("hy_oas", ("BAMLH0A0HYM2",), "US high-yield spread", "Credit & liquidity", 18, "%", "ICE BofA via FRED", lambda d: latest(d, "BAMLH0A0HYM2"), lambda v: bands(v, (2.5, 3.5, 5, 7.5, 11)), "Option-adjusted spread"),
    Metric("nfci", ("NFCI",), "Chicago Fed NFCI", "Credit & liquidity", 10, "", "Federal Reserve Bank of Chicago via FRED", lambda d: latest(d, "NFCI"), lambda v: bands(v, (-0.8, -0.4, 0, 0.5, 1.2)), "Above zero means tighter conditions"),
    Metric("curve", ("DGS10", "DGS2"), "US 10Y–2Y curve", "Macro", 8, " pp", "Federal Reserve Board via FRED", curve, lambda v: bands(-v, (-1.5, -0.5, 0, 0.5, 1.5)), "Inversion raises cyclical risk"),
    Metric("unemployment", ("UNRATE",), "Unemployment change", "Macro", 8, " pp", "U.S. Bureau of Labor Statistics via FRED", lambda d: change(d, "UNRATE", 3), lambda v: bands(v, (-0.2, 0, 0.3, 0.6, 1.2)), "Change over three monthly observations"),
    Metric("inflation", ("CPIAUCSL",), "US CPI inflation", "Macro", 8, "%", "U.S. Bureau of Labor Statistics via FRED", lambda d: yoy(d, "CPIAUCSL"), lambda v: bands(v, (1, 2, 3, 5, 8)), "Year-over-year"),
    Metric("oil", ("DCOILBRENTEU",), "Brent oil shock", "Commodities", 6, "%", "U.S. EIA via FRED", lambda d: pct_change(d, "DCOILBRENTEU", 20), lambda v: bands(abs(v), (0, 5, 10, 20, 35)), "Absolute 20-observation price move"),
)


def fetch_series(series_id: str, retries: int = 3) -> list[tuple[date, float]]:
    request = urllib.request.Request(FRED_CSV.format(series_id), headers={"User-Agent": USER_AGENT})
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            result = []
            for row in rows:
                raw = row.get(series_id, ".")
                if raw not in (None, "", "."):
                    raw_date = row.get("observation_date") or row.get("DATE")
                    if not raw_date:
                        raise ValueError(f"Unexpected FRED CSV columns: {', '.join(row)}")
                    result.append((date.fromisoformat(raw_date), float(raw)))
            if not result:
                raise ValueError(f"FRED returned no observations for {series_id}")
            return result
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch {series_id}: {last_error}")


def risk_label(score):
    if score < 25:
        return "Low", "low"
    if score < 45:
        return "Guarded", "guarded"
    if score < 65:
        return "Elevated", "elevated"
    if score < 80:
        return "High", "high"
    return "Severe", "severe"


def fmt(value, unit):
    suffix = unit if unit.startswith(" ") else (f"{unit}" if unit else "")
    return f"{value:+.2f}{suffix}" if value < 0 or unit == " pp" else f"{value:.2f}{suffix}"


def render_report(report_date, results, failures, generated_at):
    total_weight = sum(r["weight"] for r in results)
    score = sum(r["score"] * r["weight"] for r in results) / total_weight
    label, cls = risk_label(score)
    sorted_risk = sorted(results, key=lambda r: r["score"], reverse=True)
    drivers = sorted_risk[:3]
    positives = sorted(results, key=lambda r: r["score"])[:2]
    freshest = max(r["as_of"] for r in results)
    oldest = min(r["as_of"] for r in results)
    metric_rows = "".join(
        f'<tr><th scope="row">{html.escape(r["name"])}</th><td>{fmt(r["value"], r["unit"])}</td>'
        f'<td>{r["score"]:.0f}/100</td><td>{r["as_of"]}</td></tr>' for r in results
    )
    driver_items = "".join(f'<li><strong>{html.escape(r["name"])}</strong>: {r["score"]:.0f}/100 — {html.escape(r["note"])}</li>' for r in drivers)
    positive_items = "".join(f'<li><strong>{html.escape(r["name"])}</strong>: {r["score"]:.0f}/100</li>' for r in positives)
    warning = ""
    if failures:
        warning = '<aside class="warning"><strong>Partial data:</strong> ' + html.escape("; ".join(failures)) + "</aside>"
    source_items = "".join(
        f'<li>{html.escape(r["name"])} — {html.escape(r["source_label"])} ('
        + ", ".join(
            f'<a href="https://fred.stlouisfed.org/series/{series}">{series}</a>'
            for series in r["series"]
        )
        + ")</li>"
        for r in results
    )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Risk Briefing — {report_date}</title><meta name="description" content="Daily rules-based market risk briefing for {report_date}.">
<style>
:root{{--ink:#182026;--muted:#5a6770;--paper:#fff;--line:#d9e0e4;--accent:#173f5f;--low:#277a51;--guarded:#7c6b17;--elevated:#a85b0b;--high:#a12b2b;--severe:#671616}}
*{{box-sizing:border-box}} body{{margin:0;background:#f4f1ea;color:var(--ink);font:18px/1.62 Georgia,serif}}
article{{max-width:760px;margin:0 auto;background:var(--paper);padding:3.2rem 3rem 4rem}} h1,h2{{font-family:system-ui,sans-serif;line-height:1.18}} h1{{font-size:2.15rem;margin:.2rem 0}} h2{{font-size:1.35rem;margin-top:2.2rem;border-bottom:1px solid var(--line);padding-bottom:.35rem}}
.kicker,.meta{{font-family:system-ui,sans-serif;color:var(--muted);font-size:.86rem;letter-spacing:.03em}} .score{{border-left:.55rem solid var(--{cls});padding:.8rem 1.1rem;margin:1.6rem 0;background:#f7f8f8}}
.score strong{{font:700 2rem/1 system-ui,sans-serif;color:var(--{cls})}} table{{border-collapse:collapse;width:100%;font-size:.9rem}} th,td{{border-bottom:1px solid var(--line);padding:.65rem .35rem;text-align:left;vertical-align:top}} td:nth-child(n+2){{white-space:nowrap}} .warning{{background:#fff4d8;padding:.8rem 1rem;border:1px solid #e9cf86}} a{{color:var(--accent)}} footer{{color:var(--muted);font-size:.82rem;margin-top:2.5rem;border-top:1px solid var(--line);padding-top:1rem}}
@media(max-width:620px){{article{{padding:1.5rem 1.05rem 2.5rem}}body{{font-size:17px}}h1{{font-size:1.75rem}}table{{font-size:.78rem}}th,td{{padding:.5rem .2rem}}}}
</style></head><body><article>
<header><div class="kicker">DAILY • RULES-BASED • NO AI</div><h1>Market Risk Briefing</h1><p class="meta">{report_date} · Data observations {oldest} to {freshest}</p></header>
<section class="score" aria-label="Overall risk score"><div class="kicker">COMPOSITE RISK</div><strong>{score:.0f}/100 — {label}</strong><p>A weighted reading from {len(results)} public market and macro indicators. Higher means more defensive conditions.</p></section>
{warning}
<h2>Main risk drivers</h2><ol>{driver_items}</ol>
<h2>Relative stabilizers</h2><ul>{positive_items}</ul>
<h2>Indicator dashboard</h2><table><thead><tr><th>Indicator</th><th>Latest</th><th>Risk</th><th>As of</th></tr></thead><tbody>{metric_rows}</tbody></table>
<h2>How to read this</h2><p>This is a mechanical monitoring signal, not a forecast or investment recommendation. Scores use fixed threshold bands and are reweighted across available indicators. Monthly and weekly series update less often than market prices.</p>
<h2>Sources</h2><ul>{source_items}</ul>
<footer>Generated {generated_at} UTC. Public observations are downloaded from FRED. Methodology and thresholds are documented in the repository README.</footer>
</article></body></html>''', score, label


def render_index(entries):
    items = "".join(f'<li><a href="reports/{e["date"]}.html">{e["date"]}</a> — {e["score"]:.0f}/100, {html.escape(e["label"])}</li>' for e in entries)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Risk Briefing archive</title><style>body{{max-width:720px;margin:3rem auto;padding:0 1rem;font:18px/1.65 Georgia,serif;color:#182026}}h1{{font:700 2rem/1.2 system-ui,sans-serif}}li{{margin:.55rem 0}}a{{color:#173f5f}}</style></head><body><main><h1>Market Risk Briefing</h1><p>Daily, rules-based market and macro risk monitoring using public data. No generative AI.</p><h2>Archive</h2><ol>{items or '<li>No reports yet.</li>'}</ol></main></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(), help="Report date (YYYY-MM-DD)")
    parser.add_argument("--output", default="site", help="Static-site output directory")
    parser.add_argument("--fixture", help="Optional JSON fixture for offline tests")
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    if report_date > date.today():
        raise SystemExit(f"Report date {report_date} is in the future; refusing to publish.")
    out = Path(args.output)
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{report_date}.html"
    meta_path = reports_dir / f"{report_date}.json"
    if report_path.exists() or meta_path.exists():
        if not (report_path.exists() and meta_path.exists()):
            raise SystemExit(f"Incomplete existing report for {report_date}; refusing to overwrite it.")
        print(f"Report for {report_date} already exists; preserving the immutable archive entry.")
        return

    if args.fixture:
        raw = json.loads(Path(args.fixture).read_text())
        data = {k: [(date.fromisoformat(d), float(v)) for d, v in rows] for k, rows in raw.items()}
        fetch_failures = []
    else:
        data, fetch_failures = {}, []
        for series in sorted({s for metric in METRICS for s in metric.series}):
            try:
                data[series] = fetch_series(series)
                print(f"Fetched {series}: {len(data[series])} observations")
            except RuntimeError as exc:
                fetch_failures.append(str(exc))
                print(f"WARNING: {exc}", file=sys.stderr)

    # A manually generated historical report must never look ahead. This also
    # removes any unexpectedly future-dated observation from a live response.
    data = {series: [(d, v) for d, v in rows if d <= report_date] for series, rows in data.items()}
    for series in [series for series, rows in data.items() if not rows]:
        del data[series]

    results, failures = [], []
    for metric in METRICS:
        if any(s not in data for s in metric.series):
            failures.append(f"{metric.name} unavailable")
            continue
        try:
            value, as_of = metric.transform(data)
            results.append({**metric.__dict__, "value": value, "as_of": as_of.isoformat(), "score": max(0, min(100, metric.score(value)))})
        except (ValueError, ZeroDivisionError, statistics.StatisticsError) as exc:
            failures.append(f"{metric.name}: {exc}")
    if sum(r["weight"] for r in results) < 50:
        raise SystemExit("Too few indicators were available (less than 50% of configured weight); refusing to publish.")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    page, score, label = render_report(report_date.isoformat(), results, failures, generated_at)
    report_path.write_text(page, encoding="utf-8")
    meta_path.write_text(json.dumps({"date": report_date.isoformat(), "score": score, "label": label, "generated_at": generated_at}, indent=2) + "\n")
    entries = []
    for path in reports_dir.glob("????-??-??.json"):
        try:
            entries.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError):
            print(f"WARNING: ignoring invalid metadata {path}", file=sys.stderr)
    entries.sort(key=lambda e: e["date"], reverse=True)
    (out / "index.html").write_text(render_index(entries), encoding="utf-8")
    (out / "latest.html").write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=reports/' + report_date.isoformat() + '.html"><link rel="canonical" href="reports/' + report_date.isoformat() + '.html">', encoding="utf-8")
    print(f"Generated {reports_dir / f'{report_date}.html'} with score {score:.1f} ({label})")


if __name__ == "__main__":
    main()
