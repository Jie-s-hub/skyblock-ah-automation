"""Unit tests for Hypixel Auction House creation fee, collection fee, and dead zone optimization.

All values are verified against Hypixel SkyBlock wiki Auction House & Mayor Derpy rules:
- Creation fee (listing fee): 1.0% <10M, 2.0% 10M-100M, 2.5% >=100M (flat % on whole price).
- Collection fee: 0% <1M, 1.0% >=1M (normal rate), 4.0% >=1M (Derpy rate).
- Total deductions floored to whole coins.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.constants.taxes import (
    best_resale_net,
    calculate_progressive_ah_tax,
    calculate_progressive_net_profit,
    calculate_progressive_net_revenue,
    collection_fee,
    creation_fee,
    net_revenue,
    total_fee,
)
from src.election import MayorElectionTracker
from src.evaluator import FlipEvaluator


def test_hand_computed_exact_price_points_normal_rate():
    """
    Test hand-computed exact price points required by fee specification:
    - 999,999: creation=9,999, collection=0 -> net=990,000
    - 1,000,000: creation=10,000, collection=10,000 -> net=980,000
    - 9,999,999: creation=99,999, collection=99,999 -> net=9,800,001
    - 10,000,000: creation=200,000, collection=100,000 -> net=9,700,000
    - 99,999,999: creation=1,999,999, collection=999,999 -> net=97,000,001
    - 100,000,000: creation=2,500,000, collection=1,000,000 -> net=96,500,000
    - 250,000,000: creation=6,250,000, collection=2,500,000 -> net=241,250,000
    """
    # 999,999 coins
    assert creation_fee(999_999) == 9_999
    assert collection_fee(999_999, derpy_active=False) == 0
    assert net_revenue(999_999, derpy_active=False) == 990_000

    # 1,000,000 coins (1M collection fee boundary cliff)
    assert creation_fee(1_000_000) == 10_000
    assert collection_fee(1_000_000, derpy_active=False) == 10_000
    assert net_revenue(1_000_000, derpy_active=False) == 980_000

    # 9,999,999 coins (just below 10M creation cliff)
    assert creation_fee(9_999_999) == 99_999
    assert collection_fee(9_999_999, derpy_active=False) == 99_999
    assert net_revenue(9_999_999, derpy_active=False) == 9_800_001

    # 10,000,000 coins (at 10M creation cliff)
    assert creation_fee(10_000_000) == 200_000
    assert collection_fee(10_000_000, derpy_active=False) == 100_000
    assert net_revenue(10_000_000, derpy_active=False) == 9_700_000

    # 99,999,999 coins (just below 100M creation cliff)
    assert creation_fee(99_999_999) == 1_999_999
    assert collection_fee(99_999_999, derpy_active=False) == 999_999
    assert net_revenue(99_999_999, derpy_active=False) == 97_000_001

    # 100,000,000 coins (at 100M creation cliff)
    assert creation_fee(100_000_000) == 2_500_000
    assert collection_fee(100_000_000, derpy_active=False) == 1_000_000
    assert net_revenue(100_000_000, derpy_active=False) == 96_500_000

    # 250,000,000 coins
    assert creation_fee(250_000_000) == 6_250_000
    assert collection_fee(250_000_000, derpy_active=False) == 2_500_000
    assert net_revenue(250_000_000, derpy_active=False) == 241_250_000


def test_hand_computed_exact_price_points_derpy_rate():
    """
    Test hand-computed exact price points with Mayor Derpy active (4.0% collection fee):
    - 999,999: creation=9,999, collection=0 -> net=990,000
    - 1,000,000: creation=10,000, collection=40,000 -> net=950,000
    - 9,999,999: creation=99,999, collection=399,999 -> net=9,500,001
    - 10,000,000: creation=200,000, collection=400,000 -> net=9,400,000
    - 99,999,999: creation=1,999,999, collection=3,999,999 -> net=94,000,001
    - 100,000,000: creation=2,500,000, collection=4,000,000 -> net=93,500,000
    - 250,000,000: creation=6,250,000, collection=10,000,000 -> net=233,750,000
    """
    # 999,999 coins (no collection fee below 1M)
    assert creation_fee(999_999) == 9_999
    assert collection_fee(999_999, derpy_active=True) == 0
    assert net_revenue(999_999, derpy_active=True) == 990_000

    # 1,000,000 coins (4% collection fee starts)
    assert creation_fee(1_000_000) == 10_000
    assert collection_fee(1_000_000, derpy_active=True) == 40_000
    assert net_revenue(1_000_000, derpy_active=True) == 950_000

    # 9,999,999 coins
    assert creation_fee(9_999_999) == 99_999
    assert collection_fee(9_999_999, derpy_active=True) == 399_999
    assert net_revenue(9_999_999, derpy_active=True) == 9_500_001

    # 10,000,000 coins
    assert creation_fee(10_000_000) == 200_000
    assert collection_fee(10_000_000, derpy_active=True) == 400_000
    assert net_revenue(10_000_000, derpy_active=True) == 9_400_000

    # 99,999,999 coins
    assert creation_fee(99_999_999) == 1_999_999
    assert collection_fee(99_999_999, derpy_active=True) == 3_999_999
    assert net_revenue(99_999_999, derpy_active=True) == 94_000_001

    # 100,000,000 coins
    assert creation_fee(100_000_000) == 2_500_000
    assert collection_fee(100_000_000, derpy_active=True) == 4_000_000
    assert net_revenue(100_000_000, derpy_active=True) == 93_500_000

    # 250,000,000 coins
    assert creation_fee(250_000_000) == 6_250_000
    assert collection_fee(250_000_000, derpy_active=True) == 10_000_000
    assert net_revenue(250_000_000, derpy_active=True) == 233_750_000


def test_dead_zone_best_resale_net():
    """Verify best_resale_net identifies dead zones across 1M, 10M, and 100M boundaries."""
    # 1M Boundary dead zone: listing at 1,000,000 nets 980,000; listing at 999,999 nets 990,000
    res_1m = best_resale_net(1_000_000, derpy_active=False)
    assert res_1m.list_price == 999_999
    assert res_1m.net_revenue == 990_000

    # 10M Boundary dead zone: listing at 10,000,000 nets 9,700,000; listing at 9,999,999 nets 9,800,001
    res_10m = best_resale_net(10_000_000, derpy_active=False)
    assert res_10m.list_price == 9_999_999
    assert res_10m.net_revenue == 9_800_001

    # 100M Boundary dead zone: listing at 100,000,000 nets 96,500,000; listing at 99,999,999 nets 97,000,001
    res_100m = best_resale_net(100_000_000, derpy_active=False)
    assert res_100m.list_price == 99_999_999
    assert res_100m.net_revenue == 97_000_001

    # Outside dead zone: 10,200,000 nets 10,200,000 - 204,000 - 102,000 = 9,894,000 > 9,800,001
    res_above = best_resale_net(10_200_000, derpy_active=False)
    assert res_above.list_price == 10_200_000
    assert res_above.net_revenue == 9_894_000

    # Derpy dead zone at 10M: 9,999,999 nets 9,500,001 vs 10M nets 9,400,000
    res_derpy = best_resale_net(10_000_000, derpy_active=True)
    assert res_derpy.list_price == 9_999_999
    assert res_derpy.net_revenue == 9_500_001


def test_ledger_taxes_paid_equals_creation_plus_collection():
    """Verify ledger taxes paid equals creation_fee + collection_fee."""
    for price in (500_000, 1_000_000, 15_000_000, 120_000_000):
        for derpy in (False, True):
            c_fee = creation_fee(price)
            col_fee = collection_fee(price, derpy_active=derpy)
            tot = total_fee(price, derpy_active=derpy)
            assert tot == c_fee + col_fee
            assert net_revenue(price, derpy_active=derpy) == price - tot


def test_compatibility_wrappers_recomputed_values():
    """
    Verify backward-compatible wrappers using recomputed values under new creation + collection fee model:
    - 5M: creation=50k, collection=50k -> tax=100k, net=4.90M
    - 10M: creation=200k, collection=100k -> tax=300k, net=9.70M
    - 50M: creation=1M, collection=500k -> tax=1.5M, net=48.5M
    - 150M: creation=3.75M, collection=1.5M -> tax=5.25M, net=144.75M
    """
    # 5M item
    assert calculate_progressive_ah_tax(5_000_000) == 100_000
    assert calculate_progressive_net_revenue(5_000_000) == 4_900_000

    # 10M item
    assert calculate_progressive_ah_tax(10_000_000) == 300_000
    assert calculate_progressive_net_revenue(10_000_000) == 9_700_000

    # 50M item
    assert calculate_progressive_ah_tax(50_000_000) == 1_500_000
    assert calculate_progressive_net_revenue(50_000_000) == 48_500_000

    # 150M item: Buy 100M, resell 150M -> net revenue = 144.75M -> net profit = 44.75M
    assert calculate_progressive_ah_tax(150_000_000) == 5_250_000
    assert calculate_progressive_net_profit(150_000_000, 100_000_000) == 44_750_000


def test_evaluator_derpy_integration():
    """Verify FlipEvaluator integrates derpy_active flag into calculate_net_revenue."""
    cfg = Settings(DRY_RUN=True)
    evaluator = FlipEvaluator(cfg)
    assert not evaluator.derpy_active

    # Normal rate for 10M: net revenue = 9,700,000
    assert evaluator.calculate_net_revenue(10_000_000) == 9_700_000

    # Activate Derpy: net revenue = 9,400,000
    evaluator.set_derpy_active(True)
    assert evaluator.derpy_active
    assert evaluator.calculate_net_revenue(10_000_000) == 9_400_000


@pytest.mark.asyncio
async def test_mayor_election_tracker_override_and_cache(monkeypatch):
    """Verify MayorElectionTracker respects MAYOR_OVERRIDE and handles offline fallback."""
    # 1. Override via instance argument
    tracker_override = MayorElectionTracker(override="derpy")
    assert tracker_override.derpy_active is True

    tracker_paul = MayorElectionTracker(override="paul")
    assert tracker_paul.derpy_active is False

    # 2. Override via environment variable
    monkeypatch.setenv("MAYOR_OVERRIDE", "derpy")
    tracker_env = MayorElectionTracker()
    assert tracker_env.derpy_active is True

    monkeypatch.setenv("MAYOR_OVERRIDE", "diana")
    tracker_env2 = MayorElectionTracker()
    assert tracker_env2.derpy_active is False
