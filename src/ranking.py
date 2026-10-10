"""Capital-efficiency ranking and multi-factor confidence scoring engine."""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.config import Settings

logger = logging.getLogger(__name__)

from src.capital_efficiency import calculate_capital_efficiency


def compute_confidence_score(flip: Any) -> tuple[float, str]:
    """
    Compute a normalized multi-factor confidence score (0-100) and assign categorical label:
    - Valuation Methodology / Path (max 40 pts)
    - Liquidity / Daily Turnover (max 30 pts)
    - Margin Buffer Cushion (max 30 pts)
    - Guardrail / Risk Deductions (up to -20 pts)

    Categories:
    - >= 75.0: "HIGH"
    - 45.0 - 74.9: "MEDIUM"
    - < 45.0: "LOW"

    Returns:
        tuple[float, str]: (confidence_score, confidence_label)
    """
    # 1. Valuation Path Weight (Max 40 points)
    val_path = str(getattr(flip, "valuation_path", "PATH_A")).upper()
    if "COMPS_TIER_1" in val_path:
        path_pts = 40.0
    elif "COMPS_TIER_2" in val_path:
        path_pts = 32.0
    elif getattr(flip, "is_god_roll", False) or "ATTRIBUTE" in val_path:
        path_pts = 35.0
    elif val_path == "PATH_A":
        path_pts = 30.0
    elif val_path == "PATH_B":
        path_pts = 25.0
    elif val_path in ("PET", "PET_BUCKET"):
        path_pts = 35.0
    else:
        path_pts = 20.0

    # 2. Liquidity & Volume Turnover (Max 30 points)
    vel = getattr(flip, "daily_velocity", None)
    if vel is not None:
        if vel >= 20.0:
            liq_pts = 30.0
        elif vel >= 10.0:
            liq_pts = 24.0
        elif vel >= 5.0:
            liq_pts = 18.0
        elif vel >= 1.0:
            liq_pts = 10.0
        else:
            liq_pts = 5.0
    else:
        liq_pts = 15.0  # Neutral if velocity tracker inactive

    # Priority progression staples bonus
    if getattr(flip, "is_priority", False):
        liq_pts = min(30.0, liq_pts + 5.0)

    # 3. Margin Buffer Cushion (Max 30 points)
    margin = float(getattr(flip, "margin_pct", 0.0))
    if margin >= 30.0:
        margin_pts = 30.0
    elif margin >= 20.0:
        margin_pts = 24.0
    elif margin >= 15.0:
        margin_pts = 18.0
    elif margin >= 10.0:
        margin_pts = 12.0
    else:
        margin_pts = 6.0

    # 4. Guardrail & Risk Deductions
    deductions = 0.0
    if getattr(flip, "is_out_of_budget", False):
        deductions += 10.0
    if getattr(flip, "is_craft_capped", False):
        deductions += 10.0
    # Guard against artificial spreads / outlier gaps: extreme margin on PATH_A without comps
    if val_path == "PATH_A" and margin >= 60.0:
        deductions += 15.0

    raw_score = path_pts + liq_pts + margin_pts - deductions
    final_score = max(0.0, min(100.0, raw_score))

    if final_score >= 75.0:
        label = "HIGH"
    elif final_score >= 45.0:
        label = "MEDIUM"
    else:
        label = "LOW"

    # Phase 3 Honest Confidence:
    # 1. Path-B-only (networth) valuations cap at MEDIUM
    if val_path == "PATH_B" and label == "HIGH":
        label = "MEDIUM"

    # 2. HIGH requires >= MIN_COMPS_FOR_HIGH same-bucket sales in the comps window
    comp_count = getattr(flip, "comp_count", None) or 0
    min_comps_high = getattr(flip, "config", None)
    min_comps_val = 5
    if hasattr(flip, "min_comps_for_high") and flip.min_comps_for_high is not None:
        min_comps_val = flip.min_comps_for_high
    elif min_comps_high and hasattr(min_comps_high, "MIN_COMPS_FOR_HIGH"):
        min_comps_val = min_comps_high.MIN_COMPS_FOR_HIGH

    if label == "HIGH" and comp_count < min_comps_val:
        label = "MEDIUM"

    # Phase 5: The HIGH rule also requires profit on the quick-sale value
    # to meet the minimum profit and margin settings
    if label == "HIGH":
        cfg = getattr(flip, "config", None)
        min_p = getattr(cfg, "MIN_PROFIT_COINS", 500_000) if cfg else 500_000
        min_m = getattr(cfg, "MIN_PROFIT_MARGIN_PCT", 10.0) if cfg else 10.0
        quick_target = getattr(flip, "conservative_target", None) or getattr(flip, "target_resell_price", None)
        buy_p = int(getattr(flip, "buy_price", 0))
        if quick_target is not None and buy_p > 0:
            from src.constants.taxes import net_revenue

            derpy_active = getattr(flip, "derpy_active", False)
            derpy_rate = getattr(cfg, "DERPY_COLLECTION_FEE_RATE", 0.04) if cfg else 0.04
            q_net_rev = net_revenue(quick_target, derpy_active=derpy_active, derpy_rate=derpy_rate)
            q_profit = q_net_rev - buy_p
            q_margin = (q_profit / buy_p) * 100.0
            if q_profit < min_p or q_margin < min_m:
                label = "MEDIUM"

    return (round(final_score, 1), label)


def score_and_rank_flip(
    flip: Any,
    config: Optional[Settings] = None,
    db: Optional[Any] = None,
) -> Any:
    """
    Populate capital efficiency metrics and multi-factor confidence scoring onto a FlipResult.
    """
    cfg = config or (getattr(flip, "config", None) if hasattr(flip, "config") else None)
    enable_qd = getattr(cfg, "ENABLE_QUEUE_DEPTH_HOLD_TIME", True) if cfg else True
    min_hold_h = getattr(cfg, "MIN_HOLD_TIME_HOURS", None) if cfg else None
    min_comps_high = getattr(cfg, "MIN_COMPS_FOR_HIGH", 5) if cfg else 5

    # Check for empirical median time_to_sell from db if >= 10 samples
    empirical_time_s = None
    empirical_count = 0
    database = db or getattr(flip, "db", None)
    if database is not None and hasattr(database, "get_empirical_median_time_to_sell"):
        emp_res = database.get_empirical_median_time_to_sell(
            internal_id=getattr(flip, "internal_id", ""),
            features_hash=getattr(flip, "features_hash", None),
            min_samples=10,
        )
        if emp_res is not None:
            empirical_time_s = emp_res
            empirical_count = 10

    hold_hours, ces = calculate_capital_efficiency(
        net_profit=int(getattr(flip, "net_profit", 0)),
        buy_price=int(getattr(flip, "buy_price", 0)),
        daily_velocity=getattr(flip, "daily_velocity", None),
        queue_depth=int(getattr(flip, "queue_depth", 0)),
        min_hold_hours=min_hold_h,
        enable_queue_depth=enable_qd,
        empirical_median_time_to_sell_s=empirical_time_s,
        empirical_sample_count=empirical_count,
    )
    conf_score, conf_label = compute_confidence_score(flip)

    # Honor pet bucket valuation confidence label (margin review cap to LOW and comps spread to HIGH)
    if getattr(flip, "is_pet", False) and getattr(flip, "valuation_path", "") == "PET_BUCKET":
        prior_label = getattr(flip, "confidence_label", None)
        if prior_label:
            conf_label = prior_label
            if conf_label == "LOW":
                conf_score = min(conf_score, 40.0)
            elif conf_label == "HIGH":
                comp_cnt = getattr(flip, "comp_count", None) or 0
                if comp_cnt >= min_comps_high:
                    conf_score = max(conf_score, 80.0)
                else:
                    conf_label = "MEDIUM"
                    conf_score = min(conf_score, 74.9)

    # Phase 3 Honest Confidence Enforcements:
    # 1. Path-B-only (networth) valuations cap at MEDIUM
    val_path = str(getattr(flip, "valuation_path", "")).upper()
    if val_path == "PATH_B" and conf_label == "HIGH":
        conf_label = "MEDIUM"
        conf_score = min(conf_score, 74.9)

    # 2. HIGH requires score >= 75 AND >= MIN_COMPS_FOR_HIGH same-bucket sales
    comp_count = getattr(flip, "comp_count", None) or 0
    if conf_label == "HIGH" and comp_count < min_comps_high:
        conf_label = "MEDIUM"
        conf_score = min(conf_score, 74.9)

    # Phase 5: The HIGH rule also requires profit on the quick-sale value
    # to meet the minimum profit and margin settings
    if conf_label == "HIGH":
        min_p = getattr(cfg, "MIN_PROFIT_COINS", 500_000) if cfg else 500_000
        min_m = getattr(cfg, "MIN_PROFIT_MARGIN_PCT", 10.0) if cfg else 10.0
        quick_target = getattr(flip, "conservative_target", None) or getattr(flip, "target_resell_price", None)
        buy_p = int(getattr(flip, "buy_price", 0))
        if quick_target is not None and buy_p > 0:
            from src.constants.taxes import net_revenue

            derpy_active = getattr(flip, "derpy_active", False)
            derpy_rate = getattr(cfg, "DERPY_COLLECTION_FEE_RATE", 0.04) if cfg else 0.04
            q_net_rev = net_revenue(quick_target, derpy_active=derpy_active, derpy_rate=derpy_rate)
            q_profit = q_net_rev - buy_p
            q_margin = (q_profit / buy_p) * 100.0
            if q_profit < min_p or q_margin < min_m:
                conf_label = "MEDIUM"
                conf_score = min(conf_score, 74.9)

    flip.estimated_hold_hours = hold_hours
    flip.capital_efficiency = ces
    flip.confidence_score = conf_score
    flip.confidence_label = conf_label
    return flip

