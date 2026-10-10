"""Live craft cost calculator based on real-time Bazaar material prices."""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.constants.recipes import get_recipe

logger = logging.getLogger(__name__)


def calculate_craft_cost(
    internal_id: str,
    bazaar_cache: Any,
    ah_cache: Any = None,
    _visited: Optional[set[str]] = None,
) -> Optional[int]:
    """
    Calculate the total raw material craft cost for an item using real-time
    Bazaar instant-buy prices and optional Auction House base item baselines.

    Args:
        internal_id: The item's SkyBlock internal ID.
        bazaar_cache: A BazaarCache instance or dict mapping product IDs to prices.
        ah_cache: Optional FlipEvaluator, price dict, or cache with get_base_lbin.
        _visited: Set of visited internal IDs for recursion cycle detection.

    Returns:
        int: Total craft cost in coins, or None if the item is uncraftable,
             bazaar cache is unavailable, or any required ingredient price is missing/zero.
    """
    if not internal_id or bazaar_cache is None:
        return None

    clean_id = internal_id.strip().upper()
    if _visited is None:
        _visited = set()
    if clean_id in _visited:
        return None
    _visited.add(clean_id)

    recipe = get_recipe(clean_id)
    if not recipe:
        return None

    total_cost: float = 0.0

    for ingredient_id, quantity in recipe.items():
        if quantity <= 0:
            continue

        unit_price: Optional[float] = None

        # 1. First attempt: Bazaar instant-buy price
        if hasattr(bazaar_cache, "get_buy_price"):
            p = bazaar_cache.get_buy_price(ingredient_id)
            if p is not None and p > 0:
                unit_price = float(p)
        elif isinstance(bazaar_cache, dict):
            raw_val = bazaar_cache.get(ingredient_id)
            if raw_val is None:
                raw_val = bazaar_cache.get(ingredient_id.strip().upper())

            if isinstance(raw_val, (int, float)) and raw_val > 0:
                unit_price = float(raw_val)
            elif isinstance(raw_val, dict):
                p = raw_val.get("buyPrice") or raw_val.get("buy_price") or raw_val.get("price")
                if p is not None and float(p) > 0:
                    unit_price = float(p)

        # 2. Second attempt: Auction House base item lookup
        if (unit_price is None or unit_price <= 0) and ah_cache is not None:
            if hasattr(ah_cache, "get_base_lbin"):
                ah_val = ah_cache.get_base_lbin(ingredient_id)
                if ah_val is not None and ah_val > 0:
                    unit_price = float(ah_val)
            elif isinstance(ah_cache, dict):
                raw_ah = ah_cache.get(ingredient_id) or ah_cache.get(ingredient_id.strip().upper())
                if isinstance(raw_ah, (int, float)) and raw_ah > 0:
                    unit_price = float(raw_ah)
                elif isinstance(raw_ah, list) and raw_ah:
                    unit_price = float(raw_ah[0])

        # 3. Third attempt: Recursive craft cost if ingredient is craftable
        if (unit_price is None or unit_price <= 0) and get_recipe(ingredient_id):
            sub_cost = calculate_craft_cost(
                ingredient_id,
                bazaar_cache,
                ah_cache=ah_cache,
                _visited=_visited,
            )
            if sub_cost is not None and sub_cost > 0:
                unit_price = float(sub_cost)

        # Fail-safe: if any ingredient is missing or has a non-positive price, abort calculation
        if unit_price is None or unit_price <= 0:
            logger.debug(
                "Missing or non-positive price for ingredient '%s' in recipe '%s'",
                ingredient_id,
                clean_id,
            )
            return None

        # Bazaar Shock Guard: if enabled and ingredient price is shocked, abort calculation
        if hasattr(bazaar_cache, "config") and getattr(bazaar_cache.config, "ENABLE_BAZAAR_SHOCK_GUARD", False):
            if hasattr(bazaar_cache, "is_price_shocked") and bazaar_cache.is_price_shocked(ingredient_id):
                logger.debug(
                    "Ingredient '%s' is price shocked in Bazaar for recipe '%s'",
                    ingredient_id,
                    clean_id,
                )
                return None

        total_cost += float(quantity) * unit_price

    return int(round(total_cost))
