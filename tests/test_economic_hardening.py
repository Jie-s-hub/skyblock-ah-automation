"""Deterministic unit tests for Economic Hardening, Tax Cliff Optimization, Queue Depth & Anti-Wash Comps."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any

import pytest

from src.capital_efficiency import calculate_capital_efficiency
from src.comps_evaluator import (
    CompsEvaluator,
    calculate_percentiles,
    sanitize_comps,
)
from src.config import Settings
from src.constants.taxes import (
    calculate_ah_fees,
    optimize_target_resell,
)
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator, FlipResult
from src.ranking import score_and_rank_flip
from src.sales_ingestor import generate_features_hash
from src.telemetry import SurvivalAuditor
from src.velocity import SalesVelocityTracker


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database manager for isolated test runs."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = DatabaseManager(db_path)
    yield db
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


# ---------------------------------------------------------------------------
# 1. Auction House Fee Restructuring & Cliff Snapping
# ---------------------------------------------------------------------------

def test_calculate_ah_fees_flat_tiers_and_collection_tax():
    """
    Verify exact Hypixel AH listing fee, collection tax, and total deductions:
    - Under 10M: 1.0% listing fee + 1.0% collection tax (if >= 1M)
    - 10M to 100M: 2.0% listing fee + 1.0% collection tax
    - Over 100M: 2.5% listing fee + 1.0% collection tax
    - 1M Floor Cap: 1_005_000 caps collection tax at 5,000 coins
    """
    # 1. Sub-1M item (no collection tax)
    sub_1m = calculate_ah_fees(500_000)
    assert sub_1m["listing_fee"] == 5_000      # 1%
    assert sub_1m["collection_tax"] == 0      # < 1M
    assert sub_1m["total_deductions"] == 5_000
    assert sub_1m["net_revenue"] == 495_000

    # 1M boundary: per Hypixel AH fee rules, collection fee is flat 1.0% of price for all prices >= 1,000,000.
    # Recomputed by hand per Phase 1 fee model:
    # 1,005,000 * 0.01 = 10,050 creation fee; 1,005,000 * 0.01 = 10,050 collection fee.
    # total deductions = 20,100; net revenue = 1,005,000 - 20,100 = 984,900.
    cap_1m = calculate_ah_fees(1_005_000)
    assert cap_1m["collection_tax"] == 10_050
    assert cap_1m["listing_fee"] == 10_050
    assert cap_1m["total_deductions"] == 20_100
    assert cap_1m["net_revenue"] == 984_900

    # 2. Exactly at 9,999,999 (1% tier boundary)
    fees_9m = calculate_ah_fees(9_999_999)
    assert fees_9m["listing_fee"] == 99_999    # 1% of 9,999,999
    assert fees_9m["collection_tax"] == 99_999 # 1% of 9,999,999
    assert fees_9m["total_deductions"] == 199_998
    assert fees_9m["net_revenue"] == 9_800_001

    # 3. Exactly at 10,000,000 & 10,000,001 (2% tier boundary)
    fees_10m = calculate_ah_fees(10_000_000)
    assert fees_10m["listing_fee"] == 200_000   # 2% of 10M
    assert fees_10m["collection_tax"] == 100_000 # 1% of 10M
    assert fees_10m["total_deductions"] == 300_000
    assert fees_10m["net_revenue"] == 9_700_000

    fees_10m_1 = calculate_ah_fees(10_000_001)
    assert fees_10m_1["listing_fee"] == 200_000   # 2% (200,000)
    assert fees_10m_1["collection_tax"] == 100_000 # 1% (100,000)
    assert fees_10m_1["total_deductions"] == 300_000
    assert fees_10m_1["net_revenue"] == 9_700_001

    # Selling at 9,999,999 yields strictly more coins than selling at 10,000,000
    assert fees_9m["net_revenue"] > fees_10m["net_revenue"]

    # 4. Over 100M boundary (100,000,001)
    fees_100m = calculate_ah_fees(100_000_001)
    assert fees_100m["listing_fee"] == 2_500_000   # 2.5% of 100M (2,500,000)
    assert fees_100m["collection_tax"] == 1_000_000 # 1.0% of 100M (1,000,000)
    assert fees_100m["total_deductions"] == 3_500_000
    assert fees_100m["net_revenue"] == 96_500_001

    fees_99m = calculate_ah_fees(99_999_999)
    # 2% listing fee (1,999,999) + 1% tax (999,999) = 2,999,998 -> net = 97,000,001
    assert fees_99m["net_revenue"] == 97_000_001
    assert fees_99m["net_revenue"] > fees_100m["net_revenue"]


def test_calculate_ah_fees_mayor_derpy_multiplier():
    """Verify Mayor Derpy asserts 4% collection tax when derpy=True."""
    normal_fees = calculate_ah_fees(10_000_000, derpy=False)
    derpy_fees = calculate_ah_fees(10_000_000, derpy=True)

    # Asserts 4% collection tax when derpy=True
    assert normal_fees["collection_tax"] == 100_000  # 1%
    assert derpy_fees["collection_tax"] == 400_000   # 4%
    assert derpy_fees["collection_tax"] == normal_fees["collection_tax"] * 4
    assert derpy_fees["listing_fee"] == 200_000      # 2% listing fee
    assert derpy_fees["total_deductions"] == 600_000
    assert derpy_fees["net_revenue"] == 9_400_000


def test_optimize_target_resell_cliff_snapping():
    """Verify dead-zone prices snap down to tax cliff boundaries."""
    # 10,050,000 is inside [10,000,000, 10,102,040] dead zone
    # Net at 10.05M: 10.05M - (201,000 + 100,500) = 9,748,500 < 9,800,001
    assert optimize_target_resell(10_050_000) == 9_999_999

    # 100,200,000 is inside [100,000,001, 100,512,820] dead zone
    # Net at 100.2M: 100.2M - (2,505,000 + 1,002,000) = 96,693,000 < 97,000,001
    assert optimize_target_resell(100_200_000) == 99_999_999

    # Outside dead zones: prices should NOT be snapped
    # At 11,000,000: Net is 11M * 0.97 = 10,670,000 > 9,800,001
    assert optimize_target_resell(11_000_000) == 11_000_000

    # At 105,000,000: Net is 105M * 0.965 = 101,325,000 > 97,000,001
    assert optimize_target_resell(105_000_000) == 105_000_000


# ---------------------------------------------------------------------------
# 2. Queue Depth & Aligned Hold Time Modeling
# ---------------------------------------------------------------------------

def test_queue_depth_hold_time_scaling():
    """Verify hold time scales directly with order book queue depth."""
    daily_velocity = 24.0  # 1.0 sales per hour
    buy_price = 10_000_000
    net_profit = 2_000_000

    # Queue depth 0 (next in line) -> T_hold = (0 + 1) / 1.0 = 1.0 hr
    hold_0, ces_0 = calculate_capital_efficiency(net_profit, buy_price, daily_velocity, queue_depth=0)
    assert hold_0 == 1.00
    assert ces_0 == 20.00  # (2M / (1.0 * 10M)) * 100 = 20.0%

    # Queue depth 1 (1 cheaper listing ahead) -> T_hold = (1 + 1) / 1.0 = 2.0 hr
    hold_1, ces_1 = calculate_capital_efficiency(net_profit, buy_price, daily_velocity, queue_depth=1)
    assert hold_1 == 2.00
    assert ces_1 == 10.00  # 10.0%

    # Queue depth 4 (4 cheaper listings ahead) -> T_hold = (4 + 1) / 1.0 = 5.0 hr
    hold_4, ces_4 = calculate_capital_efficiency(net_profit, buy_price, daily_velocity, queue_depth=4)
    assert hold_4 == 5.00
    assert ces_4 == 4.00   # 4.0%

    # Minimum hold floor: when calculation < min_hold_hours (0.25h)
    fast_velocity = 2400.0  # 100 sales per hour
    hold_fast, _ = calculate_capital_efficiency(net_profit, buy_price, fast_velocity, queue_depth=0, min_hold_hours=0.25)
    assert hold_fast == 0.25


def test_distribution_aligned_gating_drops_p75_only_flips(temp_db):
    """
    Flips that only show theoretical profit at optimistic P75 prices
    but fail minimum profit at conservative P25/Median MUST BE DROPPED.
    """
    settings = Settings(
        DRY_RUN=True,
        ENABLE_SOLD_COMPS=True,
        MIN_COMPS_THRESHOLD=3,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        ENABLE_CLIFF_SNAPPING=False,
    )
    comps_eval = CompsEvaluator(temp_db, settings)
    flip_eval = FlipEvaluator(settings, comps_evaluator=comps_eval)

    now_ms = int(time.time() * 1000)
    item_feat = {
        "internal_id": "REAPER_SCYTHE",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
    }
    feat_hash = generate_features_hash(item_feat)

    # Record 4 historical comps:
    # Scrubbed prices: [10M, 10M, 11M, 20M]
    # P25 = 10M, Median ~ 10.5M, P75 = 15.5M
    comp_prices = [10_000_000, 10_000_000, 11_000_000, 20_000_000]
    for i, p in enumerate(comp_prices):
        temp_db.record_sale(
            uuid=f"scythe-comp-{i}",
            internal_id="REAPER_SCYTHE",
            price=p,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms - (i * 3600_000),
            features_json=item_feat,
            features_hash=feat_hash,
            normalized_tier=5,
        )

    # Candidate listing is priced at 11_000_000
    # At conservative price (P25 = 10M):
    # Net revenue = 10M - fees ~ 9.7M < buy price 11M -> NEGATIVE profit!
    # At optimistic price (P75 = 15.5M):
    # Net revenue = 15.5M - fees ~ 15.0M > buy price 11M -> +4M profit!
    unprofitable_at_conservative_auction = {
        "uuid": "candidate-scythe-1",
        "starting_bid": 11_000_000,
        "item_name": "Reaper Scythe",
    }

    result = flip_eval.evaluate_listing(unprofitable_at_conservative_auction, item_feat, now_ms=now_ms)
    # MUST BE DROPPED because it fails conservative benchmark!
    assert result is None

    # Now evaluate a candidate listed at 7_000_000
    # At conservative price (10M):
    # Net revenue = 9.8M -> profit = 2.8M (40% margin) > 1M required profit -> QUALIFIES!
    profitable_auction = {
        "uuid": "candidate-scythe-2",
        "starting_bid": 7_000_000,
        "item_name": "Reaper Scythe",
    }
    good_result = flip_eval.evaluate_listing(profitable_auction, item_feat, now_ms=now_ms)
    assert good_result is not None
    assert good_result.buy_price == 7_000_000
    assert good_result.conservative_target == 10_000_000
    assert good_result.expected_profit > 2_000_000
    # Phase 5 update: optimistic_target now exposes the median (p50) instead of p75.
    # For comps [10M, 10M, 11M, 20M], weighted p50 is 10,000,000 (equal to p25 conservative_target).
    assert good_result.optimistic_target is not None
    assert good_result.optimistic_target == 10_000_000
    assert good_result.conservative_target == 10_000_000
    assert good_result.optimistic_upside >= good_result.expected_profit


# ---------------------------------------------------------------------------
# 3. Anti-Wash Trading & Sold-Comps Sanitization
# ---------------------------------------------------------------------------

def test_sanitize_comps_self_trade_rejection():
    """Verify that comps where buyer_uuid == seller_uuid are completely dropped."""
    now = time.time() * 1000
    sales = [
        {"uuid": "s1", "buyer_uuid": "player-A", "seller_uuid": "player-B", "price": 10_000_000, "timestamp": now},
        {"uuid": "s2", "buyer_uuid": "wash-trader", "seller_uuid": "wash-trader", "price": 50_000_000, "timestamp": now},
        {"uuid": "s3", "buyer_uuid": "player-C", "seller_uuid": "player-D", "price": 10_500_000, "timestamp": now},
        {"uuid": "s4", "buyer_uuid": "player-E", "seller_uuid": "player-F", "price": 10_200_000, "timestamp": now},
    ]

    sanitized = sanitize_comps(sales)
    uuids = [s["uuid"] for s in sanitized]
    assert "s2" not in uuids
    assert len(sanitized) == 3


def test_sanitize_comps_actor_dominance_truncation_and_diversity():
    """
    10 sales where 8 share the same buyer:
    - Actor dominance caps single actor share to MAX_ACTOR_COMP_SHARE (33.3%).
    - Redundant older records for that actor are truncated.
    - If actor diversity is satisfied (>= 3 distinct buyers & sellers), truncated list is kept.
    - If actor diversity is NOT satisfied (< 3 distinct buyers or sellers), cluster is rejected.
    """
    now = time.time() * 1000
    cfg = Settings(
        DRY_RUN=True,
        MIN_DISTINCT_BUYERS=3,
        MIN_DISTINCT_SELLERS=3,
        MAX_ACTOR_COMP_SHARE=0.333,
    )

    # Cluster A: 8 sales with buyer 'whale', 1 with buyer 'b2', 1 with buyer 'b3'
    # All 10 sales have distinct sellers 's1'..'s10'
    cluster_a = []
    for i in range(8):
        cluster_a.append({
            "uuid": f"whale-sale-{i}",
            "buyer_uuid": "whale",
            "seller_uuid": f"s-{i}",
            "price": 10_000_000,
            "timestamp": now - (i * 1000),  # whale-sale-0 is newest, 7 is oldest
        })
    cluster_a.append({"uuid": "sale-b2", "buyer_uuid": "b2", "seller_uuid": "s-8", "price": 10_000_000, "timestamp": now})
    cluster_a.append({"uuid": "sale-b3", "buyer_uuid": "b3", "seller_uuid": "s-9", "price": 10_000_000, "timestamp": now})

    # Total 10 sales. Max allowed per actor = floor(10 * 0.333) = 3 sales.
    # Whale should keep 3 newest records (whale-sale-0, 1, 2) and have 5 oldest truncated.
    # Resulting sales = 3 (whale) + 1 (b2) + 1 (b3) = 5 sales.
    # Distinct buyers = {whale, b2, b3} = 3 >= 3.
    # Distinct sellers = 5 >= 3.
    sanitized_a = sanitize_comps(cluster_a, config=cfg)
    assert len(sanitized_a) == 5
    whale_count = sum(1 for s in sanitized_a if s["buyer_uuid"] == "whale")
    assert whale_count == 3
    # Check that older redundant records 3..7 were truncated
    whale_uuids = [s["uuid"] for s in sanitized_a if s["buyer_uuid"] == "whale"]
    assert "whale-sale-0" in whale_uuids
    assert "whale-sale-7" not in whale_uuids

    # Cluster B: 8 sales with buyer 'whale', 2 sales with buyer 'b2'
    # Only 2 distinct buyers total!
    cluster_b = []
    for i in range(8):
        cluster_b.append({
            "uuid": f"whale-sale-{i}",
            "buyer_uuid": "whale",
            "seller_uuid": f"s-{i}",
            "price": 10_000_000,
            "timestamp": now - (i * 1000),
        })
    cluster_b.append({"uuid": "sale-b2-1", "buyer_uuid": "b2", "seller_uuid": "s-8", "price": 10_000_000, "timestamp": now})
    cluster_b.append({"uuid": "sale-b2-2", "buyer_uuid": "b2", "seller_uuid": "s-9", "price": 10_000_000, "timestamp": now})

    # Even after truncation, distinct buyers = 2 < MIN_DISTINCT_BUYERS (3).
    # MUST BE REJECTED (returns empty list)!
    sanitized_b = sanitize_comps(cluster_b, config=cfg)
    assert len(sanitized_b) == 0


# ---------------------------------------------------------------------------
# 4. Market-Validated Alert Survival Tracking & Haircuts
# ---------------------------------------------------------------------------

def test_alert_survival_sniped_and_stale_resolution(temp_db):
    """
    Verify SurvivalAuditor lifecycle:
    - Disappearance within <= 5m (300s) -> SNIPED_BY_MARKET.
    - Persistence > 30m with hourly velocity >= 2.0 -> STALE.
    """
    vel_tracker = SalesVelocityTracker(Settings(DRY_RUN=True), auto_load=False)
    # Record 48 sales in past 24 hours for JUJU_SHORTBOW -> 2.0 sales/hour
    now_sec = time.time()
    for i in range(48):
        vel_tracker.record_sale("JUJU_SHORTBOW", now_sec - (i * 1800))

    auditor = SurvivalAuditor(temp_db, velocity_tracker=vel_tracker)

    now_ms = int(now_sec * 1000)

    # 1. Alert 1: emitted 2 minutes ago (120s ago)
    temp_db.log_alert(
        auction_uuid="alert-sniped-1",
        internal_id="JUJU_SHORTBOW",
        buy_price=20_000_000,
        estimated_resell=30_000_000,
        projected_profit=9_000_000,
        margin_pct=45.0,
        valuation_path="PATH_A",
        timestamp=now_ms - 120_000,
    )

    # 2. Alert 2: emitted 40 minutes ago (2400s ago)
    temp_db.log_alert(
        auction_uuid="alert-stale-1",
        internal_id="JUJU_SHORTBOW",
        buy_price=22_000_000,
        estimated_resell=30_000_000,
        projected_profit=7_000_000,
        margin_pct=31.8,
        valuation_path="PATH_A",
        timestamp=now_ms - (40 * 60_000),
    )

    # Active auctions on market: 'alert-stale-1' is still active, 'alert-sniped-1' has disappeared!
    active_uuids = {"alert-stale-1", "other-auction-uuid"}

    results = auditor.audit_cycle(active_uuids, current_now_ms=now_ms)
    assert results.get("SNIPED_BY_MARKET") == 1
    assert results.get("STALE") == 1

    alert1 = temp_db.get_alert("alert-sniped-1")
    assert alert1["resolved_status"] == "SNIPED_BY_MARKET"
    assert alert1["survival_duration_seconds"] == pytest.approx(120.0, rel=1e-2)
    assert alert1["survival_seconds"] == 120
    assert alert1["last_checked"] == now_ms

    alert2 = temp_db.get_alert("alert-stale-1")
    assert alert2["resolved_status"] == "STALE"
    assert alert2["survival_duration_seconds"] == pytest.approx(2400.0, rel=1e-2)
    assert alert2["survival_seconds"] == 2400
    assert alert2["last_checked"] == now_ms


def test_dynamic_lane_conversion_haircut(temp_db):
    """
    Verify that when a valuation lane's conversion rate falls below 40%,
    a 5% haircut is automatically applied to target resell price.
    """
    settings = Settings(
        DRY_RUN=True,
        ENABLE_LANE_CONVERSION_HAIRCUT=True,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=1.0,
        ENABLE_CLIFF_SNAPPING=False,
    )
    auditor = SurvivalAuditor(temp_db, config=settings)
    now_ms = int(time.time() * 1000)

    # Populate 1 sniped alert and 3 stale alerts for lane 'PATH_A'
    # Conversion rate = 1 / (1 + 3) = 0.25 (25% < 40%)
    temp_db.log_alert(
        auction_uuid="a-sniped",
        internal_id="HYPERION",
        buy_price=1_000_000_000,
        estimated_resell=1_500_000_000,
        projected_profit=400_000_000,
        margin_pct=40.0,
        valuation_path="PATH_A",
        timestamp=now_ms - 3600_000,
    )
    temp_db.update_alert_survival("a-sniped", "SNIPED_BY_MARKET", 120.0, now_ms)

    for i in range(3):
        temp_db.log_alert(
            auction_uuid=f"a-stale-{i}",
            internal_id="HYPERION",
            buy_price=1_000_000_000,
            estimated_resell=1_500_000_000,
            projected_profit=400_000_000,
            margin_pct=40.0,
            valuation_path="PATH_A",
            timestamp=now_ms - 3600_000,
        )
        temp_db.update_alert_survival(f"a-stale-{i}", "STALE", 2400.0, now_ms)

    conv_rate = auditor.get_lane_conversion_rate("PATH_A", now_ms=now_ms)
    assert conv_rate == 0.25

    haircut = auditor.get_lane_haircut("PATH_A", now_ms=now_ms)
    assert haircut == 0.05  # 5% haircut triggered!

    # Verify FlipEvaluator applies the 5% haircut to target resell price
    evaluator = FlipEvaluator(settings, survival_auditor=auditor)
    # Target resell of 100M with 5% haircut becomes 95M
    hardened_price = evaluator._apply_economic_hardening(100_000_000, "PATH_A")
    assert hardened_price == 95_000_000
