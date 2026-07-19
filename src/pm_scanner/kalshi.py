"""Read-only client and mapper for Kalshi's public trade API.

Despite the hostname, api.elections.kalshi.com serves *every* Kalshi market
and is the only host answering unauthenticated reads (the legacy
trading-api.kalshi.com now returns 401 without credentials).

We ingest via /events with nested markets rather than /markets because the
event object carries exactly the metadata later phases need: the
``mutually_exclusive`` flag (Phase 2 may only sum outcome sets the platform
asserts are exclusive) and ``settlement_sources`` (Phase 3 raw material).
"""

import time
from datetime import datetime
from typing import Any, Iterator

import httpx

from .http import get_json
from .models import NormalizedMarket

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_LIMIT = 200  # documented maximum for /events
PAGE_DELAY_SECONDS = 0.1  # stay politely under the public rate limit


class KalshiClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    def iter_events(self, status: str = "open") -> Iterator[dict[str, Any]]:
        """Yield raw event dicts (with nested markets), following the cursor."""
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_LIMIT,
                "status": status,
                "with_nested_markets": "true",
            }
            if cursor:
                params["cursor"] = cursor
            payload = get_json(self._client, "/events", params=params)
            yield from payload.get("events") or []
            cursor = payload.get("cursor")
            if not cursor:
                return
            time.sleep(PAGE_DELAY_SECONDS)

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Raw order book for one market (Phase 4 walks this for slippage)."""
        return get_json(self._client, f"/markets/{ticker}/orderbook")


def normalize_event(
    event: dict[str, Any], fetched_at: datetime
) -> list[NormalizedMarket]:
    """Map one raw Kalshi event to normalized rows, one per binary market."""
    sources = "; ".join(
        s["name"].strip()
        for s in event.get("settlement_sources") or []
        if s.get("name", "").strip()
    )
    rows = []
    for market in event.get("markets") or []:
        # Kalshi also lists scalar/structured products; only binary YES/NO
        # markets fit the probability-space schema.
        if market.get("market_type") != "binary":
            continue
        rows.append(
            NormalizedMarket(
                platform="kalshi",
                market_id=market["ticker"],
                event_id=event["event_ticker"],
                title=market.get("title") or event.get("title") or "",
                outcome_label=market.get("yes_sub_title") or "Yes",
                yes_bid=_dollars(market.get("yes_bid_dollars")),
                yes_ask=_dollars(market.get("yes_ask_dollars")),
                mutually_exclusive=event.get("mutually_exclusive"),
                resolution_date=_timestamp(
                    market.get("expected_expiration_time") or market.get("close_time")
                ),
                resolution_source=sources or None,
                book_ref=market["ticker"],
                fetched_at=fetched_at,
            )
        )
    return rows


def _dollars(value: str | None) -> float | None:
    """Parse Kalshi's dollar-string prices ("0.1200") into probabilities.

    The API once quoted integer cents; current responses quote dollar
    strings, so probability = float(value). "0.0000" is the no-quote
    sentinel — no real order can rest at price 0.
    """
    if value in (None, ""):
        return None
    price = float(value)
    return price if price > 0 else None


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
