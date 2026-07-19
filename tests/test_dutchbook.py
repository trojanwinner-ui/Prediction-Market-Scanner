"""Hand-constructed price sets for the Dutch-book detector.

Every expected number here is computed by hand in the comments so a
reviewer can check the arithmetic without running anything.
"""

from datetime import datetime, timedelta, timezone

import pytest

from pm_scanner.dutchbook import Leg, detect_run, evaluate_event, taker_fee
from pm_scanner.models import NormalizedMarket
from pm_scanner.storage import connect, insert_snapshots, latest_run

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
ONE_YEAR = NOW + timedelta(days=365)


def leg(bid=None, ask=None, res=ONE_YEAR):
    return Leg(yes_bid=bid, yes_ask=ask, resolution_date=res)


def test_long_arb_detected_with_hand_checked_numbers():
    # asks 0.30 + 0.30 + 0.35 = 0.95 -> gross = 0.05
    # fees = 0.07*(0.30*0.70)*2 + 0.07*(0.35*0.65)
    #      = 0.0147 + 0.0147 + 0.015925 = 0.0453250 -> net = 0.0046750
    legs = [leg(ask=0.30), leg(ask=0.30), leg(ask=0.35)]
    (signal,) = [s for s in evaluate_event("kalshi", "EV", legs, NOW) if s.side == "long"]
    assert signal.price_sum == pytest.approx(0.95)
    assert signal.gross_edge == pytest.approx(0.05)
    assert signal.fee_adjusted_edge == pytest.approx(0.004675)
    assert signal.capital == pytest.approx(0.95)  # the YES set is prepaid
    assert signal.exhaustiveness_verified is False  # candidate, not a claim
    # exactly one year out: annualized == net / capital
    assert signal.days_to_resolution == pytest.approx(365.0)
    assert signal.annualized_return == pytest.approx(0.004675 / 0.95)


def test_short_arb_detected_with_hand_checked_numbers():
    # bids 0.60 + 0.55 = 1.15 -> gross = 0.15
    # fees = 0.07*(0.60*0.40) + 0.07*(0.55*0.45) = 0.0168 + 0.0173250
    #      = 0.0341250 -> net = 0.1158750; capital = 2 - 1.15 = 0.85
    legs = [leg(bid=0.60), leg(bid=0.55)]
    (signal,) = evaluate_event("kalshi", "EV", legs, NOW)
    assert signal.side == "short"
    assert signal.gross_edge == pytest.approx(0.15)
    assert signal.fee_adjusted_edge == pytest.approx(0.115875)
    assert signal.capital == pytest.approx(0.85)
    assert signal.exhaustiveness_verified is None  # exclusivity suffices


def test_consistent_prices_produce_no_signals():
    # asks sum to 1.02 (no long), bids sum to 0.98 (no short): sane book.
    legs = [leg(bid=0.49, ask=0.51), leg(bid=0.49, ask=0.51)]
    assert evaluate_event("kalshi", "EV", legs, NOW) == []


def test_missing_ask_blocks_long_but_not_short():
    # One leg has no ask: the full YES set can't be bought. But two legs
    # still have bids summing to 1.10, and shorting a quoted subset of an
    # exclusive event is valid on its own.
    legs = [leg(bid=0.60, ask=0.62), leg(bid=0.50, ask=None), leg(bid=None, ask=0.05)]
    signals = evaluate_event("kalshi", "EV", legs, NOW)
    assert [s.side for s in signals] == ["short"]
    assert signals[0].num_legs == 3
    assert signals[0].legs_quoted == 2
    assert signals[0].price_sum == pytest.approx(1.10)


def test_single_leg_event_is_not_a_set():
    # sum(ask) < 1 on one market is just a market, not a Dutch book.
    assert evaluate_event("kalshi", "EV", [leg(bid=0.99, ask=0.30)], NOW) == []


def test_fee_symmetry_of_no_side():
    # Buying NO at 1-bid must cost the same fee as the formula at bid.
    assert taker_fee(1 - 0.60) == pytest.approx(taker_fee(0.60))


def test_far_dated_edge_annualizes_to_almost_nothing():
    # Same 5% gross edge, but resolving in ~73 years (the 2099 markets):
    # annualization deflates it to ~0.007%% — the thesis in one number.
    legs = [leg(ask=0.45, res=NOW + timedelta(days=73 * 365)),
            leg(ask=0.50, res=NOW + timedelta(days=73 * 365))]
    (signal,) = evaluate_event("kalshi", "EV", legs, NOW)
    assert signal.annualized_return < signal.fee_adjusted_edge / signal.capital / 70


def test_missing_or_past_resolution_dates_disable_annualization():
    for res in (None, NOW - timedelta(days=1)):
        (signal,) = evaluate_event(
            "kalshi", "EV", [leg(ask=0.40, res=res), leg(ask=0.40, res=res)], NOW
        )
        assert signal.days_to_resolution is None
        assert signal.annualized_return is None


def test_annualization_uses_latest_leg_date():
    # Legs resolve at 100 and 365 days; capital is locked until the last.
    legs = [
        leg(ask=0.40, res=NOW + timedelta(days=100)),
        leg(ask=0.40, res=NOW + timedelta(days=365)),
    ]
    (signal,) = evaluate_event("kalshi", "EV", legs, NOW)
    assert signal.days_to_resolution == pytest.approx(365.0)


def _snapshot_market(fetched_at, market_id, event_id, bid, ask, **overrides):
    defaults = dict(
        platform="kalshi",
        market_id=market_id,
        event_id=event_id,
        title=market_id,
        outcome_label="Yes",
        yes_bid=bid,
        yes_ask=ask,
        mutually_exclusive=True,
        resolution_date=ONE_YEAR,
        fetched_at=fetched_at,
    )
    return NormalizedMarket(**{**defaults, **overrides})


def test_detect_run_end_to_end_writes_signals(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(
        connection,
        [
            # Dutch-booked event: asks sum 0.95.
            _snapshot_market(fetched_at, "A-1", "EV-ARB", 0.28, 0.45),
            _snapshot_market(fetched_at, "A-2", "EV-ARB", 0.28, 0.50),
            # Sane event: no signal.
            _snapshot_market(fetched_at, "B-1", "EV-OK", 0.49, 0.51),
            _snapshot_market(fetched_at, "B-2", "EV-OK", 0.49, 0.51),
            # Not asserted exclusive: must be ignored even though asks sum < 1.
            _snapshot_market(
                fetched_at, "C-1", "EV-INDEP", 0.10, 0.12, mutually_exclusive=False
            ),
            _snapshot_market(
                fetched_at, "C-2", "EV-INDEP", 0.10, 0.12, mutually_exclusive=False
            ),
            # Polymarket exclusive event: out of scope for layer 1.
            _snapshot_market(
                fetched_at, "540900", "23799", 0.40, 0.45,
                platform="polymarket", book_ref=None,
            ),
            _snapshot_market(
                fetched_at, "540901", "23799", 0.40, 0.45,
                platform="polymarket", book_ref=None,
            ),
        ],
    )
    run_id, ts = latest_run(connection)
    signals = detect_run(connection, run_id, ts)
    assert [(s.event_id, s.side) for s in signals] == [("EV-ARB", "long")]

    stored = connection.execute(
        "SELECT run_id, event_id, side, exhaustiveness_verified FROM signals"
    ).fetchall()
    assert stored == [(run_id, "EV-ARB", "long", 0)]
