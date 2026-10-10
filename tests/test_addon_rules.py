"""Tests for complete addon rules coverage and vanity sold-comps lane (Phase 7)."""

import os
import tempfile

from src.addon_rules import (
    calculate_drill_parts_cost,
    calculate_enrichment_cost,
    calculate_pet_candy_penalty,
    calculate_potato_book_cost,
    calculate_wither_scrolls_cost,
    is_permanently_blacklisted,
    is_vanity_item,
    load_addon_rules,
)
from src.bazaar import BazaarCache
from src.comps_evaluator import CompsEvaluator
from src.config import Settings
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator
from src.networth_evaluator import calculate_item_networth


def test_load_addon_rules():
    rules = load_addon_rules()
    assert "hot_potato_book" in rules
    assert "wither_scrolls" in rules
    assert "pet_candy_penalty" in rules
    assert "vanity_rules" in rules


def test_hot_potato_and_fuming_potato_book_cost():
    bazaar = BazaarCache()
    bazaar.set_mock_prices(
        {
            "HOT_POTATO_BOOK": 60_000,
            "FUMING_POTATO_BOOK": 1_200_000,
        }
    )

    # Test 10 HPB (no FPB)
    hpb, fpb, cost, items = calculate_potato_book_cost(10, bazaar)
    assert hpb == 10
    assert fpb == 0
    assert cost == 600_000
    assert len(items) == 1
    assert items[0]["type"] == "hot_potato_book"

    # Test 15 books (10 HPB + 5 FPB)
    hpb, fpb, cost, items = calculate_potato_book_cost(15, bazaar)
    assert hpb == 10
    assert fpb == 5
    # 10 * 60k + 5 * 1.2M = 600k + 6M = 6.6M
    assert cost == 6_600_000
    assert len(items) == 2


def test_wither_scrolls_cost():
    bazaar = BazaarCache()
    bazaar.set_mock_prices(
        {
            "WITHER_SHIELD_SCROLL": 150_000_000,
            "IMPLOSION_SCROLL": 120_000_000,
            "SHADOW_WARP_SCROLL": 110_000_000,
        }
    )

    scrolls = ["WITHER_SHIELD_SCROLL", "IMPLOSION_SCROLL", "SHADOW_WARP_SCROLL"]
    cost, items = calculate_wither_scrolls_cost(scrolls, bazaar)
    assert cost == 380_000_000
    assert len(items) == 3


def test_enrichment_and_drill_parts_cost():
    bazaar = BazaarCache()
    bazaar.set_mock_prices(
        {
            "TALISMAN_ENRICHMENT_MAGIC_FIND": 12_000_000,
            "TITANIUM_DRILL_ENGINE": 25_000_000,
            "GEMSTONE_FUEL_TANK": 40_000_000,
        }
    )

    # Enrichment
    enrich_cost, enrich_items = calculate_enrichment_cost("MAGIC_FIND", bazaar)
    assert enrich_cost == 12_000_000
    assert len(enrich_items) == 1

    # Drill parts
    drill_decoded = {
        "drill_part_engine": "TITANIUM_DRILL_ENGINE",
        "drill_part_fuel_tank": "GEMSTONE_FUEL_TANK",
    }
    drill_cost, drill_items = calculate_drill_parts_cost(drill_decoded, bazaar)
    assert drill_cost == 65_000_000
    assert len(drill_items) == 2


def test_networth_integration_with_phase_7_addons():
    bazaar = BazaarCache()
    bazaar.set_mock_prices(
        {
            "HOT_POTATO_BOOK": 60_000,
            "FUMING_POTATO_BOOK": 1_000_000,
            "WITHER_SHIELD_SCROLL": 100_000_000,
        }
    )

    decoded = {
        "internal_id": "NECRON_BLADE",
        "hot_potato_count": 15,  # 10 HPB (600k) + 5 FPB (5M) = 5.6M
        "ability_scrolls": ["WITHER_SHIELD_SCROLL"],  # 100M
    }

    addons, total_addon_cost, intrinsic, retention, resell = calculate_item_networth(
        base_lbin=500_000_000,
        decoded_data=decoded,
        bazaar_cache=bazaar,
    )

    # Total addons: 5.6M + 100M = 105.6M
    assert total_addon_cost == 105_600_000
    assert intrinsic == 500_000_000 + 105_600_000
    # Tier for 500M base is 85% retention
    assert retention == 0.85
    assert resell == 500_000_000 + int(round(105_600_000 * 0.85))


def test_pet_candy_penalty_calculation():
    # 0 candy -> 0.0
    assert calculate_pet_candy_penalty(0) == 0.0

    # 5 candies -> 5 * 0.015 = 0.075 (7.5%)
    assert calculate_pet_candy_penalty(5) == 0.075

    # 10 candies -> 10 * 0.015 = 0.15 (15%)
    assert calculate_pet_candy_penalty(10) == 0.15

    # 12 candies (clamped to 10) -> 0.15
    assert calculate_pet_candy_penalty(12) == 0.15


def test_pet_candy_penalty_reduces_valuation_in_evaluator():
    config = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        PET_CANDY_PENALTY_PER_CANDY=0.015,
        ENABLE_PET_BUCKET_VALUATION=False,
    )
    bazaar = BazaarCache(config=config)
    evaluator = FlipEvaluator(config=config, bazaar_cache=bazaar)
    evaluator.set_price_cache({"PET_ENDER_DRAGON_LEGENDARY": [400_000_000, 450_000_000]})

    auction = {"uuid": "pet-candy-auc", "starting_bid": 350_000_000}

    # Case 1: Level 100 Pet with 0 candies (Premium = 80M)
    # Worth = 400M (base) + 80M (lvl 100) = 480M
    decoded_clean = {
        "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
        "pet_type": "ENDER_DRAGON",
        "pet_tier": "LEGENDARY",
        "pet_level": 100,
        "pet_candy_used": 0,
        "is_pet": True,
    }
    flip_clean = evaluator.evaluate_listing(auction, decoded_clean)
    assert flip_clean is not None
    assert flip_clean.target_resell_price == 480_000_000

    # Case 2: Same Pet with 10 candies (15% penalty applied)
    # Worth = 480M * (1 - 0.15) = 408M
    decoded_candied = {
        "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
        "pet_type": "ENDER_DRAGON",
        "pet_tier": "LEGENDARY",
        "pet_level": 100,
        "pet_candy_used": 10,
        "is_pet": True,
    }
    flip_candied = evaluator.evaluate_listing(auction, decoded_candied)
    assert flip_candied is not None
    assert flip_candied.target_resell_price == 408_000_000
    assert flip_candied.pet_candy_used == 10


def test_vanity_lane_routing():
    # 1. Permanent blacklist checks
    assert is_permanently_blacklisted("DYE_CARMINE") is True
    assert is_permanently_blacklisted("PORTAL_SCROLL_DEEP_CAVERNS") is True
    assert is_permanently_blacklisted("FURNITURE_CHAIR") is True
    assert is_permanently_blacklisted("MUSIC_DISC_13") is True
    assert is_permanently_blacklisted("PET_SKIN_SHEEP_NEON") is False
    assert is_permanently_blacklisted("COUTURE_RUNE") is False

    # 2. Vanity item detection
    assert is_vanity_item("PET_SKIN_SHEEP_NEON") is True
    assert is_vanity_item("COUTURE_RUNE") is True
    assert is_vanity_item("HYPERION") is False

    # 3. Default config: Vanity items are blacklisted
    default_config = Settings(DRY_RUN=True, ENABLE_VANITY_COMPS_LANE=False)
    evaluator_default = FlipEvaluator(config=default_config)
    assert evaluator_default.is_blacklisted("PET_SKIN_SHEEP_NEON") is True
    assert evaluator_default.is_blacklisted("COUTURE_RUNE") is True

    # 4. Vanity lane enabled:
    vanity_config = Settings(
        DRY_RUN=True,
        ENABLE_VANITY_COMPS_LANE=True,
        ENABLE_SOLD_COMPS=True,
        MIN_COMPS_THRESHOLD=3,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db_path = f.name
    db = DatabaseManager(tmp_db_path)
    try:
        comps = CompsEvaluator(db=db, config=vanity_config)
        evaluator_vanity = FlipEvaluator(config=vanity_config, comps_evaluator=comps)

        # Without sales in comps: still blacklisted
        assert evaluator_vanity.is_blacklisted("PET_SKIN_SHEEP_NEON") is True

        # Add 3 sales in comps evaluator for PET_SKIN_SHEEP_NEON
        now_ms = 1_000_000_000
        for i in range(3):
            db.record_sale(
                uuid=f"sale-{i}",
                internal_id="PET_SKIN_SHEEP_NEON",
                price=50_000_000,
                buyer_uuid="buyer-1",
                seller_uuid="seller-1",
                timestamp=now_ms,
                features_hash="hash-skin",
            )

        # Now with >= 3 sales, vanity skin is NOT blacklisted!
        assert evaluator_vanity.is_blacklisted("PET_SKIN_SHEEP_NEON") is False

        # But permanent blacklist items remain blocked even with vanity lane enabled
        assert evaluator_vanity.is_blacklisted("DYE_CARMINE") is True
        assert evaluator_vanity.is_blacklisted("PORTAL_SCROLL_HUB") is True
    finally:
        db.close()
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)
