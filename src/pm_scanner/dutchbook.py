"""Phase 2: within-platform Dutch-book detection on Kalshi.

Definitions (all prices are probabilities in [0, 1], payout is 1):

- Long side — buy YES on *every* leg of a mutually exclusive event; cost
  is sum(ask). If the outcome set is also exhaustive, exactly one leg pays
  1, so sum(ask) < 1 is an arbitrage. No API field asserts exhaustiveness,
  so long signals carry exhaustiveness_verified = False: candidates for
  review, not claimed arbs. One leg without an ask makes the set
  unbuyable, so such events produce no long signal.

- Short side — buy NO on every *quoted* leg (buying NO at 1 - yes_bid ==
  selling YES at bid). Exclusivity alone suffices: at most one shorted
  leg can win, so worst-case profit is sum(bid) - 1, and any quoted
  subset works because every extra bid only adds profit. Hence missing
  bids shrink the set instead of killing the signal.

- Fees — Kalshi's taker formula, 0.07 * P * (1 - P) per contract at the
  executed price. Buying NO at P = 1 - bid gives the same fee value as
  0.07 * bid * (1 - bid). Contract-count rounding (the real formula
  ceils to the cent per order) and slippage are Phase 4's job; Phase 2
  is deliberately a frictionless-ish upper bound on the edge.

- Annualized — simple scaling, (net / capital) * (365 / days), with days
  running to the *latest* participating leg's resolution date (the
  conservative capital-lockup horizon). None when a date is missing or
  not in the future.

Signals with positive gross edge are stored even when fees turn the net
negative: the collapse from gross to net is the project's finding.
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from . import storage

KALSHI_FEE_RATE = 0.07
MIN_LEGS = 2  # a "set" of one YES is just a market, not a book


@dataclass(frozen=True)
class Leg:
    yes_bid: float | None
    yes_ask: float | None
    resolution_date: datetime | None


@dataclass(frozen=True)
class Signal:
    platform: str
    event_id: str
    side: str  # "long" | "short"
    num_legs: int
    legs_quoted: int
    price_sum: float
    gross_edge: float
    fee_adjusted_edge: float
    capital: float
    days_to_resolution: float | None
    annualized_return: float | None
    exhaustiveness_verified: bool | None


def taker_fee(price: float) -> float:
    return KALSHI_FEE_RATE * price * (1.0 - price)


def evaluate_event(
    platform: str, event_id: str, legs: list[Leg], now: datetime
) -> list[Signal]:
    """Hand-computable core: signals for one mutually exclusive event."""
    if len(legs) < MIN_LEGS:
        return []
    signals = []

    asks = [leg.yes_ask for leg in legs]
    if all(a is not None for a in asks) and sum(asks) < 1.0:
        price_sum = sum(asks)
        gross = 1.0 - price_sum
        net = gross - sum(taker_fee(a) for a in asks)
        signals.append(
            _signal(
                platform, event_id, "long",
                num_legs=len(legs), legs_quoted=len(legs),
                price_sum=price_sum, gross=gross, net=net,
                capital=price_sum,  # the YES set is paid for upfront
                latest=_latest_date(legs), now=now,
                exhaustiveness_verified=False,
            )
        )

    quoted = [leg for leg in legs if leg.yes_bid is not None]
    bids = [leg.yes_bid for leg in quoted]
    if len(quoted) >= MIN_LEGS and sum(bids) > 1.0:
        price_sum = sum(bids)
        gross = price_sum - 1.0
        net = gross - sum(taker_fee(b) for b in bids)
        signals.append(
            _signal(
                platform, event_id, "short",
                num_legs=len(legs), legs_quoted=len(quoted),
                price_sum=price_sum, gross=gross, net=net,
                capital=len(quoted) - price_sum,  # cost of the NO set
                latest=_latest_date(quoted), now=now,
                exhaustiveness_verified=None,
            )
        )
    return signals


def detect_run(
    connection: sqlite3.Connection, run_id: int, fetched_at: datetime
) -> list[Signal]:
    """Detect over one snapshot and append the results to ``signals``.

    Kalshi only (the project's layer 1) and only events the platform
    asserts are mutually exclusive — without that assertion a price sum
    away from 1 means nothing.
    """
    rows = connection.execute(
        "SELECT m.event_id, s.yes_bid, s.yes_ask, m.resolution_date"
        " FROM price_snapshots s JOIN markets m ON m.id = s.market_ref"
        " WHERE s.run_id = ? AND m.platform = 'kalshi'"
        " AND m.mutually_exclusive = 1",
        (run_id,),
    ).fetchall()

    events: dict[str, list[Leg]] = {}
    for event_id, yes_bid, yes_ask, resolution_date in rows:
        events.setdefault(event_id, []).append(
            Leg(
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                resolution_date=(
                    datetime.fromisoformat(resolution_date)
                    if resolution_date
                    else None
                ),
            )
        )

    signals = []
    for event_id, legs in events.items():
        signals.extend(evaluate_event("kalshi", event_id, legs, fetched_at))
    storage.insert_signals(connection, run_id, signals)
    return signals


def _signal(
    platform, event_id, side, *, num_legs, legs_quoted, price_sum, gross,
    net, capital, latest, now, exhaustiveness_verified,
) -> Signal:
    days = annualized = None
    if latest is not None and latest > now:
        days = (latest - now).total_seconds() / 86400.0
        annualized = (net / capital) * (365.0 / days)
    return Signal(
        platform=platform,
        event_id=event_id,
        side=side,
        num_legs=num_legs,
        legs_quoted=legs_quoted,
        price_sum=price_sum,
        gross_edge=gross,
        fee_adjusted_edge=net,
        capital=capital,
        days_to_resolution=days,
        annualized_return=annualized,
        exhaustiveness_verified=exhaustiveness_verified,
    )


def _latest_date(legs: Iterable[Leg]) -> datetime | None:
    dates = [leg.resolution_date for leg in legs if leg.resolution_date]
    return max(dates) if dates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect Dutch books in the latest snapshot")
    parser.add_argument("--db", default="data/snapshots.db")
    args = parser.parse_args(argv)

    connection = storage.connect(args.db)
    try:
        run = storage.latest_run(connection)
        if run is None:
            print("no runs in database; ingest first")
            return 1
        run_id, fetched_at = run
        signals = detect_run(connection, run_id, fetched_at)
    finally:
        connection.close()

    longs = [s for s in signals if s.side == "long"]
    shorts = [s for s in signals if s.side == "short"]
    net_positive = [s for s in signals if s.fee_adjusted_edge > 0]
    print(
        f"run {run_id} ({fetched_at.isoformat()}): {len(signals)} signals"
        f" ({len(longs)} long-unverified, {len(shorts)} short);"
        f" {len(net_positive)} survive fees"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
