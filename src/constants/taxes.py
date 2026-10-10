"""Hypixel SkyBlock Progressive Auction House Tax Calculations."""

from __future__ import annotations


import math
from typing import TypedDict


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
    """
    Computes exact Hypixel AH listing fee, collection tax, total deductions, and net revenue.
    """
    if price <= 0:
        return {
            "listing_fee": 0,
            "collection_tax": 0,
            "total_deductions": 0,
            "net_revenue": 0,
        }

    # 1. Listing fee bracket
    if price < 10_000_000:
        listing_rate = 0.01
    elif price <= 100_000_000:
        listing_rate = 0.02
    else:
        listing_rate = 0.025
    listing_fee = math.floor(price * listing_rate)

    # 2. Collection tax bracket with 1M protected floor
    claim_rate = 0.04 if derpy else 0.01
    if collection_tax_enabled and price > collection_tax_min_price:
        raw_tax = math.floor(price * claim_rate)
        collection_tax = min(raw_tax, price - collection_tax_min_price)
    else:
        collection_tax = 0

    total_deductions = listing_fee + collection_tax
    net_revenue = price - total_deductions

    return {
        "listing_fee": listing_fee,
        "collection_tax": collection_tax,
        "total_deductions": total_deductions,
        "net_revenue": net_revenue,
    }


def optimize_target_resell(suggested_price: int, derpy: bool = False) -> int:
    """
    Clamps target resale prices out of dead zones around 10M and 100M tax cliffs
    if listing just below the cliff boundary yields strictly greater net revenue.
    """
    if suggested_price <= 0:
        return 0

    current_net = calculate_ah_fees(suggested_price, derpy)["net_revenue"]

    # 10M Boundary Check (Dead zone: ~10.0M to ~10.15M)
    if 10_000_000 <= suggested_price < 10_150_000:
        cliff_net = calculate_ah_fees(9_999_999, derpy)["net_revenue"]
        if cliff_net >= current_net:
            return 9_999_999

    # 100M Boundary Check (Dead zone: ~100.0M to ~100.60M)
    if 100_000_000 < suggested_price < 100_600_000:
        cliff_net = calculate_ah_fees(99_999_999, derpy)["net_revenue"]
        if cliff_net >= current_net:
            return 99_999_999

    return suggested_price


def calculate_progressive_ah_tax(resell_price: int) -> int:
    """
    Calculate the progressive Auction House tax on a completed sale or target resell.

    Hypixel progressive tax schedule:
    - Resell < 10M: 1% listing fee
    - Resell 10M - 100M: 2% listing fee
    - Resell > 100M: 2% on total price + 1% collection fee on amount exceeding 100M
                     (Effective tax rate: ~2.5% to 3.0%)

    Args:
        resell_price: Total sale or listing price in coins.

    Returns:
        int: Total tax deducted in coins (rounded down / truncated to integer).
    """
    if resell_price <= 0:
        return 0

    if resell_price < 10_000_000:
        return int(resell_price * 0.01)
    elif resell_price <= 100_000_000:
        return int(resell_price * 0.02)
    else:
        base_tax = resell_price * 0.02
        excess_tax = (resell_price - 100_000_000) * 0.01
        return int(base_tax + excess_tax)


def calculate_progressive_net_revenue(resell_price: int) -> int:
    """
    Calculate net revenue received after deducting progressive AH tax.

    Args:
        resell_price: Total sale or listing price in coins.

    Returns:
        int: Net revenue in coins.
    """
    if resell_price <= 0:
        return 0
    return resell_price - calculate_progressive_ah_tax(resell_price)


def calculate_progressive_net_profit(resell_price: int, buy_price: int) -> int:
    """
    Calculate net profit earned after progressive AH tax and purchase cost.

    Args:
        resell_price: Total sale or listing price in coins.
        buy_price: Original purchase price in coins.

    Returns:
        int: Net realized or projected profit in coins.
    """
    return calculate_progressive_net_revenue(resell_price) - buy_price
