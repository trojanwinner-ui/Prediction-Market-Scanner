from datetime import datetime, timezone

from pm_scanner.polymarket import normalize_market

from conftest import load_fixture


def _markets():
    return load_fixture("gamma_markets_page.json")


def test_json_string_encoded_fields_are_decoded(fetched_at):
    row = normalize_market(_markets()[0], fetched_at)
    assert row is not None
    assert row.outcome_label == "Yes"
    # book_ref must be the first CLOB token id, decoded from the
    # JSON-string-inside-JSON encoding.
    assert row.book_ref == (
        "98022490269692409998126496127597032490334070080325855126491859374983463996227"
    )


def test_binary_market_maps_top_of_book_and_event(fetched_at):
    row = normalize_market(_markets()[0], fetched_at)
    assert row.platform == "polymarket"
    assert row.market_id == "540817"
    assert row.event_id == "23784"
    assert row.yes_bid == 0.5
    assert row.yes_ask == 0.51
    assert row.mutually_exclusive is False
    assert row.resolution_source is None  # Gamma's "" means unspecified
    assert row.resolution_date == datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def test_non_binary_market_is_skipped(fetched_at):
    assert normalize_market(_markets()[1], fetched_at) is None


def test_missing_and_zero_quotes_become_none(fetched_at):
    row = normalize_market(_markets()[2], fetched_at)
    assert row is not None
    assert row.yes_bid is None  # bestBid of 0 is the no-quote sentinel
    assert row.yes_ask is None  # bestAsk absent entirely


def test_standalone_market_falls_back_to_condition_id_grouping(fetched_at):
    row = normalize_market(_markets()[2], fetched_at)
    assert row.event_id.startswith("0xffff")
    assert row.resolution_source == "Associated Press"
    assert row.book_ref is None  # empty clobTokenIds string
