"""Read-only clients and mapper for Polymarket.

Two services: the Gamma API (market/event metadata plus top-of-book, keyset
pagination) and the CLOB API (full order-book depth per outcome token,
which Phase 4 walks for slippage).
"""

import json
from datetime import datetime
from typing import Any, Iterator

import httpx

from .http import get_json
from .models import NormalizedMarket

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
PAGE_LIMIT = 100


class GammaClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=GAMMA_BASE_URL, timeout=30.0)

    def iter_markets(self) -> Iterator[dict[str, Any]]:
        """Yield raw active-market dicts via keyset pagination.

        Plain /markets rejects offsets beyond 2000 with a 422 pointing at
        /markets/keyset, so a full crawl must use the keyset endpoint and
        its opaque after_cursor instead of offsets.
        """
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "limit": PAGE_LIMIT,
                "active": "true",
                "closed": "false",
            }
            if cursor:
                params["after_cursor"] = cursor
            payload = get_json(self._client, "/markets/keyset", params=params)
            markets = payload.get("markets") or []
            yield from markets
            cursor = payload.get("next_cursor")
            if not cursor or not markets:
                return


class ClobClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=CLOB_BASE_URL, timeout=30.0)

    def get_book(self, token_id: str) -> dict[str, Any]:
        """Raw order book for one outcome token (Phase 4 depth walking)."""
        return get_json(self._client, "/book", params={"token_id": token_id})


def normalize_market(
    raw: dict[str, Any], fetched_at: datetime
) -> NormalizedMarket | None:
    """Map one raw Gamma market to a normalized row; None = skip.

    Gamma encodes list fields (``outcomes``, ``clobTokenIds``) as JSON
    *strings inside JSON*, so they need a second decode.
    """
    outcomes = _json_list(raw.get("outcomes"))
    # Exactly-two-outcome markets only: anything else can't be expressed as
    # one YES probability. (Multi-outcome *events* still arrive as several
    # two-outcome markets sharing an event id, which is what we want.)
    if len(outcomes) != 2:
        return None
    token_ids = _json_list(raw.get("clobTokenIds"))
    events = raw.get("events") or []
    return NormalizedMarket(
        platform="polymarket",
        market_id=str(raw["id"]),
        # Standalone markets lack an events list; conditionId is a stable
        # per-market fallback so the row still has a grouping key.
        event_id=str(events[0]["id"]) if events else raw.get("conditionId") or str(raw["id"]),
        title=raw.get("question") or "",
        # bestBid/bestAsk quote the *first* outcome's token, so its label is
        # the YES side ("Yes" usually; a team/candidate name in categorical
        # two-outcome markets).
        outcome_label=str(outcomes[0]),
        yes_bid=_price(raw.get("bestBid")),
        yes_ask=_price(raw.get("bestAsk")),
        # negRisk marks markets whose event's outcomes form a linked
        # mutually exclusive set (Polymarket's "negative risk" mechanism).
        mutually_exclusive=bool(raw["negRisk"]) if "negRisk" in raw else None,
        resolution_date=_timestamp(raw.get("endDate")),
        resolution_source=(raw.get("resolutionSource") or "").strip() or None,
        book_ref=str(token_ids[0]) if token_ids else None,
        fetched_at=fetched_at,
    )


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list):  # be lenient if Gamma ever fixes the encoding
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _price(value: Any) -> float | None:
    """0 is a no-quote sentinel here too: the minimum tick is 0.01."""
    if value is None:
        return None
    price = float(value)
    return price if price > 0 else None


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
