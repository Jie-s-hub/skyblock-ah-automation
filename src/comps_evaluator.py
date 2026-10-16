"""Tiered Comparable-Sales (Sold-Comps) Valuation Engine with Time-Weighted Median and IQR Outlier Scrubbing."""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from src.bazaar import BazaarCache
from src.config import Settings
from src.constants.prestige import is_crimson_isle_item
from src.db import DatabaseManager
from src.sales_ingestor import generate_features_hash

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
    quick_sale_price: int = 0


def calculate_iqr_bounds(prices: list[int]) -> tuple[float, float, int]:
    """
    Calculate Interquartile Range (IQR) and outer scrubbing bounds [Q1 - 1.5*IQR, Q3 + 1.5*IQR].
    Phase 5: Only scrubs outliers when n >= 8; below that, don't trim (lower_bound=0.0, upper_bound=inf).
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

    if n < 8:
        # Phase 5: IQR scrubbing only when n >= 8; below that, don't trim.
        return (0.0, float("inf"), int(round(iqr)))

    lower_bound = max(0.0, q1 - 1.5 * iqr)
    upper_bound = q3 + 1.5 * iqr
    return (lower_bound, upper_bound, int(round(iqr)))


def calculate_weighted_quantile(items: list[tuple[int, float]], q: float) -> int:
    """
    Compute time-decayed weighted quantile q in [0.0, 1.0] from a list of (price, weight) pairs.
    """
    if not items:
        return 0
    if len(items) == 1:
        return items[0][0]

    sorted_items = sorted(items, key=lambda x: x[0])
    total_weight = sum(w for _, w in sorted_items)
    if total_weight <= 0:
        idx = min(len(sorted_items) - 1, max(0, int(round(q * (len(sorted_items) - 1)))))
        return sorted_items[idx][0]

    cumulative = 0.0
    cutoff = total_weight * max(0.0, min(1.0, q))

    for price, w in sorted_items:
        cumulative += w
        if cumulative >= cutoff:
            return price

    return sorted_items[-1][0]


def calculate_weighted_median(items: list[tuple[int, float]]) -> int:
    """Compute time-weighted median (q=0.50) from (price, weight) pairs."""
    return calculate_weighted_quantile(items, 0.50)


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
    distinct_buyers = {
        str(s.get("buyer_uuid") or "").strip() for s in capped_sales if str(s.get("buyer_uuid") or "").strip()
    }
    distinct_sellers = {
        str(s.get("seller_uuid") or "").strip() for s in capped_sales if str(s.get("seller_uuid") or "").strip()
    }

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
        self._eval_cache: dict[tuple[Any, ...], Optional[CompsResult]] = {}
        self._recent_sales_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.telemetry_snapshot: Optional[Any] = None

    def set_telemetry_snapshot(self, snapshot: Any) -> None:
        """Set in-memory telemetry snapshot for zero-IO hot-path comps lookups."""
        self.telemetry_snapshot = snapshot

    def clear_cache(self) -> None:
        """Clear in-memory comps valuation cache."""
        self._eval_cache.clear()
        self._recent_sales_cache.clear()

    def estimate_comps_value(
        self,
        item_features: dict[str, Any],
        now_ms: Optional[float] = None,
        path_b_resell: Optional[int] = None,
        telemetry_snapshot: Optional[Any] = None,
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

        target_hash = generate_features_hash(item_features)
        # Bucket by 5-minute intervals (300,000ms) to maximize multi-cycle cache reuse
        cutoff_bucket = int(cutoff_ms // 300_000)
        cache_key = (internal_id, target_hash, path_b_resell, cutoff_bucket)
        if cache_key in self._eval_cache:
            return self._eval_cache[cache_key]

        # Zero-DB Rule: If telemetry_snapshot is active, resolve comps strictly in RAM
        snapshot = telemetry_snapshot or getattr(self, "telemetry_snapshot", None)
        if snapshot is not None:
            summary = getattr(snapshot, "comps_summary", {}) or {}
            # Tier 1 strict feature hash match
            t1_stats = summary.get(target_hash)
            if t1_stats is not None and t1_stats.comp_count >= min_threshold:
                spread_ratio = (t1_stats.iqr_spread / t1_stats.median_price) if t1_stats.median_price > 0 else 1.0
                confidence = min(1.0, t1_stats.comp_count / 5.0) * max(0.1, 1.0 - min(0.6, spread_ratio))
                quick_sale_price = t1_stats.p25_price or t1_stats.median_price
                conservative_resell = min(quick_sale_price, t1_stats.median_price)
                if path_b_resell and path_b_resell > 0 and t1_stats.median_price > 0:
                    max_div = getattr(self.config, "COMPS_MAX_PATH_B_DIVERGENCE", 0.25)
                    div_pct = abs(t1_stats.median_price - path_b_resell) / float(path_b_resell)
                    if div_pct > max_div:
                        conservative_resell = min(conservative_resell, path_b_resell)
                res = CompsResult(
                    tier="TIER_1",
                    comp_count=t1_stats.comp_count,
                    median_price=t1_stats.median_price,
                    iqr_spread=t1_stats.iqr_spread,
                    confidence_score=round(confidence, 3),
                    raw_prices=[],
                    is_divergent=False,
                    conservative_resell=conservative_resell,
                    p25_price=t1_stats.p25_price,
                    p75_price=t1_stats.p75_price,
                    quick_sale_price=quick_sale_price,
                )
                if len(self._eval_cache) >= 10_000:
                    self._eval_cache.clear()
                self._eval_cache[cache_key] = res
                return res

            # Tier 2 relaxed internal_id match
            t2_stats = summary.get(internal_id)
            if t2_stats is not None and t2_stats.comp_count >= min_threshold:
                spread_ratio = (t2_stats.iqr_spread / t2_stats.median_price) if t2_stats.median_price > 0 else 1.0
                confidence = min(1.0, t2_stats.comp_count / 5.0) * max(0.1, 1.0 - min(0.6, spread_ratio)) * 0.85
                quick_sale_price = t2_stats.p25_price or t2_stats.median_price
                conservative_resell = min(quick_sale_price, t2_stats.median_price)
                if path_b_resell and path_b_resell > 0 and t2_stats.median_price > 0:
                    max_div = getattr(self.config, "COMPS_MAX_PATH_B_DIVERGENCE", 0.25)
                    div_pct = abs(t2_stats.median_price - path_b_resell) / float(path_b_resell)
                    if div_pct > max_div:
                        conservative_resell = min(conservative_resell, path_b_resell)
                res = CompsResult(
                    tier="TIER_2",
                    comp_count=t2_stats.comp_count,
                    median_price=t2_stats.median_price,
                    iqr_spread=t2_stats.iqr_spread,
                    confidence_score=round(confidence, 3),
                    raw_prices=[],
                    is_divergent=False,
                    conservative_resell=conservative_resell,
                    p25_price=t2_stats.p25_price,
                    p75_price=t2_stats.p75_price,
                    quick_sale_price=quick_sale_price,
                )
                if len(self._eval_cache) >= 10_000:
                    self._eval_cache.clear()
                self._eval_cache[cache_key] = res
                return res

            # Missing from snapshot: return None immediately without DB calls
            if len(self._eval_cache) >= 10_000:
                self._eval_cache.clear()
            self._eval_cache[cache_key] = None
            return None

        # -------------------------------------------------------------------
        # Tier 1: Strict Feature Matching
        # -------------------------------------------------------------------
        tier1_sales = self.db.get_sales(
            internal_id=internal_id,
            features_hash=target_hash,
            start_time=cutoff_ms,
            limit=200,
        )
        tier1_sales = sanitize_comps(tier1_sales, self.config)

        if len(tier1_sales) >= min_threshold:
            res = self._aggregate_sales(
                tier_label="TIER_1",
                sales=tier1_sales,
                current_now=current_now,
                decay_lambda=decay_lambda,
                path_b_resell=path_b_resell,
            )
            if len(self._eval_cache) >= 10_000:
                self._eval_cache.clear()
            self._eval_cache[cache_key] = res
            return res

        # -------------------------------------------------------------------
        # Tier 2: Relaxed Feature Matching
        # -------------------------------------------------------------------
        # Query recent sales for this internal_id (memoized per 5-minute bucket)
        recent_sales_key = (internal_id, cutoff_bucket)
        if recent_sales_key in self._recent_sales_cache:
            all_recent_sales = self._recent_sales_cache[recent_sales_key]
        else:
            all_recent_sales = self.db.get_sales(
                internal_id=internal_id,
                start_time=cutoff_ms,
                limit=500,
            )
            if len(self._recent_sales_cache) >= 2000:
                self._recent_sales_cache.clear()
            self._recent_sales_cache[recent_sales_key] = all_recent_sales

        is_crimson = is_crimson_isle_item(internal_id)
        item_recomb = int(item_features.get("rarity_upgrades", 0))
        item_stars = int(item_features.get("dungeon_stars", 0))
        item_m_stars = 0 if is_crimson else int(item_features.get("master_stars", 0))
        item_prestige = str(item_features.get("prestige_tier", "") or "").strip().upper()

        relaxed_sales: list[dict[str, Any]] = []

        for sale in all_recent_sales:
            sale_feat = sale.get("_parsed_features")
            if sale_feat is None:
                raw_feat = sale.get("features_json")
                if isinstance(raw_feat, str):
                    try:
                        sale_feat = json.loads(raw_feat)
                    except Exception:
                        sale_feat = {}
                elif isinstance(raw_feat, dict):
                    sale_feat = raw_feat
                else:
                    sale_feat = {}
                sale["_parsed_features"] = sale_feat

            # Relaxed filter: exact recomb, prestige, and star tier +/- 1
            s_recomb = int(sale_feat.get("rarity_upgrades", 0))
            s_stars = int(sale_feat.get("dungeon_stars", 0))
            s_m_stars = 0 if is_crimson else int(sale_feat.get("master_stars", 0))
            s_prestige = str(sale_feat.get("prestige_tier", "") or "").strip().upper()

            if s_recomb == item_recomb and s_prestige == item_prestige:
                # Stars within +/- 1 and master stars equal
                if abs(s_stars - item_stars) <= 1 and s_m_stars == item_m_stars:
                    relaxed_sales.append(sale)

        relaxed_sales = sanitize_comps(relaxed_sales, self.config)

        if len(relaxed_sales) >= min_threshold:
            res = self._aggregate_sales(
                tier_label="TIER_2",
                sales=relaxed_sales,
                current_now=current_now,
                decay_lambda=decay_lambda,
                path_b_resell=path_b_resell,
            )
            if len(self._eval_cache) >= 10_000:
                self._eval_cache.clear()
            self._eval_cache[cache_key] = res
            return res

        # Tier 3: Threshold not met -> None (fallback to Path A / Path B)
        if len(self._eval_cache) >= 10_000:
            self._eval_cache.clear()
        self._eval_cache[cache_key] = None
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

        quick_sale_q = getattr(self.config, "QUICK_SALE_QUANTILE", 0.25)
        quick_sale_price = calculate_weighted_quantile(weighted_pairs, quick_sale_q)
        p25_price = calculate_weighted_quantile(weighted_pairs, 0.25)
        median_price = calculate_weighted_quantile(weighted_pairs, 0.50)
        p75_price = calculate_weighted_quantile(weighted_pairs, 0.75)

        # Recalculate IQR on scrubbed prices
        _, _, scrubbed_iqr = calculate_iqr_bounds(cleaned_prices)

        # Confidence calculation
        n = len(cleaned_prices)
        spread_ratio = (scrubbed_iqr / median_price) if median_price > 0 else 1.0
        confidence = min(1.0, n / 5.0) * max(0.1, 1.0 - min(0.6, spread_ratio))
        if tier_label == "TIER_2":
            confidence *= 0.85  # Slight discount for relaxed match

        # Cross-check against Path B addon math
        is_divergent = False
        conservative_resell = min(quick_sale_price, median_price) if quick_sale_price > 0 else median_price
        divergence_pct = 0.0
        max_div = getattr(self.config, "COMPS_MAX_PATH_B_DIVERGENCE", 0.25)

        if path_b_resell and path_b_resell > 0 and median_price > 0:
            divergence_pct = abs(median_price - path_b_resell) / float(path_b_resell)
            if divergence_pct > max_div:
                is_divergent = True
                # Favor conservative valuation
                conservative_resell = min(conservative_resell, path_b_resell)
                logger.debug(
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
            quick_sale_price=quick_sale_price,
        )
