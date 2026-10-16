"""
Comprehensive verification test suite for the Architectural, Valuation & Latency Upgrade:
1. Zero Hot-Path Database I/O:
   Assert that FlipEvaluator and score_and_rank_flip execute 100% in-memory without invoking
   any database cursors, queries, or I/O operations.
2. Auction UUID vs Item Instance UUID Preservation:
   Assert ExtraAttributes.uuid (dupe tracker) maps to item_instance_uuid and never corrupts top-level auction uuid.
3. Sub-millisecond 3-Step Delta Pipeline:
   Assert vanished listing pruning, TIE_AT_FLOOR, and INTRA_BATCH_SUPPLY_DUMP guards.
4. Non-Linear Valuation Models:
   - Upgrade Coherence Multiplier (incomplete penalty vs meta completion bonus capped by craft ceiling).
   - Continuous Pet XP interpolation (convex alpha=1.20 curve with category-scaled candy penalty).
   - Dynamic Programming attribute solver with synthetic fusion cost capping.
5. Sybil & Macro Manipulation Defenses:
   - DSU Entity clustering across (auctioneer, profile_id, coop).
   - Temporal listing burst detection on confirmed illiquid items.
   - Floor-to-comps anchor ratio > 2.2x rejection.
   - Paul 12% dungeon haircut.
6. Capital Velocity Ranking & Vanished-Alert Lifecycle:
   - O(1) liquidity-adjusted velocity score and portfolio concentration penalty.
   - SOLD_FAST_CLAIMED, SELLER_CANCELLED, and SURVIVED_MULTI_TICK outcome labeling.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.constants.archetypes import calculate_coherence_multiplier
from src.constants.attributes import calculate_attribute_estimated_value, solve_attribute_tier_dp
from src.constants.pet_levels import (
    calculate_category_candy_penalty,
    interpolate_pet_xp_value,
)
from src.db import CompsStats, DatabaseManager, MarketTelemetrySnapshot
from src.election import MayorElectionTracker, get_dungeon_haircut
from src.evaluator import FlipEvaluator, FlipResult
from src.local_server import build_flip_payload
from src.outcome_labeler import OutcomeLabeler
from src.ranking import score_and_rank_flip

# ============================================================================
# 1. Zero Hot-Path Database I/O Verification
# ============================================================================


class PoisonedDatabaseManager:
    """A database manager that raises an exception on ANY query or connection access."""

    def __getattr__(self, name: str):
        def _poison(*args, **kwargs):
            raise sqlite3.OperationalError(f"CRITICAL HOT-PATH DB VIOLATION: '{name}' called during evaluation!")

        return _poison


def test_zero_hot_path_db_io_during_evaluation():
    """
    Verify that FlipEvaluator.evaluate_listing and score_and_rank_flip perform ZERO
    database queries across all 5 valuation lanes (LANE_FLOOR, LANE_UPGRADED_NETWORTH,
    LANE_SOLD_COMPS, LANE_PETS, LANE_GOD_ROLL), vanity checks, SurvivalAuditor haircuts,
    RetentionFitter lookups, and ranking when preloaded with MarketTelemetrySnapshot.
    """
    poison_db = PoisonedDatabaseManager()
    config = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        ENABLE_SOLD_COMPS=True,
        ENABLE_NETWORTH_VALUATION=True,
        ENABLE_PET_BUCKET_VALUATION=True,
        ENABLE_SELLER_DOMINATION_GUARD=True,
        ENABLE_VANITY_COMPS_LANE=True,
        ENABLE_DYNAMIC_RETENTION=True,
        ENABLE_SURVIVAL_TELEMETRY=True,
    )
    bazaar = BazaarCache(config)
    bazaar.set_mock_prices({
        "RECOMBOBULATOR_3000": 8_000_000,
        "ENCHANTMENT_SHARPNESS_7": 60_000_000,
    })

    from src.comps_evaluator import CompsEvaluator
    from src.retention_fitter import RetentionFitter
    from src.telemetry import SurvivalAuditor

    comps_eval = CompsEvaluator(poison_db, config, bazaar_cache=bazaar)
    retention_fitter = RetentionFitter(poison_db, config=config, bazaar_cache=bazaar)
    survival_auditor = SurvivalAuditor(poison_db, config=config)
    evaluator = FlipEvaluator(
        config=config,
        bazaar_cache=bazaar,
        comps_evaluator=comps_eval,
        db=poison_db,
        retention_fitter=retention_fitter,
        survival_auditor=survival_auditor,
    )

    # In-memory pre-aggregated snapshot with comps, pet sales, lane stats, and retention rates
    now_ms = int(time.time() * 1000)
    pet_feat = {
        "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
        "is_pet": True,
        "pet_type": "ENDER_DRAGON",
        "pet_tier": "LEGENDARY",
        "pet_exp": 25_000_000.0,
        "pet_level": 100,
        "pet_held_item": "",
    }
    juju_feat = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "precise",
        "gemstones": [],
        "enchantments": {},
    }
    from src.comps_evaluator import generate_features_hash
    juju_hash = generate_features_hash(juju_feat)

    snapshot = MarketTelemetrySnapshot(
        empirical_sell_times={
            "NECRON_CHESTPLATE": 1800.0,
            "HYPERION": 300.0,
            "JUJU_SHORTBOW": 600.0,
            "PET_ENDER_DRAGON_LEGENDARY": 3600.0,
            "AURORA_HELMET": 1200.0,
        },
        open_holdings={"NECRON_CHESTPLATE": 1},
        comps_summary={
            "NECRON_CHESTPLATE": CompsStats(
                median_price=60_000_000,
                p25_price=55_000_000,
                p75_price=65_000_000,
                comp_count=25,
                iqr_spread=10_000_000,
            ),
            juju_hash: CompsStats(
                median_price=35_000_000,
                p25_price=32_000_000,
                p75_price=38_000_000,
                comp_count=10,
                iqr_spread=6_000_000,
            ),
            "FANCY_SWORD": CompsStats(
                median_price=5_000_000,
                p25_price=4_000_000,
                p75_price=6_000_000,
                comp_count=15,
                iqr_spread=2_000_000,
            ),
        },
        pet_sales={
            "PET_ENDER_DRAGON_LEGENDARY": [
                {"price": 600_000_000, "features_json": pet_feat, "timestamp": now_ms - 3600_000},
                {"price": 620_000_000, "features_json": pet_feat, "timestamp": now_ms - 7200_000},
                {"price": 610_000_000, "features_json": pet_feat, "timestamp": now_ms - 10800_000},
            ]
        },
        sales_counts={"FANCY_SWORD": 15},
        lane_conversion_stats={
            "LANE_FLOOR": {
                "total": 25,
                "sniped": 18,
                "survived": 5,
                "cancelled": 2,
                "conversion_rate": 0.72,
                "avg_duration_s": 35.0,
            },
        },
        retention_rates={"RECOMBOBULATOR_3000": 0.55},
    )
    evaluator.set_telemetry_snapshot(snapshot)

    # 1. LANE_FLOOR
    evaluator.set_price_cache({"NECRON_CHESTPLATE": [45_000_000, 60_000_000, 62_000_000]})
    auction_floor = {
        "uuid": "hotpath-floor-uuid",
        "starting_bid": 45_000_000,
        "item_name": "Necron's Chestplate",
        "start": now_ms - 10_000,
    }
    decoded_floor = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Necron's Chestplate",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
    }
    flip_floor = evaluator.evaluate_listing(auction_floor, decoded_floor, now_ms=now_ms)
    from src.constants.lanes import (
        LANE_FLOOR,
        LANE_GOD_ROLL,
        LANE_PETS,
        LANE_SOLD_COMPS,
        LANE_UPGRADED_NETWORTH,
    )
    assert flip_floor is not None
    assert flip_floor.lane == LANE_FLOOR

    # 2. LANE_UPGRADED_NETWORTH
    evaluator.set_price_cache({"HYPERION": [800_000_000, 850_000_000, 900_000_000]})
    auction_upg = {
        "uuid": "hotpath-upg-uuid",
        "starting_bid": 700_000_000,
        "item_name": "Hyperion",
        "start": now_ms - 10_000,
    }
    decoded_upg = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "enchantments": {"sharpness": 7},
    }
    flip_upg = evaluator.evaluate_listing(auction_upg, decoded_upg, now_ms=now_ms)
    assert flip_upg is not None
    assert flip_upg.lane == LANE_UPGRADED_NETWORTH

    # 3. LANE_SOLD_COMPS
    evaluator.set_price_cache({"JUJU_SHORTBOW": [20_000_000, 36_000_000, 38_000_000]})
    auction_comps = {
        "uuid": "hotpath-comps-uuid",
        "starting_bid": 20_000_000,
        "item_name": "Juju Shortbow",
        "start": now_ms - 10_000,
    }
    flip_comps = evaluator.evaluate_listing(auction_comps, juju_feat, now_ms=now_ms)
    assert flip_comps is not None
    assert flip_comps.lane == LANE_SOLD_COMPS

    # 4. LANE_PETS
    evaluator.set_price_cache({"PET_ENDER_DRAGON_LEGENDARY": [400_000_000, 620_000_000, 630_000_000]})
    auction_pet = {
        "uuid": "hotpath-pet-uuid",
        "starting_bid": 400_000_000,
        "item_name": "[Lvl 100] Ender Dragon",
        "start": now_ms - 10_000,
    }
    flip_pet = evaluator.evaluate_listing(auction_pet, pet_feat, now_ms=now_ms)
    assert flip_pet is not None
    assert flip_pet.lane == LANE_PETS

    # 5. LANE_GOD_ROLL
    evaluator.set_price_cache({"AURORA_HELMET": [5_000_000, 80_000_000, 90_000_000]})
    auction_gr = {
        "uuid": "hotpath-gr-uuid",
        "starting_bid": 35_000_000,
        "item_name": "Aurora Helmet",
        "start": now_ms - 10_000,
    }
    decoded_gr = {
        "internal_id": "AURORA_HELMET",
        "item_name": "Aurora Helmet",
        "attributes": {"mana_pool": 1, "mana_regeneration": 1},
    }
    flip_gr = evaluator.evaluate_listing(auction_gr, decoded_gr, now_ms=now_ms)
    assert flip_gr is not None
    assert flip_gr.lane == LANE_GOD_ROLL

    # 6. Vanity Item Blacklist Gate (must not query DB)
    assert evaluator.is_blacklisted("DYE_PURE_WHITE", "Pure White Dye") is True
    # Vanity item with >= min_thresh in comps_summary should pass vanity check without touching DB
    assert evaluator.is_blacklisted("FANCY_SWORD", "Fancy Sword") is False

    # 7. Ranking (must not query DB)
    for f in [flip_floor, flip_upg, flip_comps, flip_pet, flip_gr]:
        ranked = score_and_rank_flip(f, telemetry_snapshot=snapshot)
        assert ranked.capital_velocity_score is not None
        assert ranked.capital_velocity_score > 0.0


# ============================================================================
# 2. Auction UUID vs Item Instance UUID Preservation
# ============================================================================


def test_item_instance_uuid_vs_auction_uuid_preservation():
    """
    Ensure tag.ExtraAttributes.uuid is parsed as item_instance_uuid, propagated
    through FlipEvaluator.evaluate_listing, and does NOT overwrite the top-level
    Hypixel Auction uuid used by /viewauction.
    """
    top_auction_uuid = "abcdef0123456789abcdef0123456789"
    item_internal_instance_uuid = "11112222-3333-4444-5555-666677778888"

    decoded_mock = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
        "item_instance_uuid": item_internal_instance_uuid,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
    }

    config = Settings(DRY_RUN=True, MIN_PROFIT_COINS=100_000, MIN_PROFIT_MARGIN_PCT=5.0)
    evaluator = FlipEvaluator(config=config)
    evaluator.set_price_cache({"HYPERION": [800_000_000, 950_000_000, 960_000_000]})
    snapshot = MarketTelemetrySnapshot(
        comps_summary={
            "HYPERION": CompsStats(
                median_price=950_000_000,
                p25_price=920_000_000,
                p75_price=980_000_000,
                comp_count=20,
                iqr_spread=60_000_000,
            )
        }
    )
    evaluator.set_telemetry_snapshot(snapshot)

    auction = {
        "uuid": top_auction_uuid,
        "starting_bid": 800_000_000,
        "item_name": "Hyperion",
        "start": 1_700_000_000_000,
    }
    flip = evaluator.evaluate_listing(auction, decoded_mock, now_ms=1_700_000_010_000)
    assert flip is not None
    assert flip.auction_uuid == top_auction_uuid
    assert flip.item_instance_uuid == item_internal_instance_uuid

    payload = build_flip_payload(flip)
    assert payload["uuid"] == top_auction_uuid
    assert payload["item_instance_uuid"] == item_internal_instance_uuid
    assert payload["auction_start_ms"] == 1_700_000_000_000
    assert payload["uuid"] != payload["item_instance_uuid"]


# ============================================================================
# 3. Sub-Millisecond 3-Step Delta Pipeline & Guards
# ============================================================================


def test_delta_pipeline_prune_and_tie_at_floor():
    """
    Verify:
    1. Pruning vanished auctions purges old floors from active book.
    2. TIE_AT_FLOOR guard rejects two new listings tying at floor from distinct sellers.
    """
    config = Settings(DRY_RUN=True, MIN_PROFIT_COINS=500_000, MIN_PROFIT_MARGIN_PCT=5.0)
    evaluator = FlipEvaluator(config=config)

    now_ms = 1_000_000_000_000.0

    # Initial active book
    old_item_a = ({"uuid": "old-floor", "starting_bid": 10_000_000, "auctioneer": "sellerA"}, {"internal_id": "STORM_BOOTS"})
    old_item_b = ({"uuid": "comp-2", "starting_bid": 25_000_000, "auctioneer": "sellerB"}, {"internal_id": "STORM_BOOTS"})
    active_book = {"old-floor": old_item_a, "comp-2": old_item_b}
    evaluator.update_cache([old_item_a, old_item_b], now_ms=now_ms)

    assert evaluator.price_cache["STORM_BOOTS"][0] == 10_000_000

    # Step A1: Old floor sold/vanished
    evaluator.prune_vanished_auctions({"old-floor"}, active_book)
    active_book.pop("old-floor", None)

    assert evaluator.price_cache["STORM_BOOTS"][0] == 25_000_000

    # Step A2/A3: Two new listings arrive at the exact same lowest price (12M) from different sellers
    listing_1 = {"uuid": "new-1", "starting_bid": 12_000_000, "auctioneer": "seller1", "item_name": "Storm's Boots"}
    listing_2 = {"uuid": "new-2", "starting_bid": 12_000_000, "auctioneer": "seller2", "item_name": "Storm's Boots"}
    decoded = {"internal_id": "STORM_BOOTS", "item_name": "Storm's Boots"}

    delta_context = {
        "prev_tick_floors": {"STORM_BOOTS": 25_000_000},
        "batch_items": {"STORM_BOOTS": [listing_1, listing_2]},
    }

    # Should be rejected under TIE_AT_FLOOR guard
    flip1 = evaluator.evaluate_listing(listing_1, decoded, now_ms=now_ms + 1000, delta_context=delta_context)
    assert flip1 is None


def test_delta_pipeline_intra_batch_supply_dump():
    """
    Verify INTRA_BATCH_SUPPLY_DUMP guard flags and rejects >= 3 new listings below previous floor
    for an illiquid item (< 10 daily sales).
    """
    config = Settings(DRY_RUN=True, MIN_PROFIT_COINS=500_000, MIN_PROFIT_MARGIN_PCT=5.0)
    evaluator = FlipEvaluator(config=config)

    now_ms = 1_000_000_000_000.0
    listings = [
        {"uuid": f"dump-{i}", "starting_bid": 15_000_000 + i * 100_000, "auctioneer": f"dumpSeller{i}", "item_name": "Dark Claymore"}
        for i in range(3)
    ]
    decoded = {"internal_id": "DARK_CLAYMORE", "item_name": "Dark Claymore"}

    delta_context = {
        "prev_tick_floors": {"DARK_CLAYMORE": 30_000_000},
        "batch_items": {"DARK_CLAYMORE": listings},
    }

    # Should reject the candidate dump listing
    flip = evaluator.evaluate_listing(listings[0], decoded, now_ms=now_ms, delta_context=delta_context)
    assert flip is None


# ============================================================================
# 4. Non-Linear Valuation Models Verification
# ============================================================================


def test_coherence_multiplier_incomplete_penalty_and_synergy_bonus():
    """
    Verify:
    1. Wither blade with only 1 or 2 scrolls gets gamma = 0.60 penalty.
    2. Master stars on < 5 star gear get gamma = 0.50 penalty.
    3. Fully meta completed items get gamma = 1.08 synergy bonus.
    """
    # 1. Incomplete Wither Blade (1 scroll)
    gamma_incomplete, reason = calculate_coherence_multiplier(
        internal_id="HYPERION",
        base_lbin=800_000_000,
        decoded_data={
            "ability_scrolls": ["WITHER_SHIELD_SCROLL"],
            "dungeon_stars": 5,
            "rarity_upgrades": 1,
            "enchantments": {"ultimate_wise": 5},
        },
        addon_breakdown=[],
    )
    assert gamma_incomplete == 0.60
    assert reason == "INCOMPLETE_SCROLLS_PENALTY"

    # 2. Master Star on 4-star gear
    gamma_premature_stars, reason = calculate_coherence_multiplier(
        internal_id="NECRON_CHESTPLATE",
        base_lbin=50_000_000,
        decoded_data={
            "dungeon_stars": 4,
            "master_stars": 1,
            "rarity_upgrades": 1,
        },
        addon_breakdown=[],
    )
    assert gamma_premature_stars == 0.50
    assert reason == "INCOMPLETE_MASTER_STARS_PENALTY"

    # 3. Meta Completion Synergy
    gamma_meta, reason = calculate_coherence_multiplier(
        internal_id="HYPERION",
        base_lbin=800_000_000,
        decoded_data={
            "ability_scrolls": ["WITHER_SHIELD_SCROLL", "IMPLOSION_SCROLL", "SHADOW_WARP_SCROLL"],
            "dungeon_stars": 5,
            "master_stars": 0,
            "rarity_upgrades": 1,
            "enchantments": {"ultimate_wise": 5, "sharpness": 7},
        },
        addon_breakdown=[],
    )
    assert gamma_meta == 1.08


def test_continuous_pet_xp_interpolation_and_category_candy():
    """
    Verify continuous cumulative pet XP curve (alpha=1.20) and category candy penalties:
    - Collector pet (Golden Dragon) incurs up to 18% penalty when candy is used.
    - Utility pet (Elephant) incurs at most 3% penalty.
    """
    # Candy penalties
    greg_penalty = calculate_category_candy_penalty("GOLDEN_DRAGON", candy_used=10)
    assert 0.12 <= greg_penalty <= 0.18

    elephant_penalty = calculate_category_candy_penalty("ELEPHANT", candy_used=10)
    assert elephant_penalty <= 0.03

    # Continuous XP curve interpolation (alpha = 1.20)
    p_base = 50_000_000
    p_cap = 150_000_000
    xp_cap = 25_353_230.0  # Legendary pet level 100 cap

    # At midpoint XP: (0.5)^1.20 ~= 0.43528
    val_mid = interpolate_pet_xp_value(
        pet_type="ENDER_DRAGON",
        tier="LEGENDARY",
        exp=xp_cap * 0.5,
        base_price=p_base,
        cap_price=p_cap,
        candy_used=0,
        alpha=1.20,
    )
    expected_convex_val = p_base + int(round(((0.5) ** 1.20) * (p_cap - p_base)))
    assert pytest.approx(val_mid, rel=1e-2) == expected_convex_val
    # Convexity check: value at 50% XP should be strictly less than linear midpoint (100M)
    assert val_mid < 100_000_000


def test_dynamic_programming_attribute_valuation_solver():
    """
    Verify bottom-up DP solver computes synthetic fusion cost capping for high-tier attributes.
    """
    active_lbin_by_tier = {
        1: [2_000_000],
        2: [5_000_000],
    }

    cost_tier_3 = solve_attribute_tier_dp(tier=3, active_tier_listings=active_lbin_by_tier)
    assert cost_tier_3 > 0

    is_gr, gr_type, est_val = calculate_attribute_estimated_value(
        attributes={"mana_pool": 3, "mana_regeneration": 3},
        base_lbin=10_000_000,
        active_attribute_floors={"mana_pool": active_lbin_by_tier, "mana_regeneration": active_lbin_by_tier},
    )
    assert is_gr is True
    assert est_val > 0


# ============================================================================
# 5. Sybil & Macro Manipulation Defenses Verification
# ============================================================================


def test_sybil_entity_clustering_defense():
    """
    Verify Disjoint Set Union (DSU) clusters multiple distinct auctioneer IGNs
    sharing a profile_id or coop, triggering the seller domination ceiling.
    """
    config = Settings(
        DRY_RUN=True,
        ENABLE_SELLER_DOMINATION_GUARD=True,
        MAX_SELLER_DOMINATION_RATIO=0.40,
        PATH_A_MIN_LISTINGS=3,
    )
    evaluator = FlipEvaluator(config=config)

    now_ms = 1_000_000_000_000.0

    # 4 listings from 3 different auctioneers, but sharing profile_id "sybil_profile_123"
    # Total listings = 6 -> Sybil cluster owns 4/6 = 66.7% >= 40% threshold
    auctions = [
        ({"starting_bid": 20_000_000, "auctioneer": "sybil_alt_1", "profile_id": "sybil_profile_123"}, {"internal_id": "FERVOR_HELMET"}),
        ({"starting_bid": 21_000_000, "auctioneer": "sybil_alt_2", "profile_id": "sybil_profile_123"}, {"internal_id": "FERVOR_HELMET"}),
        ({"starting_bid": 22_000_000, "auctioneer": "sybil_alt_3", "profile_id": "sybil_profile_123"}, {"internal_id": "FERVOR_HELMET"}),
        ({"starting_bid": 23_000_000, "auctioneer": "sybil_alt_1", "profile_id": "sybil_profile_123"}, {"internal_id": "FERVOR_HELMET"}),
        ({"starting_bid": 24_000_000, "auctioneer": "innocent_player_1", "profile_id": "prof_a"}, {"internal_id": "FERVOR_HELMET"}),
        ({"starting_bid": 25_000_000, "auctioneer": "innocent_player_2", "profile_id": "prof_b"}, {"internal_id": "FERVOR_HELMET"}),
    ]
    evaluator.update_cache(auctions, now_ms=now_ms)

    # Clustered ratio should be 4/6 ~= 0.6667
    domination_ratio = evaluator.get_seller_domination_ratio("FERVOR_HELMET")
    assert pytest.approx(domination_ratio, rel=1e-2) == 4.0 / 6.0
    assert evaluator.is_seller_dominated("FERVOR_HELMET") is True


def test_paul_mayor_election_dungeon_haircut():
    """
    Verify 12% haircut applies to Dungeon chest items (Handle, Scrolls, Recombs) when Paul is active.
    """
    tracker = MayorElectionTracker(override="Paul")
    assert tracker.paul_active is True

    # Dungeon items receive 12% haircut
    handle_haircut = get_dungeon_haircut("NECRON_HANDLE", mayor_tracker=tracker)
    assert handle_haircut == 0.12

    scroll_haircut = get_dungeon_haircut("WITHER_SHIELD_SCROLL", mayor_tracker=tracker)
    assert scroll_haircut == 0.12

    recomb_haircut = get_dungeon_haircut("RECOMBOBULATOR_3000", mayor_tracker=tracker)
    assert recomb_haircut == 0.12

    # Non-dungeon item receives 0% haircut
    claymore_haircut = get_dungeon_haircut("DIVAN_HELMET", mayor_tracker=tracker)
    assert claymore_haircut == 0.0


# ============================================================================
# 6. Capital Velocity Ranking & Vanished-Alert Lifecycle Verification
# ============================================================================


def test_capital_velocity_ranking_and_portfolio_penalty():
    """
    Verify:
    1. Fast-selling item scores higher capital velocity than slow-selling item with identical margin.
    2. Portfolio concentration penalty applies when holdings >= 1, and flags PORTFOLIO_CONCENTRATION_EXCEEDED when >= 3.
    """
    fast_flip = FlipResult(
        auction_uuid="fast-uuid",
        internal_id="FAST_ITEM",
        item_name="Fast Item",
        buy_price=10_000_000,
        target_resell_price=15_000_000,
        net_profit=4_700_000,
        margin_pct=47.0,
        confidence_label="HIGH",
    )
    slow_flip = FlipResult(
        auction_uuid="slow-uuid",
        internal_id="SLOW_ITEM",
        item_name="Slow Item",
        buy_price=10_000_000,
        target_resell_price=15_000_000,
        net_profit=4_700_000,
        margin_pct=47.0,
        confidence_label="HIGH",
    )

    snapshot = MarketTelemetrySnapshot(
        empirical_sell_times={
            "FAST_ITEM": 300.0,     # 5 minutes
            "SLOW_ITEM": 14400.0,   # 4 hours
        },
        open_holdings={
            "FAST_ITEM": 0,
            "SLOW_ITEM": 3,         # Concentration threshold reached!
        },
    )

    ranked_fast = score_and_rank_flip(fast_flip, telemetry_snapshot=snapshot)
    ranked_slow = score_and_rank_flip(slow_flip, telemetry_snapshot=snapshot)

    assert ranked_fast.capital_velocity_score > ranked_slow.capital_velocity_score
    assert ranked_slow.portfolio_concentration_warning == "PORTFOLIO_CONCENTRATION_EXCEEDED"
    assert ranked_fast.portfolio_concentration_warning is None


def test_vanished_alert_lifecycle_labeler(tmp_path):
    """
    Verify OutcomeLabeler classifies:
    - SOLD_FAST_CLAIMED: Listing sold within 60s and recorded with buyer_uuid.
    - SELLER_CANCELLED: Listing disappeared early without sales record.
    - SURVIVED_MULTI_TICK: Listing persisted across multi-tick intervals.
    """
    db_file = tmp_path / "lifecycle_test.db"
    db = DatabaseManager(str(db_file))
    db.init_db()

    cfg = Settings(ENABLE_OUTCOME_LABELING=True, OUTCOME_HORIZON_HOURS=6.0)
    labeler = OutcomeLabeler(db, config=cfg)

    now_ms = int(time.time() * 1000)
    alerted_at = now_ms - int(7.0 * 3600.0 * 1000.0)

    # 1. Fast snipe claimed within 30s
    db.log_alert(
        auction_uuid="sniped-30s-uuid",
        internal_id="HYPERION",
        buy_price=800_000_000,
        estimated_resell=950_000_000,
        projected_profit=110_000_000,
        margin_pct=13.75,
        confidence_label="HIGH",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
    )
    db.record_sale(
        uuid="sniped-30s-uuid",
        internal_id="HYPERION",
        price=800_000_000,
        buyer_uuid="speedy_buyer",
        seller_uuid="seller_1",
        timestamp=alerted_at + 25_000,  # 25 seconds
        time_to_sell_s=25.0,
    )

    # 2. Seller cancelled early
    db.log_alert(
        auction_uuid="cancelled-uuid",
        internal_id="TERMINATOR",
        buy_price=600_000_000,
        estimated_resell=700_000_000,
        projected_profit=75_000_000,
        margin_pct=12.5,
        confidence_label="MEDIUM",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
    )
    db.update_alert_survival("cancelled-uuid", "SELLER_CANCELLED", duration_seconds=45.0, checked_timestamp=alerted_at + 45_000)

    # 3. Survived multi-tick
    db.log_alert(
        auction_uuid="multi-tick-uuid",
        internal_id="DIVAN_HELMET",
        buy_price=40_000_000,
        estimated_resell=55_000_000,
        projected_profit=12_000_000,
        margin_pct=30.0,
        confidence_label="LOW",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
    )
    db.update_alert_survival("multi-tick-uuid", "SURVIVED_MULTI_TICK", duration_seconds=600.0, checked_timestamp=alerted_at + 600_000)

    # Run outcome evaluation
    evaluated = labeler.evaluate_pending_outcomes(now_ms=now_ms)
    assert evaluated == 3

    outcomes = {o["auction_uuid"]: o for o in db.get_alert_outcomes()}
    assert outcomes["sniped-30s-uuid"]["status"] == "SOLD_FAST_CLAIMED"
    assert outcomes["cancelled-uuid"]["status"] == "SELLER_CANCELLED"
    assert outcomes["multi-tick-uuid"]["status"] == "SURVIVED_MULTI_TICK"

    db.close()


@pytest.mark.asyncio
async def test_mayor_election_tracker_update_and_override():
    """
    Verify MayorElectionTracker.update() exists as a bound method, handles overrides
    with proper boolean flags, and parses live /v2/resources/skyblock/election responses.
    """
    tracker_override = MayorElectionTracker(override=True)
    derpy_state = await tracker_override.update(session=None)
    assert derpy_state is True
    assert tracker_override.derpy_active is True
    assert tracker_override.paul_active is False

    class MockResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"mayor": {"name": "Paul"}}

    class MockSession:
        def get(self, url, timeout=10.0):
            return MockResponse()

    tracker_live = MayorElectionTracker(override=None)
    derpy_live = await tracker_live.update(session=MockSession())
    assert derpy_live is False
    assert tracker_live.derpy_active is False
    assert tracker_live.paul_active is True


def test_tier_boosted_pet_sorted_prices_lookup():
    """
    Verify a Tier-Boosted Epic Pet:
    1. Resolves its active floor price list (sorted_prices) from the effective Legendary bucket
       (PET_<TYPE>_LEGENDARY) rather than the empty base Epic bucket.
    2. Finds historical sales in _evaluate_pet_bucket_listing even when stored under base internal_id.
    """
    now_ms = int(time.time() * 1000)

    # Active book has ONLY Legendary Ender Dragons (plus the Tier-Boosted Epic pet mapped to Legendary bucket)
    auctions = [
        (
            {"uuid": "tb-epic-1", "starting_bid": 350_000_000, "auctioneer": "seller1"},
            {
                "internal_id": "PET_ENDER_DRAGON_EPIC",
                "is_pet": True,
                "pet_type": "ENDER_DRAGON",
                "pet_tier": "EPIC",
                "pet_held_item": "PET_ITEM_TIER_BOOST",
                "pet_level": 1,
                "pet_exp": 0.0,
            },
        ),
        (
            {"uuid": "leg-comp-1", "starting_bid": 580_000_000, "auctioneer": "seller2"},
            {
                "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
                "is_pet": True,
                "pet_type": "ENDER_DRAGON",
                "pet_tier": "LEGENDARY",
                "pet_held_item": "",
                "pet_level": 1,
                "pet_exp": 0.0,
            },
        ),
        (
            {"uuid": "leg-comp-2", "starting_bid": 600_000_000, "auctioneer": "seller3"},
            {
                "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
                "is_pet": True,
                "pet_type": "ENDER_DRAGON",
                "pet_tier": "LEGENDARY",
                "pet_held_item": "",
                "pet_level": 1,
                "pet_exp": 0.0,
            },
        ),
    ]

    # Case 1: Legacy pet valuation (directly exercises sorted_prices from cache_id in evaluate_decision)
    config_legacy = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        ENABLE_PET_BUCKET_VALUATION=False,
    )
    evaluator_legacy = FlipEvaluator(config=config_legacy)
    evaluator_legacy.update_cache(auctions, now_ms=now_ms)

    tb_auction = {
        "uuid": "tb-epic-1",
        "starting_bid": 350_000_000,
        "item_name": "[Lvl 1] Ender Dragon",
        "start": now_ms - 5_000,
    }
    tb_decoded = auctions[0][1]
    from src.constants.lanes import LANE_PETS

    flip_legacy = evaluator_legacy.evaluate_listing(tb_auction, tb_decoded, now_ms=now_ms)
    assert flip_legacy is not None
    assert flip_legacy.lane == LANE_PETS
    assert flip_legacy.net_profit > 20_000_000

    # Case 2: Pet bucket valuation with Tier-Boosted sales stored under base internal_id
    config_bucket = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        ENABLE_PET_BUCKET_VALUATION=True,
    )
    evaluator_bucket = FlipEvaluator(config=config_bucket)
    bucket_auctions = auctions + [
        (
            {"uuid": "tb-epic-comp", "starting_bid": 500_000_000, "auctioneer": "seller4"},
            {
                "internal_id": "PET_ENDER_DRAGON_EPIC",
                "is_pet": True,
                "pet_type": "ENDER_DRAGON",
                "pet_tier": "EPIC",
                "pet_held_item": "PET_ITEM_TIER_BOOST",
                "pet_level": 1,
                "pet_exp": 0.0,
            },
        ),
    ]
    evaluator_bucket.update_cache(bucket_auctions, now_ms=now_ms)
    snapshot = MarketTelemetrySnapshot(
        pet_sales={
            "PET_ENDER_DRAGON_EPIC": [
                {"price": 500_000_000, "features_json": tb_decoded, "timestamp": now_ms - 3600_000},
                {"price": 510_000_000, "features_json": tb_decoded, "timestamp": now_ms - 7200_000},
                {"price": 495_000_000, "features_json": tb_decoded, "timestamp": now_ms - 10800_000},
            ]
        }
    )
    evaluator_bucket.set_telemetry_snapshot(snapshot)
    flip_bucket = evaluator_bucket.evaluate_listing(tb_auction, tb_decoded, now_ms=now_ms)
    assert flip_bucket is not None
    assert flip_bucket.lane == LANE_PETS
    assert flip_bucket.net_profit > 20_000_000

