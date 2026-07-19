"""End-to-end over fixtures: raw platform payloads -> normalized -> SQLite."""

import sqlite3

from pm_scanner.ingest import run

from conftest import load_fixture


def test_run_ingests_both_platforms_into_one_snapshot(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    counts = run(
        str(db),
        kalshi_events=load_fixture("kalshi_events_page.json")["events"],
        gamma_markets=load_fixture("gamma_markets_page.json"),
        fetched_at=fetched_at,
    )
    # Kalshi fixture: 1 + 3 binary markets (scalar skipped);
    # Gamma fixture: 2 binary markets (3-outcome skipped).
    assert counts == {
        "kalshi": 4,
        "polymarket": 2,
        "polymarket_skipped": 1,
        "inserted": 6,
    }

    connection = sqlite3.connect(db)
    per_platform = connection.execute(
        "SELECT m.platform, COUNT(*) FROM price_snapshots s"
        " JOIN markets m ON m.id = s.market_ref GROUP BY m.platform"
    ).fetchall()
    assert dict(per_platform) == {"kalshi": 4, "polymarket": 2}
    # Every fact row of one ingest belongs to a single run.
    assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (1,)


def test_run_groups_multi_outcome_event_for_phase2(tmp_path, fetched_at):
    db = tmp_path / "snapshots.db"
    run(
        str(db),
        kalshi_events=load_fixture("kalshi_events_page.json")["events"],
        gamma_markets=[],
        fetched_at=fetched_at,
    )
    connection = sqlite3.connect(db)
    # The Phase 2 query shape: outcomes per mutually exclusive event.
    rows = connection.execute(
        "SELECT m.event_id, COUNT(*) FROM price_snapshots s"
        " JOIN markets m ON m.id = s.market_ref"
        " WHERE m.mutually_exclusive = 1 GROUP BY m.event_id"
    ).fetchall()
    assert rows == [("KXFAKEPARTY-28", 3)]
