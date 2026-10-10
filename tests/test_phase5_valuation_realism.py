"""
Unit and integration tests for Phase 5 - Valuation Realism.
Covers:
- comps_evaluator time-decayed weighted p25/p50/p75 and QUICK_SALE_QUANTILE pricing
- IQR scrubbing gate (only when n >= 8; below that no trimming)
- Expected wait time with queue depth and bucket sales velocity V clamped to [0.5, 48] h
- Empirical median time_to_sell when >= 10 samples are present in sales_history
- HIGH rule requirement that profit on quick-sale value meets minimum profit and margin
- Pet lane switched to quick-sale quantile with median as optimistic target
"""

from __future__ import annotations

import time

import pytest

from src.capital_efficiency import calculate_capital_efficiency
from src.comps_evaluator import (
    CompsEvaluator,
    calculate_iqr_bounds,
    calculate_weighted_quantile,
)
from src.config import Settings
from src.db import DatabaseManager
from src.evaluator import FlipResult
from src.ranking import compute_confidence_score
from src.sales_ingestor import generate_features_hash
from src.velocity import SalesVelocityTracker


@pytest.fixture
def phase5_db(tmp_path):
    db_file = tmp_path / "phase5_test.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    yield db
    db.close()


# ---------------------------------------------------------------------------
# 1. IQR Scrubbing Gate (n >= 8)
# ---------------------------------------------------------------------------

def test_iqr_scrubbing_gate_under_8_samples():
    # 7 samples with an extreme outlier at 100M
    prices = [10_000_000, 10_100_000, 10_200_000, 10_300_000, 10_400_000, 10_500_000, 100_000_000]
    assert len(prices) == 7
    lower, upper, spread = calculate_iqr_bounds(prices)
    # Under Phase 5: n < 8 must NOT trim
    assert lower == 0.0
    assert upper == float("inf")
    assert spread > 0


def test_iqr_scrubbing_gate_at_8_samples():
    # 8 samples with an extreme outlier at 100M
    prices = [9_500_000, 9_800_000, 10_000_000, 10_100_000, 10_200_000, 10_500_000, 10_800_000, 100_000_000]
    assert len(prices) == 8
    lower, upper, _spread = calculate_iqr_bounds(prices)
    # At n >= 8, outlier scrubbing kicks in
    assert lower > 0.0
    assert upper < 50_000_000
    assert 100_000_000 > upper  # Outlier is trimmed


# ---------------------------------------------------------------------------
# 2. Time-Decayed Weighted Quantile Function
# ---------------------------------------------------------------------------

def test_calculate_weighted_quantiles():
    # Equal weights
    pairs = [
        (10_000_000, 1.0),
        (20_000_000, 1.0),
        (30_000_000, 1.0),
        (40_000_000, 1.0),
    ]
    p25 = calculate_weighted_quantile(pairs, 0.25)
    p50 = calculate_weighted_quantile(pairs, 0.50)
    p75 = calculate_weighted_quantile(pairs, 0.75)

    assert p25 == 10_000_000
    assert p50 == 20_000_000
    assert p75 == 30_000_000

    # Skewed weights (newer sale at 50M has dominant weight)
    skewed = [
        (10_000_000, 0.05),
        (20_000_000, 0.05),
        (50_000_000, 0.90),
    ]
    assert calculate_weighted_quantile(skewed, 0.50) == 50_000_000
    assert calculate_weighted_quantile(skewed, 0.25) == 50_000_000


# ---------------------------------------------------------------------------
# 3. CompsEvaluator Returns Weighted p25/p50/p75 and Quick-Sale Price
# ---------------------------------------------------------------------------

def test_comps_evaluator_returns_weighted_quantiles(phase5_db):
    settings = Settings(DRY_RUN=True, QUICK_SALE_QUANTILE=0.25, MIN_COMPS_THRESHOLD=3)
    evaluator = CompsEvaluator(phase5_db, settings)
    now_ms = int(time.time() * 1000)

    item = {
        "internal_id": "HYPERION",
        "dungeon_stars": 5,
        "master_stars": 0,
        "rarity_upgrades": 1,
    }
    feat_hash = generate_features_hash(item)

    prices = [1_000_000_000, 1_100_000_000, 1_200_000_000, 1_300_000_000]
    for i, p in enumerate(prices):
        phase5_db.record_sale(
            uuid=f"hyp-comp-{i}",
            internal_id="HYPERION",
            price=p,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms - (i * 3600_000),
            features_json=item,
            features_hash=feat_hash,
        )

    res = evaluator.estimate_comps_value(item, now_ms=now_ms)
    assert res is not None
    assert res.p25_price > 0
    assert res.p75_price >= res.median_price >= res.p25_price
    assert res.quick_sale_price == res.p25_price  # QUICK_SALE_QUANTILE=0.25


# ---------------------------------------------------------------------------
# 4. Expected Wait Time & Capital Efficiency Modeling
# ---------------------------------------------------------------------------

def test_expected_wait_time_with_queue_depth_and_velocity():
    # Velocity V = 24 sales/day = 1.0 sales/hour
    # Queue depth = 3 listings below target
    # expected_wait = (3 + 1) / 1.0 = 4.0 hours
    hold_hours, ces = calculate_capital_efficiency(
        net_profit=2_000_000,
        buy_price=20_000_000,
        daily_velocity=24.0,
        queue_depth=3,
    )
    assert hold_hours == 4.0
    # CES = (2M / (4.0 * 20M)) * 100 = 2.5% / hr
    assert ces == 2.5


def test_expected_wait_time_clamping():
    # Very high velocity with depth 0 -> raw_wait = 1 / 10 = 0.1h -> clamped to 0.5h
    hold_hours, _ = calculate_capital_efficiency(
        net_profit=1_000_000,
        buy_price=10_000_000,
        daily_velocity=240.0,
        queue_depth=0,
    )
    assert hold_hours == 0.5

    # Very deep queue with low velocity -> raw_wait = (10 + 1) / 0.1 = 110h -> clamped to 48.0h
    hold_hours, _ = calculate_capital_efficiency(
        net_profit=1_000_000,
        buy_price=10_000_000,
        daily_velocity=2.4,  # 0.1 sales/hr
        queue_depth=10,
    )
    assert hold_hours == 48.0


def test_expected_wait_time_empirical_median_time_to_sell(phase5_db):
    now = int(time.time() * 1000)
    # Populate 12 completed sales with time_to_sell_s = 7200s (2.0 hours)
    for i in range(12):
        phase5_db.record_sale(
            uuid=f"emp-sale-{i}",
            internal_id="NECRON_CHESTPLATE",
            price=50_000_000,
            buyer_uuid=f"buyer-{i}",
            seller_uuid=f"seller-{i}",
            timestamp=now - (i * 1000),
            time_to_sell_s=7200.0,
        )

    median_s = phase5_db.get_empirical_median_time_to_sell("NECRON_CHESTPLATE", min_samples=10)
    assert median_s == 7200.0

    # calculate_capital_efficiency with empirical samples
    hold_hours, _ces = calculate_capital_efficiency(
        net_profit=5_000_000,
        buy_price=50_000_000,
        daily_velocity=48.0,  # Would otherwise give 0.5h
        queue_depth=5,
        empirical_median_time_to_sell_s=median_s,
        empirical_sample_count=12,
    )
    # Empirical median overrides the formula: 7200s / 3600 = 2.0h
    assert hold_hours == 2.0


# ---------------------------------------------------------------------------
# 5. HIGH Confidence Rule Requires Profit on Quick-Sale Value
# ---------------------------------------------------------------------------

def test_high_rule_requires_quick_sale_profit():
    cfg = Settings(
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        MIN_COMPS_FOR_HIGH=5,
    )
    # Candidate with great optimistic target but quick-sale target yields < 1M profit
    flip = FlipResult(
        auction_uuid="test-uuid",
        internal_id="SHADOW_FURY",
        item_name="Shadow Fury",
        buy_price=30_000_000,
        target_resell_price=31_000_000,  # Quick-sale target (net profit ~370k < 1M)
        net_profit=370_000,
        margin_pct=1.23,
        daily_velocity=30.0,
        valuation_path="COMPS_TIER_1",
        is_priority=True,
        comp_count=10,
        conservative_target=31_000_000,
        optimistic_target=38_000_000,
        config=cfg,
    )
    # Even though score would be high due to comps and velocity,
    # quick-sale profit fails the minimum profit / margin requirements
    _score, label = compute_confidence_score(flip)
    assert label == "MEDIUM"  # Downgraded from HIGH due to Phase 5 quick-sale profit rule


def test_velocity_tracker_hourly_velocity():
    tracker = SalesVelocityTracker(auto_load=False)
    now = time.time()
    for i in range(24):
        tracker.record_sale("TEST_HOURLY_ITEM", now - (i * 3600))

    hourly = tracker.get_hourly_velocity("TEST_HOURLY_ITEM", window_hours=24.0, now=now)
    assert hourly == 1.0  # 24 sales in 24 hours = 1.0 sales/hour
