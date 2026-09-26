# mlb-edge — retired

**This project is decommissioned as of 2026-09-26 and is no longer maintained.**

The automated pipeline is disabled, the published site has been taken down, and this repository is
archived read-only. Nothing here is being updated, and the data stops at the end of the 2026 MLB
regular season.

## What it was

A daily MLB totals-monitor. A Python pipeline pulled schedules, lineups, weather and odds, graded
historical results on closing lines, and published a static page flagging games that matched
backtested edge conditions. It was built to *inform* on line value — it never shopped odds or
placed bets.

## If you found this

Do not treat anything in this repo as live betting advice. The one edge it validated (UNDER on
totals of 8.0 and 9.0 at standard vig) was worth roughly 4–6% ROI across 2021–2026 — thin, highly
price-sensitive, and measured on samples of a few hundred plays. Six other angles were tested and
produced nothing. Sports betting markets are close to efficient; treat any claimed edge, including
the ones here, with suspicion.

## Layout

- `pipeline/` — data ingestion, model, backtests and research scripts
- `docs/` — the static PWA that was served via GitHub Pages
- `data/` — cached odds and season data (2018, 2021–2025)
- `archive/` — earlier iterations
