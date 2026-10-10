"""
Comprehensive test suite for Pet Bucket Valuation Engine, Merged-State Floor,
Sold-Comps Gating, and Anti-Domination Guards.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any, Optional
import pytest

from src.config import Settings
from src.constants.pet_levels import (
    get_pet_bucket_key,
    get_pet_level_bracket,
    get_pet_max_level,
    is_pet_xp_in_window,
)
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator, FlipResult


@pytest.fixture
def test_db(tmp_path):
    """Provide an isolated SQLite database manager for sold comps."""
    db_file = tmp_path / "test_market.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    yield db
    db.close()


def make_pet_fixture(
    uuid: str,
    price: int,
    pet_type: str = "GOBLIN",
    tier: str = "LEGENDARY",
    level: int = 1,
    exp: float = 0.0,
    candy: int = 0,
    skin: Optional[str] = None,
    held_item: str = "",
    seller: str = "seller1",
    start_ms: Optional[float] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Helper to construct raw auction and decoded data dicts for testing."""
    effective_tier = tier
    if held_item == "PET_ITEM_TIER_BOOST":
        rarity_order = ["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"]
        if tier in rarity_order:
            idx = rarity_order.index(tier)
            if idx < len(rarity_order) - 1:
                effective_tier = rarity_order[idx + 1]

    auction = {
        "uuid": uuid,
        "starting_bid": price,
        "item_name": f"[Lvl {level}] {tier.title()} {pet_type.title()}",
        "auctioneer": seller,
        "start": start_ms if start_ms is not None else (time.time() * 1000.0),
        "bin": True,
    }
    decoded = {
        "internal_id": f"PET_{pet_type}_{tier}",
        "item_name": f"[Lvl {level}] {tier.title()} {pet_type.title()}",
        "is_pet": True,
        "pet_type": pet_type,
        "pet_tier": tier,
        "effective_tier": effective_tier,
        "pet_level": level,
        "pet_exp": exp,
        "pet_candy_used": candy,
        "pet_skin": skin,
        "pet_held_item": held_item,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }
    return auction, decoded


def record_test_sold_comps(
    db: DatabaseManager,
    prices: list[int],
    pet_type: str = "GOBLIN",
    tier: str = "LEGENDARY",
    level: int = 1,
    exp: float = 0.0,
    candy: int = 0,
    skin: Optional[str] = None,
    held_item: str = "",
    age_hours_list: Optional[list[float]] = None,
    uuid_prefix: str = "",
) -> None:
    """Populate database sales_history with comparable pet sales."""
    now_ms = int(time.time() * 1000)
    for i, p in enumerate(prices):
        age_h = age_hours_list[i] if age_hours_list and i < len(age_hours_list) else 2.0
        ts = int(now_ms - age_h * 3600.0 * 1000.0)
        _, decoded = make_pet_fixture(
            uuid=f"{uuid_prefix}comp-{i}",
            price=p,
            pet_type=pet_type,
            tier=tier,
            level=level,
            exp=exp,
            candy=candy,
            skin=skin,
            held_item=held_item,
        )
        effective_internal_id = f"PET_{pet_type}_{decoded['effective_tier']}"
        db.record_sale(
            uuid=f"{uuid_prefix}comp-sale-{tier}-{level}-{i}-{p}",
            internal_id=effective_internal_id,
            price=p,
            buyer_uuid=f"buyer-{i}",
            seller_uuid=f"seller-{i}",
            timestamp=ts,
            features_json=decoded,
            features_hash="hash",
            normalized_tier=0,
        )


# ============================================================================
# 1. Snapshot floor 8M / 2nd LBIN 9.2M; new batch of 4x Goblin L1 at 4.99M + 1 at 9.2M -> 0 alerts
# ============================================================================
def test_merged_state_floor_suppresses_supply_dump_and_floor_lag(test_db):
    """
    Verify merged-state floor computation:
    Batch contains 4x Level 1 Legendary Goblins at 4,999,999 and 1 at 9,200,000.
    All 4 floor listings tie with each other, and the 9.2M listing is far above floor.
    Result must be exactly 0 alerts.
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    # Prepare batch of 5 listings
    batch = [
        make_pet_fixture("goblin-1", 4_999_999, seller="sellerA"),
        make_pet_fixture("goblin-2", 4_999_999, seller="sellerB"),
        make_pet_fixture("goblin-3", 4_999_999, seller="sellerC"),
        make_pet_fixture("goblin-4", 4_999_999, seller="sellerD"),
        make_pet_fixture("goblin-5", 9_200_000, seller="sellerE"),
    ]

    # Merged-state: update cache with the entire newly fetched cycle
    evaluator.update_cache(batch)

    # Evaluate each listing
    alerts = []
    for auction, decoded in batch:
        flip = evaluator.evaluate_listing(auction, decoded)
        if flip:
            alerts.append(flip)

    assert len(alerts) == 0, f"Expected 0 alerts but got {len(alerts)}: {alerts}"


# ============================================================================
# 2. Shuffle batch order 20 times. Identical results.
# ============================================================================
def test_evaluation_order_independence_across_shuffles(test_db):
    """
    Verify that evaluation outcomes are 100% deterministic and invariant
    to the order in which listings are processed.
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    base_batch = [
        make_pet_fixture("goblin-1", 4_999_999, seller="sellerA"),
        make_pet_fixture("goblin-2", 4_999_999, seller="sellerB"),
        make_pet_fixture("goblin-3", 4_999_999, seller="sellerC"),
        make_pet_fixture("goblin-4", 4_999_999, seller="sellerD"),
        make_pet_fixture("goblin-5", 9_200_000, seller="sellerE"),
    ]

    for iteration in range(20):
        shuffled = list(base_batch)
        random.seed(iteration)
        random.shuffle(shuffled)

        evaluator.update_cache(shuffled)

        alerts = []
        for auction, decoded in shuffled:
            flip = evaluator.evaluate_listing(auction, decoded)
            if flip:
                alerts.append(flip)

        assert len(alerts) == 0, f"Iteration {iteration} produced {len(alerts)} alerts!"


# ============================================================================
# 3. True positive: L1 Goblin at 6.5M, next cheapest 8.7M, 6 sold comps at 8.5-8.8M
# ============================================================================
def test_true_positive_pet_flip_with_sold_comps_ceiling(test_db):
    """
    Verify valid snipe:
    Candidate L1 Legendary Goblin at 6.5M.
    Next cheapest active listing in bucket is 8.7M.
    6 confirmed sold comps in 48h at [8.5M, 8.55M, 8.6M, 8.65M, 8.7M, 8.8M].
    Expected: Exactly one alert, with target <= min(8.7M, comps P75).
    """
    # Record 6 sold comps
    record_test_sold_comps(
        test_db,
        prices=[8_500_000, 8_550_000, 8_600_000, 8_650_000, 8_700_000, 8_800_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )

    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    cand = make_pet_fixture("cand-6.5m", 6_500_000, seller="snipe_seller")
    comp_ah = make_pet_fixture("other-8.7m", 8_700_000, seller="other_seller")
    other_high = make_pet_fixture("other-9.5m", 9_500_000, seller="third_seller")

    evaluator.update_cache([cand, comp_ah, other_high])

    flip = evaluator.evaluate_listing(cand[0], cand[1])
    assert flip is not None
    assert flip.auction_uuid == "cand-6.5m"
    assert flip.buy_price == 6_500_000
    assert flip.target_resell_price <= 8_700_000
    assert flip.target_resell_price <= 8_800_000
    assert flip.reference_price == 8_700_000
    assert flip.comp_count == 6
    assert flip.is_pet is True
    assert flip.effective_rarity == "LEGENDARY"
    assert flip.net_profit > 1_000_000
    assert flip.margin_pct > 15.0


# ============================================================================
# 4. Same as 3 with 2 comps -> Rejected LOW_COMPS
# ============================================================================
def test_pet_flip_rejected_when_comps_below_threshold(test_db):
    """
    Same setup as Test 3, but only 2 sold comps exist in the 48h lookback window.
    Must be rejected with reason LOW_COMPS.
    """
    record_test_sold_comps(
        test_db,
        prices=[8_500_000, 8_700_000],  # only 2 comps (< 5 required)
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )

    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        PET_MIN_SOLD_COMPS_48H=5,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    cand = make_pet_fixture("cand-low-comps", 6_500_000, seller="snipe_seller")
    comp_ah = make_pet_fixture("other-8.7m", 8_700_000, seller="other_seller")

    evaluator.update_cache([cand, comp_ah])

    flip = evaluator.evaluate_listing(cand[0], cand[1])
    assert flip is None
    # Recomputed for Phase 2c: rejection reason updated from LOW_COMPS to pet_sales_gate
    assert evaluator.get_last_pet_rejection_reason("cand-low-comps") in ("pet_sales_gate", "LOW_COMPS")
    assert evaluator.get_last_pet_rejection_reason("cand-low-comps") == "pet_sales_gate"


# ============================================================================
# 5. L1 pet at 5M with only L85 listings at 20M and no L1 comps -> No alert
# ============================================================================
def test_low_level_pet_isolated_from_higher_xp_listings(test_db):
    """
    L1 Goblin at 5M with only L85 listings (bracket L81-99, high XP) at 20M.
    No L1 comps exist.
    Must NOT benchmark low-level pet against higher-level listings -> 0 alerts.
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    cand_l1 = make_pet_fixture("cand-l1", 5_000_000, level=1, exp=0.0)
    # L85 has ~10M+ XP, bracket L81-99
    l85_ah = make_pet_fixture("other-l85", 20_000_000, level=85, exp=12_000_000.0)

    evaluator.update_cache([cand_l1, l85_ah])

    flip = evaluator.evaluate_listing(cand_l1[0], cand_l1[1])
    assert flip is None
    # Recomputed for Phase 2d: zero comps in bucket rejects with pet_no_comps
    assert evaluator.get_last_pet_rejection_reason("cand-l1") in ("pet_no_comps", "BUCKET_NO_MATCH", "LOW_COMPS")
    assert evaluator.get_last_pet_rejection_reason("cand-l1") == "pet_no_comps"


# ============================================================================
# 6. Boundaries L20/21, L80/81, L99/100, plus 200-cap pet at L150 and L200
# ============================================================================
def test_pet_level_bracket_boundary_mappings():
    """
    Validate data-driven bracket boundaries:
    - Phase 2b: L1 is discrete, L2-20 vs L21-80 (20 vs 21)
    - L21-80 vs L81-99 (80 vs 81)
    - L81-99 vs L100 (99 vs 100)
    - Golden Dragon 200-cap: L150 (L101-199) and L200 (L200)
    """
    # Recomputed for Phase 2b: L1 is its own bracket, L2-20 covers level 2 to 20
    assert get_pet_level_bracket("GOBLIN", 1) == "L1"
    assert get_pet_level_bracket("GOBLIN", 20) == "L2-20"
    assert get_pet_level_bracket("GOBLIN", 21) == "L21-80"

    assert get_pet_level_bracket("GOBLIN", 80) == "L21-80"
    assert get_pet_level_bracket("GOBLIN", 81) == "L81-99"

    assert get_pet_level_bracket("GOBLIN", 99) == "L81-99"
    assert get_pet_level_bracket("GOBLIN", 100) == "L100"

    # 200-cap pet (Golden Dragon)
    assert get_pet_max_level("GOLDEN_DRAGON") == 200
    assert get_pet_level_bracket("GOLDEN_DRAGON", 1) == "L1"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 20) == "L2-20"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 80) == "L21-80"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 99) == "L81-99"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 100) == "L100"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 101) == "L101-199"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 150) == "L101-199"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 199) == "L101-199"
    assert get_pet_level_bracket("GOLDEN_DRAGON", 200) == "L200"


# ============================================================================
# 7. Candy, skin, held-item, and Tier Boost pets are never compared to plain pets
# ============================================================================
def test_candy_skin_held_item_and_tier_boost_bucket_isolation():
    """
    Verify discrete 5-tuple bucket separation:
    Plain, Candy, Skin, Held-Item, and Tier Boost pets must generate
    different bucket keys and never cross-benchmark against each other.
    """
    _, plain = make_pet_fixture("p1", 5_000_000, level=1)
    _, candy = make_pet_fixture("p2", 5_000_000, level=1, candy=5)
    _, skin = make_pet_fixture("p3", 5_000_000, level=1, skin="GHOUL")
    _, held = make_pet_fixture("p4", 5_000_000, level=1, held_item="PET_ITEM_MINOS_RELIC")
    _, tier_boost = make_pet_fixture(
        "p5", 5_000_000, tier="EPIC", level=1, held_item="PET_ITEM_TIER_BOOST"
    )

    k_plain = get_pet_bucket_key(plain)
    k_candy = get_pet_bucket_key(candy)
    k_skin = get_pet_bucket_key(skin)
    k_held = get_pet_bucket_key(held)
    k_tier_boost = get_pet_bucket_key(tier_boost)

    # All 5 bucket keys must be distinct
    all_keys = [k_plain, k_candy, k_skin, k_held, k_tier_boost]
    assert len(set(all_keys)) == 5

    # Tier boost must have promoted effective rarity to LEGENDARY
    assert tier_boost["effective_tier"] == "LEGENDARY"
    assert k_tier_boost[0] == "PET_GOBLIN_LEGENDARY"
    assert k_tier_boost[4] == "PET_ITEM_TIER_BOOST"

    # Candy pet has candy_used=True
    assert k_candy[2] is True
    assert k_plain[2] is False

    # Skin pet has skin identifier
    assert k_skin[3] == "GHOUL"
    assert k_plain[3] == "none"


# ============================================================================
# 8. Same-price tie, seller domination, and stale reference rejection codes
# ============================================================================
def test_rejection_reason_codes_for_tie_domination_and_ghost_reference(test_db):
    """
    Verify exact reason codes:
    - Same-price tie -> TIE_AT_FLOOR
    - One seller owning 40%+ of floor -> SELLER_DOMINATION
    - Stale reference listing (>12h old with no confirming comps) -> GHOST_REFERENCE
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        PET_MIN_SOLD_COMPS_48H=5,
        MAX_SELLER_DOMINATION_RATIO=0.40,
        MAX_BENCHMARK_AGE_HOURS=12.0,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    # 1. Same-price tie: candidate at 5M, another active listing at 5.02M (< 1% higher)
    cand_tie = make_pet_fixture("tie-cand", 5_000_000)
    other_tie = make_pet_fixture("tie-other", 5_020_000)
    evaluator.update_cache([cand_tie, other_tie])
    res_tie = evaluator.evaluate_listing(cand_tie[0], cand_tie[1])
    assert res_tie is None
    assert evaluator.get_last_pet_rejection_reason("tie-cand") == "TIE_AT_FLOOR"

    # 2. Seller domination: candidate at 5M, next cheapest at 8M, but 3 of 4 listings owned by same seller
    record_test_sold_comps(test_db, prices=[8_000_000] * 6, pet_type="GOBLIN", tier="LEGENDARY")
    cand_dom = make_pet_fixture("dom-cand", 5_000_000, seller="snipe_guy")
    l1 = make_pet_fixture("dom-1", 8_000_000, seller="cartel_boss")
    l2 = make_pet_fixture("dom-2", 8_500_000, seller="cartel_boss")
    l3 = make_pet_fixture("dom-3", 9_000_000, seller="cartel_boss")
    evaluator.update_cache([cand_dom, l1, l2, l3])
    res_dom = evaluator.evaluate_listing(cand_dom[0], cand_dom[1])
    assert res_dom is None
    assert evaluator.get_last_pet_rejection_reason("dom-cand") == "SELLER_DOMINATION"

    # 3. Ghost/stale reference: reference listing is 16h old (>12h) at 15M, but comps P75 is 8M
    old_start = (time.time() - 16 * 3600) * 1000.0  # 16 hours old
    cand_ghost = make_pet_fixture("ghost-cand", 5_000_000, seller="good_seller")
    stale_ref = make_pet_fixture("stale-ref", 15_000_000, seller="ghost_seller", start_ms=old_start)
    # Put stale_ref directly in bucket to test ghost guard
    b_key = get_pet_bucket_key(cand_ghost[1])
    evaluator.set_pet_bucket_cache({
        b_key: [
            {"uuid": "ghost-cand", "price": 5_000_000, "xp": 0.0, "start": time.time() * 1000.0},
            {"uuid": "stale-ref", "price": 15_000_000, "xp": 0.0, "start": old_start},
        ]
    })
    res_ghost = evaluator.evaluate_listing(cand_ghost[0], cand_ghost[1])
    assert res_ghost is None
    assert evaluator.get_last_pet_rejection_reason("ghost-cand") == "GHOST_REFERENCE"


# ============================================================================
# 9. Margin above the cap yields LOW confidence
# ============================================================================
def test_margin_above_review_cap_yields_low_confidence(test_db):
    """
    Verify that when net profit margin exceeds PET_MARGIN_REVIEW_PCT (default 40.0%),
    the opportunity's confidence label is capped at 'LOW' and logged for review.
    """
    # 6 sold comps at 10M
    record_test_sold_comps(
        test_db,
        prices=[10_000_000, 10_000_000, 10_100_000, 10_100_000, 10_200_000, 10_200_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
    )

    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        PET_MARGIN_REVIEW_PCT=40.0,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings, db=test_db)

    # Buy at 5M, target resell at 10M
    # Net revenue = 10M * 0.98 = 9.8M
    # Net profit = 4.8M -> margin = 96.0% (> 40.0% cap)
    cand = make_pet_fixture("margin-cand", 5_000_000, seller="sellerA")
    ref_ah = make_pet_fixture("ref-ah", 10_100_000, seller="sellerB")

    evaluator.update_cache([cand, ref_ah])

    flip = evaluator.evaluate_listing(cand[0], cand[1])
    assert flip is not None
    assert flip.margin_pct > 40.0
    assert flip.confidence_label == "LOW"
    assert evaluator.get_last_pet_rejection_reason("margin-cand") == "MARGIN_REVIEW"


# ============================================================================
# Phase 2e: Pytest Regressions
# ============================================================================

def test_regression_goblin_12_listings_no_comps(test_db):
    """
    Phase 2e (i):
    12 identical Lvl 1 PET_GOBLIN_LEGENDARY listings at 4,999,999,
    model target 9,200,000, no comps -> 0 alerts.
    """
    settings = Settings(ENABLE_PET_BUCKET_VALUATION=True)
    evaluator = FlipEvaluator(settings, db=test_db)

    batch = [
        make_pet_fixture(f"goblin-{i}", 4_999_999, seller=f"seller-{i}", pet_type="GOBLIN", tier="LEGENDARY", level=1)
        for i in range(12)
    ]
    evaluator.update_cache(batch)

    alerts = []
    for auction, decoded in batch:
        flip = evaluator.evaluate_listing(auction, decoded)
        if flip:
            alerts.append(flip)
    assert len(alerts) == 0


def test_regression_goblin_12_listings_tight_comps(test_db):
    """
    Phase 2e (ii):
    12 identical Lvl 1 PET_GOBLIN_LEGENDARY listings at 4,999,999,
    with tight comps around 5,000,000 -> 0 alerts.
    """
    record_test_sold_comps(
        test_db,
        prices=[4_950_000, 5_000_000, 5_000_000, 5_050_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )
    settings = Settings(ENABLE_PET_BUCKET_VALUATION=True)
    evaluator = FlipEvaluator(settings, db=test_db)

    batch = [
        make_pet_fixture(f"goblin-{i}", 4_999_999, seller=f"seller-{i}", pet_type="GOBLIN", tier="LEGENDARY", level=1)
        for i in range(12)
    ]
    evaluator.update_cache(batch)

    alerts = []
    for auction, decoded in batch:
        flip = evaluator.evaluate_listing(auction, decoded)
        if flip:
            alerts.append(flip)
    assert len(alerts) == 0


def test_regression_goblin_12_listings_plus_older_listing(test_db):
    """
    Phase 2e (iii):
    The 12 listings at 4,999,999 plus an older one at 9,200,000 -> 0 alerts.
    """
    record_test_sold_comps(
        test_db,
        prices=[4_950_000, 5_000_000, 5_000_000, 5_050_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )
    settings = Settings(ENABLE_PET_BUCKET_VALUATION=True)
    evaluator = FlipEvaluator(settings, db=test_db)

    old_start = (time.time() - 3600) * 1000.0
    batch = [
        make_pet_fixture(f"goblin-{i}", 4_999_999, seller=f"seller-{i}", pet_type="GOBLIN", tier="LEGENDARY", level=1)
        for i in range(12)
    ]
    batch.append(
        make_pet_fixture("goblin-old", 9_200_000, seller="seller-old", pet_type="GOBLIN", tier="LEGENDARY", level=1, start_ms=old_start)
    )
    evaluator.update_cache(batch)

    alerts = []
    for auction, decoded in batch:
        flip = evaluator.evaluate_listing(auction, decoded)
        if flip:
            alerts.append(flip)
    assert len(alerts) == 0


def test_regression_goblin_3m_listing_with_tight_comps(test_db):
    """
    Phase 2e (iv):
    One listing at 3,000,000 with tight comps around 5,000,000 and the gate met ->
    exactly 1 alert (profit stays above default thresholds with Derpy on or off).
    """
    record_test_sold_comps(
        test_db,
        prices=[4_950_000, 5_000_000, 5_000_000, 5_050_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )

    for derpy in (False, True):
        settings = Settings(
            ENABLE_PET_BUCKET_VALUATION=True,
            MIN_PROFIT_COINS=500_000,
            MIN_PROFIT_MARGIN_PCT=10.0,
            MAYOR_OVERRIDE="DERPY" if derpy else "NONE",
        )
        evaluator = FlipEvaluator(settings, db=test_db)
        evaluator.derpy_active = derpy

        cand = make_pet_fixture("cand-3m", 3_000_000, seller="sellerA", pet_type="GOBLIN", tier="LEGENDARY", level=1)
        other_ref = make_pet_fixture("other-5.2m", 5_200_000, seller="sellerB", pet_type="GOBLIN", tier="LEGENDARY", level=1)

        evaluator.update_cache([cand, other_ref])

        flip = evaluator.evaluate_listing(cand[0], cand[1])
        assert flip is not None, f"Failed to alert with derpy={derpy}"
        assert flip.auction_uuid == "cand-3m"
        assert flip.target_resell_price == 5_000_000  # min(5.2M, comps median 5.0M)
        assert flip.net_profit >= 500_000
        assert flip.margin_pct >= 10.0


def test_regression_lvl1_never_compared_with_lvl100(test_db):
    """
    Phase 2e (v):
    Lvl 1 is never compared with the Lvl 100 bucket.
    """
    # Record comps strictly for Lvl 100
    record_test_sold_comps(
        test_db,
        prices=[25_000_000, 26_000_000, 25_500_000, 26_500_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=100,
    )
    settings = Settings(ENABLE_PET_BUCKET_VALUATION=True)
    evaluator = FlipEvaluator(settings, db=test_db)

    cand_l1 = make_pet_fixture("cand-l1", 5_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    other_l100 = make_pet_fixture("other-l100", 25_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=100)

    evaluator.update_cache([cand_l1, other_l100])

    flip = evaluator.evaluate_listing(cand_l1[0], cand_l1[1])
    assert flip is None
    assert evaluator.get_last_pet_rejection_reason("cand-l1") == "pet_no_comps"


def test_regression_gate_varies_by_rarity(test_db):
    """
    Phase 2e (vi):
    The sales gate varies by rarity:
    EPIC (gate=4) with 3 comps -> rejected with pet_sales_gate.
    LEGENDARY (gate=3) with 3 comps -> passes sales gate.
    """
    # 3 comps for EPIC
    record_test_sold_comps(
        test_db,
        prices=[5_000_000, 5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="EPIC",
        level=1,
    )
    # 3 comps for LEGENDARY
    record_test_sold_comps(
        test_db,
        prices=[5_000_000, 5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )

    settings = Settings(ENABLE_PET_BUCKET_VALUATION=True)
    evaluator = FlipEvaluator(settings, db=test_db)

    cand_epic = make_pet_fixture("epic-cand", 3_000_000, pet_type="GOBLIN", tier="EPIC", level=1)
    ref_epic = make_pet_fixture("epic-ref", 5_000_000, pet_type="GOBLIN", tier="EPIC", level=1)
    evaluator.update_cache([cand_epic, ref_epic])

    flip_epic = evaluator.evaluate_listing(cand_epic[0], cand_epic[1])
    assert flip_epic is None
    assert evaluator.get_last_pet_rejection_reason("epic-cand") == "pet_sales_gate"

    cand_leg = make_pet_fixture("leg-cand", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    ref_leg = make_pet_fixture("leg-ref", 5_200_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand_leg, ref_leg])

    flip_leg = evaluator.evaluate_listing(cand_leg[0], cand_leg[1])
    assert flip_leg is not None
    assert flip_leg.auction_uuid == "leg-cand"
