#!/usr/bin/env python3
"""Merge the market and world-news components into one static article."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


def article_body(page: str) -> str:
    match = re.search(r"<article>(.*?)</article>", page, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("input page has no <article> element")
    return match.group(1).strip()


def merge_pages(market_page: str, news_page: str, report_date: str) -> str:
    market = article_body(market_page)
    news = article_body(news_page)
    news = re.sub(
        r'^\s*<p><strong>Daily World Briefing.*?</p>',
        "<h2>World news</h2>",
        news,
        count=1,
        flags=re.DOTALL | re.IGNORECASE,
    )
    safe_date = html.escape(report_date)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Market &amp; World Briefing — {safe_date}</title>
<meta name="description" content="Daily market risk, portfolio, global economy, geopolitics and medical progress briefing.">
</head><body><article>
{market}
{news}
</article></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, help="Market component directory")
    parser.add_argument("--news", required=True, help="News component directory")
    parser.add_argument("--output", default="site")
    args = parser.parse_args()

    market_dir, news_dir, output = Path(args.market), Path(args.news), Path(args.output)
    market_meta = json.loads((market_dir / "report.json").read_text(encoding="utf-8"))
    news_meta = json.loads((news_dir / "report.json").read_text(encoding="utf-8"))
    if market_meta["date"] != news_meta["date"]:
        raise SystemExit(f"component dates differ: {market_meta['date']} and {news_meta['date']}")

    output.mkdir(parents=True, exist_ok=True)
    page = merge_pages(
        (market_dir / "index.html").read_text(encoding="utf-8"),
        (news_dir / "index.html").read_text(encoding="utf-8"),
        market_meta["date"],
    )
    (output / "index.html").write_text(page, encoding="utf-8")
    combined = {
        "date": market_meta["date"],
        "generated_at": news_meta["generated_at"],
        "market": market_meta,
        "news": news_meta,
    }
    (output / "report.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    print(f"Merged market and news components into {output / 'index.html'}")


if __name__ == "__main__":
    main()
