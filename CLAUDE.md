# prediction-market-scanner

## Project context

Public GitHub portfolio project: an arbitrage scanner for prediction markets, in two layers:

1. **Within-platform Dutch-book detection on Kalshi** — flag mutually exclusive, exhaustive
   outcome sets whose prices don't sum to 100¢.
2. **Cross-platform divergence** — compare equivalent Kalshi and Polymarket markets with
   rigorous friction accounting.

**Thesis:** apparent arbitrage mostly collapses once fees, slippage, capital lockup, and
resolution-criteria differences are accounted for. Demonstrating that collapse rigorously
*is* the finding. This project makes **no claim of edge over the market**.

Kalshi is the primary venue (public read-only data, CFTC-regulated, legally tradable by US
persons). Polymarket is the comparison leg, with the caveat that the easily-scraped global
venue isn't legally tradable by US users.

## Stack

Python 3.11+, `uv`, `httpx`, `pydantic`, `pandas`, SQLite, `pytest`, GitHub Actions,
GitHub Pages.

## Working agreement

- Work **one phase at a time**. Stop after each phase and wait for the user's review.
  Do not start the next phase unprompted.
- **Do not write Phase 3 equivalence rules** — the user owns that design. Scaffolding only,
  and only when asked.
- For Phase 2, **critique the user's logic before implementing**; don't invent the math
  for them.
- Explain non-obvious decisions in comments. Small, reviewable diffs.
- **Ask before installing packages or committing.** Never commit secrets.

## Phase plan

- **Phase 0 — Scaffolding:** package structure, `uv` setup, pinned Python version,
  `pytest` with one trivial passing test, a GitHub Actions workflow running the suite.
  Done when CI is green.
- **Phase 1 — Ingestion & normalization:** Kalshi client (cursor pagination, 429 backoff
  honoring `Retry-After`); Polymarket Gamma client (offset pagination) plus CLOB for depth;
  one normalized pydantic schema both map into; prices normalized to probability space
  [0,1] (Kalshi quotes cents, Polymarket decimals); multi-outcome events stored as multiple
  binary markets sharing an `event_id` so Phase 2 is a GROUP BY; capture `resolution_date`
  and `resolution_source` now even though Phase 1 doesn't use them (Phase 3's raw
  material); append-only timestamped `price_snapshots` table in SQLite so the DB is a time
  series; mapper tests against saved fixtures covering empty book sides (Kalshi uses 0 for
  "no quote"), JSON-string-encoded list fields from Gamma, and non-binary markets that
  should be skipped; scheduled Action pulling both platforms.
- **Phase 2 — Dutch-book detector:** cost to buy full YES set vs 100¢, plus the short
  side; annualize edge by time-to-resolution and subtract fees; store signals with
  timestamps; tests with hand-constructed price sets.
- **Phase 3 — Cross-platform matching (user-owned):** candidate generation only from
  Claude; equivalence rules and the curated verified-pairs file belong to the user.
- **Phase 4 — Friction engine:** Kalshi's price-dependent fee formula, slippage by walking
  the order book for a target size, gross→net waterfall per pair.
- **Phase 5 — Static HTML dashboard** on GitHub Pages via Actions, plus README
  methodology.

## Decisions log

- Kalshi public host is `api.elections.kalshi.com` (serves all markets; the
  legacy host 401s). Prices arrive as dollar strings, `"0.0000"` = no quote.
- Gamma caps `offset` at 2000; full crawls use `/markets/keyset` with
  `after_cursor`.
- Storage is runs/markets(dimension)/price_snapshots(facts)/signals; ~5 MB
  per run committed to the repo at 6h cadence (user accepted this rate).
- Phase 2 (user decisions, 2026-07-18): long side flagged
  `exhaustiveness_verified=false` (APIs assert exclusivity, never
  exhaustiveness); short side needs exclusivity only and may use a quoted
  subset; fees = gross plus Kalshi 0.07·P·(1−P) approximation (Phase 4
  replaces it); annualization is simple r·365/days to the latest leg date;
  detector scope is Kalshi only.
