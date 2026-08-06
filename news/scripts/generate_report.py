#!/usr/bin/env python3
"""Collect reputable feeds and generate a source-grounded daily news briefing."""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USER_AGENT = "daily-news-briefing/1.0 (public-feed reader)"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SECTIONS = ("economy",)
DISABLED_SECTIONS = {"geopolitics", "medicine"}


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    default_section: str
    source_type: str
    region: str
    tier: int = 1
    tags: tuple[str, ...] = ()


# Primary institutions and editorially independent research sources are preferred.
# Corporate feeds are useful primary evidence, but are explicitly labelled as such.
SOURCES = (
    Source("European Central Bank", "https://www.ecb.europa.eu/rss/press.html", "economy", "official", "Europe", tags=("ECB", "rates", "euro")),
    Source("IMF", "https://news.google.com/rss/search?q=site%3Aimf.org%2Fen%2FNews+when%3A4d&hl=en&gl=US&ceid=US%3Aen", "economy", "official", "Global", tags=("macro", "trade")),
    Source("Bank for International Settlements", "https://www.bis.org/doclist/all_pressrels.rss", "economy", "official", "Global", tags=("banks", "rates")),
    Source("European Commission Press Corner", "https://ec.europa.eu/commission/presscorner/api/rss?language=en", "geopolitics", "official", "Europe", tags=("EU", "trade", "sanctions")),
    Source("Council of the European Union", "https://news.google.com/rss/search?q=site%3Aconsilium.europa.eu%2Fen%2Fpress%2Fpress-releases+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "geopolitics", "official", "Europe", tags=("EU", "sanctions", "security")),
    Source("NATO", "https://news.google.com/rss/search?q=site%3Anato.int%2Fcps%2Fen%2Fnatohq%2Fnews+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "geopolitics", "official", "Europe", tags=("NATO", "security", "defence")),
    Source("SIPRI", "https://news.google.com/rss/search?q=site%3Asipri.org+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "geopolitics", "independent-analysis", "Europe", tags=("security", "defence")),
    Source("Bruegel", "https://news.google.com/rss/search?q=site%3Abruegel.org+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "economy", "independent-analysis", "Europe", tags=("EU", "macro", "trade")),
    Source("European Medicines Agency — news", "https://www.ema.europa.eu/en/news.xml", "medicine", "regulator", "Europe", tags=("approval", "safety", "medicine")),
    Source("European Medicines Agency — new medicines", "https://www.ema.europa.eu/en/new-human-medicine-new.xml", "medicine", "regulator", "Europe", tags=("approval", "medicine")),
    Source("US FDA — drugs", "https://www.fda.gov/AboutFDA/ContactFDA/StayInformed/RSSFeeds/Drugs/rss.xml", "medicine", "regulator", "United States", tags=("approval", "safety", "medicine")),
    Source("National Cancer Institute — releases", "https://www.cancer.gov/publishedcontent/rss/syndication/rss/ncinewsreleases.rss", "medicine", "public-research", "United States", tags=("cancer", "research")),
    Source("National Cancer Institute — Cancer Currents", "https://www.cancer.gov/publishedcontent/rss/news-events/cancer-currents-blog.rss", "medicine", "public-research", "United States", tags=("cancer", "care", "research")),
    Source("National Institutes of Health", "https://news.google.com/rss/search?q=site%3Anih.gov%2Fnews-events%2Fnews-releases+when%3A7d&hl=en&gl=US&ceid=US%3Aen", "medicine", "public-research", "United States", tags=("health", "research", "longevity")),
    Source("National Eye Institute", "https://news.google.com/rss/search?q=site%3Anei.nih.gov+when%3A7d&hl=en&gl=US&ceid=US%3Aen", "medicine", "public-research", "United States", tags=("eye care", "vision", "research")),
    Source("Harvard Gazette — Health & Medicine", "https://news.google.com/rss/search?q=site%3Anews.harvard.edu%2Fgazette%2Fsection%2Fhealth+when%3A7d&hl=en&gl=US&ceid=US%3Aen", "medicine", "university", "United States", tier=2, tags=("health", "research", "longevity")),
    Source("European Society of Cardiology", "https://news.google.com/rss/search?q=site%3Aescardio.org+when%3A7d&hl=en&gl=EU&ceid=US%3Aen", "medicine", "professional-society", "Europe", tier=2, tags=("cardiovascular", "care", "research")),
    Source("Novartis", "https://news.google.com/rss/search?q=site%3Anovartis.com%2Fnews+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "economy", "company", "Europe", tier=2, tags=("NVS", "Novartis", "medicine")),
    Source("ASML", "https://news.google.com/rss/search?q=site%3Aasml.com%2Fen%2Fnews+when%3A4d&hl=en&gl=EU&ceid=US%3Aen", "economy", "company", "Europe", tier=2, tags=("ASME", "ASML", "semiconductors")),
)

WATCHLIST_TERMS = {
    "SPYI": ("global equities", "world economy", "MSCI ACWI"),
    "SPYL": ("S&P 500", "US economy", "Federal Reserve"),
    "SEC0": ("semiconductor", "chip", "TSMC", "Nvidia"),
    "XNAS": ("Nasdaq", "big tech", "technology stocks"),
    "QUTM": ("quantum computing", "quantum technology"),
    "EGLN/XAU": ("gold", "bullion", "precious metals"),
    "XAIX": ("artificial intelligence", "AI regulation", "data centre"),
    "IWMO/ZPRV": ("small cap", "market breadth", "momentum stocks"),
    "NVS": ("Novartis", "pharmaceutical"),
    "NW0": ("Czechoslovak Group", "CSG", "European defence", "ammunition"),
    "ASME": ("ASML", "semiconductor equipment", "chip export controls"),
    "BTC/ADA": ("Bitcoin", "Cardano", "cryptocurrency", "crypto regulation"),
}

MEDICAL_TERMS = (
    "cancer", "tumour", "tumor", "oncology", "cardiovascular", "heart", "stroke",
    "atherosclerosis", "glaucoma", "corneal transplant", "keratoplasty", "ocular trauma", "eye injury",
    "ophthalm", "drug", "medicine", "therapy", "treatment", "diagnostic", "clinical trial",
    "patient care", "gene therapy", "immunotherapy", "longevity", "healthy ageing", "healthy aging",
)
MEDICAL_PRIORITY_TERMS = (
    "approv", "authoris", "authoriz", "clinical trial", "phase 3", "phase iii",
    "patient", "treatment", "therapy", "medicine", "cancer", "tumour", "tumor", "oncology",
    "cardiovascular", "heart", "stroke", "glaucoma", "corneal transplant", "keratoplasty",
    "ocular trauma", "eye injury", "vision recovery", "visual rehabilitation",
    "ophthalm", "diagnos", "survival", "mortality", "prevention", "care", "gene therapy",
    "immunotherapy", "longevity", "ageing", "aging", "health", "safety warning", "recall",
)
LOW_VALUE_MEDICAL_TERMS = (
    "workshop", "webinar", "small business", "warning letter", "registered outsourcing",
    "guidance for industry", "meeting announcement", "request for comments", "application form",
    "emergency preparedness", "learn | fda",
)
MEDICAL_ADVANCE_TERMS = (
    "new ", "novel", "first ", "trial", "phase 3", "phase iii", "randomized",
    "randomised", "approv", "authoris", "authoriz", "improves", "improved", "reduces",
    "reduced", "effective", "efficacy", "treatment", "therapy", "surgery", "surgical",
    "procedure", "diagnostic", "detection", "survival", "remission", "restores", "restored",
    "breakthrough", "guideline", "recommendation", "results", "discovery", "discovers",
)
EARLY_RESEARCH_TERMS = (
    "preclinical", "animal model", "mouse model", "mice", "in vitro", "laboratory study",
    "phase 1", "phase i ", "phase ia", "phase ib", "phase 2", "phase ii ", "phase iia", "phase iib",
    "first-in-human", "first in human",
)
CLINICALLY_NEAR_TERMS = (
    "approv", "authoris", "authoriz", "recommend", "guideline", "phase 3", "phase iii",
    "randomized", "randomised", "systematic review", "meta-analysis", "standard of care",
    "clinical practice", "treatment", "therapy", "surgery", "surgical", "procedure",
    "transplant", "keratoplasty", "rehabilitation", "recovery",
)
LOW_VALUE_PAGE_TERMS = (
    "arms transfers database", "media library", "daily news ", "site map", "sitemap",
    "press contacts", "transactions under its current share buyback", "registered outsourcing",
    "staff concludes staff visit", "fund to start making investments", " - uat",
)
GEOPOLITICAL_TERMS = (
    "war", "ceasefire", "sanction", "tariff", "export control", "NATO", "Ukraine", "Russia",
    "China", "Taiwan", "Middle East", "Iran", "Israel", "trade dispute", "shipping route",
    "defence", "defense", "security", "geopolit", "election", "diplomatic",
)


@dataclass
class Item:
    id: str
    title: str
    url: str
    published: str
    source: str
    section: str
    source_type: str
    region: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    score: float = 0.0


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def node_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() in names and child.text:
            return child.text.strip()
    return ""


def item_link(node: ET.Element) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == "link":
            href = child.attrib.get("href", "")
            if href and child.attrib.get("rel", "alternate") in ("alternate", ""):
                return href
            if child.text:
                return child.text.strip()
    return node_text(node, ("guid",))


def classify(source: Source, title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if any(term.lower() in text for term in MEDICAL_TERMS):
        return "medicine"
    if any(term.lower() in text for term in GEOPOLITICAL_TERMS):
        return "geopolitics"
    return source.default_section


def in_medical_scope(text: str) -> bool:
    lowered = text.lower()
    cancer = any(term in lowered for term in ("cancer", "tumor", "tumour", "oncolog", "neoplasm", "melanoma", "leukemia", "lymphoma"))
    cardiovascular = any(term in lowered for term in ("cardiovascular", "heart", "cardiac", "stroke", "coronary", "atrial fibrillation", "atherosclerosis"))
    eye = any(term in lowered for term in (
        "glaucoma", "corneal transplant", "cornea transplant", "keratoplasty", "whole-eye transplant",
        "eye transplant", "ocular trauma", "eye injury", "ocular injury", "traumatic vision",
        "vision recovery after", "visual rehabilitation after",
    ))
    longevity = any(term in lowered for term in ("longevity", "healthy ageing", "healthy aging"))
    return cancer or cardiovascular or eye or longevity


def watchlist_matches(text: str) -> list[str]:
    lowered = text.lower()
    return [symbol for symbol, terms in WATCHLIST_TERMS.items() if any(term.lower() in lowered for term in terms)]


def parse_feed(source: Source, payload: bytes, report_date: date, lookback_days: int = 4) -> list[Item]:
    root = ET.fromstring(payload)
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
    earliest = datetime.combine(report_date - timedelta(days=lookback_days), datetime.min.time(), tzinfo=timezone.utc)
    latest = datetime.combine(report_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    items = []
    for node in nodes:
        title = clean_text(node_text(node, ("title",)))
        url = item_link(node)
        raw_date = node_text(node, ("pubdate", "published", "updated", "date"))
        published = parse_datetime(raw_date)
        summary = clean_text(node_text(node, ("description", "summary", "content", "encoded")))[:1800]
        if not title or not url or (published and not earliest <= published < latest):
            continue
        section = classify(source, title, summary)
        relevance_text = f"{title} {summary}".lower()
        if any(term in relevance_text for term in LOW_VALUE_PAGE_TERMS):
            continue
        if re.search(r"\b[a-z0-9.-]+\s+\.\s+[a-z]{2,}\s+/", title.lower()):
            continue
        if "warning-letters/" in url.lower() or re.search(r"\b\d{6}\s*-\s*\d{2}/\d{2}/\d{4}\b", title):
            continue
        if section == "medicine":
            if not in_medical_scope(relevance_text):
                continue
            if not any(term in relevance_text for term in MEDICAL_PRIORITY_TERMS):
                continue
            if any(term in relevance_text for term in LOW_VALUE_MEDICAL_TERMS):
                continue
            if any(term in relevance_text for term in EARLY_RESEARCH_TERMS):
                continue
            if not any(term in relevance_text for term in CLINICALLY_NEAR_TERMS):
                continue
            if "news.google.com/" in source.url and not any(term in relevance_text for term in MEDICAL_ADVANCE_TERMS):
                continue
        # Google News discovery feeds often index navigation pages and provide only
        # the title repeated as a description. Such records cannot support a useful
        # summary and are excluded before either AI or fallback rendering.
        if "news.google.com/" in source.url:
            extra_words = title_words(summary) - title_words(title)
            if len(extra_words) < 5:
                continue
        combined = f"{title} {summary} {' '.join(source.tags)}"
        matches = watchlist_matches(combined)
        identifier = hashlib.sha256(f"{source.name}|{url}".encode()).hexdigest()[:12]
        item = Item(
            identifier, title, url, (published or latest - timedelta(seconds=1)).date().isoformat(),
            source.name, section, source.source_type, source.region, summary,
            list(source.tags), matches,
        )
        age = max(0, (report_date - date.fromisoformat(item.published)).days)
        item.score = 100 - source.tier * 8 - age * 5 + min(len(matches) * 8, 24)
        if source.source_type in ("regulator", "public-research"):
            item.score += 8
        items.append(item)
    return items


def fetch(url: str, retries: int = 3) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(str(error))


def parse_pubmed(payload: bytes, report_date: date) -> list[Item]:
    root = ET.fromstring(payload)
    items = []
    for article in root.findall(".//PubmedArticle"):
        citation = article.find(".//MedlineCitation")
        article_node = article.find(".//Article")
        if citation is None or article_node is None:
            continue
        pmid = node_text(citation, ("pmid",))
        title_node = article_node.find("ArticleTitle")
        title = clean_text("".join(title_node.itertext())) if title_node is not None else ""
        abstract_parts = []
        for part in article_node.findall(".//Abstract/AbstractText"):
            text = clean_text("".join(part.itertext()))
            label = part.attrib.get("Label", "").title()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        summary = " ".join(abstract_parts)[:2200]
        if not pmid or not title or len(summary) < 120:
            continue
        journal = clean_text(article_node.findtext("Journal/Title", default="")) or "indexed journal"
        types = [clean_text(node.text or "") for node in article_node.findall("PublicationTypeList/PublicationType")]
        title_lower = title.lower()
        combined = f"{title} {summary}".lower()
        tags = []
        if any(term in title_lower for term in ("cancer", "tumor", "tumour", "oncolog", "neoplasm", "melanoma", "leukemia", "lymphoma")):
            tags.append("cancer")
        if any(term in title_lower for term in ("cardiovascular", "heart", "cardiac", "stroke", "coronary", "atrial fibrillation")):
            tags.append("cardiovascular")
        if any(term in title_lower for term in (
            "glaucoma", "corneal transplant", "cornea transplant", "keratoplasty", "whole-eye transplant",
            "eye transplant", "ocular trauma", "eye injury", "ocular injury", "traumatic vision",
        )):
            tags.append("eye care")
        if not tags:
            continue
        if any(term in combined for term in EARLY_RESEARCH_TERMS):
            continue
        evidence = next((value for value in types if value in ("Randomized Controlled Trial", "Clinical Trial", "Meta-Analysis", "Systematic Review", "Practice Guideline")), types[0] if types else "Journal Article")
        tags.extend(("peer reviewed", evidence))
        identifier = hashlib.sha256(f"pubmed|{pmid}".encode()).hexdigest()[:12]
        direct_advance = any(term in title_lower for term in (
            "treatment", "therapy", "surgery", "procedure", "diagnos", "detection", "survival",
            "remission", "vaccine", "transplant", "prevention", "drug", "antithrombotic",
            "intervention", "rehabilitation", "training",
        ))
        if not direct_advance and evidence != "Practice Guideline":
            continue
        score = (112 if "Randomized Controlled Trial" in types else 106) + (10 if direct_advance else 0)
        if any(term in title_lower for term in ("caregiver", "survey", "protocol")):
            score -= 12
        items.append(Item(
            identifier, title, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", report_date.isoformat(),
            f"{journal} via PubMed", "medicine", "peer-reviewed", "Global", summary,
            tags, watchlist_matches(combined), score,
        ))
    return items


def fetch_pubmed(report_date: date) -> list[Item]:
    query = (
        '(cancer[Title/Abstract] OR oncology[Title/Abstract] OR cardiovascular[Title/Abstract] '
        'OR cardiac[Title/Abstract] OR stroke[Title/Abstract] OR glaucoma[Title/Abstract] '
        'OR keratoplasty[Title/Abstract] OR corneal transplant*[Title/Abstract] '
        'OR ocular trauma[Title/Abstract] OR eye injury[Title/Abstract]) '
        'AND (clinical trial[Publication Type] OR randomized controlled trial[Publication Type] '
        'OR meta-analysis[Publication Type] OR systematic review[Publication Type] '
        'OR practice guideline[Publication Type])'
    )
    search_url = PUBMED_SEARCH + "?" + urllib.parse.urlencode({
        "db": "pubmed", "term": query, "retmode": "xml", "retmax": 24, "sort": "pub date",
        "datetype": "pdat", "mindate": (report_date - timedelta(days=4)).isoformat(), "maxdate": report_date.isoformat(),
    })
    search = ET.fromstring(fetch(search_url))
    identifiers = [node.text for node in search.findall(".//IdList/Id") if node.text]
    if not identifiers:
        return []
    fetch_url = PUBMED_FETCH + "?" + urllib.parse.urlencode({"db": "pubmed", "id": ",".join(identifiers), "retmode": "xml"})
    return parse_pubmed(fetch(fetch_url), report_date)


def title_words(title: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", title.lower()) if len(word) > 3}


def deduplicate(items: list[Item]) -> list[Item]:
    kept = []
    for item in sorted(items, key=lambda value: value.score, reverse=True):
        words = title_words(item.title)
        duplicate = False
        for prior in kept:
            other = title_words(prior.title)
            if words and other and len(words & other) / len(words | other) >= 0.72:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


def select_items(items: list[Item], limits: dict[str, int] | None = None) -> list[Item]:
    limits = limits or {"economy": 8}
    selected = []
    for section in SECTIONS:
        candidates = [item for item in items if item.section == section]
        selected.extend(sorted(candidates, key=lambda value: value.score, reverse=True)[:limits[section]])
    return selected


def ai_prompt(items: list[Item], report_date: date) -> str:
    records = []
    for item in items:
        record = {key: value for key, value in asdict(item).items() if key not in ("score",)}
        record["summary"] = record["summary"][:900]
        records.append(record)
    return f"""Vytvoř stručný denní přehled v přirozené češtině pro {report_date.isoformat()} POUZE z dodaných záznamů. Překládej a zkracuj věrně; nevymýšlej souvislosti ani fakta.
Return valid JSON, with no markdown, in this exact shape:
{{"overview":["..."],"sections":{{"economy":[STORY]}}}}
Each STORY is {{"title":"...","summary":"2-3 factual sentences in Czech","why_it_matters":"...","item_ids":["id"],"evidence":"label in Czech"}}.
Use 4-6 economy stories when material permits. Merge records about the same event and cite all their IDs. Never add a fact absent from the records. Treat company and government claims as attributed claims, not independent confirmation. Explain relevant watchlist connections without forecasting prices. If evidence is thin, omit the story. Veškerý redakční text musí být česky; vlastní názvy a názvy zdrojů mohou zůstat v originále.
RECORDS:
{json.dumps(records, ensure_ascii=False)}"""


def call_groq(items: list[Item], report_date: date, api_key: str, model: str) -> dict:
    payload = json.dumps({
        "model": model,
        "temperature": 0.1,
        "max_completion_tokens": 2800,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Jsi pečlivý český překladatel a editor zpráv. Každé tvrzení opíráš výhradně o dodané záznamy."},
            {"role": "user", "content": ai_prompt(items, report_date)},
        ],
    }).encode()
    request = urllib.request.Request(GROQ_URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())
    return json.loads(result["choices"][0]["message"]["content"])


def validate_briefing(briefing: dict, items: list[Item]) -> dict:
    valid_ids = {item.id for item in items}
    sections = briefing.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("AI output has no sections object")
    for section in SECTIONS:
        stories = sections.get(section, [])
        if not isinstance(stories, list):
            raise ValueError(f"AI output section {section} is not a list")
        clean = []
        for story in stories:
            ids = story.get("item_ids", []) if isinstance(story, dict) else []
            if story.get("title") and story.get("summary") and ids and all(identifier in valid_ids for identifier in ids):
                clean.append({
                    "title": clean_text(str(story["title"])),
                    "summary": clean_text(str(story["summary"])),
                    "why_it_matters": clean_text(str(story.get("why_it_matters", ""))),
                    "evidence": clean_text(str(story.get("evidence", ""))),
                    "item_ids": ids,
                })
        sections[section] = clean
    briefing["overview"] = [clean_text(str(value)) for value in briefing.get("overview", []) if clean_text(str(value))][:5]
    return briefing


def fallback_briefing(items: list[Item]) -> dict:
    sections = {}
    limits = {"economy": 6}
    for section in SECTIONS:
        stories = []
        for item in sorted((value for value in items if value.section == section), key=lambda value: value.score, reverse=True)[:limits[section]]:
            attribution = f"{item.source} reports: " if item.source_type in ("company", "official") else ""
            if item.watchlist:
                why = f"Watchlist relevance: {', '.join(item.watchlist)}."
            elif section == "geopolitics":
                why = "Potential relevance to European security, trade, energy or cross-border economic conditions."
            elif section == "medicine":
                focus = ", ".join(tag for tag in item.tags if tag not in ("research", "health", "medicine"))
                why = f"Relevant to the briefing's medical focus{': ' + focus if focus else ''}."
            else:
                why = "Potential relevance to global economic conditions and European markets."
            stories.append({
                "title": item.title,
                "summary": attribution + (item.summary or "The source supplied no public summary; follow the source link for details."),
                "why_it_matters": why,
                "evidence": f"Extractive fallback; {item.source_type} source",
                "item_ids": [item.id],
            })
        sections[section] = stories
    overview = (
        ["V nastavených zdrojích nebyly dostatečně relevantní nedávné ekonomické zprávy."]
        if not items else
        ["Překlad nebyl dostupný; toto vydání používá původní názvy a úryvky z veřejných kanálů."]
    )
    return {"overview": overview, "sections": sections}


def render_report(report_date: date, briefing: dict, items: list[Item], failures: list[str], generated_at: str, ai_used: bool) -> str:
    by_id = {item.id: item for item in items}
    labels = {"economy": "Světová ekonomika a portfolio"}
    overview = "".join(f"<li>{html.escape(value)}</li>" for value in briefing.get("overview", []))
    sections_html = []
    for section in SECTIONS:
        stories_html = []
        for story in briefing["sections"].get(section, []):
            sources = []
            watchlist = set()
            types = set()
            for identifier in story["item_ids"]:
                item = by_id[identifier]
                types.add(item.source_type)
                watchlist.update(item.watchlist)
                sources.append(f'<a href="{html.escape(item.url, quote=True)}">{html.escape(item.source)}</a> ({item.published})')
            label = story.get("evidence", "")
            if "company" in types:
                label = (label + "; company announcement — interested-party source").strip("; ")
            stories_html.append(
                f'<li><h3>{html.escape(story["title"])}</h3>'
                f'<p>{html.escape(story["summary"])}</p>'
                f'<p><strong>Proč je to důležité:</strong> {html.escape(story.get("why_it_matters", ""))}</p>'
                + (f'<p><strong>Sledované nástroje:</strong> {html.escape(", ".join(sorted(watchlist)))}</p>' if watchlist else "")
                + (f'<p><strong>Typ důkazu/zdroje:</strong> {html.escape(label)}</p>' if label else "")
                + f'<p><strong>Zdroje:</strong> {"; ".join(sources)}</p></li>'
            )
        empty = "<p>V nastavených zdrojích nebyly dostatečně relevantní zprávy.</p>" if not stories_html else ""
        sections_html.append(f'<h2>{labels[section]}</h2>{empty}<ol>{"".join(stories_html)}</ol>')
    warning = f'<p><strong>Upozornění zdrojů:</strong> {html.escape("; ".join(failures))}</p>' if failures else ""
    mode = "strojový překlad a shrnutí založené na zdrojích" if ai_used else "výpis původních úryvků bez překladu"
    return f'''<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Denní ekonomický přehled — {report_date.isoformat()}</title><meta name="description" content="Denní přehled světové ekonomiky a portfolia.">
</head><body><article>
<p><strong>Denní ekonomický přehled — {report_date.isoformat()}.</strong> Světová ekonomika a portfolio.</p>
<h2>Dnes stručně</h2><ul>{overview}</ul>
{''.join(sections_html)}
{warning}
<h2>Redakční metoda</h2>
<p>Ekonomické zdroje jsou řazeny podle autority a nezávislosti. Firemní a vládní tvrzení jsou vždy připsána původci a nepovažují se za nezávislé potvrzení. Geopolitické a medicínské/vědecké zprávy jsou nyní vypnuté. Přehled má informační charakter a není investičním doporučením.</p>
<p>Vygenerováno {generated_at} UTC; režim: {mode}. Systém zpracoval {len(items)} vybraných záznamů z veřejných kanálů. Odkazy vedou na původní zdroje.</p>
</article></body></html>'''


def load_fixture(path: Path) -> list[Item]:
    return [Item(**record) for record in json.loads(path.read_text(encoding="utf-8"))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output", default="site")
    parser.add_argument("--fixture", help="Offline JSON item fixture")
    parser.add_argument("--no-ai", action="store_true", help="Force deterministic extractive output")
    parser.add_argument("--model", default=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    args = parser.parse_args()
    report_date = date.fromisoformat(args.date)
    if report_date > date.today():
        raise SystemExit(f"Report date {report_date} is in the future; refusing to publish.")

    failures = []
    if args.fixture:
        items = load_fixture(Path(args.fixture))
    else:
        items = []
        for source in (value for value in SOURCES if value.default_section not in DISABLED_SECTIONS):
            try:
                parsed = parse_feed(source, fetch(source.url), report_date)
                items.extend(parsed)
                print(f"Fetched {source.name}: {len(parsed)} recent items")
            except (RuntimeError, ET.ParseError, ValueError) as exc:
                failures.append(f"{source.name}: {exc}")
                print(f"WARNING: {source.name}: {exc}", file=sys.stderr)
    items = select_items(deduplicate(items))

    api_key = os.environ.get("GROQ_API_KEY", "")
    ai_used = False
    if items and api_key and not args.no_ai:
        try:
            briefing = validate_briefing(call_groq(items, report_date, api_key, args.model), items)
            ai_used = True
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
                failures.append("AI summarization unavailable: Groq rejected GROQ_API_KEY (HTTP 401); replace the repository secret")
            elif isinstance(exc, urllib.error.HTTPError) and exc.code == 413:
                failures.append("AI summarization unavailable: Groq rejected an oversized request (HTTP 413)")
            else:
                failures.append(f"AI summarization unavailable: {exc}")
            briefing = fallback_briefing(items)
    else:
        if items and not api_key and not args.no_ai:
            failures.append("GROQ_API_KEY is not configured; used extractive fallback")
        briefing = fallback_briefing(items)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(render_report(report_date, briefing, items, failures, generated_at, ai_used), encoding="utf-8")
    metadata = {"date": report_date.isoformat(), "generated_at": generated_at, "ai_used": ai_used, "model": args.model if ai_used else None, "selected_items": len(items), "feed_failures": len(failures)}
    (output / "report.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {output / 'index.html'} from {len(items)} selected records")


if __name__ == "__main__":
    main()
