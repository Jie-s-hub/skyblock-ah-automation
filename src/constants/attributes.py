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


def calculate_attribute_estimated_value(
    attributes: dict[str, int],
    base_lbin: Optional[int] = None,
) -> tuple[bool, Optional[str], int]:
    """
    Compute estimated market worth for attribute combinations.

    If the item is a God Roll:
    - Compares against attribute-specific floor rather than standard clean trash LBIN.
    - Scales by attribute tier levels (e.g. tier 5+ grants bonus value).
    - If base clean LBIN is available and exceeds the floor, applies an attribute multiplier.

    Returns:
        tuple[bool, Optional[str], int]: (is_god_roll, god_roll_type, estimated_market_value)
    """
    is_gr, gr_type, matched_set = check_god_roll(attributes)
    if not is_gr or not gr_type or not matched_set:
        return False, None, 0

    floor_val = GOD_ROLL_BASE_VALUES.get(gr_type, DEFAULT_GOD_ROLL_FLOOR)

    # Calculate tier level bonus:
    # Baseline floor corresponds to Tier 1 pair (tier_sum == 2).
    # Higher tiers add +5M per additional tier level.
    tier_sum = sum(attributes.get(k, 1) for k in matched_set)
    tier_bonus = max(0, tier_sum - 2) * 5_000_000

    estimated_value = floor_val + tier_bonus

    # If base clean LBIN exists (e.g. 50M), ensure God Roll multiplier of at least 2.5x base
    if base_lbin and base_lbin > 0:
        multiplier_val = int(base_lbin * 2.5)
        estimated_value = max(estimated_value, multiplier_val)

    return True, gr_type, estimated_value
