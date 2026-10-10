"""Tests for capital-efficiency ranking and multi-factor confidence scoring (Phase 5)."""

from src.config import Settings
from src.evaluator import FlipEvaluator, FlipResult
from src.ranking import (
    calculate_capital_efficiency,
    compute_confidence_score,
    score_and_rank_flip,
)


def test_calculate_capital_efficiency_high_velocity():
    # 24 sales/day -> 1 sale/hour -> T_hold = 1.0 hour
    # Buy: 10,000,000, Net profit: 1,000,000 (10% profit)
    # CES = (1,000,000 / (1.0 * 10,000,000)) * 100 = 10.0% / hr
    hold_hours, ces = calculate_capital_efficiency(
        net_profit=1_000_000,
        buy_price=10_000_000,
        daily_velocity=24.0,
    )
    assert hold_hours == 1.0
    assert ces == 10.0


def test_calculate_capital_efficiency_clamped_bounds():
    # Extremely high velocity: 240 sales/day -> 10 sales/hour -> 0.1 hour
    # Clamped to min 0.5 hours
    hold_hours, ces = calculate_capital_efficiency(
        net_profit=500_000,
        buy_price=5_000_000,
        daily_velocity=240.0,
    )
    assert hold_hours == 0.5
    # CES = (500k / (0.5 * 5M)) * 100 = (500k / 2.5M) * 100 = 20.0
    assert ces == 20.0

    # Extremely low velocity: 0.1 sales/day -> clamped to max 48.0 hours
    hold_hours, ces = calculate_capital_efficiency(
        net_profit=1_000_000,
        buy_price=10_000_000,
        daily_velocity=0.1,
    )
    assert hold_hours == 48.0
    # CES = (1M / (48.0 * 10M)) * 100 = (1 / 480) * 100 = 0.21%
    assert ces == 0.21


def test_calculate_capital_efficiency_none_or_zero_velocity():
    # None velocity -> fallback 0.05/hr -> 20.0 hours
    hold_hours, ces = calculate_capital_efficiency(
        net_profit=2_000_000,
        buy_price=10_000_000,
        daily_velocity=None,
    )
    assert hold_hours == 20.0
    # CES = (2M / (20.0 * 10M)) * 100 = (2 / 200) * 100 = 1.0%
    assert ces == 1.0


def test_compute_confidence_score_high_tier1_comps():
    flip = FlipResult(
        auction_uuid="test-uuid",
        internal_id="NECRON_CHESTPLATE",
        item_name="Necron's Chestplate",
        buy_price=50_000_000,
        target_resell_price=70_000_000,
        net_profit=18_600_000,
        margin_pct=37.2,
        daily_velocity=25.0,
        valuation_path="COMPS_TIER_1",
        is_priority=True,
        # Under Phase 3 honest confidence rules, HIGH requires score >= 75 AND comp_count >= 5 (MIN_COMPS_FOR_HIGH)
        comp_count=5,
    )
    # Path: 40 pts
    # Liq: 30 pts (vel >= 20 is 30, priority capped at 30)
    # Margin: 30 pts (margin >= 30%)
    # Total: 100 pts -> HIGH
    score, label = compute_confidence_score(flip)
    assert score == 100.0
    assert label == "HIGH"


def test_compute_confidence_score_medium_path_b():
    flip = FlipResult(
        auction_uuid="test-uuid",
        internal_id="HYPERION",
        item_name="Hyperion",
        buy_price=800_000_000,
        target_resell_price=950_000_000,
        net_profit=120_000_000,
        margin_pct=15.0,
        daily_velocity=4.0,
        valuation_path="PATH_B",
    )
    # Path: 25 pts
    # Liq: 10 pts (vel >= 1.0 but < 5.0)
    # Margin: 18 pts (margin >= 15.0% but < 20.0%)
    # Total: 53.0 pts -> MEDIUM
    score, label = compute_confidence_score(flip)
    assert score == 53.0
    assert label == "MEDIUM"


def test_compute_confidence_score_low_deductions():
    flip = FlipResult(
        auction_uuid="test-uuid",
        internal_id="WEIRD_ITEM",
        item_name="Weird Item",
        buy_price=60_000_000,
        target_resell_price=66_000_000,
        net_profit=4_000_000,
        margin_pct=6.6,
        daily_velocity=0.5,
        valuation_path="PATH_A",
        is_out_of_budget=True,  # -10 pts
        is_craft_capped=True,  # -10 pts
    )
    # Path: 30 pts
    # Liq: 5 pts (vel < 1.0)
    # Margin: 6 pts (margin < 10%)
    # Deductions: -20 pts
    # Total: 30 + 5 + 6 - 20 = 21.0 -> LOW
    score, label = compute_confidence_score(flip)
    assert score == 21.0
    assert label == "LOW"


def test_score_and_rank_flip_integration():
    flip = FlipResult(
        auction_uuid="test-uuid-rank",
        internal_id="JUJU_SHORTBOW",
        item_name="Juju Shortbow",
        buy_price=20_000_000,
        target_resell_price=26_000_000,
        net_profit=5_480_000,
        margin_pct=27.4,
        daily_velocity=48.0,
        valuation_path="PATH_A",
    )
    ranked = score_and_rank_flip(flip)
    assert ranked.estimated_hold_hours == 0.5  # 48/24 = 2.0/hr -> 0.5h
    assert ranked.capital_efficiency is not None
    assert ranked.capital_efficiency > 0
    assert ranked.confidence_score is not None
    assert ranked.confidence_label in ["HIGH", "MEDIUM", "LOW"]


def test_flip_evaluator_populates_phase_5_metrics():
    config = Settings(
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MIN_SAMPLE_SIZE=2,
        AH_TAX_RATE=0.02,
        ENABLE_CAPITAL_EFFICIENCY=True,
    )
    evaluator = FlipEvaluator(config=config)
    evaluator.update_cache(
        [
            ({"starting_bid": 10_000_000}, {"internal_id": "TEST_SWORD"}),
            ({"starting_bid": 15_000_000}, {"internal_id": "TEST_SWORD"}),
        ]
    )

    auction = {"uuid": "auc-123", "starting_bid": 10_000_000}
    decoded = {"internal_id": "TEST_SWORD", "item_name": "Test Sword"}
    flip = evaluator.evaluate_listing(auction, decoded)

    assert flip is not None
    assert flip.confidence_score is not None
    assert flip.confidence_label in ["HIGH", "MEDIUM", "LOW"]
    assert flip.estimated_hold_hours is not None
    assert flip.capital_efficiency is not None


def test_flip_evaluator_filters_low_capital_efficiency():
    # Require extremely high capital efficiency (e.g. 500% / hr)
    config = Settings(
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        MIN_SAMPLE_SIZE=2,
        AH_TAX_RATE=0.02,
        ENABLE_CAPITAL_EFFICIENCY=True,
        MIN_CAPITAL_EFFICIENCY=500.0,
    )
    evaluator = FlipEvaluator(config=config)
    evaluator.update_cache(
        [
            ({"starting_bid": 10_000_000}, {"internal_id": "TEST_SWORD"}),
            ({"starting_bid": 12_000_000}, {"internal_id": "TEST_SWORD"}),
        ]
    )

    auction = {"uuid": "auc-123", "starting_bid": 10_000_000}
    decoded = {"internal_id": "TEST_SWORD", "item_name": "Test Sword"}
    flip = evaluator.evaluate_listing(auction, decoded)

    # Should be rejected due to MIN_CAPITAL_EFFICIENCY
    assert flip is None
