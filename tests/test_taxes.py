"""Unit tests for Hypixel progressive Auction House tax brackets."""

import pytest

from src.config import Settings
from src.constants.taxes import (
    calculate_progressive_ah_tax,
    calculate_progressive_net_profit,
    calculate_progressive_net_revenue,
)
from src.evaluator import FlipEvaluator, FlipResult


def test_progressive_tax_brackets():
    """Verify progressive tax brackets across all price tiers."""
    # Negative / zero
    assert calculate_progressive_ah_tax(0) == 0
    assert calculate_progressive_ah_tax(-1000) == 0

    # Tier 1: < 10M (1%)
    # 5M -> 50,000 coins (1.0%)
    assert calculate_progressive_ah_tax(5_000_000) == 50_000
    assert calculate_progressive_net_revenue(5_000_000) == 4_950_000

    # 9.9M -> 99,000 coins (1.0%)
    assert calculate_progressive_ah_tax(9_900_000) == 99_000

    # Tier 2: 10M to 100M (2%)
    # 10M exact -> 200,000 coins (2.0%)
    assert calculate_progressive_ah_tax(10_000_000) == 200_000
    assert calculate_progressive_net_revenue(10_000_000) == 9_800_000

    # 50M -> 1,000,000 coins (2.0%)
    assert calculate_progressive_ah_tax(50_000_000) == 1_000_000
    assert calculate_progressive_net_revenue(50_000_000) == 49_000_000

    # 100M exact -> 2,000,000 coins (2.0%)
    assert calculate_progressive_ah_tax(100_000_000) == 2_000_000
    assert calculate_progressive_net_revenue(100_000_000) == 98_000_000

    # Tier 3: > 100M (2% on total + 1% on excess over 100M)
    # 150M -> 2% of 150M (3M) + 1% of 50M (500k) = 3,500,000 (effective 2.33%)
    assert calculate_progressive_ah_tax(150_000_000) == 3_500_000
    assert calculate_progressive_net_revenue(150_000_000) == 146_500_000

    # 200M -> 2% of 200M (4M) + 1% of 100M (1M) = 5,000,000 (effective 2.50%)
    assert calculate_progressive_ah_tax(200_000_000) == 5_000_000
    assert calculate_progressive_net_revenue(200_000_000) == 195_000_000

    # 500M -> 2% of 500M (10M) + 1% of 400M (4M) = 14,000,000 (effective 2.80%)
    assert calculate_progressive_ah_tax(500_000_000) == 14_000_000
    assert calculate_progressive_net_revenue(500_000_000) == 486_000_000

    # 1B -> 2% of 1B (20M) + 1% of 900M (9M) = 29,000,000 (effective 2.90%)
    assert calculate_progressive_ah_tax(1_000_000_000) == 29_000_000
    assert calculate_progressive_net_revenue(1_000_000_000) == 971_000_000


def test_calculate_progressive_net_profit():
    # Buy for 100M, sell for 150M
    # Tax on 150M = 3.5M -> Net revenue = 146.5M -> Net profit = 46.5M
    pnl = calculate_progressive_net_profit(resell_price=150_000_000, buy_price=100_000_000)
    assert pnl == 46_500_000


def test_evaluator_progressive_tax_toggle():
    """Verify FlipEvaluator applies progressive tax when USE_PROGRESSIVE_TAX=True."""
    cfg_prog = Settings(
        DRY_RUN=True,
        USE_PROGRESSIVE_TAX=True,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=1.0,
        MIN_SAMPLE_SIZE=2,
    )
    evaluator = FlipEvaluator(cfg_prog)
    # For a 150M resell:
    # Tax is 3.5M, net revenue is 146.5M
    net_rev = evaluator.calculate_net_revenue(150_000_000)
    assert net_rev == 146_500_000

    # When USE_PROGRESSIVE_TAX=False (flat AH_TAX_RATE = 0.01 / 1%)
    cfg_flat = Settings(
        DRY_RUN=True,
        USE_PROGRESSIVE_TAX=False,
        AH_TAX_RATE=0.01,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=1.0,
        MIN_SAMPLE_SIZE=2,
    )
    evaluator_flat = FlipEvaluator(cfg_flat)
    net_rev_flat = evaluator_flat.calculate_net_revenue(150_000_000)
    assert net_rev_flat == int(150_000_000 * 0.99)
