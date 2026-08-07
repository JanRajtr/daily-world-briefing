# Denní přehled trhů a zdravého života

Plně automatizovaný český přehled. GitHub Actions počítá transparentní skóre tržního rizika a seznam nástrojů v EUR, sbírá důvěryhodné veřejné ekonomické zdroje a přidává místní počasí, jídelníček a tip pro zdravé stárnutí. Groq slouží především k věrnému překladu a zkrácení dodaného zdrojového obsahu, nikoli k vymýšlení faktů.

Praktická ranní část může dále obsahovat český svátek a státní svátek, kurzovní lístek ČNB, kvalitu ovzduší a významný pyl, UV či jiné mimořádné počasí, události relevantní pro portfolio, zdrojové vysvětlení neobvyklých tržních pohybů a českou kulturní nebo historickou stopu dne. Každá síťová část se při nedostupnosti jednoduše vynechá.

Citát, buddhistické učení, recepty a tip pro zdravé stárnutí se každý den vyhledávají na webu pomocí Groq Compound. Každá zobrazená položka musí mít autora či vydavatele, kontext a přímý odkaz na zdroj. Nedohledaná nebo neplatná část se přeskočí; repozitář neobsahuje žádnou obsahovou rotaci ani náhradní hodnoty.

Je-li při daném spuštění nalezen ověřitelný historický citát a skutečná myšlenka buddhistického učitele či badatele, vydání jimi začíná. Bez výsledku se tato část nezobrazí.

Rešerše citátu a buddhistického učení běží odděleně od ostatního denního obsahu a při přechodné chybě se až dvakrát zopakuje. Při omezení rychlosti respektuje serverový `Retry-After`; jinak použije exponenciální prodlevu. Stav, počet pokusů a důvod případného vynechání se ukládají do `site/report.json`; chyby Groq jsou současně viditelné v logu GitHub Actions.

## What it produces

- `https://YOUR-USER.github.io/YOUR-REPO/index.html` — the current report, replaced on every run
- `.../report.json` — metadata for the current report
- a composite 0–100 score based on VIX, S&P 500 drawdown and realized volatility, high-yield spreads, financial conditions, the 10Y–2Y curve, unemployment momentum, CPI inflation and a Brent oil shock
- a market watchlist showing each instrument's EUR price, one-day and one-month moves, distance from its trailing 252-session high, and position versus its 50-day average

The HTML uses a single semantic `<article>`, real headings and lists, no JavaScript, no external fonts and no images. This gives Instapaper a clean article to parse and keeps the Kobo version light. The watchlist deliberately uses list items instead of a table because Kobo's Instapaper integration flattens table cells.

## Exact deployment steps

1. Create a new **public** GitHub repository. On GitHub Free, Pages is available for public repositories.
2. Upload the *contents* of this bundle to the repository root (so `.github/workflows/daily-briefing.yml` is at that exact path) and commit to the default branch.
3. Open **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Open **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `GROQ_API_KEY` — klíč Groq používaný pro zdrojově podložené české shrnutí ekonomických zpráv a webovou rešerši s překladem citátů, učení, receptů a zdravotního tipu.
   - `INSTAPAPER_USERNAME` — the Instapaper email address or username.
   - `INSTAPAPER_PASSWORD` — the account password. If the account has no password, save an empty value if GitHub permits it; otherwise use any placeholder value, which the Simple API documentation says is accepted for passwordless accounts.
   - Optional second account: `INSTAPAPER_USERNAME_SECONDARY` and `INSTAPAPER_PASSWORD_SECONDARY`. When the secondary username is absent, delivery to the primary account continues normally.
5. Open **Actions → Daily World Briefing → Run workflow**. Leave the date blank and `Send the published URL to Instapaper` off for the first test.
6. When the run is green, open the deployment URL shown in the `deploy` job and verify the article.
7. Run it once more with `Send the published URL to Instapaper` enabled. Confirm that the article appears in Instapaper and then sync the Instapaper integration on Kobo.

The scheduled run is every day at **06:15 UTC** (07:15 in Prague winter time, 08:15 in summer time). Edit the cron line in `.github/workflows/daily-briefing.yml` if you prefer another time. GitHub schedules are UTC and may start a little late during periods of high load.

## How the workflow behaves

1. Determines today's date in `Europe/Prague` (or validates the manual date).
2. Downloads CSV observations directly from public FRED graph endpoints; no API key is used.
3. Retries transient downloads. A partial report is allowed only while at least 50% of the configured weight remains; missing indicators are disclosed in the article.
4. Replaces `index.html` and its tiny JSON metadata record with the current report.
5. Commits the generated `site/` files back to the repository using GitHub's built-in token.
6. Deploys only `site/` through the official GitHub Pages actions.
7. Waits until the dated page is reachable, then calls `https://www.instapaper.com/api/add` over HTTPS using HTTP Basic Auth. HTTP 201 is required.

Scheduled runs always notify the primary Instapaper account and the optional secondary account when configured. Manual runs do the same only when the workflow input is enabled. Instapaper receives a run-specific query parameter so a manual rerun fetches the newly generated page instead of reusing a cached copy.

## Methodology

Each indicator is converted to a risk score from 0 to 100 by linear interpolation between five fixed thresholds. The composite is the weighted average across available indicators:

| Indicator | Weight | Direction interpreted as risk |
|---|---:|---|
| VIX | 18% | higher |
| US high-yield option-adjusted spread | 18% | wider |
| S&P 500 trailing-252-session drawdown | 14% | deeper |
| S&P 500 30-session realized volatility | 10% | higher |
| Chicago Fed National Financial Conditions Index | 10% | tighter / higher |
| US 10Y–2Y yield curve | 8% | more inverted |
| US unemployment, three-observation change | 8% | rising |
| US CPI, year over year | 8% | higher |
| Brent oil, absolute 20-observation move | 6% | larger shock |

Thresholds are intentionally explicit in `scripts/generate_report.py`; edit them there to match your own framework. The five output states are Low (0–24), Guarded (25–44), Elevated (45–64), High (65–79) and Severe (80–100).

This is monitoring, not a forecast, trading system or investment recommendation. FRED republishes several series from third parties; each report names the underlying source and links to the relevant series page. Daily series can contain holidays and delayed observations, while weekly/monthly indicators naturally carry older as-of dates.

## Local test

The generator needs only Python 3.11+ and the standard library.

```bash
python tests/make_fixture.py
python scripts/generate_report.py --date 2025-02-03 --output work/test-site --fixture work/fixture.json
python -m http.server 8000 --directory work/test-site
```

Then open `http://localhost:8000/index.html`.

For a live-data test, omit `--fixture`. The machine must be able to reach `fred.stlouisfed.org`.

## Troubleshooting

- **Pages deployment fails:** confirm Settings → Pages → Source is set to GitHub Actions and that Actions are allowed for the repository.
- **Commit/push is rejected:** in Settings → Actions → General, ensure workflow permissions allow read/write. The workflow also requests `contents: write` explicitly. Organization policy can override this.
- **Instapaper returns 403:** recreate both secrets and make sure the username is the Instapaper username, not necessarily an email address.
- **Instapaper returns 400:** the URL may be missing or the API rate limit may have been reached. Inspect the job log; credentials are never printed.
- **Report generation refuses to publish:** more than half of the risk-model weight was unavailable. Re-run later rather than publishing an overly incomplete score.
- **Schedule did not run:** scheduled workflows run only from the default branch and GitHub may disable schedules after 60 days without repository activity in a public repository. A manual run reactivates the workflow.

## Data and platform documentation

- FRED series pages and downloads: <https://fred.stlouisfed.org/>
- GitHub Pages custom workflows: <https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>
- Instapaper Simple API: <https://www.instapaper.com/api/simple>

No secrets are embedded in the generated site, repository or Pages artifact.
