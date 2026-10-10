"""Queue-depth and price-aligned hold time modeling for capital efficiency."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_capital_efficiency(
    net_profit: int,
    buy_price: int,
    daily_velocity: Optional[float] = None,
    queue_depth: int = 0,
    min_hold_hours: Optional[float] = None,
    max_hold_hours: float = 48.0,
    enable_queue_depth: bool = True,
    empirical_median_time_to_sell_s: Optional[float] = None,
    empirical_sample_count: int = 0,
) -> tuple[float, float]:
    """
    Calculate estimated hold duration (in hours) and capital efficiency (% return per hour).

    Phase 5 Valuation Realism:
    - Use bucket's empirical median time_to_sell when it has >= 10 samples (clamped to [0.5, 48] h).
    - Otherwise, expected_wait = (listings in bucket priced below target + 1) / V,
      where V = bucket sales per hour from velocity.py, clamped to [0.5, 48] h.
    - Fall back to old formula (V=0.05 sales/hr fallback) when V is unknown.

    Returns:
        tuple[float, float]: (estimated_hold_hours, capital_efficiency_pct_per_hr)
    """
    effective_depth = max(0, queue_depth) if enable_queue_depth else 0

    if (
        empirical_sample_count >= 10
        and empirical_median_time_to_sell_s is not None
        and empirical_median_time_to_sell_s > 0
    ):
        raw_hold = empirical_median_time_to_sell_s / 3600.0
        min_h = float(min_hold_hours) if min_hold_hours is not None else 0.5
        estimated_hold_hours = max(min_h, min(max_hold_hours, raw_hold))
    elif daily_velocity is not None and daily_velocity > 0:
        hourly_v = daily_velocity / 24.0
        raw_hold = (effective_depth + 1) / hourly_v
        min_h = float(min_hold_hours) if min_hold_hours is not None else 0.5
        estimated_hold_hours = max(min_h, min(max_hold_hours, raw_hold))
    else:
        # Fall back to old formula when V is unknown
        hourly_v = 0.05
        min_h = float(min_hold_hours) if min_hold_hours is not None else (0.25 if effective_depth > 0 else 0.5)
        raw_hold = (effective_depth + 1) / hourly_v
        estimated_hold_hours = max(min_h, min(max_hold_hours, raw_hold))

    if buy_price <= 0 or estimated_hold_hours <= 0:
        capital_efficiency = 0.0
    else:
        capital_efficiency = (float(net_profit) / (estimated_hold_hours * float(buy_price))) * 100.0

    return (round(estimated_hold_hours, 2), round(capital_efficiency, 2))
