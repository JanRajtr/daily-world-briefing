#!/usr/bin/env python3
"""Merge the market and world-news components into one static article."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
WEATHER_LOCATIONS = {"Horoměřice": (50.1317, 14.3388), "Prague": (50.0755, 14.4378), "Česká Lípa": (50.6855, 14.5376)}
WEATHER_CODES = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Light showers", 81: "Showers", 82: "Heavy showers", 85: "Light snow showers", 86: "Heavy snow showers", 95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail"}

MEAL_PLANS = (
    (("Breakfast", "Apple-cinnamon overnight oats", "Mix 60 g rolled oats, 150 g plain skyr, 100 ml milk, 1 grated apple, 1 tbsp chia seeds and cinnamon. Refrigerate overnight; top with a small handful of walnuts in the morning."), ("Lunch", "Lemon chicken, bulgur and roasted vegetables", "Roast 150 g chicken breast, sliced courgette, pepper and red onion with 1 tsp olive oil at 200°C for 20–25 minutes. Cook 70 g dry bulgur. Serve together with lemon juice, parsley and 2 tbsp plain yoghurt."), ("Snack", "Berry skyr crunch", "Stir 150 g plain skyr with a handful of berries and 1 tbsp pumpkin seeds. Add cinnamon or vanilla if desired."), ("Dinner", "Red lentil tomato soup with rye toast", "Soften half a chopped onion and one carrot in 1 tsp olive oil. Add 70 g rinsed red lentils, 250 g chopped tomatoes, 400 ml low-salt stock, cumin and paprika; simmer for 20 minutes. Blend partly and serve with one slice of rye bread.")),
    (("Breakfast", "Spinach and tomato eggs on rye", "Wilt a handful of spinach and chopped tomato in 1 tsp olive oil. Add 2 beaten eggs and cook gently. Serve on one slice of rye bread with black pepper."), ("Lunch", "Mediterranean chickpea bowl", "Combine 150 g drained chickpeas, chopped cucumber, tomato, pepper, parsley and 60 g cooked whole-grain couscous. Dress with lemon, 1 tsp olive oil and 30 g feta."), ("Snack", "Pear with peanut yoghurt", "Mix 1 tsp unsweetened peanut butter into 120 g plain yoghurt and eat with one sliced pear."), ("Dinner", "Baked salmon, potatoes and green beans", "Bake a 150 g salmon fillet with lemon and dill at 190°C for 15–18 minutes. Boil 200 g baby potatoes and steam a generous serving of green beans; finish the vegetables with 1 tsp olive oil.")),
    (("Breakfast", "Banana-oat pancakes", "Blend 1 small banana, 1 egg, 40 g oats and a pinch of cinnamon. Cook small pancakes in a lightly oiled pan and serve with 100 g plain yoghurt and berries."), ("Lunch", "Turkey and bean chilli", "Brown 150 g lean turkey mince with half an onion. Add 120 g drained kidney beans, 250 g chopped tomatoes, paprika and cumin; simmer for 20 minutes. Serve with 60 g cooked brown rice and fresh coriander."), ("Snack", "Hummus and crunchy vegetables", "Serve 3 tbsp hummus with sliced carrot, cucumber and pepper. Season with paprika or lemon juice."), ("Dinner", "Mushroom, pea and barley risotto", "Cook 70 g pearl barley in low-salt stock. Sauté 150 g mushrooms with garlic in 1 tsp olive oil, stir in a handful of peas and the barley, then finish with 1 tbsp grated Parmesan and parsley.")),
)
LONGEVITY_TIPS = (
    "Take a brisk 20–30 minute walk today, ideally after a meal. Regular moderate activity supports cardiovascular health, glucose control, mobility and sleep; consistency matters more than intensity.",
    "Make today a fibre-rich day: include vegetables or fruit at every meal, plus legumes or whole grains. Increase fibre gradually and drink enough water if your usual intake is low.",
    "Protect tonight's sleep: keep a consistent bedtime, dim bright light during the final hour and avoid a heavy meal or alcohol close to bed. Most adults do best with roughly 7–9 hours.",
    "Add two short strength sessions to your week. Squats to a chair, wall push-ups and resistance-band rows help preserve muscle and balance; begin comfortably and progress gradually.",
    "Call or meet someone you enjoy today. Strong social connection is part of healthy ageing, alongside movement, sleep, nutritious food and routine preventive care.",
)

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


def daily_reflection(report_date: str, generated_reflection: str = "") -> str:
    day = date.fromisoformat(report_date).toordinal()
    quote = QUOTES[day % len(QUOTES)]
    reflection = generated_reflection or BUDDHIST_REFLECTIONS[day % len(BUDDHIST_REFLECTIONS)]
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


def fetch_weather_location(name: str, latitude: float, longitude: float, report_date: str) -> dict:
    query = urllib.parse.urlencode({"latitude": latitude, "longitude": longitude, "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunrise,sunset", "timezone": "Europe/Prague", "start_date": report_date, "end_date": report_date})
    request = urllib.request.Request(f"{WEATHER_URL}?{query}", headers={"User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        daily = json.loads(response.read())["daily"]
    return {"name": name, "condition": WEATHER_CODES.get(int(daily["weather_code"][0]), "Mixed conditions"), "minimum_c": round(float(daily["temperature_2m_min"][0])), "maximum_c": round(float(daily["temperature_2m_max"][0])), "rain_probability": round(float(daily["precipitation_probability_max"][0])), "rain_mm": round(float(daily["precipitation_sum"][0]), 1), "wind_kmh": round(float(daily["wind_speed_10m_max"][0])), "sunrise": daily["sunrise"][0].rsplit("T", 1)[-1], "sunset": daily["sunset"][0].rsplit("T", 1)[-1]}


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


def fallback_daily_content(report_date: str) -> dict:
    ordinal = date.fromisoformat(report_date).toordinal()
    return {
        "reflection": BUDDHIST_REFLECTIONS[ordinal % len(BUDDHIST_REFLECTIONS)],
        "meals": [{"meal": meal, "name": name, "recipe": recipe} for meal, name, recipe in MEAL_PLANS[ordinal % len(MEAL_PLANS)]],
        "longevity_tip": LONGEVITY_TIPS[ordinal % len(LONGEVITY_TIPS)],
    }


def validate_daily_content(content: dict) -> dict:
    if not isinstance(content, dict):
        raise ValueError("daily content is not an object")
    reflection = str(content.get("reflection", "")).strip()
    tip = str(content.get("longevity_tip", "")).strip()
    meals = content.get("meals")
    if not reflection or not tip or not isinstance(meals, list) or len(meals) != 4:
        raise ValueError("daily content is incomplete")
    expected = ("Breakfast", "Lunch", "Snack", "Dinner")
    clean_meals = []
    for expected_meal, meal in zip(expected, meals):
        if not isinstance(meal, dict) or str(meal.get("meal", "")).strip().lower() != expected_meal.lower():
            raise ValueError("daily meals must be breakfast, lunch, snack and dinner in order")
        name, recipe = str(meal.get("name", "")).strip(), str(meal.get("recipe", "")).strip()
        if not name or len(recipe) < 80:
            raise ValueError(f"{expected_meal} recipe is incomplete")
        clean_meals.append({"meal": expected_meal, "name": name, "recipe": recipe})
    return {"reflection": reflection, "meals": clean_meals, "longevity_tip": tip}


def generate_daily_content(report_date: str, api_key: str, model: str) -> dict:
    prompt = f'''Create fresh daily wellbeing content for {report_date}. Return valid JSON only:
{{"reflection":"...","meals":[{{"meal":"Breakfast","name":"...","recipe":"..."}},{{"meal":"Lunch","name":"...","recipe":"..."}},{{"meal":"Snack","name":"...","recipe":"..."}},{{"meal":"Dinner","name":"...","recipe":"..."}}],"longevity_tip":"..."}}
Write in clear English for one adult in Czechia. Make the menu healthy, tasty, practical, varied, based on commonly available seasonal ingredients, and nutritionally balanced across the day. Every recipe must be complete in the text with ingredient quantities, temperatures and cooking times where relevant; never provide links. Avoid extreme diets, supplements, medical claims and raw high-risk animal foods. The longevity tip must be practical, evidence-aligned and cautious. The reflection should be an original calm 2-3 sentence Buddhist-informed perspective without inventing or quoting a historical source. Do not repeat content merely because the date repeats.'''
    payload = json.dumps({"model": model, "temperature": 0.8, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": "You are a careful wellbeing editor and practical home cook."}, {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(GROQ_URL, data=payload, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "daily-world-briefing/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read())
    return validate_daily_content(json.loads(result["choices"][0]["message"]["content"]))


def wellbeing_sections(report_date: str, weather: list[dict], content: dict) -> str:
    weather_items = "".join(f'<li><h3>{html.escape(item["name"])}</h3><p>{html.escape(item["condition"])}. {item["minimum_c"]}–{item["maximum_c"]}°C. <strong>Rain forecast:</strong> {item["rain_probability"]}% chance, {item.get("rain_mm", 0)} mm expected. Wind up to {item["wind_kmh"]} km/h. Sunrise {html.escape(item["sunrise"])}; sunset {html.escape(item["sunset"])}.</p></li>' for item in weather)
    if not weather_items:
        weather_items = "<li>Today's forecast is temporarily unavailable.</li>"
    meal_items = "".join(f'<li><h3>{html.escape(item["meal"])}: {html.escape(item["name"])}</h3><p>{html.escape(item["recipe"])}</p></li>' for item in content["meals"])
    return f'''<h2>Today's weather</h2><ul>{weather_items}</ul>
<p><small>Forecast: Open-Meteo, for {html.escape(report_date)} in Europe/Prague local time. Conditions can change.</small></p>
<h2>Healthy and tasty menu</h2><ol>{meal_items}</ol>
<p>Portions are a practical starting point for one adult; adjust them for appetite, activity, allergies and dietary needs.</p>
<h2>Longevity tip of the day</h2><p>{html.escape(content["longevity_tip"])}</p>
<p><small>Food and longevity content is general information, not individualized medical or nutritional advice.</small></p>'''


def merge_pages(market_page: str, news_page: str, report_date: str, weather: list[dict] | None = None, daily_content: dict | None = None, include_news: bool = True) -> str:
    market = article_body(market_page)
    news = ""
    if include_news:
        news = article_body(news_page)
        news = re.sub(
            r'^\s*<p><strong>Daily (?:World|Economy) Briefing.*?</p>',
            "<h2>Economy news</h2>",
            news,
            count=1,
            flags=re.DOTALL | re.IGNORECASE,
        )
    safe_date = html.escape(report_date)
    content = validate_daily_content(daily_content) if daily_content is not None else fallback_daily_content(report_date)
    reflection = daily_reflection(report_date, content["reflection"])
    wellbeing = wellbeing_sections(report_date, weather if weather is not None else fetch_weather(report_date), content)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Market &amp; Wellbeing Briefing — {safe_date}</title>
<meta name="description" content="Daily market risk, portfolio, economy, local weather, recipes and healthy-ageing briefing.">
</head><body><article>
{reflection}
{wellbeing}
{market}
{news}
<p><strong>Have a nice, calm, happy and safe day!</strong></p>
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
    if args.wellbeing_fixture:
        daily_content = validate_daily_content(json.loads(Path(args.wellbeing_fixture).read_text(encoding="utf-8")))
    else:
        api_key = os.environ.get("GROQ_API_KEY", "")
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        try:
            daily_content = generate_daily_content(market_meta["date"], api_key, model) if api_key else fallback_daily_content(market_meta["date"])
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            daily_content = fallback_daily_content(market_meta["date"])
    page = merge_pages(
        (market_dir / "index.html").read_text(encoding="utf-8"),
        (news_dir / "index.html").read_text(encoding="utf-8"),
        market_meta["date"], weather, daily_content, news_meta.get("selected_items", 0) > 0,
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
