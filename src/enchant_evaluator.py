"""Dedicated enchantment valuation engine with progression safeguards and tiered compounding."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Enchants that level through player activity / counters rather than anvil combination.
# Must resolve strictly to 1 * Price(Tier 1); NEVER multiply by level or power of 2.
PROGRESSION_ENCHANTS: set[str] = {
    "champion",
    "compact",
    "cultivating",
    "expertise",
    "hecatomb",
}

# High-tier enchants where Tier 7 cannot be combined from lower tiers, but strictly compounds from Tier 6 (2 * T6).
HIGH_TIER_COMPOUND_ENCHANTS: set[str] = {
    "growth",
    "protection",
    "sharpness",
    "power",
    "giant_killer",
    "critical",
}

# Known ultimate enchantments stems
ULTIMATE_ENCHANTS: set[str] = {
    "soul_eater",
    "wisdom",
    "legion",
    "chimera",
    "one_for_all",
    "swarm",
    "fatal_tempo",
    "rend",
    "combo",
    "bank",
    "ultimate_jerry",
    "last_stand",
    "no_pain_no_gain",
    "flash",
    "duplex",
    "inferno",
    "habanero_tactics",
    "ultimate_wise",
    "wise",
}


def _normalize_enchant_name(enchant_name: str) -> tuple[str, str, str]:
    """
    Normalize enchantment name into:
    (clean_upper_base, norm_lower_base, norm_lower_no_ult).
    """
    clean_upper = str(enchant_name).strip().upper()
    if clean_upper.startswith("ENCHANTMENT_"):
        clean_upper = clean_upper[12:]

    # Strip trailing tier number if part of name string (e.g. "GROWTH_6" -> "GROWTH")
    parts = clean_upper.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        clean_upper = parts[0]

    norm_lower = clean_upper.lower()
    if norm_lower.startswith("ultimate_"):
        norm_no_ult = norm_lower[9:]
    else:
        norm_no_ult = norm_lower

    return clean_upper, norm_lower, norm_no_ult


def get_enchant_bazaar_candidates(enchant_name: str, tier: int) -> list[str]:
    """
    Generate candidate Bazaar product IDs for an enchanted book at a specific tier level.
    """
    clean_upper, norm_lower, norm_no_ult = _normalize_enchant_name(enchant_name)

    name_no_ult = clean_upper[9:] if clean_upper.startswith("ULTIMATE_") else clean_upper
    name_with_ult = clean_upper if clean_upper.startswith("ULTIMATE_") else f"ULTIMATE_{clean_upper}"

    if norm_no_ult == "one_for_all":
        return [f"ENCHANTMENT_ULTIMATE_ONE_FOR_ALL_{tier}", "ENCHANTMENT_ULTIMATE_ONE_FOR_ALL_1"]

    candidates: list[str] = []
    if (
        clean_upper.startswith("ULTIMATE_")
        or norm_lower in ULTIMATE_ENCHANTS
        or norm_no_ult in ULTIMATE_ENCHANTS
    ):
        candidates.append(f"ENCHANTMENT_{name_with_ult}_{tier}")
        candidates.append(f"ENCHANTMENT_{name_no_ult}_{tier}")
    else:
        candidates.append(f"ENCHANTMENT_{clean_upper}_{tier}")
        candidates.append(f"ENCHANTMENT_{name_with_ult}_{tier}")
        candidates.append(f"ENCHANTMENT_{name_no_ult}_{tier}")

    # Remove duplicates preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            deduped.append(cand)
    return deduped


def _get_bazaar_buy_price(bazaar_cache: Any, product_id: str) -> Optional[float]:
    """Lookup buy price safely from BazaarCache or dict-like mapping."""
    if bazaar_cache is None:
        return None
    if hasattr(bazaar_cache, "get_buy_price"):
        return bazaar_cache.get_buy_price(product_id)
    if isinstance(bazaar_cache, dict):
        return bazaar_cache.get(product_id)
    if hasattr(bazaar_cache, "get"):
        return bazaar_cache.get(product_id)
    return None


def _lookup_tier_price(bazaar_cache: Any, enchant_name: str, tier: int) -> tuple[str, int]:
    """Check candidate Bazaar product IDs for an enchantment tier and return (product_id, price)."""
    candidates = get_enchant_bazaar_candidates(enchant_name, tier)
    for cand in candidates:
        price = _get_bazaar_buy_price(bazaar_cache, cand)
        if price is not None and price > 0:
            return cand, int(round(price))
    return "", 0


def _evaluate_enchant_bazaar_cost(
    bazaar_cache: Any,
    enchant_name: str,
    level: int,
) -> tuple[str, int]:
    """
    Internal calculation core implementing the four economic rules:
    1. Progression Enchants: Strictly 1 * Price(Tier 1). No multiplier.
    2. Exact Tier Lookup: Direct Bazaar price without multiplying by level or power of 2.
    3. High-Tier Compounding: Tier 7 compounds strictly as 2 * Price(Tier 6).
    4. Downward Anvil Compounding: Search downward for highest available base tier B < L.
       Cost = 2^(L - B) * Price(Tier B).
    """
    if not bazaar_cache or level <= 0 or not enchant_name:
        return "", 0

    clean_upper, norm_lower, norm_no_ult = _normalize_enchant_name(enchant_name)

    # 1. Progression Enchants Gate
    # Champion, Compact, Cultivating, Expertise, Hecatomb level through player activity.
    if norm_lower in PROGRESSION_ENCHANTS or norm_no_ult in PROGRESSION_ENCHANTS:
        book_id, t1_price = _lookup_tier_price(bazaar_cache, clean_upper, 1)
        if t1_price > 0:
            return book_id, t1_price
        return "", 0

    # 2. Direct Bazaar Product Lookup (ENCHANTMENT_{NAME}_{LEVEL})
    exact_id, exact_price = _lookup_tier_price(bazaar_cache, clean_upper, level)
    if exact_price > 0:
        return exact_id, exact_price

    # 3. High-Tier Compounding (T6 to T7)
    # For growth, protection, sharpness, power, giant_killer, critical:
    # Level 7 compounds as 2 * Price(Tier 6).
    if level == 7 and (norm_lower in HIGH_TIER_COMPOUND_ENCHANTS or norm_no_ult in HIGH_TIER_COMPOUND_ENCHANTS):
        t6_id, t6_price = _lookup_tier_price(bazaar_cache, clean_upper, 6)
        if t6_price > 0:
            return t6_id, t6_price * 2
        return "", 0

    # 4. Standard Anvil Compounding Fallback
    # Search downward for the highest available base tier B < L.
    # Cost = 2^(L - B) * Price(Tier B).
    if level > 1:
        for base_tier in range(level - 1, 0, -1):
            cand_id, base_price = _lookup_tier_price(bazaar_cache, clean_upper, base_tier)
            if base_price > 0:
                compounded_cost = base_price * (2 ** (level - base_tier))
                return cand_id, int(round(compounded_cost))

    return "", 0


def get_enchant_bazaar_cost(
    arg1: Any,
    arg2: Any = "",
    arg3: Any = 1,
    bazaar_cache: Any = None,
) -> tuple[str, int]:
    """
    Resolve enchantment Bazaar cost and product ID with progression safeguards,
    exact tier lookup, T6->T7 compounding, and downward anvil compounding.

    Supports calling patterns:
    - get_enchant_bazaar_cost(bazaar_cache, enchant_name, level)
    - get_enchant_bazaar_cost(enchant_name, level, bazaar_cache)
    """
    if isinstance(arg1, str):
        enchant_name = arg1
        level = int(arg2)
        cache = bazaar_cache if bazaar_cache is not None else arg3
    else:
        cache = arg1
        enchant_name = str(arg2)
        level = int(arg3)

    return _evaluate_enchant_bazaar_cost(cache, enchant_name, level)


def get_enchant_bazaar_price(
    arg1: Any,
    arg2: Any = "",
    arg3: Any = 1,
    bazaar_cache: Any = None,
) -> int:
    """
    Resolve enchantment Bazaar cost as an integer price.

    Supports calling patterns:
    - get_enchant_bazaar_price(bazaar_cache, enchant_name, level)
    - get_enchant_bazaar_price(enchant_name, level, bazaar_cache)
    - get_enchant_bazaar_price(enchant_name, level, bazaar_cache=bazaar_cache)
    """
    _, price = get_enchant_bazaar_cost(arg1, arg2, arg3, bazaar_cache=bazaar_cache)
    return price
