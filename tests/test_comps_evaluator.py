"""Unit and integration tests for Phase 3: Comparable-Sales (Sold-Comps) Engine."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.comps_evaluator import (
    CompsEvaluator,
    CompsResult,
    calculate_iqr_bounds,
    calculate_weighted_median,
)
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator
from src.sales_ingestor import (
    SalesIngestor,
    extract_high_tier_enchants,
    generate_features_hash,
    generate_relaxed_features_hash,
)


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database manager for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = DatabaseManager(db_path)
    yield db
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def dummy_bazaar_cache():
    """Mock bazaar cache returning predictable prices."""
    cache = BazaarCache(Settings(DRY_RUN=True))
    cache._buy_prices = {
        "RECOMBOBULATOR_3000": 8_000_000.0,
        "RECOMB_FRAGMENT": 1_000_000.0,
        "ENCHANTED_EMERALD": 2_000.0,
        "WITHER_ESSENCE": 4_000.0,
        "ESSENCE_UNDEAD": 4_000.0,
        "HOT_POTATO_BOOK": 150_000.0,
        "FUMING_POTATO_BOOK": 1_200_000.0,
        "ENCHANTMENT_ULTIMATE_WISE_1": 500_000.0,
    }
    cache._last_updated = time.time()
    return cache


# ---------------------------------------------------------------------------
# 1. Feature Hashing & Enchant Extraction Tests
# ---------------------------------------------------------------------------

def test_extract_high_tier_enchants():
    enchants = {
        "protection": 5,
        "growth": 6,
        "ultimate_wise": 5,
        "sharpness": 5,
        "soul_eater": 3,
        "overload": 5,
    }
    extracted = extract_high_tier_enchants(enchants)
    names = [name for name, lvl in extracted]
    assert "growth" in names
    assert "ultimate_wise" in names
    assert "soul_eater" in names
    assert "overload" in names
    assert "protection" not in names
    assert "sharpness" not in names


def test_generate_features_hash_determinism():
    item1 = {
        "internal_id": "HYPERION",
        "dungeon_stars": 5,
        "master_stars": 0,
        "rarity_upgrades": 1,
        "modifier": "heroic",
        "gemstones": [{"type": "SAPPHIRE", "quality": "FLAWLESS"}],
        "enchantments": {"ultimate_wise": 5, "smite": 7},
        "attributes": {},
        "prestige_tier": None,
        "gemstone_chambers": 0,
    }
    item2 = dict(item1)

    hash1 = generate_features_hash(item1)
    hash2 = generate_features_hash(item2)
    assert hash1 == hash2
    assert len(hash1) == 32


def test_generate_features_hash_variance():
    base = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 5,
        "master_stars": 0,
        "rarity_upgrades": 1,
        "modifier": "ancient",
        "gemstones": [],
        "enchantments": {},
    }
    h_base = generate_features_hash(base)

    # Different stars
    mod_stars = dict(base, dungeon_stars=4)
    assert generate_features_hash(mod_stars) != h_base

    # Different recomb
    mod_recomb = dict(base, rarity_upgrades=0)
    assert generate_features_hash(mod_recomb) != h_base

    # Different gemstones
    mod_gems = dict(base, gemstones=[{"type": "JASPER", "quality": "PERFECT"}])
    assert generate_features_hash(mod_gems) != h_base

    # Different reforge
    mod_reforge = dict(base, modifier="loving")
    assert generate_features_hash(mod_reforge) != h_base


def test_generate_relaxed_features_hash():
    item = {
        "internal_id": "TERMINATOR",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "prestige_tier": "",
    }
    rel_hash = generate_relaxed_features_hash(item)
    assert isinstance(rel_hash, str)
    assert len(rel_hash) == 32


# ---------------------------------------------------------------------------
# 2. IQR Bounds & Weighted Median Mathematical Tests
# ---------------------------------------------------------------------------

def test_calculate_iqr_bounds_small_sample():
    prices = [10_000_000, 11_000_000, 12_000_000]
    lower, upper, spread = calculate_iqr_bounds(prices)
    assert lower == 0.0
    assert upper == float("inf")
    assert spread == 2_000_000


def test_calculate_iqr_bounds_outlier_detection():
    # 8 normal prices around 10M, 1 severe outlier at 100M
    prices = [9_500_000, 9_800_000, 10_000_000, 10_100_000, 10_200_000, 10_500_000, 10_800_000, 100_000_000]
    lower, upper, iqr = calculate_iqr_bounds(prices)
    assert lower < 9_500_000
    assert upper < 50_000_000
    assert 100_000_000 > upper  # 100M is identified as an outlier!


def test_calculate_weighted_median():
    assert calculate_weighted_median([]) == 0
    assert calculate_weighted_median([(5_000_000, 1.0)]) == 5_000_000

    # Equal weights -> standard median
    items = [(10_000_000, 1.0), (20_000_000, 1.0), (30_000_000, 1.0)]
    assert calculate_weighted_median(items) == 20_000_000

    # Skewed weights: the 30M price has overwhelmingly high weight
    skewed = [(10_000_000, 0.1), (20_000_000, 0.1), (30_000_000, 0.9)]
    assert calculate_weighted_median(skewed) == 30_000_000


# ---------------------------------------------------------------------------
# 3. Sales Ingestion Engine Tests
# ---------------------------------------------------------------------------

def test_sales_ingestor_recording(temp_db):
    settings = Settings(DRY_RUN=True)
    ingestor = SalesIngestor(temp_db, settings)

    raw_auctions = [
        {
            "uuid": "sale-1",
            "buyer": "buyer-uuid-1",
            "seller": "seller-uuid-1",
            "bin": True,
            "starting_bid": 15_000_000,
            "timestamp": int(time.time() * 1000),
            "decoded": {
                "internal_id": "JUJU_SHORTBOW",
                "dungeon_stars": 5,
                "rarity_upgrades": 1,
            },
        },
        {
            # Not a BIN sale (auction bid) -> ignored
            "uuid": "sale-2",
            "buyer": "buyer-uuid-2",
            "seller": "seller-uuid-2",
            "bin": False,
            "starting_bid": 10_000_000,
        },
        {
            # No buyer (expired unsold) -> ignored
            "uuid": "sale-3",
            "buyer": None,
            "seller": "seller-uuid-3",
            "bin": True,
            "starting_bid": 12_000_000,
        },
    ]

    count = ingestor.ingest_ended_auctions(raw_auctions)
    assert count == 1

    sales = temp_db.get_sales(internal_id="JUJU_SHORTBOW")
    assert len(sales) == 1
    assert sales[0]["uuid"] == "sale-1"
    assert sales[0]["price"] == 15_000_000


# ---------------------------------------------------------------------------
# 4. Comps Evaluator Tier Hierarchy & Divergence Tests
# ---------------------------------------------------------------------------

def test_comps_evaluator_tier_1_strict_match(temp_db):
    settings = Settings(DRY_RUN=True, MIN_COMPS_THRESHOLD=3, COMPS_WINDOW_HOURS=72.0)
    evaluator = CompsEvaluator(temp_db, settings)

    now_ms = int(time.time() * 1000)
    item_features = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 5,
        "master_stars": 0,
        "rarity_upgrades": 1,
        "modifier": "ancient",
        "gemstones": [],
        "enchantments": {},
    }
    feat_hash = generate_features_hash(item_features)

    # Insert 4 historical sales matching exact feature hash
    prices = [40_000_000, 41_000_000, 42_000_000, 39_000_000]
    for i, p in enumerate(prices):
        temp_db.record_sale(
            uuid=f"tier1-sale-{i}",
            internal_id="NECRON_CHESTPLATE",
            price=p,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms - (i * 3600_000),  # spaced by 1 hour
            features_json=item_features,
            features_hash=feat_hash,
            normalized_tier=5,
        )

    res = evaluator.estimate_comps_value(item_features, now_ms=now_ms)
    assert res is not None
    assert res.tier == "TIER_1"
    assert res.comp_count == 4
    assert 39_000_000 <= res.median_price <= 42_000_000
    assert not res.is_divergent
    assert res.confidence_score >= 0.70


def test_comps_evaluator_tier_2_relaxed_match(temp_db):
    settings = Settings(DRY_RUN=True, MIN_COMPS_THRESHOLD=3, COMPS_WINDOW_HOURS=72.0)
    evaluator = CompsEvaluator(temp_db, settings)

    now_ms = int(time.time() * 1000)
    target_item = {
        "internal_id": "LIVID_DAGGER",
        "dungeon_stars": 5,
        "master_stars": 0,
        "rarity_upgrades": 1,
        "modifier": "fabled",
        "gemstones": [],
        "enchantments": {},
    }
    # Only 1 exact match (insufficient for Tier 1)
    target_hash = generate_features_hash(target_item)
    temp_db.record_sale(
        uuid="sale-exact-1",
        internal_id="LIVID_DAGGER",
        price=18_000_000,
        buyer_uuid="b1",
        seller_uuid="s1",
        timestamp=now_ms - 3600_000,
        features_json=target_item,
        features_hash=target_hash,
    )

    # 3 sales with 4 stars (within +/- 1 star relaxed match)
    for i, p in enumerate([17_000_000, 17_500_000, 17_200_000]):
        relaxed_feat = dict(target_item, dungeon_stars=4)
        rel_hash = generate_features_hash(relaxed_feat)
        temp_db.record_sale(
            uuid=f"sale-relaxed-{i}",
            internal_id="LIVID_DAGGER",
            price=p,
            buyer_uuid=f"br-{i}",
            seller_uuid=f"sr-{i}",
            timestamp=now_ms - ((i + 2) * 3600_000),
            features_json=relaxed_feat,
            features_hash=rel_hash,
        )

    res = evaluator.estimate_comps_value(target_item, now_ms=now_ms)
    assert res is not None
    assert res.tier == "TIER_2"
    assert res.comp_count == 4  # 1 exact + 3 relaxed
    assert 17_000_000 <= res.median_price <= 18_000_000


def test_comps_evaluator_tier_3_fallback_when_insufficient(temp_db):
    settings = Settings(DRY_RUN=True, MIN_COMPS_THRESHOLD=3, COMPS_WINDOW_HOURS=72.0)
    evaluator = CompsEvaluator(temp_db, settings)

    now_ms = int(time.time() * 1000)
    target_item = {
        "internal_id": "HYPERION",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
    }
    # Only 2 sales total (< 3 threshold)
    for i in range(2):
        temp_db.record_sale(
            uuid=f"hyp-{i}",
            internal_id="HYPERION",
            price=1_800_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms,
            features_json=target_item,
            features_hash=generate_features_hash(target_item),
        )

    res = evaluator.estimate_comps_value(target_item, now_ms=now_ms)
    assert res is None  # Tier 3 fallback triggers


def test_comps_evaluator_path_b_divergence_cap(temp_db):
    settings = Settings(
        DRY_RUN=True,
        MIN_COMPS_THRESHOLD=3,
        COMPS_WINDOW_HOURS=72.0,
        COMPS_MAX_PATH_B_DIVERGENCE=0.25,  # 25% max divergence
    )
    evaluator = CompsEvaluator(temp_db, settings)

    now_ms = int(time.time() * 1000)
    item_features = {
        "internal_id": "TERMINATOR",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
    }
    target_hash = generate_features_hash(item_features)

    # Historical sales say 1_000_000_000 (1B)
    for i in range(3):
        temp_db.record_sale(
            uuid=f"term-{i}",
            internal_id="TERMINATOR",
            price=1_000_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms,
            features_json=item_features,
            features_hash=target_hash,
        )

    # Path B calculated resell is only 600_000_000 (600M)
    path_b_resell = 600_000_000

    res = evaluator.estimate_comps_value(item_features, now_ms=now_ms, path_b_resell=path_b_resell)
    assert res is not None
    assert res.is_divergent is True
    # Conservative resell must be clamped to the lower Path B value
    assert res.conservative_resell == path_b_resell
    assert res.divergence_pct > 25.0


# ---------------------------------------------------------------------------
# 5. FlipEvaluator Integration Tests
# ---------------------------------------------------------------------------

def test_flip_evaluator_uses_comps_when_enabled(temp_db, dummy_bazaar_cache):
    settings = Settings(
        DRY_RUN=True,
        ENABLE_SOLD_COMPS=True,
        MIN_COMPS_THRESHOLD=3,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )
    comps_eval = CompsEvaluator(temp_db, settings, bazaar_cache=dummy_bazaar_cache)
    flip_eval = FlipEvaluator(settings, bazaar_cache=dummy_bazaar_cache, comps_evaluator=comps_eval)

    now_ms = int(time.time() * 1000)
    decoded_item = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "precise",
        "gemstones": [],
        "enchantments": {},
    }
    feat_hash = generate_features_hash(decoded_item)

    # Populate 3 historical sales at 30M
    for i in range(3):
        temp_db.record_sale(
            uuid=f"juju-comp-{i}",
            internal_id="JUJU_SHORTBOW",
            price=30_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms - (i * 3600_000),
            features_json=decoded_item,
            features_hash=feat_hash,
            normalized_tier=5,
        )

    # Listing listed at 20M (Profitable flip to 30M comps)
    auction = {
        "uuid": "candidate-auction-1",
        "starting_bid": 20_000_000,
        "item_name": "Juju Shortbow",
    }

    result = flip_eval.evaluate_listing(auction, decoded_item, now_ms=now_ms)
    assert result is not None
    assert result.valuation_path == "COMPS_TIER_1"
    assert result.target_resell_price == 30_000_000
    assert result.buy_price == 20_000_000
    assert result.net_profit > 5_000_000


def test_flip_evaluator_falls_back_to_path_b_when_comps_insufficient(temp_db, dummy_bazaar_cache):
    settings = Settings(
        DRY_RUN=True,
        ENABLE_SOLD_COMPS=True,
        MIN_COMPS_THRESHOLD=3,
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        RETENTION_RATE=0.70,
        MIN_SAMPLE_SIZE=1,
    )
    comps_eval = CompsEvaluator(temp_db, settings, bazaar_cache=dummy_bazaar_cache)
    flip_eval = FlipEvaluator(settings, bazaar_cache=dummy_bazaar_cache, comps_evaluator=comps_eval)

    # In-memory baseline: base LBIN 20M
    flip_eval.set_clean_price_cache({"JUJU_SHORTBOW": [20_000_000]})
    flip_eval.set_price_cache({"JUJU_SHORTBOW": [20_000_000]})

    now_ms = int(time.time() * 1000)
    decoded_item = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,  # Recomb worth 1M * 8 = 8M (or recomb frags)
        "modifier": "",
        "gemstones": [],
        "enchantments": {},
    }

    # Zero comps in DB -> comps_eval returns None -> fallback to Path B
    auction = {
        "uuid": "candidate-auction-2",
        "starting_bid": 15_000_000,
        "item_name": "Juju Shortbow",
    }

    result = flip_eval.evaluate_listing(auction, decoded_item, now_ms=now_ms)
    assert result is not None
    assert result.valuation_path == "PATH_B"
    assert result.is_networth_snipe is True


def test_flip_evaluator_unprofitable_comps_does_not_fallback(temp_db, dummy_bazaar_cache):
    """When comps exist but indicate the item is NOT profitable, do not fall back to theoretical Path B."""
    settings = Settings(
        DRY_RUN=True,
        ENABLE_SOLD_COMPS=True,
        MIN_COMPS_THRESHOLD=3,
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MIN_SAMPLE_SIZE=1,
    )
    comps_eval = CompsEvaluator(temp_db, settings, bazaar_cache=dummy_bazaar_cache)
    flip_eval = FlipEvaluator(settings, bazaar_cache=dummy_bazaar_cache, comps_evaluator=comps_eval)

    now_ms = int(time.time() * 1000)
    decoded_item = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "modifier": "",
        "gemstones": [],
        "enchantments": {},
    }
    feat_hash = generate_features_hash(decoded_item)

    # Historical sales say market value is 18M
    for i in range(3):
        temp_db.record_sale(
            uuid=f"comp-{i}",
            internal_id="JUJU_SHORTBOW",
            price=18_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms,
            features_json=decoded_item,
            features_hash=feat_hash,
            normalized_tier=5,
        )

    # Candidate listing is priced at 20M (higher than comps resell 18M)
    auction = {
        "uuid": "candidate-auction-overpriced",
        "starting_bid": 20_000_000,
        "item_name": "Juju Shortbow",
    }

    # Even though Path B addon math might calculate a higher price, comps empirical data takes precedence and rejects it!
    result = flip_eval.evaluate_listing(auction, decoded_item, now_ms=now_ms)
    assert result is None


def test_comps_evaluator_exponential_time_decay_behavior(temp_db):
    """Verify that recent sales have more weight than older sales in weighted median."""
    settings = Settings(
        DRY_RUN=True,
        MIN_COMPS_THRESHOLD=3,
        COMPS_WINDOW_HOURS=72.0,
        COMPS_TIME_DECAY_LAMBDA=0.05,
    )
    evaluator = CompsEvaluator(temp_db, settings)

    now_ms = int(time.time() * 1000)
    item_features = {
        "internal_id": "LIVID_DAGGER",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
    }
    feat_hash = generate_features_hash(item_features)

    # 2 old sales (60 hours ago) at 10M
    for i in range(2):
        temp_db.record_sale(
            uuid=f"old-{i}",
            internal_id="LIVID_DAGGER",
            price=10_000_000,
            buyer_uuid=f"bo-{i}",
            seller_uuid=f"so-{i}",
            timestamp=now_ms - (60 * 3600_000),
            features_json=item_features,
            features_hash=feat_hash,
        )

    # 2 very fresh sales (1 hour ago) at 20M
    for i in range(2):
        temp_db.record_sale(
            uuid=f"fresh-{i}",
            internal_id="LIVID_DAGGER",
            price=20_000_000,
            buyer_uuid=f"bf-{i}",
            seller_uuid=f"sf-{i}",
            timestamp=now_ms - (1 * 3600_000),
            features_json=item_features,
            features_hash=feat_hash,
        )

    res = evaluator.estimate_comps_value(item_features, now_ms=now_ms)
    assert res is not None
    # Because of exponential decay, fresh 20M sales carry significantly more weight than 60h-old 10M sales
    assert res.median_price == 20_000_000

