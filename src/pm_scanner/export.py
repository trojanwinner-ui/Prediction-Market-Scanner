"""Derived JSON summary: the small, committable face of the big DB.

The full SQLite time series lives as a GitHub Release asset (it blew past
GitHub's in-repo file limits within days — see CLAUDE.md decisions log);
the repo commits only this summary, which is also exactly the input the
Phase 5 dashboard will render. Two sections:

- ``runs``: per-run aggregates over the whole time series (small forever:
  one row per run).
- ``latest``: full signal detail for the newest run, with the most recent
  friction deep-check per signal when one exists.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import storage


def build_summary(connection) -> dict:
    runs = [
        {
            "run_id": run_id,
            "fetched_at": fetched_at,
            "markets": {"kalshi": kalshi, "polymarket": polymarket},
            "signals": {"long": longs or 0, "short": shorts or 0},
            # Phase 2 approximate-fee survivors; the frictions section of
            # `latest` carries the real-execution numbers.
            "short_fee_survivors": short_survivors or 0,
        }
        for run_id, fetched_at, kalshi, polymarket, longs, shorts, short_survivors in connection.execute(
            """
            SELECT r.id, r.fetched_at,
              (SELECT COUNT(*) FROM price_snapshots s
                 JOIN markets m ON m.id = s.market_ref
                 WHERE s.run_id = r.id AND m.platform = 'kalshi'),
              (SELECT COUNT(*) FROM price_snapshots s
                 JOIN markets m ON m.id = s.market_ref
                 WHERE s.run_id = r.id AND m.platform = 'polymarket'),
              (SELECT COUNT(*) FROM signals g
                 WHERE g.run_id = r.id AND g.side = 'long'),
              (SELECT COUNT(*) FROM signals g
                 WHERE g.run_id = r.id AND g.side = 'short'),
              (SELECT COUNT(*) FROM signals g
                 WHERE g.run_id = r.id AND g.side = 'short'
                 AND g.fee_adjusted_edge > 0)
            FROM runs r ORDER BY r.fetched_at
            """
        )
    ]

    latest: dict = {}
    run = storage.latest_run(connection)
    if run is not None:
        run_id, fetched_at = run
        signals = [
            {
                "signal_id": signal_id,
                "event_id": event_id,
                "title": title,
                "side": side,
                "num_legs": num_legs,
                "legs_quoted": legs_quoted,
                "price_sum": price_sum,
                "gross_edge": gross,
                "fee_adjusted_edge": net,
                "annualized_return": annualized,
                "exhaustiveness_verified": bool(ex) if ex is not None else None,
                "friction": None
                if f_checked is None
                else {
                    "checked_at": f_checked,
                    "contracts_requested": f_requested,
                    "contracts_filled": f_filled,
                    "walked_gross_total": f_gross,
                    "fees_total": f_fees,
                    "slippage_total": f_slip,
                    "net_total": f_net,
                },
            }
            for (
                signal_id, event_id, title, side, num_legs, legs_quoted,
                price_sum, gross, net, annualized, ex,
                f_checked, f_requested, f_filled, f_gross, f_fees, f_slip, f_net,
            ) in connection.execute(
                """
                SELECT g.id, g.event_id,
                  (SELECT title FROM markets m
                     WHERE m.platform = g.platform AND m.event_id = g.event_id
                     LIMIT 1),
                  g.side, g.num_legs, g.legs_quoted, g.price_sum,
                  g.gross_edge, g.fee_adjusted_edge, g.annualized_return,
                  g.exhaustiveness_verified,
                  f.checked_at, f.contracts_requested, f.contracts_filled,
                  f.walked_gross_total, f.fees_total, f.slippage_total,
                  f.net_total
                FROM signals g
                LEFT JOIN signal_frictions f ON f.id = (
                    SELECT id FROM signal_frictions
                    WHERE signal_id = g.id ORDER BY checked_at DESC LIMIT 1
                )
                WHERE g.run_id = ?
                ORDER BY g.fee_adjusted_edge DESC
                """,
                (run_id,),
            )
        ]
        latest = {
            "run_id": run_id,
            "fetched_at": fetched_at.isoformat(),
            "signals": signals,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": runs,
        "latest": latest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the committable JSON summary")
    parser.add_argument("--db", default="data/snapshots.db")
    parser.add_argument("--out", default="data/summary.json")
    args = parser.parse_args(argv)

    connection = storage.connect(args.db)
    try:
        summary = build_summary(connection)
    finally:
        connection.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(
        f"{args.out}: {len(summary['runs'])} runs,"
        f" {len(summary['latest'].get('signals', []))} latest signals,"
        f" {out.stat().st_size:,} bytes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
