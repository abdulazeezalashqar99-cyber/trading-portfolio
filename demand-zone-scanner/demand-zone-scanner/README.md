# Demand Zone Scanner — Engine, Data, Persistence & Alerts

Building this in layers, each independently tested before the next one
depends on it: detection/scoring engine → data pipeline (CoinGecko +
Binance) → persistence (watchlist, alert state) → Telegram alerts via
GitHub Actions (no VPS, per your choice). No exchange trading
capability at any layer, per spec.

Full test suite: **116/116 passing** across all layers (see per-layer
counts below).

## Layer 1: Engine (`engine/`)

Pure Python + pandas. Zero dependency on any exchange API, database, or
Telegram — tested and tuned in complete isolation.

- `engine/models.py` — DemandZone, ZoneEvidence, Freshness, Timeframe, ZoneStatus
- `engine/config.py` — all thresholds + the 100-point ScoringWeights table, centralized and editable
- `engine/swing_points.py` — swing high/low detection (foundation for everything else)
- `engine/structure.py` — Break of Structure, liquidity sweep, equal-lows detection
- `engine/volume.py` — volume expansion ratio
- `engine/fibonacci.py` — 50%/66% retracement confluence check
- `engine/freshness.py` — fresh / tested-once / multiple-tests / invalidated classification
- `engine/zone_detector.py` — finds base + displacement patterns anchored at real swing lows
- `engine/scoring.py` — converts zone evidence + cross-timeframe confluence into a 0-100 score and A+/A/B/C grade

**How scoring works** — per the spec's 100-point table, split into two halves:
- **Quality (60 pts)**, evaluated on a single zone: freshness (15),
  departure/displacement strength (15), volume expansion (10), break of
  structure (10), liquidity sweep (5), fib confluence (5).
- **Confluence (40 pts)**, evaluated across timeframes for the same
  price area: strong 3D demand (15), strong 1D demand (15), 4H
  confirmation (10) — earned only if a corresponding zone exists on that
  timeframe *and* overlaps the same price range.

`score_all_opportunities()` is the per-symbol entry point: pass it
`{Timeframe: [zones]}` for one symbol across all three timeframes, and
it returns the primary opportunities, deduped, scored, sorted, and
filtered to your `exclude_below_score` threshold.

**Performance & robustness review**: profiled at realistic scale (100
symbols x 3 timeframes, ~1 year of history each). Initial implementation
used `.iloc` scalar access and `DataFrame.iterrows()` in hot loops — a
well-known pandas anti-pattern. Rewritten on numpy arrays: **40s -> 7s**
(6x), with the correctness suite passing identically before and after.
Also added fail-loud input validation (`ValueError` on missing columns,
NaN prices, or non-chronological data — a malformed API response should
never silently produce an empty result). 18 tests (`test_engine.py` +
`test_edge_cases.py`) cover pattern detection plus edge cases: empty
input, boundary lengths, flat/zero-volume markets, flash-crash wicks,
duplicate/shuffled timestamps.

## Layer 2: Data pipeline (`data/`)

Universe source is **CoinGecko** (`data/coingecko.py`) — no API key, no
account, nothing to manage. CoinMarketCap remains implemented
(`data/coinmarketcap.py`) as a drop-in alternate provider: both expose
the same `get_top_n()` signature and return the same `UniverseCoin`
model, so `universe_service.py` doesn't care which one is behind it.

- `data/coingecko.py` — Top N universe with category-based stablecoin
  filtering (kept in the universe unless their 24h move is negligible, per spec)
- `data/binance.py` — OHLCV candles (3D/1D/4H) and spot-tradability
  checks, no API key required
- `data/universe_service.py` — combines both, maps universe symbols to
  Binance USDT pairs, drops anything without a valid spot pair, and
  diffs the universe against a previous snapshot to detect Top 100 entries/exits
- `data/http_client.py` — shared retry/backoff (auth errors fail fast,
  rate limits respect `Retry-After`, 5xx/network errors get exponential backoff with jitter)

**This sandbox has no network access**, so `tests/test_data_pipeline.py`
(21 tests) validates all of this against mocked HTTP responses —
including retry behavior, rate-limit handling, malformed data, and
unmapped symbols — rather than live calls. Scripts to run yourself,
no key needed for either:

```bash
python scripts/verify_coingecko.py   # shows the Top 10 by market cap
python scripts/verify_binance.py     # runs real candles through the engine end-to-end
```

## Layer 3: Persistence (`db/`)

`db/schema.sql` has the production PostgreSQL DDL (spec section 14's
required fields). The interesting part is `db/service.py`, which
reconciles each scan's freshly-detected zones against what's already stored:

- **Zone matching**: the engine has no memory between scans — each run
  re-detects zones from scratch. `reconcile_zone()` matches a new
  detection against an existing record for the same symbol/timeframe
  whose price range overlaps ("the same zone reappearing"), and updates
  it in place rather than creating a duplicate row.
- **Alert deduplication** (spec section 11): updating a matched zone
  never touches `alert_sent`/`alert_time`. Once a zone has been alerted,
  it stops appearing in `zones_needing_alert()` even though it keeps
  getting rescanned and rescored every cycle.
- **Backtesting fields survive updates too**: `result` and
  `traded_skipped` (section 14) are only ever changed by explicit calls,
  never overwritten by a rescan.
- **Universe change tracking**: `update_universe()` diffs the new Top N
  against the last stored snapshot and logs entry/exit events — except
  on the very first run ever, which is treated as a baseline, not 100
  simultaneous "entries" (this was caught and fixed during testing).

**Design note on testing**: this sandbox has neither `psycopg2` nor a
Postgres server available, and no network to get either. Rather than
skip testing the persistence logic, it's built behind a `ZoneStore`
interface (`db/store.py`) with two implementations: `InMemoryZoneStore`
(pure Python, zero dependencies) and `PostgresZoneStore` (psycopg2,
structurally mirrors the in-memory one method-for-method). All the
actual decision logic in `service.py` is tested against the in-memory
store — 18 tests (`test_db.py`) covering matching, dedup, and universe
diffing. The Postgres adapter itself is comparatively thin (translating
the same operations to SQL) but is **not** exercised by this test suite —
before relying on it, run a real scan against an actual Postgres
instance and sanity-check the results, ideally by adapting
`test_db.py` to run against `PostgresZoneStore` too once you have a
database to point it at.

```bash
# apply the schema to a running Postgres instance
psql -U <user> -d <db> -f db/schema.sql

# see the whole pipeline wired together end-to-end (uses the in-memory
# store by default; swap in PostgresZoneStore per the comment in the file)
python scripts/run_scan_example.py
```

## Layer 4: Alerts — Telegram via GitHub Actions (`notifications/`, `.github/workflows/`)

No VPS: per your choice, this runs on GitHub Actions' free scheduler
instead of a continuously-running process. Every 30 minutes (default),
a workflow run builds the universe, scans it, reconciles the watchlist,
and sends Telegram alerts for anything newly eligible — then commits the
updated SQLite state file back into the repo so the next run picks up
where this one left off.

- `notifications/notifier.py` — formats the alert exactly to spec
  section 10's example (zone range, score, freshness/departure/volume/
  BOS/liquidity/confluence checkmarks, TradingView link), plus the
  section 8 BTC-weak warning block. Never marks a zone as alerted itself
  — that only happens after a *confirmed* successful send
  (`confirm_alert_sent` in `db/service.py`), so a failed send can't
  silently lose an alert.
- `engine/market_condition.py` — the BTC Strong/Neutral/Weak classifier
  (spec section 8), using swing structure (higher highs/lows vs. lower
  highs/lows) plus price-vs-moving-average. Deliberately simple and
  rule-based, per the spec's explicit instruction not to reach for AI
  here. **Caught during testing**: an initial test fixture used
  perfectly smooth synthetic trends, which have *zero* interior swing
  points by mathematical definition — the classifier looked broken but
  the test data was unrealistic. Re-validated against realistic noisy
  trend data: the property that actually matters (never calling a
  downtrend "Strong" or an uptrend "Weak") held across 60 trials, 0
  wrong-direction calls; some genuinely ambiguous/choppy data correctly
  falls back to Neutral rather than guessing.
- `db/sqlite_store.py` — SQLite implementation of the same `ZoneStore`
  interface as the Postgres/in-memory stores. Chosen for this deployment
  path since there's no VPS to host Postgres on, and it's a legitimate
  production choice at this system's scale (order of 100 symbols, a
  handful of zones each). This is the one backend tested against a
  **real embedded database**, not mocks or a fake — 15 tests, including
  one that closes and reopens the connection to confirm state actually
  survives a fresh process, which is exactly what happens between
  scheduled runs.
- `scripts/run_scheduled_scan.py` — the full pipeline in one entrypoint:
  universe → BTC condition → per-symbol scan across 3D/1D/4H → reconcile
  → alert. Dry-run tested end-to-end with mocked data providers.
- `.github/workflows/scan.yml` — the scheduled workflow.
- `scripts/verify_telegram.py` — bot setup walkthrough (via @BotFather)
  plus a live test-message script for you to run once you have a token.

**GitHub Actions cost**: each run (checkout + install + ~100-symbol
scan) takes roughly 1.5–3 minutes. On a **public** repo, GitHub-hosted
runners are free with no minute cap — you can safely run every 5–15
minutes. On a **private** repo, the free tier is 2,000 minutes/month:
every 30 minutes (~1,440 runs/month) comfortably fits; every 15 minutes
likely will not. The workflow defaults to 30 minutes for this reason —
change the cron expression in `scan.yml` once you've decided which way
you're going. Also worth knowing: GitHub auto-disables a scheduled
workflow after 60 days with no repo activity, though the workflow
committing its own state file back on every run that has state changes
should keep resetting that clock.

```bash
export TELEGRAM_BOT_TOKEN=your-bot-token
export TELEGRAM_CHAT_ID=your-chat-id
python scripts/verify_telegram.py   # sends a real test message
```

Then set both as GitHub repo secrets (Settings → Secrets and variables →
Actions) with those exact names — never commit real values.

## Layer 5: Web dashboard (`dashboard/`, `docs/`)

Also static, per the same no-VPS philosophy: the scheduled workflow
regenerates it every scan and publishes it via **GitHub Pages**, so it
doubles as the "check whenever I want" website from your original ask —
no server, no separate hosting to manage.

- `dashboard/data_builder.py` — pure data-preparation logic (grouping
  into A+/A opportunities, active watchlist, triggered alerts, system
  status per spec section 15), fully unit-tested independent of any
  rendering.
- `dashboard/template.html` — the page itself: fetches `data.json` and
  renders it client-side. Designed deliberately rather than defaulted —
  a dark ink-navy base with monospace numerals for price/score data
  (a trading-terminal convention) and grade tiers color-coded green →
  blue → amber, rather than a single flat accent color. Responsive down
  to mobile (spec requirement). Includes a live filter bar (symbol
  search, timeframe, grade, minimum score, BTC condition, confluence)
  that narrows all four sections simultaneously, entirely client-side —
  no server round-trip, since `data.json` already contains everything
  needed to filter locally.
- `dashboard/generate_dashboard.py` — writes `docs/index.html` +
  `docs/data.json` from current store state.

**Testing note**: this sandbox has a real Node.js runtime, so unlike
most JS shipped in this project, the dashboard's actual rendering AND
filtering logic was extracted and executed for real (not just read) —
34 assertions total, covering number/date formatting, card templates,
and every filter (symbol, timeframe, grade, min score, BTC condition,
confluence — individually and combined with AND logic). One real bug
was caught and fixed along the way: the number-trimming function's
regex replace had dead logic that silently never trimmed anything.
Separately, 5 Python tests confirm the generator produces valid,
parseable `data.json` and non-empty HTML.

**One-time setup**: GitHub doesn't allow fully automating Pages
activation from a workflow, so this one step happens in the GitHub UI:
repo Settings → Pages → Source → "Deploy from a branch" → branch `main`,
folder `/docs`. After that, the scheduled workflow keeps the site
current automatically — no re-toggling needed.

## Running all the tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Current total: **116/116 passing** across engine, edge cases, data
pipeline, persistence (in-memory + real SQLite), BTC condition,
notifications, and the dashboard.

## What's next (not built yet)

1. Demand Zone detection + scoring engine (done)
2. Data pipeline (CoinGecko Top 100 + Binance OHLCV) (done)
3. PostgreSQL persistence, watchlist, alert-state tracking (done)
4. Telegram bot integration, scheduled via GitHub Actions (done)
5. Web dashboard, published via GitHub Pages (done — this delivery)
6. Docker + VPS deployment — optional now; GitHub Actions replaced the
   always-on alert loop, and GitHub Pages replaced the dashboard host.
   Only worth revisiting if you outgrow the free tiers or want
   everything on infrastructure you fully control.

## Tuning

Everything is centralized in `engine/config.py` (detection thresholds,
scoring weights). Nothing else in the codebase hard-codes a threshold.
To retune, adjust `DetectionConfig` or `ScoringWeights` and re-run the
tests — no detection logic needs to change.
