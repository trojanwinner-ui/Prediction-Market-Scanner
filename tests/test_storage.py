from datetime import timedelta

import pytest

from pm_scanner.models import NormalizedMarket
from pm_scanner.storage import connect, insert_snapshots, latest_run


def _market(fetched_at, **overrides) -> NormalizedMarket:
    defaults = dict(
        platform="kalshi",
        market_id="KXTEST-1",
        event_id="KXTEST",
        title="Test market",
        outcome_label="Yes",
        yes_bid=0.4,
        yes_ask=0.42,
        mutually_exclusive=True,
        fetched_at=fetched_at,
    )
    return NormalizedMarket(**{**defaults, **overrides})


def test_insert_and_read_back_through_join(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    assert insert_snapshots(connection, [_market(fetched_at)]) == 1
    row = connection.execute(
        "SELECT m.platform, m.market_id, s.yes_bid, s.yes_ask,"
        " m.mutually_exclusive, r.fetched_at"
        " FROM price_snapshots s"
        " JOIN markets m ON m.id = s.market_ref"
        " JOIN runs r ON r.id = s.run_id"
    ).fetchone()
    assert row == ("kalshi", "KXTEST-1", 0.4, 0.42, 1, "2026-07-18T12:00:00+00:00")


def test_two_runs_append_facts_but_not_dimension(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(connection, [_market(fetched_at)])
    insert_snapshots(
        connection, [_market(fetched_at + timedelta(hours=6), yes_bid=0.45)]
    )
    counts = connection.execute(
        "SELECT (SELECT COUNT(*) FROM runs),"
        " (SELECT COUNT(*) FROM markets),"
        " (SELECT COUNT(*) FROM price_snapshots)"
    ).fetchone()
    assert counts == (2, 1, 2)  # time series grows; dimension doesn't


def test_dimension_upsert_keeps_latest_metadata(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(connection, [_market(fetched_at)])
    insert_snapshots(
        connection,
        [_market(fetched_at + timedelta(hours=6), title="Renamed market")],
    )
    (title,) = connection.execute("SELECT title FROM markets").fetchone()
    assert title == "Renamed market"


def test_mixed_timestamps_rejected(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    with pytest.raises(ValueError):
        insert_snapshots(
            connection,
            [
                _market(fetched_at),
                _market(fetched_at + timedelta(minutes=1), market_id="KXTEST-2"),
            ],
        )


def test_none_prices_stored_as_null(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(
        connection,
        [_market(fetched_at, yes_bid=None, yes_ask=None, mutually_exclusive=None)],
    )
    row = connection.execute(
        "SELECT s.yes_bid, s.yes_ask, m.mutually_exclusive"
        " FROM price_snapshots s JOIN markets m ON m.id = s.market_ref"
    ).fetchone()
    assert row == (None, None, None)


def test_latest_run_returns_newest(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    assert latest_run(connection) is None
    insert_snapshots(connection, [_market(fetched_at)])
    insert_snapshots(connection, [_market(fetched_at + timedelta(hours=6))])
    run_id, ts = latest_run(connection)
    assert ts == fetched_at + timedelta(hours=6)


def test_connect_is_idempotent_on_existing_db(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    insert_snapshots(connect(db), [_market(fetched_at)])
    connection = connect(db)  # re-running the schema must not clobber data
    count = connection.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    assert count == 1
