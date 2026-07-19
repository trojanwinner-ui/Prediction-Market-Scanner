"""Phase 3, Claude's half: cross-platform candidate generation.

Emits *candidate* Kalshi<->Polymarket pairs ranked by transparent lexical
similarity for human curation. Deciding which candidates are genuinely
equivalent — resolution criteria, settlement sources, deadline edge cases —
is the user-owned half of Phase 3 (see CLAUDE.md); nothing in this module
encodes an equivalence rule, and its output is never treated as matched
pairs by any other phase.

Method, deliberately simple enough to audit by eye:
- A market's text is title + outcome label, lowercased, punctuation
  stripped, minus a small grammatical stopword list.
- Score is token-set Jaccard similarity. No embeddings, no fuzzy magic:
  a false candidate should be explainable by pointing at shared words.
- Blocking keeps the cross product tractable: pairs must share >= 2
  tokens (very common tokens don't nominate pairs on their own, but do
  still count toward the score), and resolution dates, when both are
  known, must be within a window (equivalent claims resolve near each
  other; the window is generous because platforms time-stamp settlement
  differently).
- Price columns in the output are context for the curator, not inputs to
  the score — scoring on price agreement would bias candidate discovery
  toward pairs that already agree, the opposite of what layer 2 studies.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import storage

STOPWORDS = frozenset(
    "will the a an of in on at to for by be is are or and vs v".split()
)
# Tokens appearing in more than this many markets on a platform are too
# generic to nominate a pair by themselves (e.g. "2026", "president").
MAX_NOMINATING_DF = 2000
MIN_SHARED_TOKENS = 2


@dataclass(frozen=True)
class MarketText:
    market_id: str
    event_id: str
    text: str
    tokens: frozenset
    yes_bid: float | None
    yes_ask: float | None
    resolution_date: datetime | None


@dataclass(frozen=True)
class Candidate:
    score: float
    date_delta_days: float | None
    kalshi: MarketText
    polymarket: MarketText


def tokenize(text: str) -> frozenset:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return frozenset(w for w in words if w not in STOPWORDS)


def generate_candidates(
    kalshi_rows: list[MarketText],
    poly_rows: list[MarketText],
    *,
    max_date_delta_days: float = 45.0,
    min_score: float = 0.25,
    top: int = 200,
) -> list[Candidate]:
    """Rank cross-platform pairs by Jaccard similarity of their token sets."""
    df = Counter()
    for row in poly_rows:
        df.update(row.tokens)
    index: dict[str, list[int]] = {}
    for i, row in enumerate(poly_rows):
        for token in row.tokens:
            if df[token] <= MAX_NOMINATING_DF:
                index.setdefault(token, []).append(i)

    candidates = []
    for k in kalshi_rows:
        shared_counts = Counter()
        for token in k.tokens:
            for i in index.get(token, ()):
                shared_counts[i] += 1
        for i, shared in shared_counts.items():
            p = poly_rows[i]
            if shared < MIN_SHARED_TOKENS:
                continue
            delta = _date_delta_days(k.resolution_date, p.resolution_date)
            if delta is not None and delta > max_date_delta_days:
                continue
            union = len(k.tokens | p.tokens)
            score = len(k.tokens & p.tokens) / union if union else 0.0
            if score >= min_score:
                candidates.append(
                    Candidate(score=score, date_delta_days=delta, kalshi=k, polymarket=p)
                )

    candidates.sort(
        key=lambda c: (
            -c.score,
            c.date_delta_days if c.date_delta_days is not None else float("inf"),
        )
    )
    return candidates[:top]


def load_platform(connection, run_id: int, platform: str) -> list[MarketText]:
    """Latest-snapshot markets with at least one live quote.

    Unquoted markets are excluded: a candidate pair without prices can't
    feed the divergence comparison the pairs exist for. KXMV* events are
    Kalshi's auto-generated multivariate parlays — combinatorial synthetic
    markets with no Polymarket analogue whose team-name-stuffed titles are
    pure lexical noise here.
    """
    rows = connection.execute(
        "SELECT m.market_id, m.event_id, m.title, m.outcome_label,"
        " s.yes_bid, s.yes_ask, m.resolution_date"
        " FROM price_snapshots s JOIN markets m ON m.id = s.market_ref"
        " WHERE s.run_id = ? AND m.platform = ?"
        " AND (s.yes_bid IS NOT NULL OR s.yes_ask IS NOT NULL)"
        " AND m.event_id NOT LIKE 'KXMV%'",
        (run_id, platform),
    ).fetchall()
    out = []
    for market_id, event_id, title, outcome_label, yes_bid, yes_ask, res in rows:
        text = f"{title} {outcome_label}"
        out.append(
            MarketText(
                market_id=market_id,
                event_id=event_id,
                text=text,
                tokens=tokenize(text),
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                resolution_date=datetime.fromisoformat(res) if res else None,
            )
        )
    return out


def write_csv(path: str | Path, candidates: list[Candidate]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "score", "date_delta_days",
                "kalshi_market_id", "kalshi_text", "kalshi_bid", "kalshi_ask",
                "kalshi_resolution",
                "poly_market_id", "poly_text", "poly_bid", "poly_ask",
                "poly_resolution",
            ]
        )
        for c in candidates:
            writer.writerow(
                [
                    f"{c.score:.3f}",
                    "" if c.date_delta_days is None else f"{c.date_delta_days:.1f}",
                    c.kalshi.market_id, c.kalshi.text,
                    c.kalshi.yes_bid, c.kalshi.yes_ask,
                    _iso(c.kalshi.resolution_date),
                    c.polymarket.market_id, c.polymarket.text,
                    c.polymarket.yes_bid, c.polymarket.yes_ask,
                    _iso(c.polymarket.resolution_date),
                ]
            )


def _date_delta_days(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return abs((a - b).total_seconds()) / 86400.0


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate cross-platform candidate pairs")
    parser.add_argument("--db", default="data/snapshots.db")
    parser.add_argument("--out", default="data/candidates.csv")
    parser.add_argument("--top", type=int, default=200)
    parser.add_argument("--min-score", type=float, default=0.25)
    parser.add_argument("--max-date-delta", type=float, default=45.0)
    args = parser.parse_args(argv)

    connection = storage.connect(args.db)
    try:
        run = storage.latest_run(connection)
        if run is None:
            print("no runs in database; ingest first")
            return 1
        run_id, fetched_at = run
        kalshi_rows = load_platform(connection, run_id, "kalshi")
        poly_rows = load_platform(connection, run_id, "polymarket")
    finally:
        connection.close()

    candidates = generate_candidates(
        kalshi_rows,
        poly_rows,
        max_date_delta_days=args.max_date_delta,
        min_score=args.min_score,
        top=args.top,
    )
    write_csv(args.out, candidates)
    print(
        f"run {run_id} ({fetched_at.isoformat()}): scored {len(kalshi_rows)} kalshi"
        f" x {len(poly_rows)} polymarket quoted markets ->"
        f" {len(candidates)} candidates -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
