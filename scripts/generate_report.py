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
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
USER_AGENT = "market-risk-briefing/1.0 (GitHub Actions; public-data reader)"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{}"


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


@dataclass(frozen=True)
class Instrument:
    label: str
    feed_symbol: str
    name: str


WATCHLIST = (
    Instrument("SPYI.IBIS2", "SPYI.DE", "SPDR MSCI ACWI IMI UCITS ETF"),
    Instrument("SPYL.IBIS2", "SPYL.DE", "SPDR S&P 500 UCITS ETF"),
    Instrument("SEC0.IBIS2", "SEC0.DE", "iShares MSCI Global Semiconductors UCITS ETF"),
    Instrument("XNAS.IBIS2", "XNAS.DE", "Xtrackers NASDAQ 100 UCITS ETF"),
    Instrument("QUTM.IBIS2", "QUTM.DE", "VanEck Quantum Computing UCITS ETF"),
    Instrument("EGLN.LSEETF", "EGLN.L", "iShares Physical Gold ETC"),
    Instrument("XAIX.IBIS2", "XAIX.DE", "Xtrackers Artificial Intelligence & Big Data UCITS ETF"),
    Instrument("IWMO.BVME.ETF", "IWMO.MI", "iShares Edge MSCI World Momentum Factor UCITS ETF"),
    Instrument("ZPRV.IBIS2", "ZPRV.DE", "SPDR MSCI USA Small Cap Value Weighted UCITS ETF"),
    Instrument("NVS.IBIS2", "NOT.DE", "Novartis"),
    Instrument("NW0.IBIS2", "NW0.DE", "Czechoslovak Group (CSG)"),
    Instrument("ASME.IBIS2", "ASME.DE", "ASML Holding"),
    Instrument("BTC", "BTC-EUR", "Bitcoin"),
    Instrument("ADA", "ADA-EUR", "Cardano"),
)


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


def fetch_chart(symbol: str, report_date: date, retries: int = 3) -> tuple[list[tuple[date, float]], str]:
    """Fetch adjusted daily closes and the trading currency from Yahoo's chart feed."""
    start = datetime.combine(report_date.replace(year=report_date.year - 2), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(report_date, datetime.max.time(), tzinfo=timezone.utc)
    query = urllib.parse.urlencode({"period1": int(start.timestamp()), "period2": int(end.timestamp()), "interval": "1d", "events": "history"})
    request = urllib.request.Request(
        f"{YAHOO_CHART.format(urllib.parse.quote(symbol, safe=''))}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (compatible; market-risk-briefing/1.0)"},
    )
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read())
            chart = payload["chart"]["result"][0]
            meta = chart["meta"]
            closes = chart["indicators"].get("adjclose", chart["indicators"]["quote"])[0].get("adjclose")
            if closes is None:
                closes = chart["indicators"]["quote"][0]["close"]
            rows = [
                (datetime.fromtimestamp(ts, timezone.utc).date(), float(close))
                for ts, close in zip(chart["timestamp"], closes)
                if close is not None and datetime.fromtimestamp(ts, timezone.utc).date() <= report_date
            ]
            if len(rows) < 2:
                raise ValueError(f"Not enough price history for {symbol}")
            return rows, meta.get("currency", "")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not fetch {symbol}: {last_error}")


def stock_summary(instrument: Instrument, rows: list[tuple[date, float]], currency: str) -> dict:
    if currency != "EUR":
        raise ValueError(f"{instrument.label} is quoted in {currency or 'an unknown currency'}, not EUR")
    trailing = rows[-252:]
    current = trailing[-1][1]
    month_base = trailing[-22][1] if len(trailing) >= 22 else trailing[0][1]
    day_base = trailing[-2][1]
    ma50 = statistics.fmean(value for _, value in trailing[-50:])
    high = max(value for _, value in trailing)
    return {
        "label": instrument.label, "name": instrument.name, "price": current,
        "day": 100 * (current / day_base - 1), "month": 100 * (current / month_base - 1),
        "from_high": 100 * (current / high - 1), "trend": "Above 50-day avg." if current >= ma50 else "Below 50-day avg.",
        "as_of": trailing[-1][0].isoformat(), "source_symbol": instrument.feed_symbol,
    }


def gold_summary(gold_rows, fx_rows):
    """Use COMEX gold as a transparent XAU proxy and convert USD/oz to EUR/oz."""
    fx_by_date = dict(fx_rows)
    shared = [(d, value / fx_by_date[d]) for d, value in gold_rows if d in fx_by_date and fx_by_date[d] > 0]
    instrument = Instrument("XAU", "GC=F", "Gold (COMEX proxy, EUR per troy ounce)")
    return stock_summary(instrument, shared, "EUR")


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


def render_market_situation(stocks):
    if not stocks:
        return "<h2>Market situation</h2><p>Watchlist data is unavailable.</p>"
    total = len(stocks)
    day_up = sum(r["day"] > 0 for r in stocks)
    day_down = sum(r["day"] < 0 for r in stocks)
    month_up = sum(r["month"] > 0 for r in stocks)
    month_down = sum(r["month"] < 0 for r in stocks)
    above_trend = sum(r["trend"].startswith("Above") for r in stocks)
    best = max(stocks, key=lambda r: r["month"])
    worst = min(stocks, key=lambda r: r["month"])
    return (
        "<h2>Market situation</h2><p>"
        f"One day: {day_up} of {total} instruments rose and {day_down} fell; "
        f"the median move was {statistics.median(r['day'] for r in stocks):+.1f}%. "
        f"One month: {month_up} rose and {month_down} fell; "
        f"the median move was {statistics.median(r['month'] for r in stocks):+.1f}%. "
        f"{above_trend} of {total} are above their 50-day average, and the median instrument is "
        f"{statistics.median(r['from_high'] for r in stocks):+.1f}% from its 52-week high. "
        f"Strongest over one month: {html.escape(best['label'])} ({best['month']:+.1f}%). "
        f"Weakest: {html.escape(worst['label'])} ({worst['month']:+.1f}%).</p>"
    )


def render_report(report_date, results, failures, generated_at, stocks=(), stock_failures=()):
    total_weight = sum(r["weight"] for r in results)
    score = sum(r["score"] * r["weight"] for r in results) / total_weight
    label, _ = risk_label(score)
    sorted_risk = sorted(results, key=lambda r: r["score"], reverse=True)
    drivers = sorted_risk[:3]
    positives = sorted(results, key=lambda r: r["score"])[:2]
    freshest = max(r["as_of"] for r in results)
    oldest = min(r["as_of"] for r in results)
    metric_items = "".join(
        f'<li><strong>{html.escape(r["name"])}.</strong> '
        f'Latest: {fmt(r["value"], r["unit"])}. Risk: {r["score"]:.0f}/100. '
        f'As of {r["as_of"]}.</li>' for r in results
    )
    driver_items = "".join(f'<li><strong>{html.escape(r["name"])}</strong>: {r["score"]:.0f}/100 — {html.escape(r["note"])}</li>' for r in drivers)
    positive_items = "".join(f'<li><strong>{html.escape(r["name"])}</strong>: {r["score"]:.0f}/100</li>' for r in positives)
    warning = ""
    if failures:
        warning = '<p><strong>Partial data:</strong> ' + html.escape("; ".join(failures)) + "</p>"
    stock_items = "".join(
        f'<li><strong>{html.escape(r["label"])} — {html.escape(r["name"])}</strong><br>'
        f'Price: €{r["price"]:,.2f}. One day: {r["day"]:+.1f}%. One month: {r["month"]:+.1f}%. '
        f'From 52-week high: {r["from_high"]:+.1f}%. {html.escape(r["trend"])}</li>'
        for r in stocks
    )
    market_situation = render_market_situation(stocks)
    stock_section = "<h2>Market watchlist</h2>"
    if stock_items:
        stock_section += f'<ul>{stock_items}</ul>'
    if stock_failures:
        stock_section += '<p><strong>Unavailable watchlist data:</strong> ' + html.escape("; ".join(stock_failures)) + "</p>"
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
<title>Market Summary — {report_date}</title><meta name="description" content="Daily market summary and rules-based risk briefing for {report_date}.">
</head><body><article>
<p><strong>Composite risk: {score:.0f}/100 — {label}.</strong> A weighted reading from {len(results)} public market and macro indicators. Higher means more defensive conditions.</p>
{market_situation}
{stock_section}
{warning}
<h2>Main risk drivers</h2><ol>{driver_items}</ol>
<h2>Relative stabilizers</h2><ul>{positive_items}</ul>
<h2>Indicator dashboard</h2><ul>{metric_items}</ul>
<h2>How to read this</h2><p>This is a mechanical monitoring signal, not a forecast or investment recommendation. Scores use fixed threshold bands and are reweighted across available indicators. Monthly and weekly series update less often than market prices.</p>
<h2>Sources</h2><ul>{source_items}</ul>
<p>Generated {generated_at} UTC. Risk observations are downloaded from FRED. Watchlist prices come from Yahoo Finance's chart feed; XAU uses COMEX gold converted to euros with EUR/USD. Methodology and thresholds are documented in the repository README.</p>
<p><strong>Report date:</strong> {report_date}. Risk data observations range from {oldest} to {freshest}.</p>
</article></body></html>''', score, label


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
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "index.html"
    meta_path = out / "report.json"

    stocks, stock_failures = [], []
    if args.fixture:
        raw = json.loads(Path(args.fixture).read_text())
        data = {k: [(date.fromisoformat(d), float(v)) for d, v in rows] for k, rows in raw.items()}
        fetch_failures = []
        stock_failures.append("watchlist omitted in offline fixture mode")
    else:
        data, fetch_failures = {}, []
        for series in sorted({s for metric in METRICS for s in metric.series}):
            try:
                data[series] = fetch_series(series)
                print(f"Fetched {series}: {len(data[series])} observations")
            except RuntimeError as exc:
                fetch_failures.append(str(exc))
                print(f"WARNING: {exc}", file=sys.stderr)
        for instrument in WATCHLIST:
            try:
                rows, currency = fetch_chart(instrument.feed_symbol, report_date)
                stocks.append(stock_summary(instrument, rows, currency))
                print(f"Fetched {instrument.label}: {len(rows)} observations")
            except (RuntimeError, ValueError, ZeroDivisionError, statistics.StatisticsError) as exc:
                stock_failures.append(f"{instrument.label}: {exc}")
                print(f"WARNING: {instrument.label}: {exc}", file=sys.stderr)
        try:
            gold_rows, gold_currency = fetch_chart("GC=F", report_date)
            fx_rows, fx_currency = fetch_chart("EURUSD=X", report_date)
            if gold_currency != "USD" or fx_currency != "USD":
                raise ValueError(f"unexpected gold/FX currencies: {gold_currency}/{fx_currency}")
            stocks.append(gold_summary(gold_rows, fx_rows))
        except (RuntimeError, ValueError, ZeroDivisionError, statistics.StatisticsError) as exc:
            stock_failures.append(f"XAU: {exc}")
            print(f"WARNING: XAU: {exc}", file=sys.stderr)

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
    page, score, label = render_report(report_date.isoformat(), results, failures, generated_at, stocks, stock_failures)
    report_path.write_text(page, encoding="utf-8")
    meta_path.write_text(json.dumps({"date": report_date.isoformat(), "score": score, "label": label, "generated_at": generated_at}, indent=2) + "\n")
    print(f"Generated {report_path} with score {score:.1f} ({label})")


if __name__ == "__main__":
    main()
