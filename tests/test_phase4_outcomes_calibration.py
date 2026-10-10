"""
Unit tests for Phase 4: Outcome Labels & Calibration Engine.
Tests live listings tracking, time_to_sell calculation, outcome status labeling,
target_hit detection, ledger PnL override, and calibration reporting.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time

import pytest

from src.config import Settings
from src.constants.lanes import LANE_FLOOR, LANE_PETS
from src.db import DatabaseManager
from src.outcome_labeler import OutcomeLabeler
from src.sales_ingestor import SalesIngestor


@pytest.fixture
def phase4_db(tmp_path):
    """Provide an isolated database for Phase 4 testing."""
    db_file = tmp_path / "phase4_test.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    yield db
    db.close()


def test_live_listings_persistence_and_time_to_sell_join(phase4_db):
    """
    Verify:
    1. Live listings start timestamps are persisted to live_listings.
    2. Ended auctions record joins on uuid and computes time_to_sell_s.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 300_000  # 300 seconds ago

    # 1. Record live listing
    phase4_db.record_live_listings([("test-live-uuid", start_ms)])
    retrieved_start = phase4_db.get_live_listing_start("test-live-uuid")
    assert retrieved_start == start_ms

    # 2. Ingest ended auction
    ingestor = SalesIngestor(phase4_db, config=Settings(DRY_RUN=True))
    raw_ended = [
        {
            "auction_id": "test-live-uuid",
            "buyer": "buyer-1",
            "seller": "seller-1",
            "bin": True,
            "price": 10_000_000,
            "timestamp": now_ms,
            "internal_id": "HYPERION",
            "decoded": {"internal_id": "HYPERION"},
        }
    ]
    count = ingestor.ingest_ended_auctions(raw_ended)
    assert count == 1

    sale = phase4_db.get_sale("test-live-uuid")
    assert sale is not None
    assert sale["price"] == 10_000_000
    assert sale["time_to_sell_s"] is not None
    # 300,000 ms = 300.0 seconds
    assert pytest.approx(sale["time_to_sell_s"], rel=1e-2) == 300.0


def test_alert_persists_listing_start(phase4_db):
    """Verify that alerts table stores the flagged listing's start timestamp."""
    now_ms = int(time.time() * 1000)
    listing_start = now_ms - 60_000

    phase4_db.log_alert(
        auction_uuid="alert-start-uuid",
        internal_id="NECRON_CHESTPLATE",
        buy_price=50_000_000,
        estimated_resell=65_000_000,
        projected_profit=12_000_000,
        margin_pct=24.0,
        confidence_label="HIGH",
        valuation_path="PATH_A",
        listing_start=listing_start,
    )

    alert = phase4_db.get_alert("alert-start-uuid")
    assert alert is not None
    assert alert["listing_start"] == listing_start


def test_outcome_labeler_sold_fast_and_sold_slow(phase4_db):
    """
    Verify background labeler assigns SOLD_FAST and SOLD_SLOW:
    - SOLD_FAST: Flagged listing sold within SNIPE_SURVIVAL_MINUTES (15m = 900s).
    - SOLD_SLOW: Flagged listing sold after SNIPE_SURVIVAL_MINUTES (> 900s).
    """
    cfg = Settings(
        ENABLE_OUTCOME_LABELING=True,
        OUTCOME_HORIZON_HOURS=6.0,
        SNIPE_SURVIVAL_MINUTES=15.0,
    )
    labeler = OutcomeLabeler(phase4_db, config=cfg)

    now_ms = int(time.time() * 1000)
    # Alert created 7 hours ago (mature > 6h horizon)
    alerted_at = now_ms - int(7.0 * 3600.0 * 1000.0)

    # Alert 1: Fast snipe (sold 5 minutes after listing)
    phase4_db.log_alert(
        auction_uuid="fast-snipe-uuid",
        internal_id="HYPERION",
        buy_price=800_000_000,
        estimated_resell=950_000_000,
        projected_profit=110_000_000,
        margin_pct=13.75,
        confidence_label="HIGH",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
        trace_json={"lane": LANE_FLOOR, "intermediates": {"confidence_score": 88.0}},
    )
    phase4_db.record_sale(
        uuid="fast-snipe-uuid",
        internal_id="HYPERION",
        price=800_000_000,
        buyer_uuid="buyer-fast",
        seller_uuid="seller-1",
        timestamp=alerted_at + 300_000,  # 300s = 5m
        time_to_sell_s=300.0,
    )

    # Alert 2: Slow sale (sold 45 minutes after listing)
    phase4_db.log_alert(
        auction_uuid="slow-sale-uuid",
        internal_id="TERMINATOR",
        buy_price=600_000_000,
        estimated_resell=700_000_000,
        projected_profit=75_000_000,
        margin_pct=12.5,
        confidence_label="MEDIUM",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
        trace_json={"lane": LANE_FLOOR, "intermediates": {"confidence_score": 65.0}},
    )
    phase4_db.record_sale(
        uuid="slow-sale-uuid",
        internal_id="TERMINATOR",
        price=600_000_000,
        buyer_uuid="buyer-slow",
        seller_uuid="seller-2",
        timestamp=alerted_at + 2700_000,  # 2700s = 45m
        time_to_sell_s=2700.0,
    )

    # Alert 3: Unsold (still not recorded in sales_history)
    phase4_db.log_alert(
        auction_uuid="unsold-uuid",
        internal_id="DIVAN_HELMET",
        buy_price=40_000_000,
        estimated_resell=55_000_000,
        projected_profit=12_000_000,
        margin_pct=30.0,
        confidence_label="LOW",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
        trace_json={"lane": LANE_FLOOR, "intermediates": {"confidence_score": 45.0}},
    )

    # Run outcome evaluation
    labeled = labeler.evaluate_pending_outcomes(now_ms=now_ms)
    assert labeled == 3

    # Check alert outcomes
    outcomes = {o["auction_uuid"]: o for o in phase4_db.get_alert_outcomes()}
    assert outcomes["fast-snipe-uuid"]["status"] == "SOLD_FAST"
    assert pytest.approx(outcomes["fast-snipe-uuid"]["listing_sold_after_s"], rel=1e-2) == 300.0

    assert outcomes["slow-sale-uuid"]["status"] == "SOLD_SLOW"
    assert pytest.approx(outcomes["slow-sale-uuid"]["listing_sold_after_s"], rel=1e-2) == 2700.0

    assert outcomes["unsold-uuid"]["status"] == "UNSOLD"
    assert outcomes["unsold-uuid"]["listing_sold_after_s"] is None


def test_outcome_labeler_target_hit_and_ledger_pnl_override(phase4_db):
    """
    Verify:
    1. target_hit detection when a relaxed-hash comparable sells at >= 95% of target.
    2. Ledger purchases table overrides realized net margin.
    """
    cfg = Settings(
        ENABLE_OUTCOME_LABELING=True,
        OUTCOME_HORIZON_HOURS=6.0,
    )
    labeler = OutcomeLabeler(phase4_db, config=cfg)
    now_ms = int(time.time() * 1000)
    alerted_at = now_ms - int(8.0 * 3600.0 * 1000.0)

    # Alert for 5-star recomb item
    features = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "prestige_tier": None,
    }
    phase4_db.log_alert(
        auction_uuid="target-hit-uuid",
        internal_id="NECRON_CHESTPLATE",
        buy_price=60_000_000,
        estimated_resell=80_000_000,
        projected_profit=16_000_000,
        margin_pct=26.6,
        confidence_label="HIGH",
        valuation_path="PATH_A",
        timestamp=alerted_at,
        listing_start=alerted_at,
        features_json=features,
        trace_json={"lane": LANE_FLOOR, "intermediates": {"confidence_score": 85.0}},
    )

    # 1. Comparable item sold at 78M (97.5% of 80M target >= 95% threshold) within horizon
    comp_features = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "prestige_tier": None,
        "enchantments": {"protection": 5},  # Enchants differ, but relaxed hash matches
    }
    phase4_db.record_sale(
        uuid="comp-sale-hit-uuid",
        internal_id="NECRON_CHESTPLATE",
        price=78_000_000,
        buyer_uuid="buyer-comp",
        seller_uuid="seller-comp",
        timestamp=alerted_at + 3600_000,  # 1 hour into horizon
        features_json=comp_features,
    )

    # 2. Player ledger row: Bought at 60M, sold at 82M with net realized PnL = 17,940,000
    # Net margin = 17,940,000 / 60,000,000 = 29.9%
    conn = phase4_db._get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO purchases (
                auction_uuid, internal_id, buy_price, buy_timestamp,
                realized_sell_price, realized_sell_timestamp, net_realized_pnl, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "target-hit-uuid",
                "NECRON_CHESTPLATE",
                60_000_000,
                alerted_at,
                82_000_000,
                alerted_at + 7200_000,
                17_940_000,
                "SOLD",
            ),
        )

    # Run labeler
    labeled = labeler.evaluate_pending_outcomes(now_ms=now_ms)
    assert labeled == 1

    outcomes = phase4_db.get_alert_outcomes()
    assert len(outcomes) == 1
    hit_record = outcomes[0]
    assert hit_record["target_hit"] == 1
    # Realized margin from ledger: 17,940,000 / 60,000,000 * 100 = 29.9%
    assert pytest.approx(hit_record["realized_net_margin"], rel=1e-2) == 29.9


def test_calibration_report_cli(phase4_db, tmp_path):
    """Verify scripts/calibration_report.py generates report and CSV export."""
    now_ms = int(time.time() * 1000)

    # Record two alert outcome records
    phase4_db.record_alert_outcome(
        alert_id=1,
        auction_uuid="calib-uuid-1",
        lane=LANE_FLOOR,
        path="PATH_A",
        bucket_key="HYPERION",
        buy_price=800_000_000,
        target_price=950_000_000,
        confidence_score=85.0,
        label="HIGH",
        alerted_at=now_ms - 20_000_000,
        status="SOLD_FAST",
        listing_sold_after_s=120.0,
        target_hit=1,
        realized_net_margin=15.5,
        evaluated_at=now_ms,
    )
    phase4_db.record_alert_outcome(
        alert_id=2,
        auction_uuid="calib-uuid-2",
        lane=LANE_PETS,
        path="PET_BUCKET",
        bucket_key="PET_GOBLIN_LEGENDARY",
        buy_price=3_000_000,
        target_price=5_000_000,
        confidence_score=72.0,
        label="MEDIUM",
        alerted_at=now_ms - 20_000_000,
        status="SOLD_SLOW",
        listing_sold_after_s=1800.0,
        target_hit=0,
        realized_net_margin=None,
        evaluated_at=now_ms,
    )

    csv_file = tmp_path / "calibration.csv"
    cmd = [
        sys.executable,
        "scripts/calibration_report.py",
        "--db",
        str(phase4_db.db_path),
        "--csv",
        str(csv_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)

    assert "ARBITRAGE OUTCOMES CALIBRATION REPORT" in res.stdout
    assert "floor" in res.stdout
    assert "pets" in res.stdout
    assert "SOLD_FAST %" in res.stdout

    # Verify CSV file
    assert csv_file.exists()
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) >= 2
        lane_rows = [r for r in rows if r["dimension"] == "lane_label"]
        assert len(lane_rows) == 2
        floor_r = next(r for r in lane_rows if r["group_key"] == "floor")
        assert floor_r["sold_fast_rate_pct"] == "100.0"
        assert floor_r["target_hit_rate_pct"] == "100.0"
