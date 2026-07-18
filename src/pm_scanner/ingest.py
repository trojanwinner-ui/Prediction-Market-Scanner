"""Ingest CLI: pull both platforms, normalize, append to SQLite.

Usage: python -m pm_scanner.ingest --db data/snapshots.db
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from . import kalshi, polymarket, storage


def run(
    db_path: str,
    kalshi_events: Iterable[dict[str, Any]],
    gamma_markets: Iterable[dict[str, Any]],
    fetched_at: datetime,
) -> dict[str, int]:
    """Pure orchestration over already-fetched iterables (testable offline)."""
    rows = []
    kalshi_count = 0
    for event in kalshi_events:
        normalized = kalshi.normalize_event(event, fetched_at)
        kalshi_count += len(normalized)
        rows.extend(normalized)

    poly_count = skipped = 0
    for raw in gamma_markets:
        normalized_market = polymarket.normalize_market(raw, fetched_at)
        if normalized_market is None:
            skipped += 1
            continue
        poly_count += 1
        rows.append(normalized_market)

    connection = storage.connect(db_path)
    try:
        inserted = storage.insert_snapshots(connection, rows)
    finally:
        connection.close()
    return {
        "kalshi": kalshi_count,
        "polymarket": poly_count,
        "polymarket_skipped": skipped,
        "inserted": inserted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/snapshots.db")
    args = parser.parse_args(argv)

    # One timestamp for the whole run so its rows form a coherent snapshot.
    fetched_at = datetime.now(timezone.utc)
    counts = run(
        args.db,
        kalshi_events=kalshi.KalshiClient().iter_events(),
        gamma_markets=polymarket.GammaClient().iter_markets(),
        fetched_at=fetched_at,
    )
    print(
        f"{fetched_at.isoformat()} -> {args.db}: "
        f"{counts['inserted']} rows inserted "
        f"(kalshi {counts['kalshi']}, polymarket {counts['polymarket']}, "
        f"skipped non-binary {counts['polymarket_skipped']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
