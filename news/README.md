# Daily World Briefing

A daily, source-grounded briefing covering:

- the global economy with explicit connections to the market-risk briefing watchlist;
- consequential global geopolitics;
- progress in cancer, cardiovascular medicine, eye care, medicines and delivery of care;
- evidence-based longevity updates when credible human evidence exists.

The project collects public RSS/Atom feeds, ranks and deduplicates recent material, asks Groq's free API for a cited synthesis, generates a lightweight static HTML article, deploys it to GitHub Pages and optionally sends it to two Instapaper accounts. If a publisher advertises no working direct feed, a narrowly scoped Google News RSS query is used only to discover pages on that publisher's own domain. If AI is unavailable, the project publishes an extractive source digest instead.

## Editorial safeguards

- AI receives only feed records and must cite their internal IDs. Unknown citations are rejected.
- Regulators, public research institutions and primary records receive the highest ranking.
- Company releases are retained as valuable primary evidence but are visibly labelled as interested-party sources.
- Government and military statements are attributed rather than treated as independent verification.
- Medical stories must state their evidence stage; preliminary findings must not be described as established treatment.
- The report links to every underlying source and contains no medical or investment recommendation.

Configured European sources include the ECB, European Commission, Council of the EU, EMA, NATO, SIPRI, Bruegel, the European Society of Cardiology, Novartis and ASML. US and global sources add FDA, NCI, NEI, Harvard, IMF and BIS coverage. A structured PubMed search adds recent clinical trials, systematic reviews, meta-analyses and guidelines with substantive abstracts for cancer, cardiovascular disease and eye care. Edit `SOURCES` in `scripts/generate_report.py` to add or remove feeds.

## Local verification

The generator requires Python 3.11+ and no third-party packages.

```bash
python -m unittest discover -s tests -v
python scripts/generate_report.py --date 2026-08-05 --output work/test-site --fixture tests/fixture.json --no-ai
python -m http.server 8000 --directory work/test-site
```

For a live run, omit `--fixture`. Set `GROQ_API_KEY` to enable synthesis; without it, the safe extractive fallback is used.

## GitHub setup

1. Create a public repository and place these files at its root.
2. Set **Settings → Pages → Source** to **GitHub Actions**.
3. Add `GROQ_API_KEY` under **Settings → Secrets and variables → Actions**.
4. Add `INSTAPAPER_USERNAME` and `INSTAPAPER_PASSWORD`; optionally add the corresponding `_SECONDARY` secrets.
5. Run **Daily World Briefing** manually once without Instapaper, inspect the Pages article, then run it with notification enabled.

The schedule is 05:35 UTC, which is 07:35 in Prague summer time and 06:35 in winter. GitHub Actions schedules use UTC.
