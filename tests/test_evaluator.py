"""Tests for arbitrage margin calculations, tax deduction, and bait filtering."""

from __future__ import annotations

import pytest

from src.config import Settings
from src.evaluator import PROGRESSION_STAPLES, FlipEvaluator, FlipResult


@pytest.fixture
def base_config() -> Settings:
    return Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        AH_TAX_RATE=0.02,
        MIN_SAMPLE_SIZE=3,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )


def test_profitable_flip_detection(base_config: Settings):
    """Test that an auction listed below LBIN triggers a FlipResult when thresholds are met."""
    evaluator = FlipEvaluator(base_config)
    evaluator.set_price_cache({"HYPERION": [10_000_000, 15_000_000, 16_000_000]})

    auction = {"uuid": "test-uuid-1", "starting_bid": 10_000_000}
    decoded = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "enchantments": {"sharpness": 7},
    }

    result = evaluator.evaluate_listing(auction, decoded)

    assert result is not None
    assert isinstance(result, FlipResult)
    assert result.auction_uuid == "test-uuid-1"
    assert result.buy_price == 10_000_000
    assert result.target_resell_price == 15_000_000
    # Net revenue = 15,000,000 * 0.98 = 14,700,000
    # Net profit = 14,700,000 - 10,000,000 = 4,700,000
    assert result.net_profit == 4_700_000
    # Margin = 4,700,000 / 10,000,000 * 100 = 47.0%
    assert result.margin_pct == 47.0
    assert result.dungeon_stars == 5
    assert result.rarity_upgrades == 1


def test_ah_tax_deduction_accuracy(base_config: Settings):
    """Verify exact 2% AH tax calculation on target resell price."""
    evaluator = FlipEvaluator(base_config)
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [20_000_000, 25_000_000, 26_000_000]})

    auction = {"uuid": "test-uuid-2", "starting_bid": 20_000_000}
    decoded = {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None

    expected_net_revenue = 25_000_000 * (1.0 - 0.02)  # 24,500,000
    expected_profit = int(expected_net_revenue - 20_000_000)  # 4,500,000
    expected_margin = (expected_profit / 20_000_000) * 100.0  # 22.5%

    assert result.net_profit == expected_profit
    assert result.margin_pct == round(expected_margin, 2)


def test_rejection_when_coin_profit_insufficient(base_config: Settings):
    """Reject flip if net coin profit is less than MIN_PROFIT_COINS even if margin % is high."""
    evaluator = FlipEvaluator(base_config)
    # Buy 100k, resell 1.2M -> profit ~1.076M passes, but buy 100k, resell 500k -> profit 390k < 1M
    evaluator.set_price_cache({"CHEAP_ITEM": [100_000, 500_000, 600_000]})

    auction = {"uuid": "test-uuid-3", "starting_bid": 100_000}
    decoded = {"internal_id": "CHEAP_ITEM", "item_name": "Cheap Item"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_rejection_when_margin_pct_insufficient(base_config: Settings):
    """Reject flip if margin % is less than MIN_PROFIT_MARGIN_PCT even if absolute profit > 1M."""
    evaluator = FlipEvaluator(base_config)
    # Buy 100M, resell 105M. Net revenue: 102.9M. Profit: 2.9M > 1M coins, but margin is 2.9% < 15%
    evaluator.set_price_cache({"EXPENSIVE_ITEM": [100_000_000, 105_000_000, 106_000_000]})

    auction = {"uuid": "test-uuid-4", "starting_bid": 100_000_000}
    decoded = {"internal_id": "EXPENSIVE_ITEM", "item_name": "Expensive Item"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_rejection_below_min_sample_size(base_config: Settings):
    """Reject item if total active listings are fewer than MIN_SAMPLE_SIZE (anti-manipulation)."""
    evaluator = FlipEvaluator(base_config)
    # Only 2 listings active (< MIN_SAMPLE_SIZE = 3)
    evaluator.set_price_cache({"RARE_BAIT": [5_000_000, 20_000_000]})

    auction = {"uuid": "test-uuid-5", "starting_bid": 5_000_000}
    decoded = {"internal_id": "RARE_BAIT", "item_name": "Rare Bait Item"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_rejection_by_anti_bait_ceiling(base_config: Settings):
    """Reject listing if target resell exceeds buy price by more than MAX_TARGET_TO_BUY_RATIO."""
    evaluator = FlipEvaluator(base_config)
    # Buy at 10M, 2nd lowest at 50M (ratio = 5.0x > MAX 3.0x ceiling)
    evaluator.set_price_cache({"SUSPICIOUS_ITEM": [10_000_000, 50_000_000, 55_000_000]})

    auction = {"uuid": "test-uuid-6", "starting_bid": 10_000_000}
    decoded = {"internal_id": "SUSPICIOUS_ITEM", "item_name": "Suspicious Bait"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_rejection_tied_lowest_prices(base_config: Settings):
    """Reject if there are tied lowest prices because flipping yields 0 or negative net profit."""
    evaluator = FlipEvaluator(base_config)
    evaluator.set_price_cache({"TIED_ITEM": [10_000_000, 10_000_000, 20_000_000]})

    auction = {"uuid": "test-uuid-7", "starting_bid": 10_000_000}
    decoded = {"internal_id": "TIED_ITEM", "item_name": "Tied Item"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_update_cache_from_auctions(base_config: Settings):
    """Verify update_cache correctly sorts and groups active BIN listings by internal_id."""
    evaluator = FlipEvaluator(base_config)

    sample_batch = [
        ({"starting_bid": 15_000_000}, {"internal_id": "SHADOW_FURY"}),
        ({"starting_bid": 12_000_000}, {"internal_id": "SHADOW_FURY"}),
        ({"starting_bid": 18_000_000}, {"internal_id": "SHADOW_FURY"}),
        ({"starting_bid": 5_000_000}, {"internal_id": "LIVID_DAGGER"}),
        ({"starting_bid": 0}, {"internal_id": "INVALID"}),
        ({"starting_bid": 10_000_000}, {"internal_id": "UNKNOWN"}),
    ]

    evaluator.update_cache(sample_batch)

    assert "SHADOW_FURY" in evaluator.price_cache
    assert evaluator.price_cache["SHADOW_FURY"] == [12_000_000, 15_000_000, 18_000_000]
    assert evaluator.price_cache["LIVID_DAGGER"] == [5_000_000]
    assert "UNKNOWN" not in evaluator.price_cache


def test_is_blacklisted_cosmetic_categories(base_config: Settings):
    """Verify is_blacklisted_cosmetic correctly identifies all cosmetic and vanity categories."""
    evaluator = FlipEvaluator(base_config)

    # Pet skins
    assert evaluator.is_blacklisted_cosmetic("PET_SKIN_BABY_YETI_ICE") is True
    assert evaluator.is_blacklisted_cosmetic("PET_SKIN_BLACK_CAT_ONYX") is True
    assert evaluator.is_blacklisted_cosmetic("PET_SKIN_GOLDEN_DRAGON_RETRO") is True

    # Dyes
    assert evaluator.is_blacklisted_cosmetic("DYE_NECRON") is True
    assert evaluator.is_blacklisted_cosmetic("DYE_PURE_BLACK") is True
    assert evaluator.is_blacklisted_cosmetic("DYE_CARMINE") is True
    assert evaluator.is_blacklisted_cosmetic("DYE_ICEBERG") is True
    assert evaluator.is_blacklisted_cosmetic("DYE_BONE") is True

    # Runes (exact ID, prefix, substring, and item_name patterns)
    assert evaluator.is_blacklisted_cosmetic("RUNE") is True
    assert evaluator.is_blacklisted_cosmetic("RUNE_COUTURE") is True
    assert evaluator.is_blacklisted_cosmetic("RUNE_BLOOD") is True
    assert evaluator.is_blacklisted_cosmetic("RUNE_ZOMBIE") is True
    assert evaluator.is_blacklisted_cosmetic("GRAND_SEARING_RUNE") is True
    assert evaluator.is_blacklisted_cosmetic("HOT_SNOW_RUNE") is True
    assert evaluator.is_blacklisted_cosmetic("MISC_ITEM", item_name="Couture Rune I") is True
    assert evaluator.is_blacklisted_cosmetic("MISC_ITEM", item_name="Blood Rune III") is True
    assert evaluator.is_blacklisted_cosmetic("MISC_ITEM", item_name="◆ Grand Searing Rune I ◆") is True

    # Furniture & island vanity items
    assert evaluator.is_blacklisted_cosmetic("FURNITURE_CHAIR") is True
    assert evaluator.is_blacklisted_cosmetic("FURNITURE_TABLE") is True
    assert evaluator.is_blacklisted_cosmetic("BALLOON_SLIME") is True
    assert evaluator.is_blacklisted_cosmetic("PLUSHIE_CAT") is True
    assert evaluator.is_blacklisted_cosmetic("CARPET_FANCY") is True
    assert evaluator.is_blacklisted_cosmetic("BANNER_BLUE") is True
    assert evaluator.is_blacklisted_cosmetic("PAINTING_MONA") is True
    assert evaluator.is_blacklisted_cosmetic("MODEL_SHIP") is True
    assert evaluator.is_blacklisted_cosmetic("FIREWORK_ROCKET") is True
    assert evaluator.is_blacklisted_cosmetic("BED_COMFY") is True

    # Minion skins
    assert evaluator.is_blacklisted_cosmetic("MINION_SKIN_SANTA") is True
    assert evaluator.is_blacklisted_cosmetic("MINION_SKIN_TIGER") is True

    # Fire sale skins
    assert evaluator.is_blacklisted_cosmetic("WARDEN_HELMET_SKIN_DISCO") is True
    assert evaluator.is_blacklisted_cosmetic("NECRON_HELMET_SKIN_GOLD") is True
    assert evaluator.is_blacklisted_cosmetic("BONZO_MASK_SKIN") is True


def test_is_blacklisted_cosmetic_boundary_weapons_and_armor(base_config: Settings):
    """
    Ensure delimiter boundaries prevent false positives:
    Core combat weapons and armor must never be dropped.
    """
    evaluator = FlipEvaluator(base_config)

    # Core progression weapons and armor
    assert evaluator.is_blacklisted_cosmetic("RUNAAN_BOW", "Runaan's Bow") is False
    assert evaluator.is_blacklisted_cosmetic("HYPERION", "Heroic Hyperion") is False
    assert evaluator.is_blacklisted_cosmetic("NECRON_CHESTPLATE", "Necron's Chestplate") is False
    assert evaluator.is_blacklisted_cosmetic("TERMINATOR", "Terminator") is False
    assert evaluator.is_blacklisted_cosmetic("JUJU_SHORTBOW", "Juju Shortbow") is False
    assert evaluator.is_blacklisted_cosmetic("LIVID_DAGGER", "Livid Dagger") is False
    assert evaluator.is_blacklisted_cosmetic("SHADOW_FURY", "Shadow Fury") is False
    assert evaluator.is_blacklisted_cosmetic("VALKYRIE", "Valkyrie") is False
    assert evaluator.is_blacklisted_cosmetic("SCYLLA", "Scylla") is False
    assert evaluator.is_blacklisted_cosmetic("ASTRAEA", "Astraea") is False
    assert evaluator.is_blacklisted_cosmetic("GIANTS_SWORD", "Giant's Sword") is False
    assert evaluator.is_blacklisted_cosmetic("STORM_CHESTPLATE", "Storm's Chestplate") is False
    assert evaluator.is_blacklisted_cosmetic("GOLDOR_CHESTPLATE", "Goldor's Chestplate") is False
    assert evaluator.is_blacklisted_cosmetic("MAXOR_BOOTS", "Maxor's Boots") is False
    assert evaluator.is_blacklisted_cosmetic("WARDEN_HELMET", "Warden Helmet") is False

    # Weapons/armor with applied rune or dyed metadata in display names
    assert evaluator.is_blacklisted_cosmetic("HYPERION", "◆ Heroic Hyperion ✪✪✪✪✪ ◆") is False
    assert evaluator.is_blacklisted_cosmetic("NECRON_CHESTPLATE", "Necron's Chestplate") is False

    # Edge cases
    assert evaluator.is_blacklisted_cosmetic("UNKNOWN") is False
    assert evaluator.is_blacklisted_cosmetic("") is False


def test_cosmetic_items_rejected_in_evaluate_listing(base_config: Settings):
    """Verify vanity/cosmetic items are rejected in evaluate_listing prior to pricing checks."""
    evaluator = FlipEvaluator(base_config)

    # Populate cache with prices that would otherwise meet margin criteria
    evaluator.set_price_cache({
        "PET_SKIN_BABY_YETI_ICE": [20_000_000, 50_000_000, 55_000_000],
        "DYE_NECRON": [50_000_000, 100_000_000, 110_000_000],
        "RUNE_COUTURE": [2_000_000, 5_000_000, 6_000_000],
        "WARDEN_HELMET_SKIN_DISCO": [40_000_000, 90_000_000, 95_000_000],
        "MINION_SKIN_SANTA": [5_000_000, 12_000_000, 13_000_000],
        "FURNITURE_CHAIR": [1_000_000, 3_000_000, 3_500_000],
    })

    cosmetic_candidates = [
        ({"uuid": "c-1", "starting_bid": 20_000_000}, {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Skin"}),
        ({"uuid": "c-2", "starting_bid": 50_000_000}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
        ({"uuid": "c-3", "starting_bid": 2_000_000}, {"internal_id": "RUNE_COUTURE", "item_name": "Couture Rune"}),
        ({"uuid": "c-4", "starting_bid": 40_000_000}, {"internal_id": "WARDEN_HELMET_SKIN_DISCO", "item_name": "Disco Warden Helmet Skin"}),
        ({"uuid": "c-5", "starting_bid": 5_000_000}, {"internal_id": "MINION_SKIN_SANTA", "item_name": "Santa Minion Skin"}),
        ({"uuid": "c-6", "starting_bid": 1_000_000}, {"internal_id": "FURNITURE_CHAIR", "item_name": "Chair"}),
    ]

    for auction, decoded in cosmetic_candidates:
        result = evaluator.evaluate_listing(auction, decoded)
        assert result is None, f"Expected {decoded['internal_id']} to be rejected as cosmetic"


def test_core_weapons_and_armor_retained_in_evaluate_listing(base_config: Settings):
    """Verify core weapons and armor (RUNAAN_BOW, HYPERION, NECRON_CHESTPLATE) are retained and evaluated."""
    evaluator = FlipEvaluator(base_config)
    evaluator.set_price_cache({
        "RUNAAN_BOW": [2_000_000, 4_000_000, 4_500_000],
        "HYPERION": [10_000_000, 15_000_000, 16_000_000],
        "NECRON_CHESTPLATE": [20_000_000, 25_000_000, 26_000_000],
    })

    # 1. RUNAAN_BOW (Must not conflict with RUNE)
    runaan_res = evaluator.evaluate_listing(
        {"uuid": "core-1", "starting_bid": 2_000_000},
        {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"},
    )
    assert runaan_res is not None
    assert runaan_res.internal_id == "RUNAAN_BOW"
    assert runaan_res.buy_price == 2_000_000

    # 2. HYPERION
    hyp_res = evaluator.evaluate_listing(
        {"uuid": "core-2", "starting_bid": 10_000_000},
        {"internal_id": "HYPERION", "item_name": "Heroic Hyperion"},
    )
    assert hyp_res is not None
    assert hyp_res.internal_id == "HYPERION"

    # 3. NECRON_CHESTPLATE (Must not conflict with DYE_NECRON)
    necron_res = evaluator.evaluate_listing(
        {"uuid": "core-3", "starting_bid": 20_000_000},
        {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"},
    )
    assert necron_res is not None
    assert necron_res.internal_id == "NECRON_CHESTPLATE"


def test_cosmetic_items_excluded_from_update_cache(base_config: Settings):
    """Verify vanity/cosmetic items are excluded during update_cache so they never enter _price_cache."""
    evaluator = FlipEvaluator(base_config)

    batch = [
        ({"starting_bid": 20_000_000}, {"internal_id": "PET_SKIN_BABY_YETI_ICE"}),
        ({"starting_bid": 50_000_000}, {"internal_id": "DYE_NECRON"}),
        ({"starting_bid": 2_000_000}, {"internal_id": "RUNE_COUTURE"}),
        ({"starting_bid": 1_000_000}, {"internal_id": "FURNITURE_CHAIR"}),
        ({"starting_bid": 40_000_000}, {"internal_id": "WARDEN_HELMET_SKIN_DISCO"}),
        ({"starting_bid": 5_000_000}, {"internal_id": "RUNE"}),
        ({"starting_bid": 3_000_000}, {"internal_id": "RUNAAN_BOW"}),
        ({"starting_bid": 100_000_000}, {"internal_id": "HYPERION"}),
    ]

    evaluator.update_cache(batch)

    # Cosmetics must not be present
    assert "PET_SKIN_BABY_YETI_ICE" not in evaluator.price_cache
    assert "DYE_NECRON" not in evaluator.price_cache
    assert "RUNE_COUTURE" not in evaluator.price_cache
    assert "FURNITURE_CHAIR" not in evaluator.price_cache
    assert "WARDEN_HELMET_SKIN_DISCO" not in evaluator.price_cache
    assert "RUNE" not in evaluator.price_cache

    # Core items must be present
    assert "RUNAAN_BOW" in evaluator.price_cache
    assert evaluator.price_cache["RUNAAN_BOW"] == [3_000_000]
    assert "HYPERION" in evaluator.price_cache
    assert evaluator.price_cache["HYPERION"] == [100_000_000]


def test_config_toggle_disables_cosmetic_filter():
    """Verify setting COSMETIC_FILTER_ENABLED=False disables all cosmetic filtering."""
    disabled_config = Settings(
        DRY_RUN=True,
        COSMETIC_FILTER_ENABLED=False,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        MIN_SAMPLE_SIZE=3,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )
    evaluator = FlipEvaluator(disabled_config)

    # Direct helper returns False
    assert evaluator.is_blacklisted_cosmetic("PET_SKIN_BABY_YETI_ICE") is False
    assert evaluator.is_blacklisted_cosmetic("DYE_NECRON") is False
    assert evaluator.is_blacklisted_cosmetic("RUNE") is False

    # Cache retains cosmetic item when filter is disabled
    batch = [
        ({"starting_bid": 20_000_000}, {"internal_id": "PET_SKIN_BABY_YETI_ICE"}),
        ({"starting_bid": 30_000_000}, {"internal_id": "PET_SKIN_BABY_YETI_ICE"}),
        ({"starting_bid": 35_000_000}, {"internal_id": "PET_SKIN_BABY_YETI_ICE"}),
    ]
    evaluator.update_cache(batch)
    assert "PET_SKIN_BABY_YETI_ICE" in evaluator.price_cache

    # evaluate_listing produces FlipResult for cosmetic item when filter is disabled
    result = evaluator.evaluate_listing(
        {"uuid": "skin-1", "starting_bid": 20_000_000},
        {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Skin"},
    )
    assert result is not None
    assert result.internal_id == "PET_SKIN_BABY_YETI_ICE"
    assert result.buy_price == 20_000_000


def test_progression_staple_qualified_at_relaxed_thresholds(base_config: Settings):
    """Verify progression staples qualify at relaxed thresholds (500k profit, 8% margin)."""
    evaluator = FlipEvaluator(base_config)
    # Buy at 6M, resell at 6.7M -> Net revenue 6,566,000, profit 566,000 (>= 500k, < 1M), margin 9.43% (>= 8%, < 15%)
    evaluator.set_price_cache({"LIVID_DAGGER": [6_000_000, 6_700_000, 7_000_000]})

    auction = {"uuid": "staple-1", "starting_bid": 6_000_000}
    decoded = {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert isinstance(result, FlipResult)
    assert result.internal_id == "LIVID_DAGGER"
    assert result.net_profit == 566_000
    assert result.margin_pct == 9.43
    assert result.is_priority is True
    assert result.staple_category == "MIDGAME_COMBAT"


def test_non_staple_rejection_at_relaxed_margin(base_config: Settings):
    """Verify non-staples fail evaluation when only meeting relaxed staple thresholds."""
    evaluator = FlipEvaluator(base_config)
    # Identical 566k profit and 9.43% margin on non-staple item
    evaluator.set_price_cache({"STANDARD_NON_STAPLE": [6_000_000, 6_700_000, 7_000_000]})

    auction = {"uuid": "non-staple-1", "starting_bid": 6_000_000}
    decoded = {"internal_id": "STANDARD_NON_STAPLE", "item_name": "Standard Non Staple"}

    result = evaluator.evaluate_listing(auction, decoded)
    # Rejection: non-staple strictly requires 1M profit and 15% margin
    assert result is None


def test_non_staple_rejection_at_8pct_margin_with_high_coin_profit(base_config: Settings):
    """Verify non-staples with >1M coin profit but <15% margin are rejected, while staples pass."""
    evaluator = FlipEvaluator(base_config)
    # Buy at 20M, resell at 22.2M -> Net revenue 21,756,000, profit 1,756,000 (>1M), margin 8.78% (>=8%, <15%)
    evaluator.set_price_cache({
        "FARMING_TOOL": [20_000_000, 22_200_000, 23_000_000],
        "NECRON_CHESTPLATE": [20_000_000, 22_200_000, 23_000_000],
    })

    # Non-staple fails because 8.78% < 15.0%
    non_staple_result = evaluator.evaluate_listing(
        {"uuid": "tool-1", "starting_bid": 20_000_000},
        {"internal_id": "FARMING_TOOL", "item_name": "Farming Tool"},
    )
    assert non_staple_result is None

    # Staple passes because 8.78% >= 8.0% and 1.756M >= 500k
    staple_result = evaluator.evaluate_listing(
        {"uuid": "necron-1", "starting_bid": 20_000_000},
        {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"},
    )
    assert staple_result is not None
    assert staple_result.is_priority is True
    assert staple_result.staple_category == "WITHER_ARMOR"
    assert staple_result.net_profit == 1_756_000


def test_progression_staple_rejection_below_relaxed_thresholds(base_config: Settings):
    """Verify progression staples are rejected if they fail even relaxed staple thresholds."""
    evaluator = FlipEvaluator(base_config)
    # Buy at 10M, resell at 10.4M -> Net revenue 10,192,000, profit 192,000 (< 500k), margin 1.92% (< 8%)
    evaluator.set_price_cache({"HYPERION": [10_000_000, 10_400_000, 11_000_000]})

    auction = {"uuid": "hyp-low", "starting_bid": 10_000_000}
    decoded = {"internal_id": "HYPERION", "item_name": "Hyperion"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


def test_all_four_progression_staple_categories(base_config: Settings):
    """Verify samples from all four staple categories are correctly classified with is_priority=True."""
    evaluator = FlipEvaluator(base_config)
    # Generous margin and profit to qualify all
    test_cases = [
        ("WITHER_ARMOR", "STORM_BOOTS", "Storm's Boots"),
        ("WITHER_ARMOR", "GOLDOR_HELMET", "Goldor's Helmet"),
        ("WITHER_ARMOR", "MAXOR_LEGGINGS", "Maxor's Leggings"),
        ("MIDGAME_COMBAT", "JUJU_SHORTBOW", "Juju Shortbow"),
        ("MIDGAME_COMBAT", "SHADOW_FURY", "Shadow Fury"),
        ("MIDGAME_COMBAT", "GIANTS_SWORD", "Giant's Sword"),
        ("MIDGAME_COMBAT", "GIANT_SWORD", "Giant Sword"),
        ("MIDGAME_COMBAT", "SHADOW_ASSASSIN_CHESTPLATE", "Shadow Assassin Chestplate"),
        ("ENDGAME_PROGRESSION", "HYPERION", "Hyperion"),
        ("ENDGAME_PROGRESSION", "TERMINATOR", "Terminator"),
        ("ENDGAME_PROGRESSION", "DARK_CLAYMORE", "Dark Claymore"),
        ("SLAYER_CRAFTABLE", "WARDEN_HELMET", "Warden Helmet"),
        ("SLAYER_CRAFTABLE", "ATOMSPLIT_KATANA", "Atomsplit Katana"),
        ("SLAYER_CRAFTABLE", "OVERFLUX_POWER_ORB", "Overflux Power Orb"),
        ("SLAYER_CRAFTABLE", "FINAL_DESTINATION_CHESTPLATE", "Final Destination Chestplate"),
    ]

    for expected_cat, item_id, item_name in test_cases:
        evaluator.set_price_cache({item_id: [10_000_000, 15_000_000, 16_000_000]})
        res = evaluator.evaluate_listing(
            {"uuid": f"uuid-{item_id}", "starting_bid": 10_000_000},
            {"internal_id": item_id, "item_name": item_name},
        )
        assert res is not None, f"Expected {item_id} to qualify"
        assert res.is_priority is True
        assert res.staple_category == expected_cat


def test_standard_flip_has_priority_false_and_none_category(base_config: Settings):
    """Verify non-staple items that qualify under standard thresholds have is_priority=False and None category."""
    evaluator = FlipEvaluator(base_config)
    evaluator.set_price_cache({"SUPER_CLEAVER": [1_000_000, 3_000_000, 3_500_000]})

    auction = {"uuid": "cleaver-1", "starting_bid": 1_000_000}
    decoded = {"internal_id": "SUPER_CLEAVER", "item_name": "Super Cleaver"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.is_priority is False
    assert result.staple_category is None


def test_progression_staples_taxonomy_dictionary():
    """Verify PROGRESSION_STAPLES dictionary contains all required staple categories and mappings."""
    assert "NECRON_CHESTPLATE" in PROGRESSION_STAPLES
    assert PROGRESSION_STAPLES["NECRON_CHESTPLATE"] == "WITHER_ARMOR"
    assert PROGRESSION_STAPLES["LIVID_DAGGER"] == "MIDGAME_COMBAT"
    assert PROGRESSION_STAPLES["HYPERION"] == "ENDGAME_PROGRESSION"
    assert PROGRESSION_STAPLES["WARDEN_HELMET"] == "SLAYER_CRAFTABLE"
    assert PROGRESSION_STAPLES["GIANTS_SWORD"] == "MIDGAME_COMBAT"
    assert PROGRESSION_STAPLES["GIANT_SWORD"] == "MIDGAME_COMBAT"
    assert len(PROGRESSION_STAPLES) >= 50


