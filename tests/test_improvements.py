"""Unit tests for flipper improvements: DB logging aliases, clean cache integrity, stacked items, craft recipes, and confidence scoring."""

import pytest
from src.config import Settings
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator, FlipResult
from src.nbt_decoder import decode_item_bytes
from src.ranking import compute_confidence_score
from src.craft_evaluator import calculate_craft_cost


def test_db_log_alert_keyword_aliases(tmp_path):
    """Verify log_alert handles target_resell_price and net_profit keyword arguments seamlessly."""
    db_file = tmp_path / "test_intel.db"
    db = DatabaseManager(str(db_file))

    success = db.log_alert(
        auction_uuid="uuid-alias-test-1",
        internal_id="FLORID_ZOMBIE_SWORD",
        buy_price=1_560_000,
        target_resell_price=3_000_000,
        net_profit=1_380_000,
        margin_pct=88.5,
        confidence_label="MEDIUM",
        valuation_path="PATH_A",
    )
    assert success is True

    alert = db.get_alert("uuid-alias-test-1")
    assert alert is not None
    assert alert["buy_price"] == 1_560_000
    assert alert["estimated_resell"] == 3_000_000
    assert alert["projected_profit"] == 1_380_000
    assert alert["margin_pct"] == 88.5


def test_clean_cache_excludes_gemstones_and_valuable_enchants():
    """Verify items with gemstones or valuable enchants never contaminate clean_price_cache."""
    cfg = Settings(DRY_RUN=True)
    evaluator = FlipEvaluator(cfg)
    now_ms = 1_000_000_000_000

    # 1. Truly clean item
    clean_item = (
        {"starting_bid": 1_500_000, "start": now_ms, "end": now_ms + 3600000},
        {"internal_id": "TEST_SWORD", "count": 1},
    )
    # 2. Item with applied gemstones
    gem_item = (
        {"starting_bid": 5_000_000, "start": now_ms, "end": now_ms + 3600000},
        {
            "internal_id": "TEST_SWORD",
            "count": 1,
            "gemstones": [{"type": "JASPER", "quality": "PERFECT"}],
        },
    )
    # 3. Item with valuable enchant (e.g. Chimera or Soul Eater)
    enchant_item = (
        {"starting_bid": 10_000_000, "start": now_ms, "end": now_ms + 3600000},
        {
            "internal_id": "TEST_SWORD",
            "count": 1,
            "enchantments": {"chimera": 1},
        },
    )

    evaluator.update_cache([clean_item, gem_item, enchant_item], now_ms=now_ms)

    assert evaluator.clean_price_cache.get("TEST_SWORD") == [1_500_000]
    assert evaluator.price_cache.get("TEST_SWORD") == [1_500_000, 5_000_000, 10_000_000]


def test_stacked_items_price_normalization():
    """Verify that multi-count stacked items (count > 1) normalize unit price in cache."""
    cfg = Settings(DRY_RUN=True)
    evaluator = FlipEvaluator(cfg)
    now_ms = 1_000_000_000_000

    # Stack of 16 Recombobulators for 160M -> unit price = 10M
    stack_item = (
        {"starting_bid": 160_000_000, "start": now_ms, "end": now_ms + 3600000},
        {"internal_id": "RECOMBOBULATOR_3000", "count": 16},
    )
    # Single Recombobulator for 10.5M
    single_item = (
        {"starting_bid": 10_500_000, "start": now_ms, "end": now_ms + 3600000},
        {"internal_id": "RECOMBOBULATOR_3000", "count": 1},
    )

    evaluator.update_cache([stack_item, single_item], now_ms=now_ms)

    # Both should be around 10M, not 160M
    assert evaluator.price_cache.get("RECOMBOBULATOR_3000") == [10_000_000, 10_500_000]


def test_florid_zombie_sword_craft_ceiling():
    """Verify Florid Zombie Sword craft cost is calculated from Golden Teeth Bazaar price."""
    fake_bazaar = {
        "GOLDEN_TOOTH": 60_000,
        "ENCHANTED_IRON": 1_500,
    }
    # 29 Golden Teeth * 60,000 + 2 Enchanted Iron * 1,500 = 1,740,000 + 3,000 = 1,743,000
    craft_cost = calculate_craft_cost("FLORID_ZOMBIE_SWORD", fake_bazaar)
    assert craft_cost == 1_743_000


def test_confidence_scoring_extreme_margin_path_a_deduction():
    """Verify that unverified extreme margins (>60%) on PATH_A receive deductions instead of inflating to HIGH."""
    flip = FlipResult(
        auction_uuid="test-uuid",
        internal_id="FLORID_ZOMBIE_SWORD",
        item_name="Florid Zombie Sword",
        buy_price=1_560_000,
        target_resell_price=3_000_000,
        net_profit=1_380_000,
        margin_pct=88.5,
        daily_velocity=25.0,
        valuation_path="PATH_A",
        is_priority=False,
    )
    score, label = compute_confidence_score(flip)
    # Path A: 30 pts, Liq: 30 pts, Margin: 30 pts, Deduction for extreme margin: -15 pts
    # Total = 75 pts -> reduced from 90 pts
    assert score == 75.0


def test_hybrid_craft_cost_resolution():
    """Verify calculate_craft_cost resolves hybrid recipes combining AH base items with Bazaar fragments."""
    fake_bazaar = {
        "THORN_FRAGMENT": 6_250,
    }
    fake_ah = {
        "BAT_WAND": 10_000_000,
    }
    # 1x BAT_WAND (10,000,000 from AH) + 8x THORN_FRAGMENT (8 * 6,250 = 50,000 from Bazaar) = 10,050,000
    cost = calculate_craft_cost("STARRED_BAT_WAND", fake_bazaar, ah_cache=fake_ah)
    assert cost == 10_050_000


def test_fragged_spirit_sceptre_above_upgrade_cost_rejected():
    """
    Verify the exact user scenario:
    - Base Spirit Sceptre (BAT_WAND) lowest price = 10,000,000
    - Upgrade cost (8x Thorn Fragments @ 6.25k) = 50,000
    - Seller lists Heroic Spirit Sceptre (STARRED_BAT_WAND) for 10,500,000
    - Another listing sits at 15,200,000
    Evaluator MUST reject this listing as a flip because 10.5M is above the upgrade/craft cost (~10.05M).
    """
    cfg = Settings(DRY_RUN=True, MIN_SAMPLE_SIZE=2, PATH_A_MIN_LISTINGS=2)
    fake_bazaar = {
        "THORN_FRAGMENT": 6_250,
    }
    evaluator = FlipEvaluator(cfg, bazaar_cache=fake_bazaar)

    # Base item has listings around 10M
    evaluator.set_price_cache({
        "BAT_WAND": [10_000_000, 10_200_000, 10_500_000],
        # Another overpriced fragged sceptre sits at 15.2M
        "STARRED_BAT_WAND": [10_500_000, 15_200_000],
    })

    auction = {
        "uuid": "01c129b582da4a46bcabd863dcf4",
        "starting_bid": 10_500_000,
        "item_name": "Heroic Spirit Sceptre",
    }
    decoded = {
        "internal_id": "STARRED_BAT_WAND",
        "item_name": "Heroic Spirit Sceptre",
        "modifier": "heroic",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "enchantments": {"bane_of_arthropods": 5, "cleave": 5},
    }

    result = evaluator.evaluate_listing(auction, decoded)
    # Must be completely rejected!
    assert result is None


def test_fragged_spirit_sceptre_genuine_snipe_clamped_resell():
    """
    Verify that if a fragged sceptre is listed well below craft cost (e.g. 7.5M),
    its target resell price is clamped to the craft/upgrade ceiling (~10.5M), NOT inflated to 15.2M.
    """
    cfg = Settings(DRY_RUN=True, MIN_SAMPLE_SIZE=2, PATH_A_MIN_LISTINGS=2, MIN_PROFIT_COINS=500_000, MIN_PROFIT_MARGIN_PCT=10.0)
    fake_bazaar = {
        "THORN_FRAGMENT": 6_250,
    }
    evaluator = FlipEvaluator(cfg, bazaar_cache=fake_bazaar)

    evaluator.set_price_cache({
        "BAT_WAND": [10_000_000, 10_200_000],
        "STARRED_BAT_WAND": [7_500_000, 15_200_000],
    })

    auction = {
        "uuid": "snipe-uuid",
        "starting_bid": 7_500_000,
        "item_name": "Heroic Spirit Sceptre",
    }
    decoded = {
        "internal_id": "STARRED_BAT_WAND",
        "item_name": "Heroic Spirit Sceptre",
        "modifier": "heroic",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "enchantments": {},
    }

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.buy_price == 7_500_000
    # Resell price MUST NOT be 15,200,000; it must be clamped to craft ceiling (~10.55M)
    assert result.target_resell_price <= 10_600_000
    assert result.target_resell_price > 10_000_000


def test_craftable_accessory_recipe_coverage():
    """Verify that all 181 craftable accessories are registered in RECIPES."""
    from src.constants.recipes import get_recipe, RECIPES
    from src.constants.accessory_recipes import ACCESSORY_RECIPES

    assert len(ACCESSORY_RECIPES) == 181
    for acc_id in ACCESSORY_RECIPES:
        rec = get_recipe(acc_id)
        assert rec is not None, f"Missing recipe for accessory {acc_id}"
        assert len(rec) > 0


def test_craftable_accessory_craft_cost_and_overpriced_rejection():
    """
    Verify that an accessory (e.g. CROPIE_TALISMAN crafted from 256 CROPIE)
    calculates craft cost correctly and rejects overpriced listings.
    CROPIE Bazaar buy price = 5,000 -> craft cost = 256 * 5,000 = 1,280,000.
    If listed on AH for 2,500,000 with next listing at 4,000,000,
    evaluator MUST reject it because buy_price >= 0.95 * craft_cost.
    """
    cfg = Settings(DRY_RUN=True, MIN_SAMPLE_SIZE=2, PATH_A_MIN_LISTINGS=2)
    fake_bazaar = {
        "CROPIE": 5_000,
    }
    evaluator = FlipEvaluator(cfg, bazaar_cache=fake_bazaar)

    craft_cost = calculate_craft_cost("CROPIE_TALISMAN", fake_bazaar)
    assert craft_cost == 1_280_000

    # Put an overpriced listing on AH
    evaluator.set_price_cache({
        "CROPIE_TALISMAN": [2_500_000, 4_000_000],
    })

    auction = {
        "uuid": "cropie-talisman-test",
        "starting_bid": 2_500_000,
        "item_name": "Cropie Talisman",
    }
    decoded = {
        "internal_id": "CROPIE_TALISMAN",
        "item_name": "Cropie Talisman",
        "modifier": "",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "enchantments": {},
    }

    # Must be rejected because 2.5M > 1.28M craft cost!
    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


