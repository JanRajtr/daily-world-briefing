#!/usr/bin/env python3
"""Merge the market and world-news components into one static article."""

from __future__ import annotations

import argparse
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CALENDAR_URL = "https://svatkyapi.netlify.app/api/day"
CNB_URL = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.xml"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
WEATHER_LOCATIONS = {"Horoměřice": (50.1317, 14.3388), "Prague": (50.0755, 14.4378), "Česká Lípa": (50.6855, 14.5376)}
WEATHER_CODES = {0: "Jasno", 1: "Převážně jasno", 2: "Polojasno", 3: "Zataženo", 45: "Mlha", 48: "Mrznoucí mlha", 51: "Slabé mrholení", 53: "Mrholení", 55: "Silné mrholení", 61: "Slabý déšť", 63: "Déšť", 65: "Silný déšť", 71: "Slabé sněžení", 73: "Sněžení", 75: "Silné sněžení", 80: "Slabé přeháňky", 81: "Přeháňky", 82: "Silné přeháňky", 85: "Slabé sněhové přeháňky", 86: "Silné sněhové přeháňky", 95: "Bouřky", 96: "Bouřky s kroupami", 99: "Silné bouřky s kroupami"}

def sourced_item(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    clean = {key: str(value.get(key, "")).strip() for key in ("text", "author", "work", "url")}
    return clean if all(clean.values()) and clean["url"].startswith(("https://", "http://")) else None


def daily_reflection(content: dict) -> str:
    items = [item for item in (sourced_item(content.get("quote")), sourced_item(content.get("buddhist_teaching"))) if item]
    if not items:
        return ""
    blocks = "".join(
        f'<blockquote><p>„{html.escape(item["text"])}“</p><p>— {html.escape(item["author"])}, '
        f'<a href="{html.escape(item["url"], quote=True)}">{html.escape(item["work"])}</a></p></blockquote>'
        for item in items
    )
    return f'<h2>Myšlenka dne</h2>{blocks}<p><small>Český překlad; odkazy vedou na použité webové zdroje.</small></p>'


def article_body(page: str) -> str:
    match = re.search(r"<article>(.*?)</article>", page, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("input page has no <article> element")
    return match.group(1).strip()


def fetch_weather_location(name: str, latitude: float, longitude: float, report_date: str) -> dict:
    query = urllib.parse.urlencode({"latitude": latitude, "longitude": longitude, "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max,uv_index_max,sunrise,sunset", "timezone": "Europe/Prague", "start_date": report_date, "end_date": report_date})
    request = urllib.request.Request(f"{WEATHER_URL}?{query}", headers={"User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        daily = json.loads(response.read())["daily"]
    return {"name": name, "condition": WEATHER_CODES.get(int(daily["weather_code"][0]), "Smíšené podmínky"), "minimum_c": round(float(daily["temperature_2m_min"][0])), "maximum_c": round(float(daily["temperature_2m_max"][0])), "rain_probability": round(float(daily["precipitation_probability_max"][0])), "rain_mm": round(float(daily["precipitation_sum"][0]), 1), "wind_kmh": round(float(daily["wind_speed_10m_max"][0])), "uv_index": round(float(daily["uv_index_max"][0]), 1), "sunrise": daily["sunrise"][0].rsplit("T", 1)[-1], "sunset": daily["sunset"][0].rsplit("T", 1)[-1]}


def fetch_weather(report_date: str) -> list[dict]:
    forecasts = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch_weather_location, name, latitude, longitude, report_date): name for name, (latitude, longitude) in WEATHER_LOCATIONS.items()}
        for future in as_completed(futures):
            try:
                forecasts.append(future.result())
            except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                pass
    order = {name: index for index, name in enumerate(WEATHER_LOCATIONS)}
    return sorted(forecasts, key=lambda forecast: order[forecast["name"]])


def fetch_calendar(report_date: str) -> dict:
    request = urllib.request.Request(f"{CALENDAR_URL}/{report_date}", headers={"User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        value = json.loads(response.read())
    return {key: value.get(key) for key in ("date", "dayInWeek", "name", "isHoliday", "holidayName")}


def fetch_fx_rates(report_date: str) -> dict:
    def load(value: str) -> tuple[str, dict]:
        formatted = ".".join(reversed(value.split("-")))
        request = urllib.request.Request(f"{CNB_URL}?date={formatted}", headers={"User-Agent": "daily-world-briefing/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
        rates = {}
        for row in root.iter():
            code = row.attrib.get("kod")
            if code in ("EUR", "USD", "CHF"):
                amount = float(row.attrib.get("mnozstvi", "1").replace(",", "."))
                rates[code] = float(row.attrib["kurz"].replace(",", ".")) / amount
        return root.attrib.get("datum", value), rates
    current_date, rates = load(report_date)
    prior_date = (date.fromisoformat(report_date) - timedelta(days=30)).isoformat()
    _, prior = load(prior_date)
    monthly = {code: 100 * (value / prior[code] - 1) for code, value in rates.items() if code in prior and prior[code]}
    return {"date": current_date, "rates": rates, "monthly": monthly}


def fetch_air_quality_location(name: str, latitude: float, longitude: float) -> dict:
    hourly = "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,ragweed_pollen"
    query = urllib.parse.urlencode({"latitude": latitude, "longitude": longitude, "current": "european_aqi,pm2_5", "hourly": hourly, "timezone": "Europe/Prague", "forecast_days": 1})
    request = urllib.request.Request(f"{AIR_QUALITY_URL}?{query}", headers={"User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        value = json.loads(response.read())
    pollen = {}
    for key, label in (("alder_pollen", "olše"), ("birch_pollen", "bříza"), ("grass_pollen", "trávy"), ("mugwort_pollen", "pelyněk"), ("ragweed_pollen", "ambrozie")):
        readings = [float(number) for number in value.get("hourly", {}).get(key, []) if number is not None]
        if readings and max(readings) >= 10:
            pollen[label] = round(max(readings), 1)
    current = value["current"]
    return {"name": name, "aqi": round(float(current["european_aqi"])), "pm25": round(float(current["pm2_5"]), 1), "pollen": pollen}


def fetch_air_quality() -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(fetch_air_quality_location, name, latitude, longitude) for name, (latitude, longitude) in WEATHER_LOCATIONS.items()]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
    order = {name: index for index, name in enumerate(WEATHER_LOCATIONS)}
    return sorted(results, key=lambda item: order[item["name"]])


def validate_daily_content(content: dict) -> dict:
    if not isinstance(content, dict):
        raise ValueError("daily content is not an object")
    clean = {}
    for key in ("quote", "buddhist_teaching"):
        item = sourced_item(content.get(key))
        if item:
            clean[key] = item
    tip = sourced_item(content.get("longevity_tip"))
    if tip:
        clean["longevity_tip"] = tip
    note = sourced_item(content.get("cultural_note"))
    if note:
        clean["cultural_note"] = note
    for key in ("portfolio_events", "market_explanations"):
        values = []
        for value in content.get(key, []) if isinstance(content.get(key), list) else []:
            if not isinstance(value, dict):
                continue
            item = {field: str(value.get(field, "")).strip() for field in ("title", "text", "url")}
            if all(item.values()) and item["url"].startswith(("https://", "http://")):
                values.append(item)
        if values:
            clean[key] = values[:4]
    meals = content.get("meals")
    if isinstance(meals, list) and len(meals) == 4:
        expected = ("Breakfast", "Lunch", "Snack", "Dinner")
        clean_meals = []
        for expected_meal, meal in zip(expected, meals):
            if not isinstance(meal, dict) or str(meal.get("meal", "")).strip().lower() != expected_meal.lower():
                clean_meals = []
                break
            values = {key: str(meal.get(key, "")).strip() for key in ("name", "recipe", "source_title", "source_url")}
            if not all(values.values()) or len(values["recipe"]) < 80 or not values["source_url"].startswith(("https://", "http://")):
                clean_meals = []
                break
            clean_meals.append({"meal": expected_meal, **values})
        if clean_meals:
            clean["meals"] = clean_meals
    return clean


def request_groq_json(prompt: str, api_key: str, model: str) -> dict:
    payload = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"}, "compound_custom": {"tools": {"enabled_tools": ["web_search", "visit_website"]}}, "messages": [{"role": "system", "content": "Jsi přesný český rešeršér a překladatel. Bez dohledatelného webového zdroje obsah vynecháš."}, {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(GROQ_URL, data=payload, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())
    value = json.loads(result["choices"][0]["message"]["content"])
    if not isinstance(value, dict):
        raise ValueError("Groq response is not a JSON object")
    return value


def retry_delay(error: BaseException, attempt: int, maximum: float = 120.0) -> float:
    """Honor Groq's Retry-After header, with exponential backoff as a fallback."""
    value = error.headers.get("Retry-After") if isinstance(error, urllib.error.HTTPError) and error.headers else None
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = 0
        if delay > 0:
            return min(delay, maximum)
    return min(5.0 * 2 ** (attempt - 1), maximum)


def generate_with_retries(label: str, prompt: str, api_key: str, model: str, attempts: int = 3) -> tuple[dict, dict]:
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            return request_groq_json(prompt, api_key, model), {"status": "ok", "attempts": attempt}
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = f"{type(error).__name__}: {error}"
            print(f"Groq {label} attempt {attempt}/{attempts} failed: {last_error}", file=sys.stderr)
            if attempt < attempts:
                delay = retry_delay(error, attempt)
                print(f"Groq {label} retrying in {delay:g} seconds", file=sys.stderr)
                time.sleep(delay)
    return {}, {"status": "error", "attempts": attempts, "reason": last_error}


def generate_daily_content(report_date: str, api_key: str, model: str) -> tuple[dict, dict]:
    reflection_prompt = f'''Pro den {report_date} použij webové vyhledávání a vrať pouze platný JSON. Najdi (1) ověřitelný historický citát a (2) skutečné, konkrétní učení buddhistického učitele nebo badatele. Vše věrně přelož nebo shrň do češtiny; nic nevymýšlej. Každá položka musí mít autora, dílo nebo kontext a přímý webový zdroj. Pokud položku neověříš, vrať null.
Schéma: {{"quote":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null,"buddhist_teaching":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null}}.'''
    extras_prompt = f'''Pro den {report_date} použij webové vyhledávání a vrať pouze platný JSON. Najdi: (1) čtyři zdravé recepty pro jednoho dospělého dostupné v Česku, (2) praktické doporučení pro zdravé stárnutí z autoritativního zdravotnického zdroje, (3) dnešní či bezprostředně nadcházející události relevantní pro portfolio a makroekonomiku, (4) vysvětlení pouze skutečně neobvyklých dnešních pohybů sledovaných trhů a (5) kvalitní českou kulturní nebo historickou poznámku vztahující se k datu. Sledované nástroje a témata jsou SPYI, SPYL, SEC0, XNAS, QUTM, EGLN/XAU, XAIX, IWMO, ZPRV, NVS, NW0/CSG, ASME/ASML, BTC a ADA. Vše věrně přelož nebo shrň do češtiny; nic nevymýšlej. Každá položka musí mít přímý webový zdroj. Pokud některou část nenajdeš, neověříš nebo není relevantní, vrať null či prázdné pole.
Schéma: {{"meals":[{{"meal":"Breakfast|Lunch|Snack|Dinner","name":"...","recipe":"úplný recept s množstvím a časy","source_title":"...","source_url":"https://..."}}]|null,"longevity_tip":{{"text":"...","author":"organizace nebo autor","work":"název stránky","url":"https://..."}}|null,"portfolio_events":[{{"title":"...","text":"...","url":"https://..."}}],"market_explanations":[{{"title":"...","text":"...","url":"https://..."}}],"cultural_note":{{"text":"...","author":"instituce nebo autor","work":"název stránky","url":"https://..."}}|null}}. Pole meal musí být v pořadí Breakfast, Lunch, Snack, Dinner.'''
    reflection, reflection_status = generate_with_retries("reflection", reflection_prompt, api_key, model)
    extras, extras_status = generate_with_retries("extras", extras_prompt, api_key, model)
    content = validate_daily_content({**extras, **reflection})
    reflection_status["quote"] = "present" if content.get("quote") else "omitted"
    reflection_status["buddhist_teaching"] = "present" if content.get("buddhist_teaching") else "omitted"
    if reflection_status["status"] == "ok" and reflection_status["buddhist_teaching"] == "omitted":
        reflection_status["buddhist_teaching_reason"] = "Groq returned no teaching with text, author, work, and a valid source URL"
    return content, {"model": model, "reflection": reflection_status, "extras": extras_status}


def wellbeing_sections(report_date: str, weather: list[dict], content: dict, calendar: dict | None = None, fx: dict | None = None, air_quality: list[dict] | None = None) -> str:
    meal_labels = {"Breakfast": "Snídaně", "Lunch": "Oběd", "Snack": "Svačina", "Dinner": "Večeře"}
    weather_items = "".join(f'<li><h3>{html.escape(item["name"])}</h3><p>{html.escape(item["condition"])}. {item["minimum_c"]}–{item["maximum_c"]} °C. <strong>Déšť:</strong> pravděpodobnost {item["rain_probability"]} %, očekávaný úhrn {item.get("rain_mm", 0)} mm. Vítr až {item["wind_kmh"]} km/h. Východ slunce {html.escape(item["sunrise"])}; západ {html.escape(item["sunset"])}.</p></li>' for item in weather)
    sections = []
    if calendar and calendar.get("name"):
        holiday = f' Státní svátek: <strong>{html.escape(str(calendar["holidayName"]))}</strong>.' if calendar.get("isHoliday") and calendar.get("holidayName") else ""
        tomorrow = f' Zítra má svátek <strong>{html.escape(str(calendar["tomorrow"]["name"]))}</strong>.' if calendar.get("tomorrow", {}).get("name") else ""
        sections.append(f'<h2>Český kalendář</h2><p>{html.escape(str(calendar.get("dayInWeek", "")).capitalize())}: svátek má <strong>{html.escape(str(calendar["name"]))}</strong>.{holiday}{tomorrow}</p><p><small>Zdroj: <a href="https://svatkyapi.cz/">Svatky API</a>.</small></p>')
    if weather_items:
        sections.append(f'''<h2>Dnešní počasí</h2><ul>{weather_items}</ul>
<p><small>Zdroj předpovědi: Open-Meteo; datum {html.escape(report_date)}, místní čas Europe/Prague. Podmínky se mohou změnit.</small></p>''')
        alerts = []
        for item in weather:
            conditions = []
            if item["minimum_c"] <= 0: conditions.append("mráz")
            if item["maximum_c"] >= 30: conditions.append("horko")
            if item["wind_kmh"] >= 50: conditions.append("silný vítr")
            if item["rain_mm"] >= 20: conditions.append("vydatný déšť")
            if item.get("uv_index", 0) >= 6: conditions.append(f'vysoké UV {item["uv_index"]}')
            if conditions:
                alerts.append(f'<li><strong>{html.escape(item["name"])}</strong>: {html.escape(", ".join(conditions))}</li>')
        if alerts:
            sections.append(f'<h2>Dnes vyžaduje pozornost</h2><ul>{"".join(alerts)}</ul>')
    if air_quality:
        items = []
        for item in air_quality:
            pollen = ", ".join(f"{name} {value:g} zrn/m³" for name, value in item["pollen"].items())
            extra = f" Zvýšený pyl: {pollen}." if pollen else ""
            items.append(f'<li><strong>{html.escape(item["name"])}</strong>: evropský index kvality ovzduší {item["aqi"]}, PM2,5 {item["pm25"]:g} µg/m³.{html.escape(extra)}</li>')
        sections.append(f'<h2>Ovzduší a pyl</h2><ul>{"".join(items)}</ul><p><small>Modelová data: <a href="https://open-meteo.com/en/docs/air-quality-api">Open-Meteo Air Quality API</a>.</small></p>')
    if fx and fx.get("rates"):
        rates = "; ".join(f'1 {code} = {value:.3f} Kč' + (f' ({fx.get("monthly", {}).get(code):+.1f} % za 30 dní)' if code in fx.get("monthly", {}) else "") for code, value in fx["rates"].items())
        sections.append(f'<h2>Česká koruna</h2><p>{html.escape(rates)}.</p><p><small>Kurzovní lístek ke dni {html.escape(str(fx.get("date", report_date)))}; zdroj: <a href="https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/">Česká národní banka</a>.</small></p>')
    if content.get("meals"):
        meal_items = "".join(f'<li><h3>{meal_labels[item["meal"]]}: {html.escape(item["name"])}</h3><p>{html.escape(item["recipe"])}</p><p><small>Zdroj: <a href="{html.escape(item["source_url"], quote=True)}">{html.escape(item["source_title"])}</a></small></p></li>' for item in content["meals"])
        sections.append(f'''<h2>Zdravý a chutný jídelníček</h2><ol>{meal_items}</ol>
<p>Porce jsou orientační pro jednoho dospělého; upravte je podle chuti, aktivity, alergií a stravovacích potřeb.</p>''')
    tip = content.get("longevity_tip")
    if tip:
        sections.append(f'<h2>Tip pro zdravé stárnutí</h2><p>{html.escape(tip["text"])}</p><p><small>Zdroj: <a href="{html.escape(tip["url"], quote=True)}">{html.escape(tip["author"])} — {html.escape(tip["work"])}</a>. Obecná informace, nikoli individuální lékařské doporučení.</small></p>')
    for key, heading in (("portfolio_events", "Kalendář portfolia"), ("market_explanations", "Co vysvětluje neobvyklé pohyby")):
        if content.get(key):
            items = "".join(f'<li><h3>{html.escape(item["title"])}</h3><p>{html.escape(item["text"])}</p><p><small><a href="{html.escape(item["url"], quote=True)}">Zdroj</a></small></p></li>' for item in content[key])
            sections.append(f'<h2>{heading}</h2><ul>{items}</ul>')
    note = content.get("cultural_note")
    if note:
        sections.append(f'<h2>Česká kulturní a historická stopa</h2><p>{html.escape(note["text"])}</p><p><small>Zdroj: <a href="{html.escape(note["url"], quote=True)}">{html.escape(note["author"])} — {html.escape(note["work"])}</a>.</small></p>')
    return "\n".join(sections)


def merge_pages(market_page: str, news_page: str, report_date: str, weather: list[dict] | None = None, daily_content: dict | None = None, include_news: bool = True, calendar: dict | None = None, fx: dict | None = None, air_quality: list[dict] | None = None) -> str:
    market = article_body(market_page)
    news = ""
    if include_news:
        news = article_body(news_page)
        news = re.sub(
            r'^\s*<p><strong>(?:Daily (?:World|Economy) Briefing|Podstatné denní zprávy).*?</p>',
            "<h2>Ekonomické zprávy</h2>",
            news,
            count=1,
            flags=re.DOTALL | re.IGNORECASE,
        )
    safe_date = html.escape(report_date)
    content = validate_daily_content(daily_content or {})
    reflection = daily_reflection(content)
    wellbeing = wellbeing_sections(report_date, weather if weather is not None else fetch_weather(report_date), content, calendar, fx, air_quality)
    return f'''<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Denní přehled trhů a zdravého života — {safe_date}</title>
<meta name="description" content="Denní přehled tržního rizika, portfolia, ekonomiky, počasí, receptů a zdravého stárnutí.">
<style>
article > h2 {{ break-before: page; page-break-before: always; }}
article > h2:first-child {{ break-before: auto; page-break-before: auto; }}
h2, h3 {{ break-after: avoid; page-break-after: avoid; }}
</style>
</head><body><article>
{reflection}
{wellbeing}
{market}
{news}
<p><strong>Přeji hezký, klidný, šťastný a bezpečný den!</strong></p>
</article></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, help="Market component directory")
    parser.add_argument("--news", required=True, help="News component directory")
    parser.add_argument("--output", default="site")
    parser.add_argument("--weather-fixture", help="Offline JSON weather fixture")
    parser.add_argument("--wellbeing-fixture", help="Offline JSON AI-content fixture")
    args = parser.parse_args()

    market_dir, news_dir, output = Path(args.market), Path(args.news), Path(args.output)
    market_meta = json.loads((market_dir / "report.json").read_text(encoding="utf-8"))
    news_meta = json.loads((news_dir / "report.json").read_text(encoding="utf-8"))
    if market_meta["date"] != news_meta["date"]:
        raise SystemExit(f"component dates differ: {market_meta['date']} and {news_meta['date']}")

    output.mkdir(parents=True, exist_ok=True)
    weather = json.loads(Path(args.weather_fixture).read_text(encoding="utf-8")) if args.weather_fixture else None
    calendar = fx = None
    air_quality = []
    try:
        calendar = fetch_calendar(market_meta["date"])
        tomorrow_date = (date.fromisoformat(market_meta["date"]) + timedelta(days=1)).isoformat()
        calendar["tomorrow"] = fetch_calendar(tomorrow_date)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    try:
        fx = fetch_fx_rates(market_meta["date"])
    except (OSError, ET.ParseError, KeyError, TypeError, ValueError):
        pass
    try:
        air_quality = fetch_air_quality()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    generation = {"status": "fixture"} if args.wellbeing_fixture else {"status": "not_requested"}
    if args.wellbeing_fixture:
        daily_content = validate_daily_content(json.loads(Path(args.wellbeing_fixture).read_text(encoding="utf-8")))
    else:
        api_key = os.environ.get("GROQ_API_KEY", "")
        model = os.environ.get("GROQ_WEB_MODEL", "groq/compound")
        if api_key:
            daily_content, generation = generate_daily_content(market_meta["date"], api_key, model)
        else:
            daily_content = {}
            generation = {"status": "error", "reason": "GROQ_API_KEY is not configured"}
            print("Daily web content omitted: GROQ_API_KEY is not configured", file=sys.stderr)
    page = merge_pages(
        (market_dir / "index.html").read_text(encoding="utf-8"),
        (news_dir / "index.html").read_text(encoding="utf-8"),
        market_meta["date"], weather, daily_content, news_meta.get("selected_items", 0) > 0, calendar, fx, air_quality,
    )
    (output / "index.html").write_text(page, encoding="utf-8")
    combined = {
        "date": market_meta["date"],
        "generated_at": news_meta["generated_at"],
        "market": market_meta,
        "news": news_meta,
        "daily_content": generation,
    }
    (output / "report.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    print(f"Merged market and news components into {output / 'index.html'}")


if __name__ == "__main__":
    main()
