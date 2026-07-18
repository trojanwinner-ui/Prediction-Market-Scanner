from pm_scanner.models import NormalizedMarket
from pm_scanner.storage import connect, insert_snapshots


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


def test_insert_and_read_back(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    connection = connect(db)
    inserted = insert_snapshots(connection, [_market(fetched_at)])
    assert inserted == 1
    row = connection.execute(
        "SELECT platform, market_id, yes_bid, yes_ask, mutually_exclusive,"
        " fetched_at FROM price_snapshots"
    ).fetchone()
    assert row == ("kalshi", "KXTEST-1", 0.4, 0.42, 1, "2026-07-18T12:00:00+00:00")


def test_repeated_inserts_append_rather_than_overwrite(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    connection = connect(db)
    insert_snapshots(connection, [_market(fetched_at)])
    insert_snapshots(connection, [_market(fetched_at)])  # same market, same ts
    count = connection.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    assert count == 2


def test_none_prices_stored_as_null(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    connection = connect(db)
    insert_snapshots(
        connection,
        [_market(fetched_at, yes_bid=None, yes_ask=None, mutually_exclusive=None)],
    )
    row = connection.execute(
        "SELECT yes_bid, yes_ask, mutually_exclusive FROM price_snapshots"
    ).fetchone()
    assert row == (None, None, None)


def test_connect_is_idempotent_on_existing_db(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    insert_snapshots(connect(db), [_market(fetched_at)])
    connection = connect(db)  # re-running the schema must not clobber data
    count = connection.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    assert count == 1
