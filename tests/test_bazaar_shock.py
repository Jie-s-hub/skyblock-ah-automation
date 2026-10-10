"""Tests for Bazaar Price Shock Guard (Phase 6)."""

import time
import pytest
from src.bazaar import BazaarCache
from src.config import Settings
from src.craft_evaluator import calculate_craft_cost
from src.evaluator import FlipEvaluator


def test_bazaar_cache_rolling_history_maxlen():
    cache = BazaarCache()
    prod = "RECOMBOBULATOR_3000"

    # Add 15 snapshots
    for i in range(15):
        cache.record_snapshot({prod: 10_000_000 + (i * 100_000)})

    history = cache._price_history[prod]
    # Verify deque maxlen is 10
    assert len(history) == 10
    # Oldest in deque should be snapshot index 5 (10_500_000)
    assert history[0][1] == 10_500_000
    # Newest should be index 14 (11_400_000)
    assert history[-1][1] == 11_400_000


def test_rolling_median_calculation():
    cache = BazaarCache()
    prod = "FIRST_MASTER_STAR"
    now = time.time()

    # Snapshots: 20M, 22M, 21M, 20M, 24M (Median of [20, 20, 21, 22, 24] is 21M)
    for price in [20_000_000, 22_000_000, 21_000_000, 20_000_000, 24_000_000]:
        cache.record_snapshot({prod: price}, timestamp=now)

    median = cache.get_rolling_median_buy_price(prod, now=now)
    assert median == 21_000_000.0


def test_price_shock_detection():
    config = Settings(
        ENABLE_BAZAAR_SHOCK_GUARD=True,
        BAZAAR_SHOCK_THRESHOLD_PCT=15.0,
        BAZAAR_SHOCK_MIN_SNAPSHOTS=3,
    )
    cache = BazaarCache(config=config)
    prod = "RECOMBOBULATOR_3000"
    now = time.time()

    # Baseline: 10M, 10M, 10M -> Median = 10M
    for _ in range(4):
        cache.record_snapshot({prod: 10_000_000}, timestamp=now)

    # 1. Price at 11M (+10% deviation) -> NOT shocked (<= 15%)
    assert not cache.is_price_shocked(prod, current_price=11_000_000, now=now)

    # 2. Price at 12M (+20% deviation) -> SHOCKED (> 15%)
    assert cache.is_price_shocked(prod, current_price=12_000_000, now=now)

    # 3. Price crash to 8M (-20% deviation) -> SHOCKED (> 15%)
    assert cache.is_price_shocked(prod, current_price=8_000_000, now=now)


def test_insufficient_snapshots_grace_period():
    config = Settings(
        ENABLE_BAZAAR_SHOCK_GUARD=True,
        BAZAAR_SHOCK_THRESHOLD_PCT=15.0,
        BAZAAR_SHOCK_MIN_SNAPSHOTS=3,
    )
    cache = BazaarCache(config=config)
    prod = "RECOMBOBULATOR_3000"

    # Only 2 snapshots (threshold requires 3)
    cache.record_snapshot({prod: 10_000_000})
    cache.record_snapshot({prod: 10_000_000})

    # Even with huge spike, returns False due to sample size requirement
    assert not cache.is_price_shocked(prod, current_price=20_000_000)


def test_upgraded_flip_rejected_on_addon_price_shock():
    config = Settings(
        ENABLE_BAZAAR_SHOCK_GUARD=True,
        BAZAAR_SHOCK_THRESHOLD_PCT=15.0,
        BAZAAR_SHOCK_MIN_SNAPSHOTS=3,
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MIN_SAMPLE_SIZE=2,
    )
    bazaar = BazaarCache(config=config)
    now = time.time()

    # Seed baseline prices for RECOMBOBULATOR_3000 at 10M
    for _ in range(4):
        bazaar.record_snapshot({"RECOMBOBULATOR_3000": 10_000_000}, timestamp=now)

    # Now simulate a massive Bazaar price spike to 15M (+50%)
    bazaar.set_mock_prices({"RECOMBOBULATOR_3000": 15_000_000}, timestamp=now)

    evaluator = FlipEvaluator(config=config, bazaar_cache=bazaar)
    evaluator.set_price_cache({"SHADOW_ASSASSIN_CHESTPLATE": [20_000_000, 22_000_000]})

    auction = {"uuid": "test-recomb-shock", "starting_bid": 18_000_000}
    decoded = {
        "internal_id": "SHADOW_ASSASSIN_CHESTPLATE",
        "item_name": "Shadow Assassin Chestplate",
        "rarity_upgrades": 1,
    }

    # Should be rejected because RECOMBOBULATOR_3000 is price-shocked (> 15% from 10M median)
    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is None

    # Now restore Recombobulator to 10M (within median)
    bazaar.set_mock_prices({"RECOMBOBULATOR_3000": 10_000_000}, timestamp=now)
    flip = evaluator.evaluate_listing(auction, decoded)
    # With 10M recomb retained at 70% (7M), worth is 20M + 7M = 27M. Buy at 18M -> profit!
    assert flip is not None


def test_craft_cost_aborts_on_ingredient_price_shock():
    config = Settings(
        ENABLE_BAZAAR_SHOCK_GUARD=True,
        BAZAAR_SHOCK_THRESHOLD_PCT=15.0,
        BAZAAR_SHOCK_MIN_SNAPSHOTS=3,
    )
    bazaar = BazaarCache(config=config)
    now = time.time()

    # SUPER_COMPACTOR_3000 recipe: ENCHANTED_COBBLESTONE x 448, ENCHANTED_REDSTONE_BLOCK x 1
    # Seed normal prices
    for _ in range(4):
        bazaar.record_snapshot({
            "ENCHANTED_COBBLESTONE": 1_000,
            "ENCHANTED_REDSTONE_BLOCK": 40_000,
        }, timestamp=now)

    bazaar.set_mock_prices({
        "ENCHANTED_COBBLESTONE": 1_000,
        "ENCHANTED_REDSTONE_BLOCK": 40_000,
    })

    # Normal craft cost should succeed: 448 * 1,000 + 40,000 = 488,000
    cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bazaar)
    assert cost == 488_000

    # Now shock ENCHANTED_COBBLESTONE to 2,000 (+100%)
    bazaar.set_mock_prices({
        "ENCHANTED_COBBLESTONE": 2_000,
        "ENCHANTED_REDSTONE_BLOCK": 40_000,
    }, timestamp=now)

    # With shock guard enabled, calculate_craft_cost should return None
    shocked_cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bazaar)
    assert shocked_cost is None
