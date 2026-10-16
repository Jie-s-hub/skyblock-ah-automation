"""Tests for dual-mode evaluation: Path A clean listings vs Path B networth addons."""

from __future__ import annotations

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.evaluator import FlipEvaluator


@pytest.fixture
def base_settings() -> Settings:
    return Settings(
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        AH_TAX_RATE=0.02,
        MIN_SAMPLE_SIZE=3,
        ENABLE_NETWORTH_VALUATION=True,
        RETENTION_RATE=0.70,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )


@pytest.fixture
def mock_bazaar() -> BazaarCache:
    cache = BazaarCache()
    cache.set_mock_prices(
        {
            "RECOMBOBULATOR_3000": 10_000_000,
            "ESSENCE_WITHER": 4_000,
            "ESSENCE_UNDEAD": 1_000,
            "PRECURSOR_GEAR": 7_000_000,
            "DRAGON_CLAW": 5_000_000,
            "ENCHANTMENT_ULTIMATE_SOUL_EATER_5": 16_000_000,
            "ENCHANTMENT_ULTIMATE_LEGION_5": 20_000_000,
            "ENCHANTMENT_SHARPNESS_7": 12_000_000,
            "ENCHANTMENT_OVERLOAD_5": 8_000_000,
        }
    )
    return cache


def test_is_upgraded_item_detection(base_settings: Settings):
    """Verify detection of stars, recomb, valuable enchants, and reforge stones."""
    evaluator = FlipEvaluator(base_settings)

    clean_item = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "clean",
        "enchantments": {"sharpness": 5, "protection": 5},
    }
    assert evaluator.is_upgraded_item(clean_item) is False

    # Starred
    assert evaluator.is_upgraded_item({**clean_item, "dungeon_stars": 5}) is True

    # Recombobulated
    assert evaluator.is_upgraded_item({**clean_item, "rarity_upgrades": 1}) is True

    # Reforge stone modifier
    assert evaluator.is_upgraded_item({**clean_item, "modifier": "ancient"}) is True
    # Non-bazaar modifier
    assert evaluator.is_upgraded_item({**clean_item, "modifier": "spicy"}) is False

    # Valuable enchants
    assert (
        evaluator.is_upgraded_item(
            {
                **clean_item,
                "enchantments": {"ultimate_soul_eater": 5},
            }
        )
        is True
    )

    assert (
        evaluator.is_upgraded_item(
            {
                **clean_item,
                "enchantments": {"sharpness": 7},
            }
        )
        is True
    )

    assert (
        evaluator.is_upgraded_item(
            {
                **clean_item,
                "enchantments": {"overload": 5},
            }
        )
        is True
    )

    # Standard non-valuable enchants
    assert (
        evaluator.is_upgraded_item(
            {
                **clean_item,
                "enchantments": {"sharpness": 5, "protection": 5},
            }
        )
        is False
    )


def test_is_valuable_enchant_rules(base_settings: Settings):
    """Verify enchantment value classification."""
    evaluator = FlipEvaluator(base_settings)

    # Ultimate enchants always qualify
    assert evaluator.is_valuable_enchant("ultimate_soul_eater", 1) is True
    assert evaluator.is_valuable_enchant("legion", 5) is True
    assert evaluator.is_valuable_enchant("one_for_all", 1) is True

    # Tier >= 6 always qualifies
    assert evaluator.is_valuable_enchant("sharpness", 6) is True
    assert evaluator.is_valuable_enchant("protection", 7) is True

    # Tier 5 special enchants
    assert evaluator.is_valuable_enchant("overload", 5) is True
    assert evaluator.is_valuable_enchant("mana_vampire", 5) is True

    # Tier 5 standard enchants do not qualify
    assert evaluator.is_valuable_enchant("sharpness", 5) is False
    assert evaluator.is_valuable_enchant("power", 5) is False
    assert evaluator.is_valuable_enchant("growth", 5) is False


def test_path_a_clean_listing_evaluated_when_not_upgraded(base_settings: Settings, mock_bazaar: BazaarCache):
    """Verify clean listings use Path A (2nd Lowest BIN comparison)."""
    evaluator = FlipEvaluator(base_settings, bazaar_cache=mock_bazaar)
    evaluator.set_price_cache({"SUPER_CLEAVER": [1_000_000, 3_000_000, 3_500_000]})

    auction = {
        "uuid": "clean-auction-uuid",
        "starting_bid": 1_000_000,
        "item_name": "Super Cleaver",
    }
    decoded = {
        "internal_id": "SUPER_CLEAVER",
        "item_name": "Super Cleaver",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is False
    assert flip.buy_price == 1_000_000
    assert flip.target_resell_price == 3_000_000
    # Net profit: (3,000,000 * 0.98) - 1,000,000 = 2,940,000 - 1,000,000 = 1,940,000
    assert flip.net_profit == 1_940_000
    assert flip.addon_breakdown == []


def test_path_b_upgraded_item_networth_engine(base_settings: Settings, mock_bazaar: BazaarCache):
    """Verify upgraded item evaluates via Bazaar networth with 70% retention baseline."""
    evaluator = FlipEvaluator(base_settings, bazaar_cache=mock_bazaar)
    # Active clean market baseline: 60M, 61M, 62M (50M-500M qualifies for 70% dynamic retention)
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [60_000_000, 61_000_000, 62_000_000]})

    # Upgraded listing listed at 65,000,000
    auction = {
        "uuid": "upgraded-auction-uuid",
        "starting_bid": 65_000_000,
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
        "dungeon_stars": 5,  # 2,250 Wither Essence * 4,000 = 9,000,000
        "rarity_upgrades": 1,  # Recomb = 10,000,000
        "modifier": "ancient",  # Precursor Gear = 7,000,000
        "enchantments": {
            "legion": 5,  # Legion 5 = 20,000,000
        },
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is True
    assert flip.base_lbin == 60_000_000
    assert flip.buy_price == 65_000_000
    assert flip.retention_rate == 0.70

    # Total addons: 9M (stars) + 10M (recomb) + 7M (ancient) + 20M (legion) = 46,000,000
    assert flip.total_addon_cost == 46_000_000
    # Intrinsic worth: 60M base + 46M addons = 106,000,000
    assert flip.intrinsic_worth == 106_000_000

    # Resell baseline: 60,000,000 + int(46,000,000 * 0.70) = 60,000,000 + 32,200,000 = 92,200,000
    assert flip.target_resell_price == 92_200_000

    # Recomputed by hand per Phase 1 fee model (92.2M in 10M-100M tier: 2% creation + 1% collection = 3%):
    # Total fee = 92,200,000 * 0.03 = 2,766,000 -> Net revenue = 89,434,000
    # Net profit: 89,434,000 - 65,000,000 = 24,434,000
    assert flip.net_profit == 24_434_000
    assert flip.margin_pct == pytest.approx(37.59, abs=0.1)

    # Verify addon breakdown items
    breakdown_types = [a["type"] for a in flip.addon_breakdown]
    assert "recomb" in breakdown_types
    assert "essence" in breakdown_types
    assert "reforge" in breakdown_types
    assert "enchant" in breakdown_types

    # Necron Chestplate is a progression staple
    assert flip.is_priority is True
    assert flip.staple_category == "WITHER_ARMOR"


def test_path_b_rejected_when_resell_baseline_below_buy_price(base_settings: Settings, mock_bazaar: BazaarCache):
    """Verify listing is rejected if buy price exceeds estimated resell value."""
    evaluator = FlipEvaluator(base_settings, bazaar_cache=mock_bazaar)
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [20_000_000, 21_000_000, 22_000_000]})

    auction = {
        "uuid": "overpriced-uuid",
        "starting_bid": 60_000_000,  # Higher than 52.2M resell baseline
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "ancient",
        "enchantments": {},
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is None


def test_path_b_anti_bait_ratio_ceiling(base_settings: Settings, mock_bazaar: BazaarCache):
    """Verify anti-bait ceiling rejects suspiciously low buy prices relative to resell baseline."""
    # Settings max target to buy ratio = 3.0
    evaluator = FlipEvaluator(base_settings, bazaar_cache=mock_bazaar)
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [20_000_000, 21_000_000, 22_000_000]})

    # Resell baseline ~ 52.2M. If buy price is 10M, ratio is 5.22 > 3.0 -> rejected
    auction = {
        "uuid": "bait-uuid",
        "starting_bid": 10_000_000,
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Ancient Necron's Chestplate ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "ancient",
        "enchantments": {"legion": 5},
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is None


def test_path_b_fallback_when_networth_disabled(base_settings: Settings, mock_bazaar: BazaarCache):
    """Verify fallback to Path A when ENABLE_NETWORTH_VALUATION is False."""
    settings = Settings(
        ENABLE_NETWORTH_VALUATION=False,
        MIN_SAMPLE_SIZE=3,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings, bazaar_cache=mock_bazaar)
    # Price cache has 3 listings: buy_price = 15M, target = 20M
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [15_000_000, 20_000_000, 22_000_000]})

    auction = {
        "uuid": "fallback-uuid",
        "starting_bid": 15_000_000,
        "item_name": "Necron's Chestplate ✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Necron's Chestplate ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "",
        "enchantments": {},
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is False  # Evaluated via Path A
    assert flip.target_resell_price == 20_000_000


def test_path_b_fallback_when_bazaar_cache_none(base_settings: Settings):
    """Verify fallback to Path A when bazaar_cache is None (backward compatibility)."""
    evaluator = FlipEvaluator(base_settings, bazaar_cache=None)
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [15_000_000, 20_000_000, 22_000_000]})

    auction = {
        "uuid": "no-bazaar-uuid",
        "starting_bid": 15_000_000,
        "item_name": "Necron's Chestplate ✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Necron's Chestplate ✪✪✪✪✪",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "",
        "enchantments": {},
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is False
    assert flip.target_resell_price == 20_000_000


def test_cheap_item_recomb_retention_zero(mock_bazaar: BazaarCache):
    """Verify that items with base_lbin < 5M do not retain value from Recombobulator 3000."""
    from src.networth_evaluator import calculate_item_networth

    decoded = {
        "internal_id": "CRYPT_DREADLORD_SWORD",
        "rarity_upgrades": 1,
    }
    # Base LBIN = 20,000 (well under 5M)
    addons, total_addon_cost, intrinsic, retention, resell = calculate_item_networth(
        base_lbin=20_000,
        decoded_data=decoded,
        bazaar_cache=mock_bazaar,
    )
    # Raw recomb cost is recorded in breakdown
    assert total_addon_cost == 10_000_000
    # But resell baseline must NOT include recomb value
    assert resell == 20_000


def test_budget_item_addon_cap(mock_bazaar: BazaarCache):
    """Verify that budget items (< 20M) have retained addon value capped at 2.0x base_lbin."""
    from src.networth_evaluator import calculate_item_networth

    decoded = {
        "internal_id": "BUDGET_SWORD",
        "enchantments": {"sharpness": 7},  # Sharpness 7 = 12,000,000 in mock_bazaar
    }
    # Base LBIN = 1,000,000 -> 2.0x cap = 2,000,000 max retained addons
    addons, total_addon_cost, intrinsic, retention, resell = calculate_item_networth(
        base_lbin=1_000_000,
        decoded_data=decoded,
        bazaar_cache=mock_bazaar,
    )
    assert total_addon_cost == 12_000_000
    # Normally 12M * 0.50 = 6M, but capped at 2.0x base (2M) -> resell = 1M + 2M = 3M
    assert resell == 3_000_000


def test_listing_age_gate_rejects_stale_auctions(base_settings: Settings):
    """Verify listings older than MAX_SNIPE_LISTING_AGE_SECONDS are rejected as stale."""
    base_settings.MAX_SNIPE_LISTING_AGE_SECONDS = 300
    evaluator = FlipEvaluator(base_settings)
    evaluator.set_price_cache({"SUPER_CLEAVER": [1_000_000, 3_000_000, 3_500_000]})

    now_ms = 1_000_000_000_000
    # Listed 600 seconds ago (exceeds 300s limit)
    stale_auction = {
        "uuid": "stale-auction-uuid",
        "starting_bid": 1_000_000,
        "item_name": "Super Cleaver",
        "start": now_ms - 600_000,
    }
    decoded = {
        "internal_id": "SUPER_CLEAVER",
        "item_name": "Super Cleaver",
    }

    decision = evaluator.evaluate_decision(stale_auction, decoded, now_ms=now_ms)
    assert decision.accepted is False
    assert any("listing_too_old" in r for r in decision.reasons)

    # Fresh listing listed 30 seconds ago passes
    fresh_auction = {
        "uuid": "fresh-auction-uuid",
        "starting_bid": 1_000_000,
        "item_name": "Super Cleaver",
        "start": now_ms - 30_000,
    }
    fresh_decision = evaluator.evaluate_decision(fresh_auction, decoded, now_ms=now_ms)
    assert fresh_decision.accepted is True
    assert fresh_decision.flip is not None
    assert fresh_decision.flip.features_json is not None


def test_crimson_isle_armor_stars_never_counted_as_master_stars(mock_bazaar: BazaarCache):
    """Verify Crimson Isle items with 6-10 stars are valued with Crimson Essence and NEVER master stars."""
    from src.networth_evaluator import calculate_item_addons

    # Mock Crimson Essence price
    mock_bazaar.set_mock_prices(
        {
            "ESSENCE_CRIMSON": 3_000,
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
            "FOURTH_MASTER_STAR": 40_000_000,
            "FIFTH_MASTER_STAR": 65_000_000,
        }
    )

    # 10-star Aurora Chestplate (even if raw dict accidentally had master_stars: 5)
    aurora_10 = {
        "internal_id": "AURORA_CHESTPLATE",
        "dungeon_stars": 10,
        "master_stars": 5,
        "rarity_upgrades": 0,
    }
    addons, total_addon_cost = calculate_item_addons(aurora_10, mock_bazaar)
    addon_types = [a["type"] for a in addons]

    assert "master_star" not in addon_types
    assert "essence" in addon_types
    essence_addon = next(a for a in addons if a["type"] == "essence")
    # 10 stars Crimson Essence = 18,450 essence * 3,000 coins = 55,350,000 coins
    assert essence_addon["cost"] == 18_450 * 3_000
    assert essence_addon["product_ids"] == ["ESSENCE_CRIMSON"]
    assert total_addon_cost == 55_350_000  # NOT 147M+ from master stars!

    # 8-star Crimson Boots
    crimson_8 = {
        "internal_id": "CRIMSON_BOOTS",
        "dungeon_stars": 8,
        "master_stars": 0,
        "rarity_upgrades": 0,
    }
    addons_8, total_8 = calculate_item_addons(crimson_8, mock_bazaar)
    addon_types_8 = [a["type"] for a in addons_8]

    assert "master_star" not in addon_types_8
    assert "essence" in addon_types_8
    essence_addon_8 = next(a for a in addons_8 if a["type"] == "essence")
    # 8 stars Crimson Essence = 8,950 essence * 3,000 = 26,850,000 coins
    assert essence_addon_8["cost"] == 8_950 * 3_000
    assert total_8 == 26_850_000

    # Contrast with Dungeon Item (Necron Chestplate 8 stars = 5 essence + 3 master stars)
    mock_bazaar.set_mock_prices(
        {
            "ESSENCE_WITHER": 4_000,
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
        }
    )
    necron_8 = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 8,
        "master_stars": 3,
        "rarity_upgrades": 0,
    }
    addons_necron, total_necron = calculate_item_addons(necron_8, mock_bazaar)
    addon_types_necron = [a["type"] for a in addons_necron]
    assert "master_star" in addon_types_necron
    assert "essence" in addon_types_necron
