"""SQLite storage: market dimension + append-only numeric facts.

Four tables:

- ``runs``: one row per ingest run; the run timestamp lives here once.
- ``markets``: one row per (platform, market_id); metadata is upserted so
  it always reflects the latest crawl (it's a dimension, not history).
- ``price_snapshots``: (run_id, market_ref, yes_bid, yes_ask), append-only.
  This is the time series; a run's rows form one coherent snapshot.
- ``signals``: Phase 2 detector output, appended per run.

Prices are the only per-run facts, so snapshot rows are pure numbers. The
repeated strings (titles, sources, ...) that made the v1 flat rows ~460
bytes each live once in the dimension — that's what keeps the committed
DB's per-run growth small enough to git-scrape 4x/day.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from .models import NormalizedMarket

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL UNIQUE      -- UTC ISO-8601
);
CREATE TABLE IF NOT EXISTS markets (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    outcome_label TEXT NOT NULL,
    mutually_exclusive INTEGER,          -- 1/0/NULL (platform didn't say)
    resolution_date TEXT,
    resolution_source TEXT,
    book_ref TEXT,
    UNIQUE (platform, market_id)
);
CREATE INDEX IF NOT EXISTS idx_markets_event ON markets (platform, event_id);
CREATE TABLE IF NOT EXISTS price_snapshots (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    market_ref INTEGER NOT NULL REFERENCES markets(id),
    yes_bid REAL,                        -- probability in [0,1]; NULL = no quote
    yes_ask REAL,
    PRIMARY KEY (run_id, market_ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_snapshots_market ON price_snapshots (market_ref);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    platform TEXT NOT NULL,
    event_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long', 'short')),
    num_legs INTEGER NOT NULL,
    legs_quoted INTEGER NOT NULL,
    price_sum REAL NOT NULL,
    gross_edge REAL NOT NULL,
    fee_adjusted_edge REAL NOT NULL,
    capital REAL NOT NULL,
    days_to_resolution REAL,
    annualized_return REAL,
    -- 0 on long signals: the API asserts exclusivity but nothing asserts
    -- exhaustiveness, so long signals are candidates pending review.
    -- NULL on short signals: exclusivity alone makes them valid.
    exhaustiveness_verified INTEGER
);
CREATE INDEX IF NOT EXISTS idx_signals_run ON signals (run_id);
CREATE TABLE IF NOT EXISTS signal_frictions (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    checked_at TEXT NOT NULL,                -- books are fetched live, hours
    contracts_requested REAL NOT NULL,       -- after the snapshot; this
    contracts_filled REAL NOT NULL,          -- timestamp keeps that honest
    snapshot_gross_per_contract REAL NOT NULL,
    walked_gross_total REAL,                 -- NULL = set unfillable
    fees_total REAL,
    slippage_total REAL,
    net_total REAL
);
"""

UPSERT_MARKET = """
INSERT INTO markets (
    platform, market_id, event_id, title, outcome_label,
    mutually_exclusive, resolution_date, resolution_source, book_ref
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (platform, market_id) DO UPDATE SET
    event_id = excluded.event_id,
    title = excluded.title,
    outcome_label = excluded.outcome_label,
    mutually_exclusive = excluded.mutually_exclusive,
    resolution_date = excluded.resolution_date,
    resolution_source = excluded.resolution_source,
    book_ref = excluded.book_ref
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def insert_snapshots(
    connection: sqlite3.Connection, markets: Iterable[NormalizedMarket]
) -> int:
    """Record one run: upsert the dimension, append one fact row per market.

    All rows must share one ``fetched_at`` (the ingest run's timestamp).
    """
    rows = list(markets)
    if not rows:
        return 0
    timestamps = {m.fetched_at for m in rows}
    if len(timestamps) != 1:
        raise ValueError("one insert_snapshots call must be one run")

    with connection:  # one transaction: a run is all-or-nothing
        run_id = connection.execute(
            "INSERT INTO runs (fetched_at) VALUES (?)", (_iso(rows[0].fetched_at),)
        ).lastrowid
        connection.executemany(
            UPSERT_MARKET,
            [
                (
                    m.platform,
                    m.market_id,
                    m.event_id,
                    m.title,
                    m.outcome_label,
                    None if m.mutually_exclusive is None else int(m.mutually_exclusive),
                    _iso(m.resolution_date),
                    m.resolution_source,
                    m.book_ref,
                )
                for m in rows
            ],
        )
        refs = dict(
            connection.execute("SELECT platform || ':' || market_id, id FROM markets")
        )
        # OR REPLACE: pagination drift can hand us the same market twice in
        # one crawl; the later observation wins rather than aborting the run.
        connection.executemany(
            "INSERT OR REPLACE INTO price_snapshots"
            " (run_id, market_ref, yes_bid, yes_ask) VALUES (?, ?, ?, ?)",
            [
                (run_id, refs[f"{m.platform}:{m.market_id}"], m.yes_bid, m.yes_ask)
                for m in rows
            ],
        )
    return len(rows)


def latest_run(connection: sqlite3.Connection) -> tuple[int, datetime] | None:
    row = connection.execute(
        "SELECT id, fetched_at FROM runs ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return row[0], datetime.fromisoformat(row[1])


def insert_signals(
    connection: sqlite3.Connection, run_id: int, signals: Sequence
) -> int:
    with connection:
        connection.executemany(
            "INSERT INTO signals ("
            " run_id, platform, event_id, side, num_legs, legs_quoted,"
            " price_sum, gross_edge, fee_adjusted_edge, capital,"
            " days_to_resolution, annualized_return, exhaustiveness_verified"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    s.platform,
                    s.event_id,
                    s.side,
                    s.num_legs,
                    s.legs_quoted,
                    s.price_sum,
                    s.gross_edge,
                    s.fee_adjusted_edge,
                    s.capital,
                    s.days_to_resolution,
                    s.annualized_return,
                    None
                    if s.exhaustiveness_verified is None
                    else int(s.exhaustiveness_verified),
                )
                for s in signals
            ],
        )
    return len(signals)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
