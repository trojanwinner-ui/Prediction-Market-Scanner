"""End-to-end friction waterfall with injected (deterministic) books."""

import pytest

from pm_scanner.dutchbook import detect_run
from pm_scanner.frictions import deep_check
from pm_scanner.models import NormalizedMarket
from pm_scanner.storage import connect, insert_snapshots, latest_run

ONE_YEAR = "2027-07-18T12:00:00+00:00"


def _market(fetched_at, market_id, bid, ask):
    from datetime import datetime

    return NormalizedMarket(
        platform="kalshi",
        market_id=market_id,
        event_id="EV-ARB",
        title=market_id,
        outcome_label="Yes",
        yes_bid=bid,
        yes_ask=ask,
        mutually_exclusive=True,
        resolution_date=datetime.fromisoformat(ONE_YEAR),
        book_ref=market_id,
        fetched_at=fetched_at,
    )


def _book(yes_bids, no_bids):
    return {
        "orderbook_fp": {
            "yes_dollars": [[f"{p:.4f}", f"{s:.2f}"] for p, s in yes_bids],
            "no_dollars": [[f"{p:.4f}", f"{s:.2f}"] for p, s in no_bids],
        }
    }


# Snapshot: asks 0.45 + 0.50 = 0.95 -> long signal, gross 0.05/contract.
# Live books: 100 contracts rest at those asks, then it gets pricier.
BOOKS = {
    # NO bids: best 0.55 (= YES ask 0.45) x100, then 0.50 (= ask 0.50) x200.
    "A-1": _book(yes_bids=[(0.40, 500)], no_bids=[(0.50, 200), (0.55, 100)]),
    # NO bids: best 0.50 (= YES ask 0.50) x100, then 0.45 (= ask 0.55) x200.
    "A-2": _book(yes_bids=[(0.40, 500)], no_bids=[(0.45, 200), (0.50, 100)]),
}


@pytest.fixture
def db_with_signal(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(
        connection,
        [
            _market(fetched_at, "A-1", 0.40, 0.45),
            _market(fetched_at, "A-2", 0.40, 0.50),
        ],
    )
    run_id, ts = latest_run(connection)
    signals = detect_run(connection, run_id, ts)
    assert [s.side for s in signals] == ["long"]
    return connection


def test_waterfall_at_depth_available_at_top(db_with_signal):
    (report,) = deep_check(
        db_with_signal,
        get_orderbook=BOOKS.__getitem__,
        get_fee_multiplier=lambda series: 1.0,
        contracts=100,
    )
    # 100 contracts rest at the top of each book: no slippage.
    # cost = 100*0.45 + 100*0.50 = 95 -> gross = 100 - 95 = 5.
    # fees = ceil(0.07*100*0.45*0.55) + ceil(0.07*100*0.50*0.50)
    #      = ceil(1.7325)->1.74 + 1.75 = 3.49 -> net = 1.51
    assert report.contracts_filled == 100
    assert report.walked_gross_total == pytest.approx(5.0)
    assert report.slippage_total == pytest.approx(0.0)
    assert report.fees_total == pytest.approx(3.49)
    assert report.net_total == pytest.approx(1.51)


def test_waterfall_slippage_beyond_top_of_book(db_with_signal):
    (report,) = deep_check(
        db_with_signal,
        get_orderbook=BOOKS.__getitem__,
        get_fee_multiplier=lambda series: 1.0,
        contracts=200,
    )
    # Each leg fills 100 at top + 100 one level deeper (0.05 worse):
    # slippage = 2 legs * 100 * 0.05 = 10.0; gross shrinks accordingly:
    # cost = (45+50) + (50+55) = 200*0.95 + 10 -> gross = 200-200 = ... :
    # gross = 200 - (95*2 + 10) = 0.0 -> the "arb" is gone before fees.
    assert report.contracts_filled == 200
    assert report.slippage_total == pytest.approx(10.0)
    assert report.walked_gross_total == pytest.approx(0.0)
    assert report.net_total < 0  # fees push it underwater


def test_feasible_size_capped_by_thinnest_leg(db_with_signal):
    (report,) = deep_check(
        db_with_signal,
        get_orderbook=BOOKS.__getitem__,
        get_fee_multiplier=lambda series: 1.0,
        contracts=1000,
    )
    # Books hold 300/leg total; the set is capped there, not at request.
    assert report.contracts_filled == 300
    assert report.contracts_requested == 1000


def test_results_are_stored_with_timestamp(db_with_signal):
    deep_check(
        db_with_signal,
        get_orderbook=BOOKS.__getitem__,
        get_fee_multiplier=lambda series: 1.0,
        contracts=100,
    )
    row = db_with_signal.execute(
        "SELECT contracts_filled, net_total, checked_at IS NOT NULL"
        " FROM signal_frictions"
    ).fetchone()
    assert row == (100.0, pytest.approx(1.51), 1)
