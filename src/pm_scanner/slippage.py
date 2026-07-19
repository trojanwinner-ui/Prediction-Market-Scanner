"""Order-book walking: what a target size actually costs.

``walk_book`` is the core: given best-first levels for the side being
taken, fill a quantity level by level and report the average and worst
prices paid. The adapters translate each platform's book encoding into
those levels:

- Kalshi (GET /markets/{t}/orderbook, ``orderbook_fp``): both arrays are
  resting *bids* in ascending price order — ``yes_dollars`` bids on YES,
  ``no_dollars`` bids on NO. Buying YES crosses the NO bids (buying YES at
  price p fills a NO bid at 1-p), so the YES ask ladder is the NO bids
  reversed and complemented; the best ask comes from the *highest* NO bid.
- Polymarket CLOB (GET /book): bids and asks on the YES token directly,
  as {"price": str, "size": str} objects, unsorted-by-contract.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Fill:
    """Result of walking one side of one book for a target quantity."""

    requested: float
    filled: float          # < requested when the book ran out
    cost: float            # dollars paid for `filled` contracts
    top_price: float | None
    worst_price: float | None
    fills: tuple           # ((price, quantity), ...) per level consumed

    @property
    def average_price(self) -> float | None:
        return self.cost / self.filled if self.filled else None

    @property
    def slippage(self) -> float | None:
        """Average price deterioration vs the top of book, per contract."""
        if not self.filled:
            return None
        return self.average_price - self.top_price


def walk_book(levels: list[tuple[float, float]], quantity: float) -> Fill:
    """Fill ``quantity`` against best-first ``levels`` [(price, size), ...]."""
    remaining = quantity
    cost = 0.0
    consumed = []
    for price, size in levels:
        if remaining <= 0:
            break
        take = min(size, remaining)
        consumed.append((price, take))
        cost += price * take
        remaining -= take
    return Fill(
        requested=quantity,
        filled=quantity - remaining,
        cost=cost,
        top_price=levels[0][0] if levels else None,
        worst_price=consumed[-1][0] if consumed else None,
        fills=tuple(consumed),
    )


def kalshi_yes_ask_levels(orderbook: dict[str, Any]) -> list[tuple[float, float]]:
    """YES ask ladder, best (cheapest) first, from the NO-bid array."""
    no_bids = (orderbook.get("orderbook_fp") or {}).get("no_dollars") or []
    return [(1.0 - float(price), float(size)) for price, size in reversed(no_bids)]


def kalshi_yes_bid_levels(orderbook: dict[str, Any]) -> list[tuple[float, float]]:
    """YES bid ladder, best (highest) first."""
    yes_bids = (orderbook.get("orderbook_fp") or {}).get("yes_dollars") or []
    return [(float(price), float(size)) for price, size in reversed(yes_bids)]


def kalshi_no_ask_levels(orderbook: dict[str, Any]) -> list[tuple[float, float]]:
    """NO ask ladder (what shorting YES actually buys), best first."""
    yes_bids = (orderbook.get("orderbook_fp") or {}).get("yes_dollars") or []
    return [(1.0 - float(price), float(size)) for price, size in reversed(yes_bids)]


def clob_ask_levels(book: dict[str, Any]) -> list[tuple[float, float]]:
    levels = [(float(l["price"]), float(l["size"])) for l in book.get("asks") or []]
    return sorted(levels)


def clob_bid_levels(book: dict[str, Any]) -> list[tuple[float, float]]:
    levels = [(float(l["price"]), float(l["size"])) for l in book.get("bids") or []]
    return sorted(levels, reverse=True)
