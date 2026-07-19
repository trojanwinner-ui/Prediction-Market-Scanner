"""Kalshi's published price-dependent trading fee, exactly.

The general schedule is quadratic: fee = ceil-to-cent(0.07 * C * P * (1-P))
per order, with C contracts executed at price P dollars. Series metadata
(GET /series/{ticker}) exposes ``fee_type`` ("quadratic",
"quadratic_with_maker_fees", ...) and a ``fee_multiplier`` scaling the
rate; the maker-fee variants never hit us because every execution we model
is a taker crossing the book. Polymarket charges no base taker/maker fee
on the markets we crawl, so its exact trading fee is 0 and this module is
Kalshi-only.

This replaces Phase 2's smooth 0.07*P*(1-P) approximation; the difference
is the multiplier and the per-order ceil-to-cent rounding, which is why
functions here take contract counts instead of pretending fees scale
linearly.
"""

import math

BASE_RATE = 0.07


def kalshi_taker_fee(price: float, contracts: float, multiplier: float = 1.0) -> float:
    """Fee in dollars for one order of ``contracts`` executed at ``price``."""
    raw = BASE_RATE * multiplier * contracts * price * (1.0 - price)
    return ceil_to_cent(raw)


def kalshi_order_fee(fills: list[tuple[float, float]], multiplier: float = 1.0) -> float:
    """Fee for one order filled across several book levels.

    Kalshi assesses the fee on the order, so the quadratic term accrues
    per fill price but the ceil-to-cent rounding applies once.
    """
    raw = sum(
        BASE_RATE * multiplier * quantity * price * (1.0 - price)
        for price, quantity in fills
    )
    return ceil_to_cent(raw)


def ceil_to_cent(dollars: float) -> float:
    # round() first so a float artifact like 1.7500000000000002 cents
    # doesn't ceil an exact-cent fee up a cent.
    return math.ceil(round(dollars * 100, 6)) / 100
