"""Phase 4 (pairs-independent half): the gross->net waterfall, on Kalshi.

Re-prices each Phase 2 signal as an actual execution at a target size:
walk today's order books leg by leg, charge the exact fee schedule, and
report gross -> after-slippage -> after-fees. The same machinery will
price the cross-platform legs once the user's verified pairs exist.

Honesty notes, also printed with results:
- Books are fetched live, so they are *hours newer* than the snapshot
  that produced the signal; a vanished edge may have been taken, or may
  never have been fillable. Both readings support the thesis, but the
  comparison is stored (snapshot edge vs walked edge) rather than hidden.
- The long side still carries the exhaustiveness caveat from Phase 2;
  frictions don't fix an unverified outcome set.
- Feasible size is the *minimum* leg depth: an arb needs every leg, so
  one thin book caps the whole set (that cap is itself a friction).
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import storage
from .fees import kalshi_order_fee
from .http import get_json
from .kalshi import KalshiClient
from .slippage import kalshi_no_ask_levels, kalshi_yes_ask_levels, walk_book


@dataclass(frozen=True)
class FrictionReport:
    signal_id: int
    side: str
    event_id: str
    contracts_requested: float
    contracts_filled: float
    snapshot_gross_per_contract: float
    walked_gross_total: float | None  # None when the set is unfillable
    fees_total: float | None
    slippage_total: float | None
    net_total: float | None


def deep_check(
    connection,
    *,
    get_orderbook: Callable[[str], dict[str, Any]],
    get_fee_multiplier: Callable[[str], float],
    contracts: float,
    limit: int | None = None,
) -> list[FrictionReport]:
    run = storage.latest_run(connection)
    if run is None:
        return []
    run_id, _ = run
    checked_at = datetime.now(timezone.utc)

    query = (
        "SELECT id, event_id, side, gross_edge FROM signals"
        " WHERE run_id = ? AND platform = 'kalshi'"
        " ORDER BY fee_adjusted_edge DESC"
    )
    signal_rows = connection.execute(query, (run_id,)).fetchall()
    if limit is not None:
        signal_rows = signal_rows[:limit]

    books: dict[str, dict[str, Any]] = {}
    reports = []
    for signal_id, event_id, side, snapshot_gross in signal_rows:
        legs = connection.execute(
            "SELECT m.market_id, s.yes_bid, s.yes_ask"
            " FROM price_snapshots s JOIN markets m ON m.id = s.market_ref"
            " WHERE s.run_id = ? AND m.platform = 'kalshi' AND m.event_id = ?",
            (run_id, event_id),
        ).fetchall()
        # Evaluate the signal as defined at detection time: the long side
        # committed to every leg, the short side to the legs that had bids.
        if side == "short":
            legs = [l for l in legs if l[1] is not None]
        for ticker, _, _ in legs:
            if ticker not in books:
                books[ticker] = get_orderbook(ticker)
        multiplier = get_fee_multiplier(event_id.split("-")[0])
        reports.append(
            _price_set(
                signal_id, event_id, side, snapshot_gross,
                [books[t] for t, _, _ in legs], contracts, multiplier,
            )
        )

    _store(connection, reports, checked_at)
    return reports


def _price_set(
    signal_id, event_id, side, snapshot_gross, leg_books, contracts, multiplier
) -> FrictionReport:
    to_levels = kalshi_yes_ask_levels if side == "long" else kalshi_no_ask_levels
    ladders = [to_levels(book) for book in leg_books]

    # First pass finds the feasible size: one thin leg caps the whole set.
    feasible = min(walk_book(l, contracts).filled for l in ladders)
    if feasible <= 0:
        return FrictionReport(
            signal_id=signal_id, side=side, event_id=event_id,
            contracts_requested=contracts, contracts_filled=0.0,
            snapshot_gross_per_contract=snapshot_gross,
            walked_gross_total=None, fees_total=None,
            slippage_total=None, net_total=None,
        )

    fills = [walk_book(l, feasible) for l in ladders]
    cost = sum(f.cost for f in fills)
    # Payout per set of `feasible` contracts: the long YES set pays 1 per
    # set (if exhaustive); the short's NO set pays (legs - 1) because at
    # most one shorted leg wins.
    payout = feasible if side == "long" else feasible * (len(fills) - 1)
    gross = payout - cost
    fees = sum(kalshi_order_fee(list(f.fills), multiplier) for f in fills)
    slippage = sum(f.cost - f.filled * f.top_price for f in fills)
    return FrictionReport(
        signal_id=signal_id, side=side, event_id=event_id,
        contracts_requested=contracts, contracts_filled=feasible,
        snapshot_gross_per_contract=snapshot_gross,
        walked_gross_total=gross, fees_total=fees,
        slippage_total=slippage, net_total=gross - fees,
    )


def _store(connection, reports, checked_at) -> None:
    with connection:
        connection.executemany(
            "INSERT INTO signal_frictions ("
            " signal_id, checked_at, contracts_requested, contracts_filled,"
            " snapshot_gross_per_contract, walked_gross_total, fees_total,"
            " slippage_total, net_total"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.signal_id, checked_at.isoformat(), r.contracts_requested,
                    r.contracts_filled, r.snapshot_gross_per_contract,
                    r.walked_gross_total, r.fees_total, r.slippage_total,
                    r.net_total,
                )
                for r in reports
            ],
        )


def _live_fee_multiplier(client: KalshiClient) -> Callable[[str], float]:
    cache: dict[str, float] = {}

    def get(series_ticker: str) -> float:
        if series_ticker not in cache:
            try:
                payload = get_json(client._client, f"/series/{series_ticker}")
                cache[series_ticker] = float(
                    (payload.get("series") or {}).get("fee_multiplier") or 1.0
                )
            except Exception:
                cache[series_ticker] = 1.0  # the general schedule
        return cache[series_ticker]

    return get


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-price the latest run's signals against live order books"
    )
    parser.add_argument("--db", default="data/snapshots.db")
    parser.add_argument("--contracts", type=float, default=100.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    client = KalshiClient()
    connection = storage.connect(args.db)
    try:
        reports = deep_check(
            connection,
            get_orderbook=client.get_orderbook,
            get_fee_multiplier=_live_fee_multiplier(client),
            contracts=args.contracts,
            limit=args.limit,
        )
    finally:
        connection.close()

    if not reports:
        print("no signals to check; run ingest + dutchbook first")
        return 1

    fillable = [r for r in reports if r.contracts_filled > 0]
    survivors = [r for r in fillable if r.net_total and r.net_total > 0]
    print(
        f"checked {len(reports)} signals at {args.contracts:g} contracts/leg:"
        f" {len(fillable)} fillable at any size,"
        f" {len(survivors)} net-positive after walking books + exact fees"
        f" (books are live; snapshot is older)"
    )
    for r in sorted(
        survivors, key=lambda r: r.net_total or 0.0, reverse=True
    )[:10]:
        print(
            f"  {r.side:5} {r.event_id}: filled {r.contracts_filled:g}/leg,"
            f" gross ${r.walked_gross_total:.2f} - slippage-in"
            f" ${r.slippage_total:.2f} - fees ${r.fees_total:.2f}"
            f" -> net ${r.net_total:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
