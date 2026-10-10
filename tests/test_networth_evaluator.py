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
    cache.set_mock_prices({
        "RECOMBOBULATOR_3000": 10_000_000,
        "ESSENCE_WITHER": 4_000,
        "ESSENCE_UNDEAD": 1_000,
        "PRECURSOR_GEAR": 7_000_000,
        "DRAGON_CLAW": 5_000_000,
        "ENCHANTMENT_ULTIMATE_SOUL_EATER_5": 16_000_000,
        "ENCHANTMENT_ULTIMATE_LEGION_5": 20_000_000,
        "ENCHANTMENT_SHARPNESS_7": 12_000_000,
        "ENCHANTMENT_OVERLOAD_5": 8_000_000,
    })
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
    assert evaluator.is_upgraded_item({
        **clean_item,
        "enchantments": {"ultimate_soul_eater": 5},
    }) is True

    assert evaluator.is_upgraded_item({
        **clean_item,
        "enchantments": {"sharpness": 7},
    }) is True

    assert evaluator.is_upgraded_item({
        **clean_item,
        "enchantments": {"overload": 5},
    }) is True

    # Standard non-valuable enchants
    assert evaluator.is_upgraded_item({
        **clean_item,
        "enchantments": {"sharpness": 5, "protection": 5},
    }) is False


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

    # Net revenue after 2% tax: 92,200,000 * 0.98 = 90,356,000
    # Net profit: 90,356,000 - 65,000,000 = 25,356,000
    assert flip.net_profit == 25_356_000
    assert flip.margin_pct == pytest.approx(39.01, abs=0.1)

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
