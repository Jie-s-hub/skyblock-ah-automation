"""Tests for critical valuation edge cases:

1. Bazaar Book Tier Compounding (Minecraft anvil math: 2^(N-1) * Price(Tier 1)).
2. Missing Clean Baseline Fallback (Endgame items like Hyperion/Terminator with 0 clean listings).
3. Universal Blacklist Gate (Cosmetic, dye, rune, and bait filtering before Path A and Path B).
4. Prestige Tier Isolation (Burning Crimson does not compare against Basic Crimson).
5. Divan Chambers Cost Calculation (Divan's armor gemstone chamber networth).
"""

from __future__ import annotations

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.constants.prestige import get_prestige_tier, is_divan_piece
from src.evaluator import FlipEvaluator
from src.nbt_decoder import parse_divan_gemstone_chambers
from src.networth_evaluator import calculate_enchant_bazaar_cost, calculate_item_addons


@pytest.fixture
def base_settings() -> Settings:
    return Settings(
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        AH_TAX_RATE=0.02,
        MIN_SAMPLE_SIZE=3,
        ENABLE_NETWORTH_VALUATION=True,
        RETENTION_RATE=0.70,
        MAX_TARGET_TO_BUY_RATIO=3.0,
        COSMETIC_FILTER_ENABLED=True,
        ENABLE_VELOCITY_FILTER=False,  # Isolated to evaluate valuation logic
    )


# ============================================================================
# 1. Bazaar Book Tier Compounding Tests
# ============================================================================


def test_book_tier_compounding_ultimate_wise_5(base_settings: Settings):
    """
    Mock a Bazaar cache where only ENCHANTMENT_ULTIMATE_WISE_1 exists at 150,000 coins.
    Verify that an item with Ultimate Wise 5 evaluates the book's value to
    exactly 2^(5 - 1) * 150,000 = 16 * 150,000 = 2,400,000 coins.
    """
    cache = BazaarCache()
    cache.set_mock_prices({
        "ENCHANTMENT_ULTIMATE_WISE_1": 150_000,
    })

    # Test direct standalone calculator
    book_id, cost = calculate_enchant_bazaar_cost(cache, "ultimate_wise", 5)
    assert book_id == "ENCHANTMENT_ULTIMATE_WISE_1"
    assert cost == 2_400_000  # 16 * 150,000

    # Test via BazaarCache helper
    assert cache.get_enchant_price("ultimate_wise", 5) == 2_400_000

    # Test via FlipEvaluator method
    evaluator = FlipEvaluator(base_settings, bazaar_cache=cache)
    res_book, res_cost = evaluator._resolve_enchant_bazaar_cost("ultimate_wise", 5)
    assert res_book == "ENCHANTMENT_ULTIMATE_WISE_1"
    assert res_cost == 2_400_000


def test_book_compounding_in_full_evaluation(base_settings: Settings):
    """Verify that an item with Ultimate Wise 5 produces an itemized addon costing 2,400,000 coins."""
    cache = BazaarCache()
    cache.set_mock_prices({
        "ENCHANTMENT_ULTIMATE_WISE_1": 150_000,
    })
    evaluator = FlipEvaluator(base_settings, bazaar_cache=cache)
    evaluator.set_price_cache({"BONZO_STAFF": [3_000_000, 3_500_000, 4_000_000]})

    auction = {
        "uuid": "bonzo-uuid",
        "starting_bid": 3_100_000,
        "item_name": "Bonzo's Staff",
    }
    decoded = {
        "internal_id": "BONZO_STAFF",
        "item_name": "Bonzo's Staff",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {
            "ultimate_wise": 5,
        },
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is True

    # Check addon breakdown
    enchant_addons = [a for a in flip.addon_breakdown if a["type"] == "enchant"]
    assert len(enchant_addons) == 1
    assert enchant_addons[0]["name"] == "Ultimate Wise 5"
    assert enchant_addons[0]["cost"] == 2_400_000


def test_book_tier_exact_match_preferred_over_compounding():
    """Verify that if an exact tier book exists in Bazaar, its exact price is used without compounding."""
    cache = BazaarCache()
    cache.set_mock_prices({
        "ENCHANTMENT_GROWTH_6": 8_000_000,
        "ENCHANTMENT_GROWTH_1": 10_000,  # 2^5 * 10k = 320k (should NOT be used)
    })

    book_id, cost = calculate_enchant_bazaar_cost(cache, "growth", 6)
    assert book_id == "ENCHANTMENT_GROWTH_6"
    assert cost == 8_000_000


def test_book_tier_safe_zero_when_missing():
    """Verify that non-existent enchants return 0 safely without throwing an exception."""
    cache = BazaarCache()
    cache.set_mock_prices({"SOME_OTHER_ITEM": 1_000_000})

    book_id, cost = calculate_enchant_bazaar_cost(cache, "non_existent_enchant", 5)
    assert book_id == ""
    assert cost == 0
    assert cache.get_enchant_price("non_existent_enchant", 5) == 0


# ============================================================================
# 2. Missing Clean Baseline Fallback Tests
# ============================================================================


def test_missing_clean_baseline_endgame_fallback(base_settings: Settings):
    """
    Mock a scenario where 3 Hyperions exist on the AH (all with 5 stars and recombs)
    and 0 clean Hyperions exist.
    Verify that the evaluator successfully falls back to the lowest active BIN
    without raising an exception.
    """
    cache = BazaarCache()
    cache.set_mock_prices({
        "RECOMBOBULATOR_3000": 10_000_000,
        "ESSENCE_WITHER": 4_000,  # 2,250 * 4,000 = 9,000,000
        "WITHER_BLOOD": 6_000_000,
    })

    evaluator = FlipEvaluator(base_settings, bazaar_cache=cache)

    # 3 Hyperions on AH, ALL upgraded (5 stars, recomb) -> 0 clean Hyperions
    hyp1_raw = {"uuid": "hyp-1", "starting_bid": 1_400_000_000, "item_name": "Hyperion ✪✪✪✪✪"}
    hyp1_dec = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "withered",
    }

    hyp2_raw = {"uuid": "hyp-2", "starting_bid": 1_500_000_000, "item_name": "Hyperion ✪✪✪✪✪"}
    hyp2_dec = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "withered",
    }

    hyp3_raw = {"uuid": "hyp-3", "starting_bid": 1_600_000_000, "item_name": "Hyperion ✪✪✪✪✪"}
    hyp3_dec = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "withered",
    }

    # Update cache from the 3 active listings
    evaluator.update_cache([
        (hyp1_raw, hyp1_dec),
        (hyp2_raw, hyp2_dec),
        (hyp3_raw, hyp3_dec),
    ])

    # Assert 0 clean listings exist in clean cache
    assert evaluator.clean_price_cache.get("HYPERION", []) == []

    # Assert all 3 exist in total active price cache
    assert evaluator.price_cache.get("HYPERION") == [1_400_000_000, 1_500_000_000, 1_600_000_000]

    # Verify get_base_lbin falls back to lowest active BIN (1,400,000,000)
    base_lbin = evaluator.get_base_lbin("HYPERION")
    assert base_lbin == 1_400_000_000

    # Evaluate a snipe listing: listed at 1,200,000,000 with 5 stars, recomb, withered
    # Total addon cost = 9M (stars) + 10M (recomb) + 6M (withered) = 25M
    # Base LBIN = 1.4B (>= 500M) -> Dynamic Retention Rate = 85% (0.85)
    # Resell baseline = 1.4B + round(25M * 0.85) = 1,400,000,000 + 21,250,000 = 1,421,250,000
    # Net revenue (2% tax) = 1,421,250,000 * 0.98 = 1,392,825,000
    # Net profit = 1,392,825,000 - 1,200,000,000 = 192,825,000 coins
    snipe_auction = {
        "uuid": "hyp-snipe-uuid",
        "starting_bid": 1_200_000_000,
        "item_name": "Withered Hyperion ✪✪✪✪✪",
    }
    snipe_decoded = {
        "internal_id": "HYPERION",
        "item_name": "Withered Hyperion ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "withered",
        "enchantments": {},
    }

    flip = evaluator.evaluate_listing(snipe_auction, snipe_decoded)
    assert flip is not None
    assert flip.is_networth_snipe is True
    assert flip.base_lbin == 1_400_000_000
    assert flip.buy_price == 1_200_000_000
    assert flip.retention_rate == 0.85
    assert flip.target_resell_price == 1_421_250_000
    # Recomputed by hand per Phase 1 fee model (1,421,250,000 in >=100M tier: 2.5% creation + 1% collection = 3.5%):
    # Total fee = 1,421,250,000 * 0.035 = 49,743,750 -> Net revenue = 1,371,506,250
    # Net profit: 1,371,506,250 - 1,200,000,000 = 171,506,250 coins
    assert flip.net_profit == 171_506_250


def test_missing_clean_baseline_empty_cache_returns_none(base_settings: Settings):
    """Verify get_base_lbin returns None without raising an IndexError when no listings exist."""
    evaluator = FlipEvaluator(base_settings)
    assert evaluator.get_base_lbin("TERMINATOR") is None


def test_clean_baseline_used_when_available(base_settings: Settings):
    """Verify clean listing price is preferred as base_lbin when clean listings do exist."""
    evaluator = FlipEvaluator(base_settings)
    evaluator.set_price_cache(
        cache={"NECRON_CHESTPLATE": [20_000_000, 25_000_000, 30_000_000]},
        clean_cache={"NECRON_CHESTPLATE": [20_000_000, 21_000_000]},
    )

    assert evaluator.get_base_lbin("NECRON_CHESTPLATE") == 20_000_000


# ============================================================================
# 3. Universal Blacklist Filtering Tests
# ============================================================================


def test_universal_blacklist_sheep_skin_and_rune_couture(base_settings: Settings):
    """
    Verify that an item with ID SHEEP_SKIN_NEON or RUNE_COUTURE returns None
    immediately in evaluate_listing(), even if it has high simulated profit.
    """
    evaluator = FlipEvaluator(base_settings)

    # Populate cache with high prices that would otherwise guarantee a flip
    evaluator.set_price_cache({
        "SHEEP_SKIN_NEON": [1_000_000, 50_000_000, 60_000_000],
        "RUNE_COUTURE": [500_000, 20_000_000, 25_000_000],
    })

    # Test SHEEP_SKIN_NEON
    skin_auction = {
        "uuid": "skin-uuid",
        "starting_bid": 1_000_000,
        "item_name": "Neon Sheep Skin",
    }
    skin_decoded = {
        "internal_id": "SHEEP_SKIN_NEON",
        "item_name": "Neon Sheep Skin",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }
    assert evaluator.evaluate_listing(skin_auction, skin_decoded) is None

    # Test RUNE_COUTURE
    rune_auction = {
        "uuid": "rune-uuid",
        "starting_bid": 500_000,
        "item_name": "Couture Rune III",
    }
    rune_decoded = {
        "internal_id": "RUNE_COUTURE",
        "item_name": "Couture Rune III",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }
    assert evaluator.evaluate_listing(rune_auction, rune_decoded) is None


def test_universal_blacklist_all_blocked_keywords(base_settings: Settings):
    """
    Verify all required keywords are blocked:
    _SKIN, PET_SKIN_, DYE_, RUNE, PORTAL, FURNITURE, MUSIC_DISC.
    """
    evaluator = FlipEvaluator(base_settings)

    # Keywords in internal_id
    assert evaluator.is_blacklisted("SHEEP_SKIN_NEON") is True
    assert evaluator.is_blacklisted("BONZO_MASK_SKIN") is True
    assert evaluator.is_blacklisted("PET_SKIN_BABY_YETI") is True
    assert evaluator.is_blacklisted("DYE_CARMINE") is True
    assert evaluator.is_blacklisted("DYE_NECRON") is True
    assert evaluator.is_blacklisted("RUNE") is True
    assert evaluator.is_blacklisted("RUNE_COUTURE") is True
    assert evaluator.is_blacklisted("GRAND_SEARING_RUNE") is True
    assert evaluator.is_blacklisted("PORTAL_SCROLL_HUB") is True
    assert evaluator.is_blacklisted("PORTAL_NETHER") is True
    assert evaluator.is_blacklisted("FURNITURE_CHAIR") is True
    assert evaluator.is_blacklisted("MUSIC_DISC_CAT") is True
    assert evaluator.is_blacklisted("MUSIC_DISC_13") is True


def test_universal_blacklist_protects_core_weapons_and_armor(base_settings: Settings):
    """Verify core progression weapons and armor are never accidentally blacklisted."""
    evaluator = FlipEvaluator(base_settings)

    assert evaluator.is_blacklisted("RUNAAN_BOW", "Runaan's Bow") is False
    assert evaluator.is_blacklisted("HYPERION", "Heroic Hyperion") is False
    assert evaluator.is_blacklisted("NECRON_CHESTPLATE", "Necron's Chestplate") is False
    assert evaluator.is_blacklisted("TERMINATOR", "Terminator") is False
    assert evaluator.is_blacklisted("JUJU_SHORTBOW", "Juju Shortbow") is False
    assert evaluator.is_blacklisted("LIVID_DAGGER", "Livid Dagger") is False
    assert evaluator.is_blacklisted("GIANTS_SWORD", "Giant's Sword") is False

    # Applied rune display name on a real weapon
    assert evaluator.is_blacklisted("HYPERION", "◆ Heroic Hyperion ✪✪✪✪✪ ◆") is False


# ============================================================================
# 4. Prestige Tier Isolation Tests
# ============================================================================


def test_prestige_tier_isolation_edge_case(base_settings: Settings):
    """
    Verify that Burning Crimson Chestplate is strictly isolated and never compares
    against a clean Basic Crimson Chestplate.
    """
    evaluator = FlipEvaluator(base_settings)

    now_ms = 1_000_000_000_000
    start_time = now_ms - 60_000

    auctions = [
        ({"starting_bid": 10_000_000, "start": start_time}, {"internal_id": "CRIMSON_CHESTPLATE"}),
        ({"starting_bid": 12_000_000, "start": start_time}, {"internal_id": "CRIMSON_CHESTPLATE"}),
        ({"starting_bid": 14_000_000, "start": start_time}, {"internal_id": "CRIMSON_CHESTPLATE"}),
        ({"starting_bid": 150_000_000, "start": start_time}, {"internal_id": "BURNING_CRIMSON_CHESTPLATE"}),
        ({"starting_bid": 160_000_000, "start": start_time}, {"internal_id": "BURNING_CRIMSON_CHESTPLATE"}),
        ({"starting_bid": 170_000_000, "start": start_time}, {"internal_id": "BURNING_CRIMSON_CHESTPLATE"}),
    ]
    evaluator.update_cache(auctions, now_ms=now_ms)

    assert evaluator.get_base_lbin("CRIMSON_CHESTPLATE") == 10_000_000
    assert evaluator.get_base_lbin("BURNING_CRIMSON_CHESTPLATE") == 150_000_000

    candidate_auction = {
        "uuid": "burning-cand-uuid",
        "starting_bid": 110_000_000,
        "item_name": "Burning Crimson Chestplate",
    }
    candidate_decoded = {
        "internal_id": "BURNING_CRIMSON_CHESTPLATE",
        "item_name": "Burning Crimson Chestplate",
        "prestige_tier": "BURNING",
    }

    flip = evaluator.evaluate_listing(candidate_auction, candidate_decoded)
    assert flip is not None
    assert flip.internal_id == "BURNING_CRIMSON_CHESTPLATE"
    assert flip.prestige_tier == "BURNING"
    assert flip.base_lbin == 150_000_000


# ============================================================================
# 5. Divan Chambers Cost Calculation Tests
# ============================================================================


def test_divan_chambers_valuation_edge_case(base_settings: Settings):
    """
    Verify parsing Divan chambers and calculating exact Bazaar networth for unlocked chambers.
    """
    assert is_divan_piece("DIVAN_HELMET") is True
    assert is_divan_piece("DIVAN_CHESTPLATE") is True
    assert is_divan_piece("DIVAN_LEGGINGS") is True
    assert is_divan_piece("DIVAN_BOOTS") is True
    assert is_divan_piece("NECRON_CHESTPLATE") is False

    # Parsing tests
    assert parse_divan_gemstone_chambers("DIVAN_CHESTPLATE", {"gemstone_chambers": 5}) == 5
    assert parse_divan_gemstone_chambers("DIVAN_LEGGINGS", {"gems": {"unlocked_slots": 4}}) == 4
    assert parse_divan_gemstone_chambers("NECRON_CHESTPLATE", {"gemstone_chambers": 5}) == 0

    # Pricing tests with BazaarCache
    bz = BazaarCache()
    bz.set_mock_prices({"GEMSTONE_CHAMBER": 12_000_000})

    decoded = {
        "internal_id": "DIVAN_CHESTPLATE",
        "gemstone_chambers": 3,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
    }

    breakdown, total_cost = calculate_item_addons(decoded, bz)
    assert total_cost == 36_000_000  # 3 * 12M
    chamber_addon = next((a for a in breakdown if a["type"] == "gemstone_chambers"), None)
    assert chamber_addon is not None
    assert chamber_addon["cost"] == 36_000_000
    assert chamber_addon["count"] == 3

