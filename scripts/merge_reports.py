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

sys.path.insert(0, str(Path(__file__).parent))
from profiles import DEFAULT_PROFILE, load_profile

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CALENDAR_URL = "https://svatkyapi.netlify.app/api/day"
CNB_URL = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.xml"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SERPAPI_URL = "https://serpapi.com/search.json"
GROQ_TPM_COOLDOWN = 65.0
GROQ_REQUEST_SPACING = 65.0
GROQ_TPD_RETRY_WINDOW = 300.0
WEATHER_LOCATIONS = {"Horoměřice": (50.1317, 14.3388), "Prague": (50.0755, 14.4378), "Česká Lípa": (50.6855, 14.5376)}
WEATHER_CODES = {0: "Jasno", 1: "Převážně jasno", 2: "Polojasno", 3: "Zataženo", 45: "Mlha", 48: "Mrznoucí mlha", 51: "Slabé mrholení", 53: "Mrholení", 55: "Silné mrholení", 61: "Slabý déšť", 63: "Déšť", 65: "Silný déšť", 71: "Slabé sněžení", 73: "Sněžení", 75: "Silné sněžení", 80: "Slabé přeháňky", 81: "Přeháňky", 82: "Silné přeháňky", 85: "Slabé sněhové přeháňky", 86: "Silné sněhové přeháňky", 95: "Bouřky", 96: "Bouřky s kroupami", 99: "Silné bouřky s kroupami"}


def summer_2027_flight_dates() -> list[tuple[str, str]]:
    """Cover the bookable summer window without an unaffordable daily API sweep."""
    first = date(2027, 6, 1)
    last_departure = date(2027, 8, 1)
    departures = []
    current = first
    while current <= last_departure:
        departures.append(current)
        current += timedelta(days=7)
    if departures[-1] != last_departure:
        departures.append(last_departure)
    return [(outbound.isoformat(), (outbound + timedelta(days=30)).isoformat()) for outbound in departures]


def fetch_flight_candidate(api_key: str, outbound_date: str, return_date: str) -> dict | None:
    query = urllib.parse.urlencode({
        "engine": "google_flights", "api_key": api_key,
        "departure_id": "PRG", "arrival_id": "CGK", "type": 1,
        "outbound_date": outbound_date, "return_date": return_date,
        "travel_class": 3, "adults": 1, "stops": 2, "sort_by": 2,
        "currency": "CZK", "hl": "cs", "gl": "cz",
    })
    request = urllib.request.Request(f"{SERPAPI_URL}?{query}", headers={"User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read())
    if payload.get("error"):
        raise ValueError(str(payload["error"]))
    candidates = payload.get("best_flights", []) + payload.get("other_flights", [])
    valid = []
    for candidate in candidates:
        flights = candidate.get("flights", []) if isinstance(candidate, dict) else []
        price = candidate.get("price") if isinstance(candidate, dict) else None
        if not flights or len(flights) > 2 or not isinstance(price, (int, float)) or price <= 0:
            continue
        airlines = sorted({str(flight.get("airline", "")).strip() for flight in flights if flight.get("airline")})
        valid.append({
            "price_czk": round(price), "outbound_date": outbound_date, "return_date": return_date,
            "airlines": airlines, "outbound_stops": len(flights) - 1,
            "duration_minutes": candidate.get("total_duration"),
            "url": payload.get("search_metadata", {}).get("google_flights_url", ""),
        })
    return min(valid, key=lambda item: item["price_czk"]) if valid else None


def fetch_summer_flight_price(api_key: str) -> tuple[dict | None, dict]:
    dates = summer_2027_flight_dates()
    candidates = []
    failures = []
    for outbound, returning in dates:
        try:
            candidate = fetch_flight_candidate(api_key, outbound, returning)
            if candidate:
                candidates.append(candidate)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            failures.append(f"{outbound}: {type(error).__name__}: {error}")
    status = {"status": "ok" if candidates else "error", "searched_date_pairs": len(dates), "successful_date_pairs": len(candidates)}
    if failures:
        status["failures"] = failures
    if not candidates:
        status["reason"] = "No verified Google Flights result was returned"
        return None, status
    cheapest = min(candidates, key=lambda item: item["price_czk"])
    cheapest["checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cheapest["sampled_date_pairs"] = len(dates)
    return cheapest, status


def flight_price_section(flight: dict | None, language="cs") -> str:
    if not flight:
        return ""
    price = f'{int(flight["price_czk"]):,}'.replace(",", " ")
    airlines = ", ".join(flight.get("airlines", [])) or "aerolinka uvedená ve výsledku"
    stops = "bez přestupu" if flight.get("outbound_stops") == 0 else "1 přestup"
    duration = ""
    if isinstance(flight.get("duration_minutes"), (int, float)):
        hours, minutes = divmod(round(flight["duration_minutes"]), 60)
        duration = f"; cesta tam {hours} h {minutes} min"
    link = html.escape(str(flight.get("url", "")), quote=True)
    source = f'<a href="{link}">Google Flights</a>' if link.startswith("http") else "Google Flights"
    if language == "id":
        stops = "tanpa transit" if flight.get("outbound_stops") == 0 else "1 kali transit"
        return ('<h2>Harga tiket pulang-pergi Praha–Jakarta hari ini</h2>'
                f'<p><strong>{price} CZK</strong> untuk satu orang di kelas bisnis. Berangkat {html.escape(flight["outbound_date"])}, kembali {html.escape(flight["return_date"])}; {html.escape(airlines)}, {stops}{duration}.</p>'
                f'<p><small>Harga terendah yang ditemukan saat ini dari {flight["sampled_date_pairs"]} tanggal keberangkatan mingguan pada musim panas 2027, selalu kembali setelah 30 hari dan maksimal satu kali transit. Sumber: {source}; diperiksa {html.escape(flight["checked_at"])}. Harga dan ketersediaan dapat berubah sebelum pemesanan.</small></p>')
    return (
        '<h2>Dnešní cena zpáteční letenky Praha–Jakarta</h2>'
        f'<p><strong>{price} Kč</strong> za jednu osobu v business class. '
        f'Odlet {html.escape(flight["outbound_date"])}, návrat {html.escape(flight["return_date"])}; '
        f'{html.escape(airlines)}, {stops}{duration}.</p>'
        f'<p><small>Nejnižší aktuálně nalezená cena mezi {flight["sampled_date_pairs"]} týdenními termíny odletu '
        f'v létě 2027, vždy s návratem po 30 dnech a nejvýše jedním přestupem. Zdroj: {source}; '
        f'ověřeno {html.escape(flight["checked_at"])}. Cena a dostupnost se mohou změnit před rezervací.</small></p>'
    )


def sourced_item(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    clean = {key: str(value.get(key, "")).strip() for key in ("text", "author", "work", "url")}
    return clean if all(clean.values()) and clean["url"].startswith(("https://", "http://")) else None


def daily_reflection(content: dict, profile=None) -> str:
    profile = profile or load_profile()
    teaching = content.get("spiritual_teaching") or content.get("buddhist_teaching")
    items = [item for item in (sourced_item(content.get("quote")), sourced_item(teaching)) if item]
    if not items:
        return ""
    blocks = "".join(
        f'<blockquote><p>„{html.escape(item["text"])}“</p><p>— {html.escape(item["author"])}, '
        f'<a href="{html.escape(item["url"], quote=True)}">{html.escape(item["work"])}</a></p></blockquote>'
        for item in items
    )
    if profile["language"] == "id":
        return f'<h2>Renungan hari ini</h2>{blocks}<p><small>Terjemahan Bahasa Indonesia; tautan menuju sumber web yang digunakan.</small></p>'
    return f'<h2>Myšlenka dne</h2>{blocks}<p><small>Český překlad; odkazy vedou na použité webové zdroje.</small></p>'


def article_body(page: str) -> str:
    match = re.search(r"<article>(.*?)</article>", page, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("input page has no <article> element")
    return match.group(1).strip()


def demote_headings(fragment: str, levels: int = 1) -> str:
    """Move component headings down without cascading replacements."""
    def replace(match: re.Match) -> str:
        level = min(6, int(match.group(2)) + levels)
        return f"<{match.group(1)}h{level}{match.group(3)}>"
    return re.sub(r"<(\/?)h([1-6])([^>]*)>", replace, fragment, flags=re.IGNORECASE)


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


def validate_daily_content(content: dict, profile=None) -> dict:
    profile = profile or load_profile()
    if not isinstance(content, dict):
        raise ValueError("daily content is not an object")
    clean = {}
    for key in ("quote", "buddhist_teaching", "spiritual_teaching"):
        item = sourced_item(content.get(key))
        if item:
            if key == "spiritual_teaching" and profile["tradition"] == "islam":
                host = urllib.parse.urlparse(item["url"]).hostname or ""
                if host not in ("quran.kemenag.go.id", "quran.com", "www.sunnah.com", "sunnah.com"):
                    continue
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


def request_groq_json(prompt: str, api_key: str, model: str, max_completion_tokens: int) -> tuple[dict, dict]:
    payload = json.dumps({"model": model, "temperature": 0, "max_completion_tokens": max_completion_tokens, "response_format": {"type": "json_object"}, "compound_custom": {"tools": {"enabled_tools": ["web_search"]}}, "messages": [{"role": "system", "content": "You are an exact researcher and translator. Follow the requested output language and omit anything without a verifiable direct web source."}, {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(GROQ_URL, data=payload, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())
        headers = response.headers
    value = json.loads(result["choices"][0]["message"]["content"])
    if not isinstance(value, dict):
        raise ValueError("Groq response is not a JSON object")
    rate_limit = {
        "limit_tokens": int(headers.get("x-ratelimit-limit-tokens", 0) or 0),
        "remaining_tokens": int(headers.get("x-ratelimit-remaining-tokens", 0) or 0),
        "reset_tokens_seconds": groq_duration_seconds(headers.get("x-ratelimit-reset-tokens")),
    }
    return value, rate_limit


def groq_duration_seconds(value: str | None) -> float:
    """Parse Groq reset durations such as 7.66s or 2m59.56s."""
    if not value:
        return 0
    match = re.fullmatch(r"\s*(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?\s*", value)
    if not match or not any(match.groups()):
        return 0
    return float(match.group(1) or 0) * 3600 + float(match.group(2) or 0) * 60 + float(match.group(3) or 0)


def retry_delay(error: BaseException, attempt: int, maximum: float = 120.0) -> float:
    """Honor Groq's Retry-After header, with exponential backoff as a fallback."""
    headers = error.headers if isinstance(error, urllib.error.HTTPError) and error.headers else {}
    value = headers.get("Retry-After")
    reset_delay = groq_duration_seconds(headers.get("x-ratelimit-reset-tokens"))
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = 0
        if delay > 0:
            if isinstance(error, urllib.error.HTTPError) and error.code == 429:
                delay = max(delay + 0.5, GROQ_TPM_COOLDOWN)
            delay = max(delay, reset_delay + 0.5 if reset_delay else 0)
            return min(delay, maximum)
    body_delay = getattr(error, "groq_retry_after", 0)
    if body_delay > 0:
        delay = body_delay + 0.5
        if isinstance(error, urllib.error.HTTPError) and error.code == 429:
            delay = max(delay, GROQ_TPM_COOLDOWN)
        delay = max(delay, reset_delay + 0.5 if reset_delay else 0)
        return min(delay, maximum)
    if reset_delay:
        return min(max(reset_delay + 0.5, GROQ_TPM_COOLDOWN), maximum)
    return min(5.0 * 2 ** (attempt - 1), maximum)


def retryable_groq_error(error: BaseException) -> bool:
    if not isinstance(error, urllib.error.HTTPError):
        return isinstance(error, (OSError, TimeoutError))
    if getattr(error, "groq_limit_kind", "") == "TPD":
        return False
    return error.code in (408, 429, 498) or 500 <= error.code < 600


def groq_error_reason(error: BaseException) -> str:
    reason = f"{type(error).__name__}: {error}"
    if not isinstance(error, urllib.error.HTTPError):
        return reason
    try:
        body = error.read(2048).decode("utf-8", errors="replace")
        value = json.loads(body)
        detail = value.get("error", {}).get("message", "") if isinstance(value, dict) else ""
        if detail:
            safe_detail = re.sub(r"\s+", " ", str(detail)).strip()[:500]
            limit_match = re.search(r"tokens per (minute|day) \((TPM|TPD)\)", safe_detail, re.IGNORECASE)
            if limit_match:
                error.groq_limit_kind = limit_match.group(2).upper()
            retry_match = re.search(r"try again in ((?:\d+(?:\.\d+)?[hms])+)", safe_detail, re.IGNORECASE)
            if retry_match:
                error.groq_retry_after = groq_duration_seconds(retry_match.group(1).lower())
            return f"{reason}: {safe_detail}"
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return reason


def generate_with_retries(label: str, prompt: str, api_key: str, model: str, max_completion_tokens: int, attempts: int = 3) -> tuple[dict, dict]:
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            value, rate_limit = request_groq_json(prompt, api_key, model, max_completion_tokens)
            status = {"status": "ok", "attempts": attempt, "max_completion_tokens": max_completion_tokens}
            status.update({key: value for key, value in rate_limit.items() if value})
            return value, status
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = groq_error_reason(error)
            print(f"Groq {label} attempt {attempt}/{attempts} failed: {last_error}", file=sys.stderr)
            if (
                getattr(error, "groq_limit_kind", "") == "TPD"
                and attempt == 1
                and 0 < getattr(error, "groq_retry_after", 0) <= GROQ_TPD_RETRY_WINDOW
            ):
                print(f"Groq {label} daily token capacity may recover soon; retrying once in {GROQ_TPD_RETRY_WINDOW:g} seconds", file=sys.stderr)
                time.sleep(GROQ_TPD_RETRY_WINDOW)
                continue
            if not retryable_groq_error(error):
                status = {"status": "error", "attempts": attempt, "reason": last_error, "max_completion_tokens": max_completion_tokens}
                if getattr(error, "groq_limit_kind", ""):
                    status["rate_limit"] = error.groq_limit_kind
                if getattr(error, "groq_retry_after", 0):
                    status["retry_after_seconds"] = error.groq_retry_after
                return {}, status
            if attempt < attempts:
                delay = retry_delay(error, attempt)
                print(f"Groq {label} retrying in {delay:g} seconds", file=sys.stderr)
                time.sleep(delay)
    return {}, {"status": "error", "attempts": attempts, "reason": last_error, "max_completion_tokens": max_completion_tokens}


def generate_daily_content(report_date: str, api_key: str, model: str, reflection_model: str = "groq/compound-mini", profile=None) -> tuple[dict, dict]:
    profile = profile or load_profile()
    if profile["tradition"] == "islam":
        reflection_prompt = f'''Untuk {report_date}, gunakan pencarian web dan kembalikan hanya JSON valid. Temukan (1) kutipan sejarah yang dapat diverifikasi dan (2) ajaran Islam yang konkret dan dapat diverifikasi dalam Bahasa Indonesia. Untuk ajaran Islam, utamakan ayat Al-Qur'an dengan rujukan surah/ayat dan terjemahan resmi Kementerian Agama RI, atau hadis sahih dengan koleksi dan nomor yang jelas. Jangan menciptakan atau memparafrasekan seolah-olah itu wahyu. Setiap item wajib memiliki teks, author, work, dan URL sumber langsung; jika tidak dapat diverifikasi, gunakan null. Skema: {{"quote":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null,"spiritual_teaching":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null}}.'''
    else:
        reflection_prompt = f'''Pro den {report_date} použij webové vyhledávání a vrať pouze platný JSON. Najdi (1) ověřitelný historický citát a (2) skutečné, konkrétní učení buddhistického učitele nebo badatele. Vše věrně přelož nebo shrň do češtiny; nic nevymýšlej. Každá položka musí mít autora, dílo nebo kontext a přímý webový zdroj. Pokud položku neověříš, vrať null. Schéma: {{"quote":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null,"spiritual_teaching":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null}}.'''
    extras_prompt = f'''Pro den {report_date} použij webové vyhledávání a vrať pouze platný JSON. Najdi: (1) čtyři zdravé recepty pro jednoho dospělého dostupné v Česku, (2) praktické doporučení pro zdravé stárnutí z autoritativního zdravotnického zdroje, (3) dnešní či bezprostředně nadcházející události relevantní pro portfolio a makroekonomiku, (4) vysvětlení pouze skutečně neobvyklých dnešních pohybů sledovaných trhů a (5) kvalitní českou kulturní nebo historickou poznámku vztahující se k datu. Sledované nástroje a témata jsou SPYI, SPYL, SEC0, XNAS, QUTM, EGLN/XAU, XAIX, IWMO, ZPRV, NVS, NW0/CSG, ASME/ASML, BTC a ADA. Vše věrně přelož nebo shrň do češtiny; nic nevymýšlej. Každá položka musí mít přímý webový zdroj. Pokud některou část nenajdeš, neověříš nebo není relevantní, vrať null či prázdné pole.
Schéma: {{"meals":[{{"meal":"Breakfast|Lunch|Snack|Dinner","name":"...","recipe":"úplný recept s množstvím a časy","source_title":"...","source_url":"https://..."}}]|null,"longevity_tip":{{"text":"...","author":"organizace nebo autor","work":"název stránky","url":"https://..."}}|null,"portfolio_events":[{{"title":"...","text":"...","url":"https://..."}}],"market_explanations":[{{"title":"...","text":"...","url":"https://..."}}],"cultural_note":{{"text":"...","author":"instituce nebo autor","work":"název stránky","url":"https://..."}}|null}}. Pole meal musí být v pořadí Breakfast, Lunch, Snack, Dinner.'''
    if profile["language"] == "id":
        extras_prompt = f'''Untuk {report_date}, gunakan pencarian web dan kembalikan hanya JSON valid dalam Bahasa Indonesia. Temukan empat resep sehat untuk satu orang dewasa dengan bahan yang tersedia di Ceko; saran praktis penuaan sehat dari sumber kesehatan berwenang; peristiwa portofolio dan makroekonomi hari ini atau segera; penjelasan hanya untuk pergerakan pasar yang benar-benar luar biasa; serta catatan budaya atau sejarah Ceko yang berkaitan dengan tanggal ini. Jangan menciptakan fakta. Setiap item wajib memiliki URL sumber langsung; gunakan null atau array kosong jika tidak terverifikasi. Instrumen: SPYI, SPYL, SEC0, XNAS, QUTM, EGLN/XAU, XAIX, IWMO, ZPRV, NVS, NW0/CSG, ASME/ASML, BTC, ADA. Skema: {{"meals":[{{"meal":"Breakfast|Lunch|Snack|Dinner","name":"...","recipe":"resep lengkap dengan jumlah dan waktu","source_title":"...","source_url":"https://..."}}]|null,"longevity_tip":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null,"portfolio_events":[{{"title":"...","text":"...","url":"https://..."}}],"market_explanations":[{{"title":"...","text":"...","url":"https://..."}}],"cultural_note":{{"text":"...","author":"...","work":"...","url":"https://..."}}|null}}. Urutan meal wajib Breakfast, Lunch, Snack, Dinner.'''
    reflection, reflection_status = generate_with_retries("reflection", reflection_prompt, api_key, reflection_model, max_completion_tokens=1000)
    spacing = max(GROQ_REQUEST_SPACING, reflection_status.get("reset_tokens_seconds", 0) + 0.5)
    print(f"Groq waiting {spacing:g} seconds before extras", file=sys.stderr)
    time.sleep(spacing)
    extras, extras_status = generate_with_retries("extras", extras_prompt, api_key, model, max_completion_tokens=3200)
    content = validate_daily_content({**extras, **reflection}, profile)
    reflection_status["quote"] = "present" if content.get("quote") else "omitted"
    teaching = content.get("spiritual_teaching") or content.get("buddhist_teaching")
    reflection_status["spiritual_teaching"] = "present" if teaching else "omitted"
    if profile["tradition"] == "buddhism":
        reflection_status["buddhist_teaching"] = reflection_status["spiritual_teaching"]
    if reflection_status["status"] == "ok" and reflection_status["spiritual_teaching"] == "omitted":
        reflection_status["spiritual_teaching_reason"] = "Groq returned no teaching with text, author, work, and a valid source URL"
        if profile["tradition"] == "buddhism":
            reflection_status["buddhist_teaching_reason"] = reflection_status["spiritual_teaching_reason"]
    reflection_status["model"] = reflection_model
    extras_status["model"] = model
    return content, {"models": {"reflection": reflection_model, "extras": model}, "reflection": reflection_status, "extras": extras_status}


def wellbeing_sections(report_date: str, weather: list[dict], content: dict, calendar: dict | None = None, fx: dict | None = None, air_quality: list[dict] | None = None, profile=None) -> str:
    profile = profile or load_profile()
    if profile["language"] == "id":
        sections = []
        weather_id = {"Jasno": "Cerah", "Převážně jasno": "Sebagian besar cerah", "Polojasno": "Berawan sebagian", "Zataženo": "Berawan", "Mlha": "Kabut", "Déšť": "Hujan", "Silný déšť": "Hujan lebat", "Sněžení": "Salju", "Bouřky": "Badai petir", "Smíšené podmínky": "Kondisi campuran"}
        if calendar and calendar.get("name"):
            sections.append(f'<h2>Kalender Ceko</h2><p>Hari nama: <strong>{html.escape(str(calendar["name"]))}</strong>' + (f'; besok <strong>{html.escape(str(calendar["tomorrow"]["name"]))}</strong>' if calendar.get("tomorrow", {}).get("name") else "") + '.</p><p><small>Sumber: <a href="https://svatkyapi.cz/">Svatky API</a>.</small></p>')
        if weather:
            items = "".join(f'<li><h3>{html.escape(item["name"])}</h3><p>{html.escape(weather_id.get(item["condition"], item["condition"]))}. {item["minimum_c"]}–{item["maximum_c"]} °C. <strong>Hujan:</strong> peluang {item["rain_probability"]}%, jumlah {item.get("rain_mm", 0)} mm. Angin hingga {item["wind_kmh"]} km/jam. Matahari terbit {html.escape(item["sunrise"])}; terbenam {html.escape(item["sunset"])}.</p></li>' for item in weather)
            sections.append(f'<h2>Cuaca hari ini</h2><ul>{items}</ul><p><small>Sumber: Open-Meteo; zona waktu Europe/Prague.</small></p>')
        if air_quality:
            items = "".join(f'<li><strong>{html.escape(item["name"])}</strong>: indeks kualitas udara Eropa {item["aqi"]}, PM2,5 {item["pm25"]:g} µg/m³.</li>' for item in air_quality)
            sections.append(f'<h2>Udara dan serbuk sari</h2><ul>{items}</ul>')
        if fx and fx.get("rates"):
            rates = "; ".join(f'1 {code} = {value:.3f} CZK' for code, value in fx["rates"].items())
            sections.append(f'<h2>Koruna Ceko</h2><p>{html.escape(rates)}.</p><p><small>Sumber: Bank Nasional Ceko.</small></p>')
        if content.get("meals"):
            labels = {"Breakfast": "Sarapan", "Lunch": "Makan siang", "Snack": "Camilan", "Dinner": "Makan malam"}
            items = "".join(f'<li><h3>{labels[item["meal"]]}: {html.escape(item["name"])}</h3><p>{html.escape(item["recipe"])}</p><p><small>Sumber: <a href="{html.escape(item["source_url"], quote=True)}">{html.escape(item["source_title"])}</a></small></p></li>' for item in content["meals"])
            sections.append(f'<h2>Menu sehat dan lezat</h2><ol>{items}</ol>')
        if content.get("longevity_tip"):
            tip = content["longevity_tip"]
            sections.append(f'<h2>Tip menua dengan sehat</h2><p>{html.escape(tip["text"])}</p><p><small>Sumber: <a href="{html.escape(tip["url"], quote=True)}">{html.escape(tip["author"])} — {html.escape(tip["work"])}</a>.</small></p>')
        for key, heading in (("portfolio_events", "Kalender portofolio"), ("market_explanations", "Penjelasan pergerakan luar biasa")):
            if content.get(key):
                items = "".join(f'<li><h3>{html.escape(item["title"])}</h3><p>{html.escape(item["text"])}</p><p><small><a href="{html.escape(item["url"], quote=True)}">Sumber</a></small></p></li>' for item in content[key])
                sections.append(f'<h2>{heading}</h2><ul>{items}</ul>')
        if content.get("cultural_note"):
            note = content["cultural_note"]
            sections.append(f'<h2>Jejak budaya dan sejarah Ceko</h2><p>{html.escape(note["text"])}</p><p><small>Sumber: <a href="{html.escape(note["url"], quote=True)}">{html.escape(note["author"])} — {html.escape(note["work"])}</a>.</small></p>')
        return "\n".join(sections)
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


def merge_pages(market_page: str, news_page: str, report_date: str, weather: list[dict] | None = None, daily_content: dict | None = None, include_news: bool = True, calendar: dict | None = None, fx: dict | None = None, air_quality: list[dict] | None = None, flight: dict | None = None, profile=None) -> str:
    profile = profile or load_profile()
    market = article_body(market_page)
    market = re.sub(r"^\s*<h2>.*?</h2>", "", market, count=1, flags=re.DOTALL | re.IGNORECASE)
    market = demote_headings(market)
    market_heading = "Situasi pasar" if profile["language"] == "id" else "Situace na trzích"
    market = f"<h2>{market_heading}</h2>{market}"
    news = ""
    if include_news:
        news = article_body(news_page)
        news = re.sub(
            r'^\s*<p><strong>(?:Daily (?:World|Economy) Briefing|Podstatné denní zprávy|Berita harian penting).*?</p>',
            "",
            news,
            count=1,
            flags=re.DOTALL | re.IGNORECASE,
        )
        news = demote_headings(news)
        news_heading = "Berita ekonomi" if profile["language"] == "id" else "Ekonomické zprávy"
        news = f"<h2>{news_heading}</h2>{news}"
    safe_date = html.escape(report_date)
    content = validate_daily_content(daily_content or {}, profile)
    reflection = daily_reflection(content, profile)
    wellbeing = wellbeing_sections(report_date, weather if weather is not None else fetch_weather(report_date), content, calendar, fx, air_quality, profile)
    flight_section = flight_price_section(flight, profile["language"])
    title = html.escape(profile["title"])
    description = "Ringkasan harian risiko pasar, portofolio, ekonomi, cuaca, resep, dan hidup sehat." if profile["language"] == "id" else "Denní přehled tržního rizika, portfolia, ekonomiky, počasí, receptů a zdravého stárnutí."
    farewell = "Semoga hari Anda menyenangkan, tenang, bahagia, dan aman!" if profile["language"] == "id" else "Přeji hezký, klidný, šťastný a bezpečný den!"
    language_link = '<p><a href="../index.html">Česká verze</a></p>' if profile["language"] == "id" else '<p><a href="id/index.html">Versi Bahasa Indonesia</a></p>'
    return f'''<!doctype html>
<html lang="{profile['language']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {safe_date}</title>
<meta name="description" content="{description}">
<style>
article > h2 {{ break-before: page; page-break-before: always; }}
h1, h2, h3, h4 {{ break-after: avoid; page-break-after: avoid; }}
</style>
</head><body><article>
<h1>{title} — {safe_date}</h1>
{language_link}
{reflection}
{wellbeing}
{flight_section}
{news}
{market}
<p><strong>{farewell}</strong></p>
</article></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, help="Market component directory")
    parser.add_argument("--news", required=True, help="News component directory")
    parser.add_argument("--output", default="site")
    parser.add_argument("--weather-fixture", help="Offline JSON weather fixture")
    parser.add_argument("--wellbeing-fixture", help="Offline JSON AI-content fixture")
    parser.add_argument("--flight-fixture", help="Reuse a verified flight result from another profile report")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    args = parser.parse_args()
    profile = load_profile(args.profile)

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
        daily_content = validate_daily_content(json.loads(Path(args.wellbeing_fixture).read_text(encoding="utf-8")), profile)
    else:
        api_key = os.environ.get("GROQ_API_KEY", "")
        model = os.environ.get("GROQ_WEB_MODEL", "groq/compound")
        reflection_model = os.environ.get("GROQ_REFLECTION_MODEL", "groq/compound-mini")
        if api_key:
            daily_content, generation = generate_daily_content(market_meta["date"], api_key, model, reflection_model, profile)
        else:
            daily_content = {}
            generation = {"status": "error", "reason": "GROQ_API_KEY is not configured"}
            print("Daily web content omitted: GROQ_API_KEY is not configured", file=sys.stderr)
    flight = None
    flight_generation = {"status": "error", "reason": "SERPAPI_API_KEY is not configured"}
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")
    if args.flight_fixture:
        reused = json.loads(Path(args.flight_fixture).read_text(encoding="utf-8"))
        flight = reused.get("flight")
        flight_generation = {"status": "reused" if flight else "error", "source_profile": reused.get("profile")}
    elif serpapi_key:
        flight, flight_generation = fetch_summer_flight_price(serpapi_key)
    else:
        print("Flight price omitted: SERPAPI_API_KEY is not configured", file=sys.stderr)
    page = merge_pages(
        (market_dir / "index.html").read_text(encoding="utf-8"),
        (news_dir / "index.html").read_text(encoding="utf-8"),
        market_meta["date"], weather, daily_content, news_meta.get("selected_items", 0) > 0, calendar, fx, air_quality, flight, profile,
    )
    (output / "index.html").write_text(page, encoding="utf-8")
    combined = {
        "date": market_meta["date"],
        "generated_at": news_meta["generated_at"],
        "market": market_meta,
        "news": news_meta,
        "daily_content": generation,
        "flight_price": flight_generation,
        "flight": flight,
        "profile": profile["name"],
    }
    (output / "report.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    print(f"Merged market and news components into {output / 'index.html'}")


if __name__ == "__main__":
    main()
