"""Normalized market schema that every platform mapper produces."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NormalizedMarket(BaseModel):
    """One binary outcome of a prediction-market event, in probability space.

    Multi-outcome events become several rows sharing an ``event_id``, so the
    Phase 2 Dutch-book check is a GROUP BY event_id over one ingest run.
    """

    platform: Literal["kalshi", "polymarket"]
    market_id: str
    event_id: str
    title: str
    # Human label for the YES side of this binary market ("Yes", "Mars", a
    # team name, ...). Distinguishes sibling outcomes within one event.
    outcome_label: str
    # Best quotes for the YES side, as probabilities in [0, 1]. None means
    # "no resting orders on that side": both platforms use a sentinel for
    # that (Kalshi a 0 price, Gamma a missing/zero field), and a genuine
    # order can never rest at price 0, so 0 is safe to translate to None.
    yes_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    yes_ask: float | None = Field(default=None, ge=0.0, le=1.0)
    # Whether the platform asserts this event's outcomes are mutually
    # exclusive (Kalshi: event.mutually_exclusive, Polymarket: negRisk).
    # None when the platform doesn't say. Phase 2 must only sum outcome
    # sets where this is True.
    mutually_exclusive: bool | None = None
    # Captured now, used in Phase 3: when and per what source the market
    # settles. resolution_source is a platform-reported source string
    # (possibly several, "; "-joined), not the full rules text.
    resolution_date: datetime | None = None
    resolution_source: str | None = None
    # Platform-native key for fetching order-book depth in Phase 4:
    # Kalshi market ticker / Polymarket CLOB token id of the YES outcome.
    book_ref: str | None = None
    fetched_at: datetime
