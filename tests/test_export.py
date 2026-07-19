import json
from datetime import datetime, timedelta

from pm_scanner.dutchbook import detect_run
from pm_scanner.export import build_summary
from pm_scanner.models import NormalizedMarket
from pm_scanner.storage import connect, insert_snapshots, latest_run

ONE_YEAR = datetime.fromisoformat("2027-07-18T12:00:00+00:00")


def _market(fetched_at, market_id, bid, ask, platform="kalshi"):
    return NormalizedMarket(
        platform=platform,
        market_id=market_id,
        event_id="EV-ARB" if platform == "kalshi" else "23000",
        title=f"Title {market_id}",
        outcome_label="Yes",
        yes_bid=bid,
        yes_ask=ask,
        mutually_exclusive=True,
        resolution_date=ONE_YEAR,
        fetched_at=fetched_at,
    )


def test_summary_shape_and_aggregates(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    insert_snapshots(
        connection,
        [
            _market(fetched_at, "A-1", 0.28, 0.45),
            _market(fetched_at, "A-2", 0.28, 0.50),
            _market(fetched_at, "540900", 0.40, 0.45, platform="polymarket"),
        ],
    )
    run_id, ts = detect_input = latest_run(connection)
    detect_run(connection, run_id, ts)

    summary = build_summary(connection)
    assert json.dumps(summary)  # serializable

    (run,) = summary["runs"]
    assert run["markets"] == {"kalshi": 2, "polymarket": 1}
    assert run["signals"] == {"long": 1, "short": 0}

    (signal,) = summary["latest"]["signals"]
    assert signal["event_id"] == "EV-ARB"
    assert signal["title"] == "Title A-1"
    assert signal["exhaustiveness_verified"] is False
    assert signal["friction"] is None  # no deep-check ran


def test_summary_tracks_runs_over_time(tmp_path, fetched_at):
    connection = connect(tmp_path / "snapshots.db")
    for i in range(3):
        insert_snapshots(
            connection, [_market(fetched_at + timedelta(hours=6 * i), "A-1", 0.5, 0.52)]
        )
    summary = build_summary(connection)
    assert len(summary["runs"]) == 3
    assert summary["latest"]["run_id"] == summary["runs"][-1]["run_id"]


def test_empty_database_summary(tmp_path):
    summary = build_summary(connect(tmp_path / "empty.db"))
    assert summary["runs"] == []
    assert summary["latest"] == {}
