from datetime import datetime, timedelta, timezone

from pm_scanner.candidates import (
    Candidate,
    MarketText,
    generate_candidates,
    tokenize,
    write_csv,
)

RES = datetime(2027, 2, 7, 12, 0, tzinfo=timezone.utc)


def market(market_id, text, *, platform_res=RES, bid=0.4, ask=0.45):
    return MarketText(
        market_id=market_id,
        event_id=f"EV-{market_id}",
        text=text,
        tokens=tokenize(text),
        yes_bid=bid,
        yes_ask=ask,
        resolution_date=platform_res,
    )


def test_tokenize_strips_punctuation_and_stopwords():
    assert tokenize("Will the Chiefs win Super Bowl LXI?") == {
        "chiefs", "win", "super", "bowl", "lxi",
    }


def test_equivalent_looking_markets_rank_first():
    kalshi = [
        market("KXSB-61-KC", "Pro Football Champion 2027? Chiefs"),
        market("KXGDP-26", "US GDP growth above 3% in 2026? Yes"),
    ]
    poly = [
        market("801", "Super Bowl Champion 2027 Chiefs"),
        market("802", "Will Bitcoin reach $500k? Yes"),
    ]
    candidates = generate_candidates(kalshi, poly, min_score=0.2)
    assert candidates  # the Chiefs pair survives
    best = candidates[0]
    assert (best.kalshi.market_id, best.polymarket.market_id) == ("KXSB-61-KC", "801")
    # shared {chiefs, champion, 2027} of 5+5-3=7 union tokens -> 3/7
    assert best.score == 3 / 7


def test_unrelated_markets_produce_no_candidates():
    kalshi = [market("KXGDP-26", "US GDP growth above 3% in 2026? Yes")]
    poly = [market("802", "Will Bitcoin reach $500k? Yes")]
    assert generate_candidates(kalshi, poly) == []


def test_date_window_blocks_far_apart_resolutions():
    kalshi = [market("KXSB-61-KC", "Super Bowl Champion 2027 Chiefs")]
    poly = [
        market(
            "801",
            "Super Bowl Champion 2027 Chiefs",
            platform_res=RES + timedelta(days=400),
        )
    ]
    assert generate_candidates(kalshi, poly, max_date_delta_days=45) == []
    # ...but an unknown date on one side does not exclude the pair.
    poly_no_date = [
        market("801", "Super Bowl Champion 2027 Chiefs", platform_res=None)
    ]
    (candidate,) = generate_candidates(kalshi, poly_no_date)
    assert candidate.date_delta_days is None


def test_top_limit_and_ordering():
    kalshi = [market(f"K{i}", f"Team Alpha wins game {i} Alpha") for i in range(5)]
    poly = [market(f"P{i}", f"Team Alpha wins game {i} Alpha") for i in range(5)]
    candidates = generate_candidates(kalshi, poly, min_score=0.0, top=3)
    assert len(candidates) == 3
    assert all(
        candidates[i].score >= candidates[i + 1].score
        for i in range(len(candidates) - 1)
    )


def test_write_csv_round_trips(tmp_path):
    kalshi = [market("KXSB-61-KC", "Super Bowl Champion 2027 Chiefs")]
    poly = [market("801", "Super Bowl Champion 2027 Chiefs")]
    out = tmp_path / "candidates.csv"
    write_csv(out, generate_candidates(kalshi, poly))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("score,date_delta_days,kalshi_market_id")
    assert len(lines) == 2
    assert "KXSB-61-KC" in lines[1] and "801" in lines[1]
