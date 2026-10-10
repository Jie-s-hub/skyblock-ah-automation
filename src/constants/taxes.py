"""Hypixel SkyBlock Auction House Tax & Fee Calculations.

Single source of truth for creation fees, collection taxes, net revenues, and tax cliff optimization.
"""

from __future__ import annotations

import math
from typing import NamedTuple, TypedDict

# VERIFY against the wiki (Auction House and Derpy pages)
# Hypixel Auction House Fee Schedule Table:
# -----------------------------------------------------------------------------------------
# Price Tier                   | Creation Fee Rate | Collection Fee (Normal) | Derpy Mayor
# -----------------------------------------------------------------------------------------
# 0 to 999,999                 | 1.0%              | 0.0%                    | 0.0%
# 1,000,000 to 9,999,999       | 1.0%              | 1.0%                    | 4.0%
# 10,000,000 to 99,999,999     | 2.0%              | 1.0%                    | 4.0%
# >= 100,000,000               | 2.5%              | 1.0%                    | 4.0%
# -----------------------------------------------------------------------------------------
# Note: All fees are floored to whole coins. Creation fee applies to the entire price.
FEE_SCHEDULE = [
    {
        "tier_name": "SUB_1M",
        "min_price": 0,
        "max_price": 999_999,
        "creation_rate": 0.01,
        "collection_rate": 0.0,
        "derpy_collection_rate": 0.0,
    },
    {
        "tier_name": "1M_TO_10M",
        "min_price": 1_000_000,
        "max_price": 9_999_999,
        "creation_rate": 0.01,
        "collection_rate": 0.01,
        "derpy_collection_rate": 0.04,
    },
    {
        "tier_name": "10M_TO_100M",
        "min_price": 10_000_000,
        "max_price": 99_999_999,
        "creation_rate": 0.02,
        "collection_rate": 0.01,
        "derpy_collection_rate": 0.04,
    },
    {
        "tier_name": "OVER_100M",
        "min_price": 100_000_000,
        "max_price": float("inf"),
        "creation_rate": 0.025,
        "collection_rate": 0.01,
        "derpy_collection_rate": 0.04,
    },
]


def creation_fee(price: int) -> int:
    """
    Calculate Hypixel AH listing creation fee floored to whole coins.

    Tier cliffs (flat % applied to the entire price):
    - price < 10,000,000: 1.0%
    - 10,000,000 <= price < 100,000,000: 2.0%
    - price >= 100,000,000: 2.5%
    """
    if price <= 0:
        return 0
    if price < 10_000_000:
        return math.floor(price * 0.01)
    elif price < 100_000_000:
        return math.floor(price * 0.02)
    else:
        return math.floor(price * 0.025)


def collection_fee(
    price: int,
    *,
    derpy_active: bool = False,
    derpy_rate: float = 0.04,
) -> int:
    """
    Calculate Hypixel AH collection fee floored to whole coins.
    - price < 1,000,000: 0
    - price >= 1,000,000: 1.0% normal, or derpy_rate (default 4.0%) when Derpy is mayor.
    """
    if price < 1_000_000:
        return 0
    rate = derpy_rate if derpy_active else 0.01
    return math.floor(price * rate)


def total_fee(
    price: int,
    *,
    derpy_active: bool = False,
    derpy_rate: float = 0.04,
) -> int:
    """Total fees deducted (creation_fee + collection_fee)."""
    if price <= 0:
        return 0
    return creation_fee(price) + collection_fee(price, derpy_active=derpy_active, derpy_rate=derpy_rate)


def net_revenue(
    price: int,
    *,
    derpy_active: bool = False,
    derpy_rate: float = 0.04,
) -> int:
    """
    Calculate net revenue after deducting creation and collection fees.
    net_revenue(price, *, derpy_active) =
        price - creation_fee(price) - collection_fee(price, derpy_active)
    """
    if price <= 0:
        return 0
    return price - creation_fee(price) - collection_fee(price, derpy_active=derpy_active, derpy_rate=derpy_rate)


# Backwards compatibility alias
calculate_net_revenue = net_revenue


class BestResaleResult(NamedTuple):
    """Result container for tax cliff optimized resale."""
    net_revenue: int
    list_price: int

    @property
    def net(self) -> int:
        return self.net_revenue

    @property
    def price(self) -> int:
        return self.list_price


def best_resale_net(
    target: int,
    *,
    derpy_active: bool = False,
    derpy_rate: float = 0.04,
) -> BestResaleResult:
    """
    Compare listing at target vs at (boundary - 1) for each boundary at or below target (1M, 10M, 100M).
    Returns the higher net and the list price.
    """
    if target <= 0:
        return BestResaleResult(0, 0)

    best_price = target
    best_net = net_revenue(target, derpy_active=derpy_active, derpy_rate=derpy_rate)

    boundaries = (1_000_000, 10_000_000, 100_000_000)
    for b in boundaries:
        if target >= b:
            candidate_price = b - 1
            candidate_net = net_revenue(candidate_price, derpy_active=derpy_active, derpy_rate=derpy_rate)
            if candidate_net > best_net:
                best_net = candidate_net
                best_price = candidate_price

    return BestResaleResult(best_net, best_price)


# ---------------------------------------------------------------------------
# Compatibility & Legacy Wrappers
# ---------------------------------------------------------------------------

class AHFeeBreakdown(TypedDict):
    listing_fee: int
    collection_tax: int
    total_deductions: int
    net_revenue: int


def calculate_ah_fees(
    price: int,
    derpy: bool = False,
    collection_tax_enabled: bool = True,
    collection_tax_min_price: int = 1_000_000,
) -> AHFeeBreakdown:
    """Legacy helper returning full fee breakdown dictionary."""
    if price <= 0:
        return {
            "listing_fee": 0,
            "collection_tax": 0,
            "total_deductions": 0,
            "net_revenue": 0,
        }

    c_fee = creation_fee(price)
    col_fee = collection_fee(price, derpy_active=derpy) if collection_tax_enabled else 0
    tot = c_fee + col_fee
    return {
        "listing_fee": c_fee,
        "collection_tax": col_fee,
        "total_deductions": tot,
        "net_revenue": price - tot,
    }


def optimize_target_resell(suggested_price: int, derpy: bool = False) -> int:
    """Clamps target resale prices out of dead zones using best_resale_net."""
    return best_resale_net(suggested_price, derpy_active=derpy).list_price


def calculate_progressive_ah_tax(resell_price: int) -> int:
    """Backward compatibility wrapper returning total fees."""
    return total_fee(resell_price, derpy_active=False)


def calculate_progressive_net_revenue(resell_price: int) -> int:
    """Backward compatibility wrapper returning net revenue."""
    return net_revenue(resell_price, derpy_active=False)


def calculate_progressive_net_profit(resell_price: int, buy_price: int) -> int:
    """Backward compatibility wrapper returning net profit after purchase cost."""
    return net_revenue(resell_price, derpy_active=False) - buy_price
