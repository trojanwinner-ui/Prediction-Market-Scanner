"""Append-only SQLite storage.

Every ingest run appends one row per market and never updates or deletes,
so ``price_snapshots`` is a time series keyed by
(platform, market_id, fetched_at). All rows of one run share a single
``fetched_at``, making "the latest snapshot" a plain MAX(fetched_at) filter.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import NormalizedMarket

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL,          -- UTC ISO-8601
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    outcome_label TEXT NOT NULL,
    yes_bid REAL,                      -- probability in [0,1]; NULL = no quote
    yes_ask REAL,
    mutually_exclusive INTEGER,        -- 1/0/NULL (platform didn't say)
    resolution_date TEXT,
    resolution_source TEXT,
    book_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_market
    ON price_snapshots (platform, market_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_event
    ON price_snapshots (platform, event_id, fetched_at);
"""

INSERT = """
INSERT INTO price_snapshots (
    fetched_at, platform, market_id, event_id, title, outcome_label,
    yes_bid, yes_ask, mutually_exclusive, resolution_date,
    resolution_source, book_ref
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection


def insert_snapshots(
    connection: sqlite3.Connection, markets: Iterable[NormalizedMarket]
) -> int:
    rows = [
        (
            _iso(m.fetched_at),
            m.platform,
            m.market_id,
            m.event_id,
            m.title,
            m.outcome_label,
            m.yes_bid,
            m.yes_ask,
            None if m.mutually_exclusive is None else int(m.mutually_exclusive),
            _iso(m.resolution_date),
            m.resolution_source,
            m.book_ref,
        )
        for m in markets
    ]
    with connection:  # one transaction per ingest batch
        connection.executemany(INSERT, rows)
    return len(rows)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
