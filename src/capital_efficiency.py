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
) -> tuple[float, float]:
    """
    Calculate estimated hold duration (in hours) and capital efficiency (% return per hour),
    directly aligned with active order book queue depth.

    Formula:
        Hourly Velocity: V = daily_velocity / 24.0 (fallback 0.05 if unknown or <= 0)
        Estimated Time to Sell:
            T_hold = max(min_hold_hours, min(max_hold_hours, (queue_depth + 1) / V))
            (Default min_hold_hours = 0.25h when queue_depth > 0, otherwise 0.5h; max 48.0h)
        Capital Efficiency Score (CES):
            (net_profit / (T_hold * buy_price)) * 100.0

    Returns:
        tuple[float, float]: (estimated_hold_hours, capital_efficiency_pct_per_hr)
    """
    if daily_velocity is not None and daily_velocity > 0:
        hourly_v = daily_velocity / 24.0
    else:
        # Conservative fallback assumption: 1.2 sales/day (0.05 sales/hr)
        hourly_v = 0.05

    effective_depth = max(0, queue_depth) if enable_queue_depth else 0
    if min_hold_hours is not None:
        min_h = float(min_hold_hours)
    else:
        min_h = 0.25 if effective_depth > 0 else 0.5

    raw_hold = (effective_depth + 1) / hourly_v
    estimated_hold_hours = max(min_h, min(max_hold_hours, raw_hold))

    if buy_price <= 0 or estimated_hold_hours <= 0:
        capital_efficiency = 0.0
    else:
        capital_efficiency = (float(net_profit) / (estimated_hold_hours * float(buy_price))) * 100.0

    return (round(estimated_hold_hours, 2), round(capital_efficiency, 2))
