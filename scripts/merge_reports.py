#!/usr/bin/env python3
"""Merge the market and world-news components into one static article."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date
from pathlib import Path

QUOTES = (
    {
        "text": "Men are disturbed not by things, but by the views which they take of things.",
        "author": "Epictetus",
        "work": "The Enchiridion, V",
        "url": "https://www.gutenberg.org/cache/epub/45109/pg45109-images.html",
    },
    {
        "text": "Nothing can bring you peace but yourself. Nothing can bring you peace but the triumph of principles.",
        "author": "Ralph Waldo Emerson",
        "work": "Self-Reliance",
        "url": "https://www.gutenberg.org/cache/epub/2944/pg2944-images.html",
    },
    {
        "text": "Demand not that events should happen as you wish; but wish them to happen as they do happen, and you will go on well.",
        "author": "Epictetus",
        "work": "The Enchiridion, VIII",
        "url": "https://www.gutenberg.org/cache/epub/45109/pg45109-images.html",
    },
    {
        "text": "In the things which thou doest, do nothing either inconsiderately or otherwise than as justice herself would act.",
        "author": "Marcus Aurelius",
        "work": "Meditations, X.24",
        "url": "https://gutenberg.org/cache/epub/15877/pg15877-images.html",
    },
    {
        "text": "Nothing is at last sacred but the integrity of your own mind.",
        "author": "Ralph Waldo Emerson",
        "work": "Self-Reliance",
        "url": "https://www.gutenberg.org/cache/epub/2944/pg2944-images.html",
    },
    {
        "text": "Good men are happy, and the wicked miserable.",
        "author": "Cicero",
        "work": "Tusculan Disputations",
        "url": "https://www.gutenberg.org/cache/epub/14988/pg14988-images.html",
    },
    {
        "text": "Upon every accident, remember to turn toward yourself and inquire what faculty you have for its use.",
        "author": "Epictetus",
        "work": "The Enchiridion, X",
        "url": "https://www.gutenberg.org/cache/epub/45109/pg45109-images.html",
    },
)

BUDDHIST_REFLECTIONS = (
    "Notice how quickly the mind turns an event into a permanent story. A Buddhist perspective invites us to meet the event before the story: changing, conditioned and not entirely ours to control. Respond carefully to what is here, then let the unnecessary narrative loosen.",
    "Impermanence is not only the loss of pleasant things; it is also why pain, confusion and difficult circumstances can change. Remembering this can soften both grasping and despair. Care for the present without demanding that it remain still.",
    "Before reacting, observe the first movement of the mind: attraction, resistance or indifference. That small pause is already a form of freedom. It allows a response shaped by clarity and compassion rather than habit.",
    "Compassion does not require agreement or passivity. It begins by recognizing that harmful behaviour often grows from fear, confusion and craving. Set necessary boundaries while resisting the temptation to make another person less human.",
    "The idea of non-self can be approached practically: no mood, role, success or failure is the whole of you. Experience arises from many changing conditions. Hold identity lightly enough to learn, repair and begin again.",
    "Equanimity is not emotional numbness. It is the steadiness that lets joy be joyful and pain be painful without being carried away by either. A balanced mind can care more effectively because it sees more clearly.",
    "Attention is a form of stewardship. What we repeatedly attend to becomes the climate of the mind. Today, choose deliberately which fears deserve action, which pleasures deserve gratitude and which distractions can be released.",
)


def daily_reflection(report_date: str) -> str:
    day = date.fromisoformat(report_date).toordinal()
    quote = QUOTES[day % len(QUOTES)]
    reflection = BUDDHIST_REFLECTIONS[day % len(BUDDHIST_REFLECTIONS)]
    return (
        '<h2>Daily reflection</h2>'
        f'<blockquote><p>“{html.escape(quote["text"])}”</p>'
        f'<p>— {html.escape(quote["author"])}, '
        f'<a href="{html.escape(quote["url"], quote=True)}">{html.escape(quote["work"])}</a></p></blockquote>'
        f'<p><strong>A Buddhist perspective:</strong> {html.escape(reflection)}</p>'
    )


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
    reflection = daily_reflection(report_date)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Market &amp; World Briefing — {safe_date}</title>
<meta name="description" content="Daily market risk, portfolio, global economy, geopolitics and medical progress briefing.">
</head><body><article>
{reflection}
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
