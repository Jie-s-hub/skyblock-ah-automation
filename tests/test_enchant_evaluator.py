"""Unit tests for dedicated enchantment evaluation engine and progression safeguards."""

from __future__ import annotations

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.enchant_evaluator import (
    get_enchant_bazaar_cost,
    get_enchant_bazaar_price,
)
from src.networth_evaluator import calculate_enchant_bazaar_cost, calculate_item_addons


@pytest.fixture
def bz_cache() -> BazaarCache:
    """Fixture providing a BazaarCache instance with mock prices."""
    config = Settings(DRY_RUN=True)
    cache = BazaarCache(config)
    return cache


# ---------------------------------------------------------------------------
# 1. Progression Enchants (Champion, Compact, Cultivating, Expertise, Hecatomb)
# ---------------------------------------------------------------------------


def test_champion_10_progression_no_multiplier(bz_cache: BazaarCache):
    """
    Champion 10 with Champion 1 priced at 4,000,000 returns exactly 4,000,000.
    Must NOT apply 2^(10 - 1) = 512x multiplier to produce 2,048,000,000.
    """
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_CHAMPION_1": 4_000_000,
        }
    )

    # Test via get_enchant_bazaar_price
    price = get_enchant_bazaar_price(bz_cache, "champion", 10)
    assert price == 4_000_000

    # Test via get_enchant_bazaar_cost
    book_id, cost = get_enchant_bazaar_cost(bz_cache, "champion", 10)
    assert book_id == "ENCHANTMENT_CHAMPION_1"
    assert cost == 4_000_000

    # Test via networth_evaluator delegate
    networth_book, networth_cost = calculate_enchant_bazaar_cost(bz_cache, "champion", 10)
    assert networth_book == "ENCHANTMENT_CHAMPION_1"
    assert networth_cost == 4_000_000


@pytest.mark.parametrize("ench_name", ["compact", "cultivating", "expertise", "hecatomb", "toxophilite"])
def test_all_progression_enchants_resolve_to_tier_1_price(bz_cache: BazaarCache, ench_name: str):
    """All progression enchants level through player activity; level 10 resolves strictly to 1 * Tier 1."""
    prod_id = f"ENCHANTMENT_{ench_name.upper()}_1"
    bz_cache.set_mock_prices(
        {
            prod_id: 1_500_000,
        }
    )

    price_lvl_10 = get_enchant_bazaar_price(bz_cache, ench_name, 10)
    assert price_lvl_10 == 1_500_000

    price_lvl_5 = get_enchant_bazaar_price(bz_cache, ench_name, 5)
    assert price_lvl_5 == 1_500_000

    price_lvl_1 = get_enchant_bazaar_price(bz_cache, ench_name, 1)
    assert price_lvl_1 == 1_500_000


def test_progression_enchant_missing_tier_1_returns_zero(bz_cache: BazaarCache):
    """Progression enchant with missing Tier 1 price safely returns 0."""
    bz_cache.set_mock_prices({"SOME_OTHER_ITEM": 100_000})
    price = get_enchant_bazaar_price(bz_cache, "champion", 10)
    assert price == 0


# ---------------------------------------------------------------------------
# 2. Exact Tier Direct Lookup (No Double Multiplication)
# ---------------------------------------------------------------------------


def test_ultimate_wise_5_direct_bazaar_no_double_multiplication(bz_cache: BazaarCache):
    """
    Ultimate Wise 5 returns the direct Bazaar price of Tier 5 without being multiplied by 5 or 16.
    """
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_ULTIMATE_WISE_5": 2_000_000,
            "ENCHANTMENT_ULTIMATE_WISE_1": 150_000,
        }
    )

    # When Tier 5 exists, it must return 2,000,000 directly
    price = get_enchant_bazaar_price(bz_cache, "ultimate_wise", 5)
    assert price == 2_000_000
    assert price != 2_000_000 * 5
    assert price != 2_000_000 * 16

    book_id, cost = get_enchant_bazaar_cost(bz_cache, "ultimate_wise", 5)
    assert book_id == "ENCHANTMENT_ULTIMATE_WISE_5"
    assert cost == 2_000_000


def test_legion_5_direct_bazaar_price(bz_cache: BazaarCache):
    """Legion 5 present on Bazaar returns direct Tier 5 price."""
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_ULTIMATE_LEGION_5": 30_000_000,
            "ENCHANTMENT_ULTIMATE_LEGION_1": 5_000_000,
        }
    )

    price = get_enchant_bazaar_price(bz_cache, "legion", 5)
    assert price == 30_000_000


# ---------------------------------------------------------------------------
# 3. High-Tier Compounding (T6 to T7)
# ---------------------------------------------------------------------------


def test_growth_7_compounds_from_tier_6(bz_cache: BazaarCache):
    """
    Growth 7 compounds as 2 * Growth 6 price when Growth 7 is missing from Bazaar.
    Must NOT compound as 64 * Growth 1.
    """
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_GROWTH_6": 12_000_000,
            "ENCHANTMENT_GROWTH_1": 100_000,
        }
    )

    # Growth 7 is missing: compounds as 2 * 12,000,000 = 24,000,000
    price = get_enchant_bazaar_price(bz_cache, "growth", 7)
    assert price == 24_000_000
    # Assert it did NOT compound from Tier 1 (2^6 * 100,000 = 6,400,000)
    assert price != 6_400_000

    book_id, cost = get_enchant_bazaar_cost(bz_cache, "growth", 7)
    assert book_id == "ENCHANTMENT_GROWTH_6"
    assert cost == 24_000_000


@pytest.mark.parametrize("ench_name", ["protection", "sharpness", "power", "giant_killer", "critical"])
def test_all_high_tier_enchants_compound_tier_7_from_tier_6(bz_cache: BazaarCache, ench_name: str):
    """High-tier enchants (T6 to T7) compound strictly as 2 * Price(Tier 6)."""
    t6_prod = f"ENCHANTMENT_{ench_name.upper()}_6"
    t1_prod = f"ENCHANTMENT_{ench_name.upper()}_1"
    bz_cache.set_mock_prices(
        {
            t6_prod: 15_000_000,
            t1_prod: 50_000,
        }
    )

    price = get_enchant_bazaar_price(bz_cache, ench_name, 7)
    assert price == 30_000_000  # 2 * 15M


def test_growth_7_exact_match_preferred_if_present(bz_cache: BazaarCache):
    """If Growth 7 is explicitly listed in Bazaar, its exact price is used directly."""
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_GROWTH_7": 28_000_000,
            "ENCHANTMENT_GROWTH_6": 12_000_000,
        }
    )

    price = get_enchant_bazaar_price(bz_cache, "growth", 7)
    assert price == 28_000_000


# ---------------------------------------------------------------------------
# 4. Standard Anvil Compounding Fallback
# ---------------------------------------------------------------------------


def test_anvil_fallback_tier_4_compounding(bz_cache: BazaarCache):
    """
    Anvil fallback for a missing Tier 4 book compounds correctly:
    - 2 * Tier 3 when Tier 3 is the highest available base tier.
    - 8 * Tier 1 when Tier 1 is the highest available base tier (T3 and T2 missing).
    """
    # Scenario A: Missing Tier 4, Tier 3 available at 1,000,000
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_SMITE_3": 1_000_000,
            "ENCHANTMENT_SMITE_1": 100_000,
        }
    )
    # Highest available B < 4 is B=3: 2^(4 - 3) * 1,000,000 = 2 * 1,000,000 = 2,000,000
    price_from_t3 = get_enchant_bazaar_price(bz_cache, "smite", 4)
    assert price_from_t3 == 2_000_000

    # Scenario B: Missing Tier 4, 3, 2; Tier 1 available at 100,000
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_SMITE_1": 100_000,
        }
    )
    # Highest available B < 4 is B=1: 2^(4 - 1) * 100,000 = 8 * 100,000 = 800,000
    price_from_t1 = get_enchant_bazaar_price(bz_cache, "smite", 4)
    assert price_from_t1 == 800_000

    # Scenario C: Missing Tier 4, 3; Tier 2 available at 250,000
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_SMITE_2": 250_000,
            "ENCHANTMENT_SMITE_1": 100_000,
        }
    )
    # Highest available B < 4 is B=2: 2^(4 - 2) * 250,000 = 4 * 250,000 = 1,000,000
    price_from_t2 = get_enchant_bazaar_price(bz_cache, "smite", 4)
    assert price_from_t2 == 1_000_000


# ---------------------------------------------------------------------------
# 5. Integration with Networth Item Addon Calculation
# ---------------------------------------------------------------------------


def test_networth_integration_progression_champion_10(bz_cache: BazaarCache):
    """
    An item with Champion 10 should produce an addon item costing 4,000,000 coins,
    not 2,048,000,000 coins.
    """
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_CHAMPION_1": 4_000_000,
        }
    )

    decoded = {
        "internal_id": "REAPER_FALCHION",
        "enchantments": {
            "champion": 10,
        },
    }

    addons, total_cost = calculate_item_addons(decoded, bz_cache)
    enchant_addons = [a for a in addons if a["type"] == "enchant"]
    assert len(enchant_addons) == 1
    assert enchant_addons[0]["name"] == "Champion 10"
    assert enchant_addons[0]["cost"] == 4_000_000
    assert total_cost == 4_000_000


def test_networth_integration_growth_7_high_tier(bz_cache: BazaarCache):
    """
    An item with Growth 7 should compound from Growth 6 (2 * 12M = 24M).
    """
    bz_cache.set_mock_prices(
        {
            "ENCHANTMENT_GROWTH_6": 12_000_000,
            "ENCHANTMENT_GROWTH_1": 100_000,
        }
    )

    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "enchantments": {
            "growth": 7,
        },
    }

    addons, total_cost = calculate_item_addons(decoded, bz_cache)
    enchant_addons = [a for a in addons if a["type"] == "enchant"]
    assert len(enchant_addons) == 1
    assert enchant_addons[0]["name"] == "Growth 7"
    assert enchant_addons[0]["cost"] == 24_000_000
    assert total_cost == 24_000_000


# ---------------------------------------------------------------------------
# 6. Edge Cases & Calling Patterns
# ---------------------------------------------------------------------------


def test_edge_cases_none_cache_or_invalid_tier():
    """Verify safe zero returns when cache is None or tier <= 0."""
    assert get_enchant_bazaar_price(None, "champion", 10) == 0
    assert get_enchant_bazaar_price(None, "growth", 7) == 0

    cache = BazaarCache()
    assert get_enchant_bazaar_price(cache, "champion", 0) == 0
    assert get_enchant_bazaar_price(cache, "champion", -1) == 0
    assert get_enchant_bazaar_price(cache, "", 5) == 0


def test_dict_cache_compatibility():
    """Verify get_enchant_bazaar_price supports plain dictionary mock caches."""
    raw_dict = {
        "ENCHANTMENT_CHAMPION_1": 4_000_000,
    }
    # Pass dict as bazaar_cache
    assert get_enchant_bazaar_price(raw_dict, "champion", 10) == 4_000_000
    # Alternative argument order
    assert get_enchant_bazaar_price("champion", 10, bazaar_cache=raw_dict) == 4_000_000
