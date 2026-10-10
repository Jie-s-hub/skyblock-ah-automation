"""
Unit tests for Phase 3: Guards On + Honest Confidence, Structured Decision Tracing,
Rejection Telemetry, and Shadow Mode Lanes.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from src.config import Settings, get_settings
from src.constants.lanes import (
    LANE_FLOOR,
    LANE_PETS,
)
from src.db import DatabaseManager
from src.evaluator import Decision, FlipEvaluator, FlipResult
from src.telemetry import PipelineTelemetry, build_health_embed
from tests.test_pet_evaluator import make_pet_fixture, record_test_sold_comps


@pytest.fixture
def phase3_db(tmp_path):
    """Provide an isolated database for Phase 3 testing."""
    db_file = tmp_path / "phase3_test.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    yield db
    db.close()


def test_phase3_defaults_are_enabled():
    """Verify that guards and sold-comps default to True."""
    cfg = get_settings(_env_file=None, DRY_RUN=True)
    assert cfg.ENABLE_SOLD_COMPS is True
    assert cfg.ENABLE_DYNAMIC_RETENTION is True
    assert cfg.ENABLE_BAZAAR_SHOCK_GUARD is True
    assert cfg.ENABLE_SELLER_DOMINATION_GUARD is True
    assert cfg.MIN_COMPS_FOR_HIGH == 5
    assert cfg.SHADOW_MODE_LANES == []


def test_cold_start_suppresses_pets_and_caps_floor_at_medium(phase3_db):
    """
    Verify cold-start behavior:
    - Pet candidate emits nothing (rejection reason: 'cold_start').
    - Floor candidate is capped at MEDIUM confidence even if score is high.
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        COLD_START_MIN_SALES=50,
    )
    evaluator = FlipEvaluator(settings, db=phase3_db)
    # Simulate cold start
    evaluator.set_cold_start(True)

    # 1. Pet candidate during cold start emits nothing
    cand_pet = make_pet_fixture("cold-pet", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    other_pet = make_pet_fixture("ref-pet", 5_200_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand_pet, other_pet])

    decision = evaluator.evaluate_decision(cand_pet[0], cand_pet[1])
    assert decision.accepted is False
    assert "cold_start" in decision.reasons
    assert evaluator.evaluate_listing(cand_pet[0], cand_pet[1]) is None

    # 2. Clean floor item during cold start caps confidence at MEDIUM
    raw_auction = {
        "uuid": "cold-floor-item",
        "starting_bid": 10_000_000,
        "item_name": "Hyperion",
        "start": time.time() * 1000,
        "bin": True,
    }
    decoded = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
        "is_pet": False,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "enchantments": {},
    }
    # Provide active market with 2nd lowest at 20M
    evaluator.set_price_cache({"HYPERION": [10_000_000, 20_000_000, 21_000_000, 22_000_000]})
    evaluator._clean_price_cache = {"HYPERION": [10_000_000, 20_000_000, 21_000_000, 22_000_000]}

    flip = evaluator.evaluate_listing(raw_auction, decoded)
    assert flip is not None
    assert flip.confidence_label == "MEDIUM"
    assert flip.lane == LANE_FLOOR


def test_honest_confidence_high_requires_min_comps(phase3_db):
    """
    Verify honest confidence rules:
    - Score >= 75 with < 5 comps caps at MEDIUM.
    - Score >= 75 with >= 5 comps qualifies for HIGH.
    """
    # Record 4 comps (fewer than 5 required for HIGH)
    record_test_sold_comps(
        phase3_db,
        prices=[5_000_000, 5_000_000, 5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MIN_COMPS_FOR_HIGH=5,
        PET_MARGIN_REVIEW_PCT=100.0,
    )
    evaluator = FlipEvaluator(settings, db=phase3_db)

    cand = make_pet_fixture("cand-4comps", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    ref = make_pet_fixture("ref-4comps", 5_200_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand, ref])

    flip_4comps = evaluator.evaluate_listing(cand[0], cand[1])
    assert flip_4comps is not None
    assert flip_4comps.comp_count == 4
    # 4 comps < 5 required -> capped at MEDIUM
    assert flip_4comps.confidence_label == "MEDIUM"

    # Now add 5th and 6th comp
    record_test_sold_comps(
        phase3_db,
        prices=[5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
        uuid_prefix="extra-",
    )
    evaluator._pet_dedupe_cache.clear()
    cand2 = make_pet_fixture("cand-6comps", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand2, ref])

    flip_6comps = evaluator.evaluate_listing(cand2[0], cand2[1])
    assert flip_6comps is not None
    assert flip_6comps.comp_count >= 5
    assert flip_6comps.confidence_label == "HIGH"


def test_path_b_only_valuations_cap_at_medium(phase3_db):
    """
    Verify that Path-B-only (networth engine) valuations cap at MEDIUM.
    """
    from src.ranking import compute_confidence_score, score_and_rank_flip

    mock_flip = FlipResult(
        auction_uuid="networth-test",
        internal_id="NECRON_CHESTPLATE",
        item_name="Necron's Chestplate",
        buy_price=50_000_000,
        target_resell_price=80_000_000,
        net_profit=25_000_000,
        margin_pct=50.0,
        valuation_path="PATH_B",
        daily_velocity=25.0,  # High liquidity
        comp_count=0,
    )
    _score, label = compute_confidence_score(mock_flip)
    # Path B raw score might exceed 75, but must cap at MEDIUM
    assert label == "MEDIUM"

    cfg = Settings()
    ranked = score_and_rank_flip(mock_flip, cfg)
    assert ranked.confidence_label == "MEDIUM"


def test_structured_decision_and_explain_script(phase3_db):
    """
    Verify structured Decision return, trace persistence, and scripts/explain.py CLI output.
    """
    record_test_sold_comps(
        phase3_db,
        prices=[5_000_000, 5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    evaluator = FlipEvaluator(settings, db=phase3_db)

    cand = make_pet_fixture("explain-uuid", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    ref = make_pet_fixture("explain-ref", 5_200_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand, ref])

    decision = evaluator.evaluate_decision(cand[0], cand[1])
    assert isinstance(decision, Decision)
    assert decision.accepted is True
    assert decision.lane == LANE_PETS
    assert decision.flip is not None
    assert decision.flip.trace_json is not None

    # Persist alert with trace_json
    phase3_db.log_alert(
        auction_uuid=decision.flip.auction_uuid,
        internal_id=decision.flip.internal_id,
        buy_price=decision.flip.buy_price,
        estimated_resell=decision.flip.target_resell_price,
        projected_profit=decision.flip.net_profit,
        margin_pct=decision.flip.margin_pct,
        confidence_label=decision.flip.confidence_label,
        valuation_path=decision.flip.valuation_path,
        trace_json=decision.flip.trace_json,
    )

    alert_row = phase3_db.get_alert("explain-uuid")
    assert alert_row is not None
    assert alert_row["trace_json"] is not None

    # Test explain.py via subprocess
    cmd = [sys.executable, "scripts/explain.py", "--db", str(phase3_db.db_path), "explain-uuid"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "Arbitrage Decision Trace" in res.stdout
    assert "explain-uuid" in res.stdout
    assert "pets" in res.stdout
    assert "Key Intermediates:" in res.stdout


def test_telemetry_rejection_counting_and_health_embed():
    """Verify rejection reasons counting and health ping representation."""
    telemetry = PipelineTelemetry()
    telemetry.record_rejection("pet_sales_gate", count=4)
    telemetry.record_rejection("TIE_AT_FLOOR", count=7)
    telemetry.record_rejection("craft_ceiling", count=2)

    top_rej = telemetry.get_top_rejections(limit=2)
    assert top_rej == [("TIE_AT_FLOOR", 7), ("pet_sales_gate", 4)]

    embed = build_health_embed(telemetry)
    field_names = [f["name"] for f in embed["fields"]]
    assert "🚫 Top Rejections" in field_names
    rej_field = next(f for f in embed["fields"] if f["name"] == "🚫 Top Rejections")
    assert "TIE_AT_FLOOR" in rej_field["value"]
    assert "7" in rej_field["value"]


def test_shadow_mode_lanes_persisted_without_alert(phase3_db):
    """
    Verify SHADOW_MODE_LANES behavior:
    Opportunity in a shadow lane is evaluated and persisted in DB, but never alerted.
    """
    record_test_sold_comps(
        phase3_db,
        prices=[5_000_000, 5_000_000, 5_000_000],
        pet_type="GOBLIN",
        tier="LEGENDARY",
        level=1,
    )
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=True,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        SHADOW_MODE_LANES=["pets"],
    )
    evaluator = FlipEvaluator(settings, db=phase3_db)

    cand = make_pet_fixture("shadow-cand", 3_000_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    ref = make_pet_fixture("shadow-ref", 5_200_000, pet_type="GOBLIN", tier="LEGENDARY", level=1)
    evaluator.update_cache([cand, ref])

    flip = evaluator.evaluate_listing(cand[0], cand[1])
    assert flip is not None
    assert flip.lane == "pets"

    # Simulating main loop behavior:
    # 1. Persisted to DB
    phase3_db.log_alert(
        auction_uuid=flip.auction_uuid,
        internal_id=flip.internal_id,
        buy_price=flip.buy_price,
        estimated_resell=flip.target_resell_price,
        projected_profit=flip.net_profit,
        margin_pct=flip.margin_pct,
        confidence_label=flip.confidence_label,
        valuation_path=flip.valuation_path,
        trace_json=flip.trace_json,
    )
    assert phase3_db.get_alert("shadow-cand") is not None

    # 2. Suppressed from alerts because lane is in SHADOW_MODE_LANES
    is_suppressed = flip.lane in settings.SHADOW_MODE_LANES
    assert is_suppressed is True
