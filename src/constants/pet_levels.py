"""Pet experience curves, level calculation, held item valuations, and level premiums."""

from __future__ import annotations

from typing import Any, Optional

# Standard cumulative pet XP thresholds to reach level 100 by rarity tier
# Based on Hypixel SkyBlock pet leveling mechanics
PET_MAX_XP: dict[str, float] = {
    "COMMON": 5_624_785.0,
    "UNCOMMON": 8_644_220.0,
    "RARE": 12_626_665.0,
    "EPIC": 18_608_500.0,
    "LEGENDARY": 25_353_230.0,
    "MYTHIC": 25_353_230.0,
}

# Golden Dragon reaches Level 200 requiring ~210,353,230 cumulative XP
GDRAG_MAX_XP = 210_353_230.0
GDRAG_LEVEL_100_XP = 25_353_230.0
GDRAG_XP_PER_POST_100_LEVEL = (GDRAG_MAX_XP - GDRAG_LEVEL_100_XP) / 100.0  # 1,850,000 XP/level


def _build_cumulative_table(max_xp: float, levels: int = 100) -> list[float]:
    """
    Build a smooth progressive cumulative XP curve from Level 1 to `levels`.
    Uses quadratic scaling matching SkyBlock's pet leveling progression.
    """
    table = [0.0]
    total_weight = sum(i**1.8 for i in range(1, levels))
    cumulative = 0.0
    for i in range(1, levels):
        step = (i**1.8 / total_weight) * max_xp
        cumulative += step
        table.append(cumulative)
    return table


# Precompute cumulative XP tables per tier for fast binary/linear search
PET_CUMULATIVE_XP_TABLES: dict[str, list[float]] = {
    tier: _build_cumulative_table(max_xp, 100) for tier, max_xp in PET_MAX_XP.items()
}


def calculate_pet_level(pet_type: str, tier: str, exp: float) -> int:
    """
    Calculate the integer level of a pet given its species, rarity, and total XP.

    - Standard pets: Levels 1-100.
    - Golden Dragon: Levels 1-200.
    """
    clean_type = pet_type.strip().upper()
    clean_tier = tier.strip().upper()
    if clean_tier not in PET_MAX_XP:
        clean_tier = "LEGENDARY"

    if exp <= 0:
        return 1

    # Special handling for Golden Dragon (Levels 1-200)
    if clean_type == "GOLDEN_DRAGON":
        if exp >= GDRAG_MAX_XP:
            return 200
        if exp >= GDRAG_LEVEL_100_XP:
            extra_levels = int((exp - GDRAG_LEVEL_100_XP) / GDRAG_XP_PER_POST_100_LEVEL)
            return min(200, max(100, 100 + extra_levels))
        # Levels 1 to 100 follow standard Legendary table
        table = PET_CUMULATIVE_XP_TABLES["LEGENDARY"]
        for lvl in range(100, 0, -1):
            if exp >= table[lvl - 1]:
                return lvl
        return 1

    # Standard pets (Levels 1-100)
    max_xp = PET_MAX_XP[clean_tier]
    if exp >= max_xp:
        return 100

    table = PET_CUMULATIVE_XP_TABLES[clean_tier]
    for lvl in range(100, 0, -1):
        if exp >= table[lvl - 1]:
            return lvl
    return 1


# Catalog of valuable pet items with clean display names and fallback base market values
VALUABLE_PET_ITEMS: dict[str, dict[str, Any]] = {
    "PET_ITEM_TIER_BOOST": {"name": "Tier Boost", "base_price": 50_000_000},
    "PET_ITEM_MINOS_RELIC": {"name": "Minos Relic", "base_price": 75_000_000},
    "PET_ITEM_QUICK_CLAW": {"name": "Quick Claw", "base_price": 60_000_000},
    "PET_ITEM_EXP_SHARE": {"name": "Exp Share", "base_price": 1_500_000},
    "PET_ITEM_LUCKY_CLOVER": {"name": "Lucky Clover", "base_price": 8_000_000},
    "PET_ITEM_SPOOKY_CUPCAKE": {"name": "Spooky Cupcake", "base_price": 2_000_000},
    "PET_ITEM_ALL_SKILLS_BOOST_COMMON": {"name": "All Skills Boost", "base_price": 1_000_000},
    "PET_ITEM_REMEDY": {"name": "Antique Remedies", "base_price": 10_000_000},
    "PET_ITEM_DWARF_TURTLE_SHELMET": {"name": "Dwarf Turtle Shelmet", "base_price": 5_000_000},
    "PET_ITEM_CROCHET_TIGER_PLUSHIE": {"name": "Crochet Tiger Plushie", "base_price": 6_000_000},
}


def get_pet_held_item_info(
    held_item_id: Optional[str],
    bazaar_cache: Optional[Any] = None,
) -> tuple[str, int]:
    """
    Lookup display name and estimated/Bazaar value for a pet's held item.

    Returns:
        tuple[str, int]: (display_name, value_coins)
    """
    if not held_item_id:
        return ("None", 0)

    clean_id = str(held_item_id).strip().upper()
    if clean_id in ("NONE", "", "NULL"):
        return ("None", 0)

    # Check Bazaar first if cache is provided
    if bazaar_cache is not None:
        bz_price = bazaar_cache.get_buy_price(clean_id)
        if bz_price and bz_price > 0:
            info = VALUABLE_PET_ITEMS.get(clean_id)
            name = info["name"] if info else clean_id.replace("PET_ITEM_", "").replace("_", " ").title()
            return (name, int(bz_price))

    # Fallback to static catalog
    if clean_id in VALUABLE_PET_ITEMS:
        item_info = VALUABLE_PET_ITEMS[clean_id]
        return (item_info["name"], int(item_info["base_price"]))

    clean_display = clean_id.replace("PET_ITEM_", "").replace("_", " ").title()
    return (clean_display, 0)


# Max-level premiums for meta pets (added on top of Level 1 base LBIN)
MAX_LEVEL_PREMIUMS: dict[str, int] = {
    "GOLDEN_DRAGON": 400_000_000,  # Level 200 premium
    "ENDER_DRAGON": 80_000_000,  # Level 100 premium
    "BABY_YETI": 15_000_000,
    "BLACK_CAT": 15_000_000,
    "SHEEP": 10_000_000,
    "SCATHA": 50_000_000,
}
DEFAULT_LEGENDARY_LVL_100_PREMIUM = 20_000_000
DEFAULT_EPIC_LVL_100_PREMIUM = 10_000_000


def get_pet_level_premium(pet_type: str, tier: str, level: int) -> int:
    """
    Compute level premium for max-level (or near max-level) pets over Level 1 base LBIN.
    """
    clean_type = pet_type.strip().upper()
    clean_tier = tier.strip().upper()

    if clean_type == "GOLDEN_DRAGON":
        if level >= 200:
            return MAX_LEVEL_PREMIUMS["GOLDEN_DRAGON"]
        if level >= 150:
            return MAX_LEVEL_PREMIUMS["GOLDEN_DRAGON"] // 2
        return 0

    if level >= 100:
        if clean_type in MAX_LEVEL_PREMIUMS:
            return MAX_LEVEL_PREMIUMS[clean_type]
        if clean_tier in ("LEGENDARY", "MYTHIC"):
            return DEFAULT_LEGENDARY_LVL_100_PREMIUM
        if clean_tier == "EPIC":
            return DEFAULT_EPIC_LVL_100_PREMIUM
        return 0

    if level >= 90:
        # Partial premium for level 90+
        if clean_type in MAX_LEVEL_PREMIUMS:
            return MAX_LEVEL_PREMIUMS[clean_type] // 2
        if clean_tier in ("LEGENDARY", "MYTHIC"):
            return DEFAULT_LEGENDARY_LVL_100_PREMIUM // 2
        return 0

    return 0


# Data-driven pet max-level caps
PET_MAX_LEVEL_CAPS: dict[str, int] = {
    "GOLDEN_DRAGON": 200,
}


def get_pet_max_level(pet_type: str) -> int:
    """Return maximum attainable level for a pet species (default: 100)."""
    return PET_MAX_LEVEL_CAPS.get(pet_type.strip().upper(), 100)


def get_pet_level_bracket(pet_type: str, level: int) -> str:
    """
    Map pet level to its discrete valuation bracket:
    - Standard pets: 'L1', 'L2-20', 'L21-80', 'L81-99', 'L100'
    - 200-cap pets (e.g. Golden Dragon): 'L1', 'L2-20', 'L21-80', 'L81-99', 'L100', 'L101-199', 'L200'
    Level 1, level 100 and max level (200 where applicable) are their own brackets; never compare across brackets.
    """
    max_lvl = get_pet_max_level(pet_type)
    if level <= 1:
        return "L1"
    if level <= 20:
        return "L2-20"
    if level <= 80:
        return "L21-80"
    if level <= 99:
        return "L81-99"
    if level == 100:
        return "L100"
    if max_lvl > 100:
        if level < max_lvl:
            return f"L101-{max_lvl - 1}"
        return f"L{max_lvl}"
    return "L100"


def get_held_item_group(
    held_item_id: Optional[str],
    bazaar_cache: Optional[Any] = None,
    minor_threshold: int = 2_000_000,
) -> str:
    """
    Group pet held items:
    - 'none' for no held item
    - Distinct ID for high-value items (> minor_threshold, or PET_ITEM_TIER_BOOST)
    - 'minor' for low-value held items (<= minor_threshold) to prevent thin buckets
    """
    if not held_item_id:
        return "none"
    clean_id = str(held_item_id).strip().upper()
    if clean_id in ("NONE", "", "NULL"):
        return "none"
    if clean_id == "PET_ITEM_TIER_BOOST":
        return "PET_ITEM_TIER_BOOST"

    _, val = get_pet_held_item_info(clean_id, bazaar_cache=bazaar_cache)
    if val > minor_threshold:
        return clean_id
    return "minor"


def get_pet_bucket_key(
    decoded: dict[str, Any],
    minor_threshold: int = 2_000_000,
    bazaar_cache: Optional[Any] = None,
) -> tuple[str, str, bool, str, str]:
    """
    Compute 5-tuple discrete pet bucket key:
    (effective_internal_id, level_bracket, candy_used_bool, skin_str, held_item_group)
    """
    pet_type = str(decoded.get("pet_type", "")).strip().upper()
    effective_tier = str(decoded.get("effective_tier") or decoded.get("pet_tier", "COMMON")).strip().upper()
    effective_internal_id = f"PET_{pet_type}_{effective_tier}"

    level = int(decoded.get("pet_level", 1))
    bracket = get_pet_level_bracket(pet_type, level)

    candy_used = bool(int(decoded.get("pet_candy_used", 0)) > 0)

    skin_raw = decoded.get("pet_skin")
    skin = (
        str(skin_raw).strip().upper()
        if skin_raw and str(skin_raw).strip().upper() not in ("NONE", "NULL", "")
        else "none"
    )

    held_item = decoded.get("pet_held_item", "")
    held_group = get_held_item_group(held_item, bazaar_cache=bazaar_cache, minor_threshold=minor_threshold)

    return (effective_internal_id, bracket, candy_used, skin, held_group)


def is_pet_xp_in_window(
    cand_xp: float,
    ref_xp: float,
    window_pct: float = 25.0,
) -> bool:
    """
    Determine if ref_xp is within +/- window_pct of cand_xp.
    Never benchmarks a low-level pet against higher-XP listings.
    For early pets (cand_xp < 100,000 XP), applies +/- 50,000 absolute tolerance
    so fresh Level 1 pets (0 XP) can benchmark against other Level 1 pets.
    """
    if cand_xp < 100_000:
        max_allowed_xp = max(cand_xp * (1.0 + window_pct / 100.0), 50_000.0)
        return 0.0 <= ref_xp <= max_allowed_xp

    min_allowed = cand_xp * (1.0 - window_pct / 100.0)
    max_allowed = cand_xp * (1.0 + window_pct / 100.0)
    return min_allowed <= ref_xp <= max_allowed
