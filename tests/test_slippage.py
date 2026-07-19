import pytest

from pm_scanner.slippage import (
    clob_ask_levels,
    clob_bid_levels,
    kalshi_no_ask_levels,
    kalshi_yes_ask_levels,
    kalshi_yes_bid_levels,
    walk_book,
)

# Real shape: both arrays are resting bids, ascending by price.
KALSHI_BOOK = {
    "orderbook_fp": {
        "yes_dollars": [["0.0100", "3005.78"], ["0.1200", "1624.61"]],
        "no_dollars": [["0.0100", "19741.00"], ["0.8700", "900.32"]],
    }
}


def test_walk_book_average_price_across_levels():
    # 100 @ 0.10 + 50 @ 0.12 -> cost 16.0 for 150.
    fill = walk_book([(0.10, 100), (0.12, 200)], 150)
    assert fill.filled == 150
    assert fill.cost == pytest.approx(16.0)
    assert fill.average_price == pytest.approx(16.0 / 150)
    assert fill.worst_price == 0.12
    assert fill.slippage == pytest.approx(16.0 / 150 - 0.10)


def test_walk_book_partial_fill_when_book_runs_out():
    fill = walk_book([(0.10, 40)], 100)
    assert fill.filled == 40
    assert fill.requested == 100
    assert fill.cost == pytest.approx(4.0)


def test_walk_book_empty_levels():
    fill = walk_book([], 100)
    assert fill.filled == 0
    assert fill.average_price is None
    assert fill.slippage is None


def test_kalshi_yes_ask_ladder_comes_from_highest_no_bid():
    # Best NO bid 0.87 -> best YES ask 0.13 (matches the live market's
    # quoted yes_ask); the deep 0.01 NO bid is the worst ask at 0.99.
    levels = kalshi_yes_ask_levels(KALSHI_BOOK)
    assert levels[0] == (pytest.approx(0.13), 900.32)
    assert levels[-1] == (pytest.approx(0.99), 19741.00)


def test_kalshi_yes_bid_ladder_is_reversed():
    assert kalshi_yes_bid_levels(KALSHI_BOOK)[0] == (0.12, 1624.61)


def test_kalshi_no_ask_ladder_complements_yes_bids():
    # Shorting YES = buying NO; best YES bid 0.12 -> best NO ask 0.88.
    assert kalshi_no_ask_levels(KALSHI_BOOK)[0] == (pytest.approx(0.88), 1624.61)


def test_clob_adapters_sort_best_first():
    book = {
        "bids": [{"price": "0.01", "size": "10"}, {"price": "0.5", "size": "5"}],
        "asks": [{"price": "0.99", "size": "10"}, {"price": "0.51", "size": "5"}],
    }
    assert clob_bid_levels(book)[0] == (0.5, 5.0)
    assert clob_ask_levels(book)[0] == (0.51, 5.0)


def test_missing_book_sides_yield_empty_ladders():
    assert kalshi_yes_ask_levels({"orderbook_fp": {}}) == []
    assert clob_ask_levels({}) == []
