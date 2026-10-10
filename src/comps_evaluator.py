"""Tiered Comparable-Sales (Sold-Comps) Valuation Engine with Time-Weighted Median and IQR Outlier Scrubbing."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
import logging
import math
import time
from typing import Any, Optional

from src.bazaar import BazaarCache
from src.config import Settings
from src.db import DatabaseManager
from src.sales_ingestor import generate_features_hash, generate_relaxed_features_hash

logger = logging.getLogger(__name__)


@dataclass
class CompsResult:
    """Valuation result produced by the Sold-Comps engine."""

    tier: str  # 'TIER_1' (Strict), 'TIER_2' (Relaxed), 'FALLBACK'
    comp_count: int
    median_price: int
    iqr_spread: int
    confidence_score: float
    raw_prices: list[int] = field(default_factory=list)
    is_divergent: bool = False
    conservative_resell: Optional[int] = None
    divergence_pct: float = 0.0
    p25_price: int = 0
    p75_price: int = 0


def calculate_iqr_bounds(prices: list[int]) -> tuple[float, float, int]:
    """
    Calculate Interquartile Range (IQR) and outer scrubbing bounds [Q1 - 1.5*IQR, Q3 + 1.5*IQR].
    Returns (lower_bound, upper_bound, iqr_spread).
    """
    if len(prices) < 4:
        # Not enough data for meaningful quartile estimation
        spread = max(prices) - min(prices) if prices else 0
        return (0.0, float("inf"), spread)

    sorted_p = sorted(prices)
    n = len(sorted_p)

    def percentile(p_pct: float) -> float:
        k = (n - 1) * p_pct
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_p[int(k)])
        d0 = sorted_p[int(f)] * (c - k)
        d1 = sorted_p[int(c)] * (k - f)
        return float(d0 + d1)

    q1 = percentile(0.25)
    q3 = percentile(0.75)
    iqr = q3 - q1
    lower_bound = max(0.0, q1 - 1.5 * iqr)
    upper_bound = q3 + 1.5 * iqr
    return (lower_bound, upper_bound, int(round(iqr)))


def calculate_weighted_median(items: list[tuple[int, float]]) -> int:
    """
    Compute time-weighted median from a list of (price, weight) pairs.
    """
    if not items:
        return 0
    if len(items) == 1:
        return items[0][0]

    # Sort pairs by price ascending
    sorted_items = sorted(items, key=lambda x: x[0])
    total_weight = sum(w for _, w in sorted_items)
    if total_weight <= 0:
        return sorted_items[len(sorted_items) // 2][0]

    cumulative = 0.0
    cutoff = total_weight * 0.5

    for price, w in sorted_items:
        cumulative += w
        if cumulative >= cutoff:
            return price

def calculate_percentiles(prices: list[int]) -> tuple[int, int]:
    """Calculate P25 and P75 on prices."""
    if not prices:
        return 0, 0
    if len(prices) == 1:
        return prices[0], prices[0]
    sorted_p = sorted(prices)
    n = len(sorted_p)

    def percentile(p_pct: float) -> float:
        k = (n - 1) * p_pct
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return float(sorted_p[int(k)])
        d0 = sorted_p[int(f)] * (c - k)
        d1 = sorted_p[int(c)] * (k - f)
        return float(d0 + d1)

    return int(round(percentile(0.25))), int(round(percentile(0.75)))


def sanitize_comps(
    sales: list[dict[str, Any]],
    config: Optional[Settings] = None,
) -> list[dict[str, Any]]:
    """
    Sanitize comps against wash trading and market manipulation:
    1. Drop self-trades: buyer_uuid == seller_uuid.
    2. Single-actor dominance capping: no buyer or seller may account for more than
       MAX_ACTOR_COMP_SHARE (default: 0.333) of total comps. Cap their contributions
       by truncating their oldest redundant records.
    3. Actor diversity gate: cluster must contain at least MIN_DISTINCT_BUYERS (default: 3)
       and MIN_DISTINCT_SELLERS (default: 3). If unfulfilled, discard comps (return []).
    """
    if not sales:
        return []

    cfg = config or Settings(DRY_RUN=True)
    max_actor_share = getattr(cfg, "MAX_ACTOR_COMP_SHARE", 0.333)
    min_distinct_buyers = getattr(cfg, "MIN_DISTINCT_BUYERS", 3)
    min_distinct_sellers = getattr(cfg, "MIN_DISTINCT_SELLERS", 3)

    # 1. Drop self-trades
    clean_sales: list[dict[str, Any]] = []
    for s in sales:
        b = str(s.get("buyer_uuid") or "").strip()
        seller = str(s.get("seller_uuid") or "").strip()
        if b and seller and b == seller:
            continue
        clean_sales.append(s)

    if not clean_sales:
        return []

    # Sort sales by timestamp descending (newest first) to prioritize fresh comps
    sorted_sales = sorted(clean_sales, key=lambda x: float(x.get("timestamp") or 0), reverse=True)

    # 2. Single-Actor Dominance Capping
    n_total = len(sorted_sales)
    max_allowed = max(1, int(math.floor(n_total * max_actor_share + 1e-5)))

    buyer_counts: dict[str, int] = defaultdict(int)
    seller_counts: dict[str, int] = defaultdict(int)
    capped_sales: list[dict[str, Any]] = []

    for s in sorted_sales:
        b = str(s.get("buyer_uuid") or "").strip()
        seller = str(s.get("seller_uuid") or "").strip()

        # If actor reached the cap, truncate this older redundant record
        if b and buyer_counts[b] >= max_allowed:
            continue
        if seller and seller_counts[seller] >= max_allowed:
            continue

        if b:
            buyer_counts[b] += 1
        if seller:
            seller_counts[seller] += 1
        capped_sales.append(s)

    if not capped_sales:
        return []

    # 3. Actor Diversity Gate
    distinct_buyers = {str(s.get("buyer_uuid") or "").strip() for s in capped_sales if str(s.get("buyer_uuid") or "").strip()}
    distinct_sellers = {str(s.get("seller_uuid") or "").strip() for s in capped_sales if str(s.get("seller_uuid") or "").strip()}

    if len(distinct_buyers) < min_distinct_buyers or len(distinct_sellers) < min_distinct_sellers:
        logger.debug(
            "Rejecting comps cluster due to lack of actor diversity: buyers=%d (< %d) or sellers=%d (< %d)",
            len(distinct_buyers),
            min_distinct_buyers,
            len(distinct_sellers),
            min_distinct_sellers,
        )
        return []

    return capped_sales


class CompsEvaluator:
    """
    Tiered comparable-sales valuation engine:
    - Tier 1: Exact feature hash matching
    - Tier 2: Relaxed star (+/- 1) & recomb matching
    - Outlier rejection via 1.5 * IQR
    - Exponential time-decay weighting: w_i = e^(-lambda * delta_hours)
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[Settings] = None,
        bazaar_cache: Optional[BazaarCache] = None,
    ) -> None:
        self.db = db
        self.config = config or Settings(DRY_RUN=True)
        self.bazaar_cache = bazaar_cache

    def estimate_comps_value(
        self,
        item_features: dict[str, Any],
        now_ms: Optional[float] = None,
        path_b_resell: Optional[int] = None,
    ) -> Optional[CompsResult]:
        """
        Estimate item resale value from recent completed auction house sales.
        Falls back through Tier 1 -> Tier 2 -> None (Tier 3 fallback).
        """
        internal_id = str(item_features.get("internal_id", "")).strip().upper()
        if not internal_id or internal_id == "UNKNOWN":
            return None

        current_now = now_ms if now_ms is not None else (time.time() * 1000.0)
        window_hours = getattr(self.config, "COMPS_WINDOW_HOURS", 72.0)
        min_threshold = getattr(self.config, "MIN_COMPS_THRESHOLD", 3)
        decay_lambda = getattr(self.config, "COMPS_TIME_DECAY_LAMBDA", 0.02)
        window_ms = int(window_hours * 3600.0 * 1000.0)
        cutoff_ms = int(current_now - window_ms)

        # -------------------------------------------------------------------
        # Tier 1: Strict Feature Matching
        # -------------------------------------------------------------------
        target_hash = generate_features_hash(item_features)
        tier1_sales = self.db.get_sales(
            internal_id=internal_id,
            features_hash=target_hash,
            start_time=cutoff_ms,
            limit=200,
        )
        tier1_sales = sanitize_comps(tier1_sales, self.config)

        if len(tier1_sales) >= min_threshold:
            return self._aggregate_sales(
                tier_label="TIER_1",
                sales=tier1_sales,
                current_now=current_now,
                decay_lambda=decay_lambda,
                path_b_resell=path_b_resell,
            )

        # -------------------------------------------------------------------
        # Tier 2: Relaxed Feature Matching
        # -------------------------------------------------------------------
        # Query all recent sales for this internal_id within the sliding window
        all_recent_sales = self.db.get_sales(
            internal_id=internal_id,
            start_time=cutoff_ms,
            limit=500,
        )

        item_recomb = int(item_features.get("rarity_upgrades", 0))
        item_stars = int(item_features.get("dungeon_stars", 0))
        item_m_stars = int(item_features.get("master_stars", 0))
        item_prestige = str(item_features.get("prestige_tier", "") or "").strip().upper()

        relaxed_sales: list[dict[str, Any]] = []

        for sale in all_recent_sales:
            raw_feat = sale.get("features_json")
            sale_feat: dict[str, Any] = {}
            if isinstance(raw_feat, str):
                try:
                    sale_feat = json.loads(raw_feat)
                except Exception:
                    continue
            elif isinstance(raw_feat, dict):
                sale_feat = raw_feat

            # Relaxed filter: exact recomb, prestige, and star tier +/- 1
            s_recomb = int(sale_feat.get("rarity_upgrades", 0))
            s_stars = int(sale_feat.get("dungeon_stars", 0))
            s_m_stars = int(sale_feat.get("master_stars", 0))
            s_prestige = str(sale_feat.get("prestige_tier", "") or "").strip().upper()

            if s_recomb == item_recomb and s_prestige == item_prestige:
                # Stars within +/- 1 and master stars equal
                if abs(s_stars - item_stars) <= 1 and s_m_stars == item_m_stars:
                    relaxed_sales.append(sale)

        relaxed_sales = sanitize_comps(relaxed_sales, self.config)

        if len(relaxed_sales) >= min_threshold:
            return self._aggregate_sales(
                tier_label="TIER_2",
                sales=relaxed_sales,
                current_now=current_now,
                decay_lambda=decay_lambda,
                path_b_resell=path_b_resell,
            )

        # Tier 3: Threshold not met -> None (fallback to Path A / Path B)
        return None

    def _aggregate_sales(
        self,
        tier_label: str,
        sales: list[dict[str, Any]],
        current_now: float,
        decay_lambda: float,
        path_b_resell: Optional[int],
    ) -> CompsResult:
        """
        Scrub outliers using 1.5 * IQR, calculate exponential time-weighted median,
        and calculate P25/P75 percentiles.
        """
        raw_prices = [int(s["price"]) for s in sales if int(s.get("price", 0)) > 0]
        lower_bound, upper_bound, iqr_spread = calculate_iqr_bounds(raw_prices)

        # Retain only non-outlier sales and apply exponential time decay
        weighted_pairs: list[tuple[int, float]] = []
        cleaned_prices: list[int] = []

        for sale in sales:
            p = int(sale["price"])
            if lower_bound <= p <= upper_bound:
                ts = float(sale["timestamp"])
                delta_hours = max(0.0, (current_now - ts) / (3600.0 * 1000.0))
                # Exponential decay weight: w_i = e^(-lambda * delta_hours)
                weight = math.exp(-decay_lambda * delta_hours)
                weighted_pairs.append((p, weight))
                cleaned_prices.append(p)

        if not weighted_pairs:
            weighted_pairs = [(p, 1.0) for p in raw_prices]
            cleaned_prices = raw_prices

        median_price = calculate_weighted_median(weighted_pairs)

        # Recalculate IQR on scrubbed prices
        _, _, scrubbed_iqr = calculate_iqr_bounds(cleaned_prices)
        p25_price, p75_price = calculate_percentiles(cleaned_prices)

        # Confidence calculation
        n = len(cleaned_prices)
        spread_ratio = (scrubbed_iqr / median_price) if median_price > 0 else 1.0
        confidence = min(1.0, n / 5.0) * max(0.1, 1.0 - min(0.6, spread_ratio))
        if tier_label == "TIER_2":
            confidence *= 0.85  # Slight discount for relaxed match

        # Cross-check against Path B addon math
        is_divergent = False
        conservative_resell = min(p25_price, median_price) if p25_price > 0 else median_price
        divergence_pct = 0.0
        max_div = getattr(self.config, "COMPS_MAX_PATH_B_DIVERGENCE", 0.25)

        if path_b_resell and path_b_resell > 0 and median_price > 0:
            divergence_pct = abs(median_price - path_b_resell) / float(path_b_resell)
            if divergence_pct > max_div:
                is_divergent = True
                # Favor conservative valuation
                conservative_resell = min(conservative_resell, path_b_resell)
                logger.info(
                    "Comps valuation divergence detected (%.1f%% > %.1f%%): comps=%d vs path_b=%d -> capped at %d",
                    divergence_pct * 100.0,
                    max_div * 100.0,
                    median_price,
                    path_b_resell,
                    conservative_resell,
                )

        return CompsResult(
            tier=tier_label,
            comp_count=n,
            median_price=median_price,
            iqr_spread=scrubbed_iqr,
            confidence_score=round(confidence, 3),
            raw_prices=cleaned_prices,
            is_divergent=is_divergent,
            conservative_resell=conservative_resell,
            divergence_pct=round(divergence_pct * 100.0, 2),
            p25_price=p25_price,
            p75_price=p75_price,
        )
