import pytest

from pm_scanner.fees import ceil_to_cent, kalshi_order_fee, kalshi_taker_fee


def test_fee_at_even_money_100_contracts_is_exact():
    # 0.07 * 100 * 0.5 * 0.5 = 1.75 exactly: no rounding needed.
    assert kalshi_taker_fee(0.50, 100) == 1.75


def test_fee_rounds_up_to_the_next_cent():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> 1.75 cents -> ceil -> 2 cents.
    assert kalshi_taker_fee(0.50, 1) == 0.02
    # 0.07 * 100 * 0.05 * 0.95 = 0.3325 -> 33.25c -> 34c.
    assert kalshi_taker_fee(0.05, 100) == 0.34


def test_fee_multiplier_scales_the_rate():
    assert kalshi_taker_fee(0.50, 100, multiplier=0.5) == pytest.approx(0.875 + 0.005, abs=0.005)
    assert kalshi_taker_fee(0.50, 100, multiplier=0.5) == 0.88  # 87.5c ceils


def test_order_fee_rounds_once_across_levels():
    # Two fills of 50 @ 0.50: raw = 2 * (0.07*50*0.25) = 1.75 exactly.
    # Rounding per level would give 0.88 + 0.88 = 1.76.
    assert kalshi_order_fee([(0.50, 50), (0.50, 50)]) == 1.75


def test_ceil_to_cent_survives_float_artifacts():
    assert ceil_to_cent(0.017500000000000002) == 0.02
    assert ceil_to_cent(1.75) == 1.75
