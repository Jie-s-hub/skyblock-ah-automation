"""Constants, pairings, and valuation rules for Crimson Isle and Nether gear attributes."""

from __future__ import annotations

from typing import Optional

# Recognized God Roll attribute combinations
# Mage: Mana Pool + Mana Regeneration
# Slayer / Combat: Magic Find + Veteran
# Tank / Survivability: Vitality + Dominance
GOD_ROLL_COMBINATIONS: dict[str, set[str]] = {
    "MAGE": {"mana_pool", "mana_regeneration"},
    "SLAYER": {"magic_find", "veteran"},
    "SURVIVABILITY": {"vitality", "dominance"},
}

# Display names for SkyBlock attributes
ATTRIBUTE_DISPLAY_NAMES: dict[str, str] = {
    "mana_pool": "Mana Pool",
    "mana_regeneration": "Mana Regeneration",
    "magic_find": "Magic Find",
    "veteran": "Veteran",
    "vitality": "Vitality",
    "dominance": "Dominance",
    "breeze": "Breeze",
    "speed": "Speed",
    "fortitude": "Fortitude",
    "lifeline": "Lifeline",
    "mending": "Mending",
    "undead": "Undead",
    "arachno": "Arachno",
    "blazing": "Blazing",
    "combo": "Combo",
    "elite": "Elite",
    "ignition": "Ignition",
    "midas_touch": "Midas Touch",
    "infection": "Infection",
    "warrior": "Warrior",
    "deadeye": "Deadeye",
    "experience": "Experience",
    "hunter": "Hunter",
    "fishing_experience": "Fishing Experience",
    "trophy_hunter": "Trophy Hunter",
}

# Base estimated market value floors for God Roll pairings
GOD_ROLL_BASE_VALUES: dict[str, int] = {
    "MAGE": 120_000_000,  # Mana Pool + Mana Regen (highest demand Kuudra/Mage staple)
    "SLAYER": 80_000_000,  # Magic Find + Veteran (Slayer progression)
    "SURVIVABILITY": 50_000_000,  # Vitality + Dominance (Tank/Kuudra)
}
DEFAULT_GOD_ROLL_FLOOR = 50_000_000


def to_roman_numeral(num: int) -> str:
    """Convert integer tier 1-10 to Roman numeral."""
    if num <= 0:
        return str(num)
    val = [10, 9, 5, 4, 1]
    syb = ["X", "IX", "V", "IV", "I"]
    roman_num = ""
    i = 0
    rem = num
    while rem > 0 and i < len(val):
        for _ in range(rem // val[i]):
            roman_num += syb[i]
            rem -= val[i]
        i += 1
    return roman_num or str(num)


def check_god_roll(attributes: dict[str, int]) -> tuple[bool, Optional[str], Optional[set[str]]]:
    """
    Check if an item's attributes contain a recognized God Roll combination.

    Returns:
        tuple[bool, Optional[str], Optional[set[str]]]: (is_god_roll, god_roll_type, matched_pair)
    """
    if not attributes or len(attributes) < 2:
        return False, None, None

    attr_keys = {k.strip().lower() for k in attributes.keys()}
    for gr_name, gr_set in GOD_ROLL_COMBINATIONS.items():
        if gr_set.issubset(attr_keys):
            return True, gr_name, gr_set

    return False, None, None


def format_attributes(attributes: dict[str, int]) -> str:
    """
    Format attributes with Roman numerals and flag God Rolls.
    Example: '✦ Mana Pool V  ✦ Mana Regeneration V (GOD ROLL)'
    """
    if not attributes:
        return "None"

    parts: list[str] = []
    for name, lvl in sorted(attributes.items()):
        clean_name = name.strip().lower()
        disp = ATTRIBUTE_DISPLAY_NAMES.get(clean_name, clean_name.replace("_", " ").title())
        roman = to_roman_numeral(lvl)
        parts.append(f"✦ {disp} {roman}")

    text = "  ".join(parts)
    is_gr, _, _ = check_god_roll(attributes)
    if is_gr:
        text += " (GOD ROLL)"
    return text


def solve_attribute_tier_dp(
    tier: int,
    active_tier_listings: dict[int, list[int]],
    fusion_cost_per_tier: Optional[dict[int, int]] = None,
) -> int:
    """
    Bottom-up Dynamic Programming solver for optimal attribute sourcing:
    C(t) = min(LBIN(t), C(t-1)_1 + C(t-1)_2 + FusionCost(t))
    where C(t-1)_1 and C(t-1)_2 are the first and second cheapest ways to source tier t-1.
    """
    if tier <= 1:
        t1_prices = active_tier_listings.get(1, [])
        return t1_prices[0] if t1_prices else 5_000_000

    dp_cheapest: dict[int, tuple[int, int]] = {}

    t1_list = sorted(active_tier_listings.get(1, []))
    if len(t1_list) >= 2:
        dp_cheapest[1] = (t1_list[0], t1_list[1])
    elif len(t1_list) == 1:
        dp_cheapest[1] = (t1_list[0], int(t1_list[0] * 1.05))
    else:
        dp_cheapest[1] = (5_000_000, 5_250_000)

    for t in range(2, tier + 1):
        prev_c1, prev_c2 = dp_cheapest[t - 1]
        fusion_fee = (fusion_cost_per_tier or {}).get(t, 1_000_000 * t)
        craft_cost = prev_c1 + prev_c2 + fusion_fee

        active_t = sorted(active_tier_listings.get(t, []))
        if active_t:
            lbin_t1 = active_t[0]
            lbin_t2 = active_t[1] if len(active_t) > 1 else int(active_t[0] * 1.05)
            best_1 = min(lbin_t1, craft_cost)
            best_2 = min(lbin_t2, craft_cost + int(prev_c1 * 0.05))
        else:
            best_1 = craft_cost
            best_2 = craft_cost + int(prev_c1 * 0.05)

        dp_cheapest[t] = (best_1, best_2)

    return dp_cheapest[tier][0]


def calculate_attribute_estimated_value(
    attributes: dict[str, int],
    base_lbin: Optional[int] = None,
    active_attribute_floors: Optional[dict[str, dict[int, list[int]]]] = None,
) -> tuple[bool, Optional[str], int]:
    """
    Compute estimated market worth for attribute combinations using dynamic programming.

    If the item is a God Roll:
    - Compares against attribute-specific floor rather than standard clean trash LBIN.
    - Scales by attribute tier levels (e.g. tier 5+ grants bonus value).
    - If base clean LBIN is available and exceeds the floor, applies an attribute multiplier.
    - Caps attribute component at optimal sourcing DP cost C(t).

    Returns:
        tuple[bool, Optional[str], int]: (is_god_roll, god_roll_type, estimated_market_value)
    """
    is_gr, gr_type, matched_set = check_god_roll(attributes)
    if not is_gr or not gr_type or not matched_set:
        return False, None, 0

    floor_val = GOD_ROLL_BASE_VALUES.get(gr_type, DEFAULT_GOD_ROLL_FLOOR)

    norm_attrs: dict[str, int] = {}
    for k, v in attributes.items():
        try:
            norm_attrs[str(k).strip().lower()] = int(v)
        except (ValueError, TypeError):
            pass

    tier_sum = sum(norm_attrs.get(k, 1) for k in matched_set)
    tier_bonus = max(0, tier_sum - 2) * 5_000_000

    estimated_value = floor_val + tier_bonus

    # If base clean LBIN exists (e.g. 50M), ensure God Roll multiplier of at least 2.5x base
    if base_lbin and base_lbin > 0:
        multiplier_val = int(base_lbin * 2.5)
        estimated_value = max(estimated_value, multiplier_val)

    # Cap single/paired attribute valuation at DP optimal sourcing cost if book available
    if active_attribute_floors:
        total_dp_cap = 0
        for attr in matched_set:
            t = norm_attrs.get(attr, 1)
            book = active_attribute_floors.get(attr, {})
            if book:
                total_dp_cap += solve_attribute_tier_dp(t, book)
        if total_dp_cap > 0 and base_lbin:
            estimated_value = min(estimated_value, base_lbin + total_dp_cap)

    return True, gr_type, estimated_value
