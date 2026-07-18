from datetime import datetime, timezone

from pm_scanner.kalshi import normalize_event

from conftest import load_fixture


def _events():
    return load_fixture("kalshi_events_page.json")["events"]


def test_single_market_event_maps_prices_to_probability_space(fetched_at):
    (row,) = normalize_event(_events()[0], fetched_at)
    assert row.platform == "kalshi"
    assert row.market_id == "KXELONMARS-99"
    assert row.event_id == "KXELONMARS-99"
    assert row.outcome_label == "Mars"
    assert row.yes_bid == 0.12
    assert row.yes_ask == 0.13
    assert row.mutually_exclusive is False
    assert row.book_ref == "KXELONMARS-99"
    assert row.fetched_at == fetched_at


def test_settlement_sources_joined_and_blank_names_dropped(fetched_at):
    (row,) = normalize_event(_events()[0], fetched_at)
    assert row.resolution_source == "The Guardian; Reuters"


def test_resolution_date_prefers_expected_expiration(fetched_at):
    (row,) = normalize_event(_events()[0], fetched_at)
    assert row.resolution_date == datetime(2099, 8, 1, 15, 0, tzinfo=timezone.utc)


def test_multi_outcome_event_shares_event_id(fetched_at):
    rows = normalize_event(_events()[1], fetched_at)
    assert {r.event_id for r in rows} == {"KXFAKEPARTY-28"}
    assert [r.outcome_label for r in rows] == ["Democrat", "Republican", "Other"]
    assert all(r.mutually_exclusive is True for r in rows)


def test_zero_price_sentinel_becomes_none(fetched_at):
    rows = normalize_event(_events()[1], fetched_at)
    empty_book = next(r for r in rows if r.market_id == "KXFAKEPARTY-28-O")
    assert empty_book.yes_bid is None
    assert empty_book.yes_ask is None


def test_non_binary_market_is_skipped(fetched_at):
    rows = normalize_event(_events()[1], fetched_at)
    assert "KXFAKEPARTY-28-MARGIN" not in {r.market_id for r in rows}


def test_missing_expected_expiration_falls_back_to_close_time(fetched_at):
    rows = normalize_event(_events()[1], fetched_at)
    republican = next(r for r in rows if r.market_id == "KXFAKEPARTY-28-R")
    assert republican.resolution_date == datetime(
        2028, 11, 8, 4, 59, tzinfo=timezone.utc
    )


def test_event_without_settlement_sources_has_none(fetched_at):
    rows = normalize_event(_events()[1], fetched_at)
    assert all(r.resolution_source is None for r in rows)
