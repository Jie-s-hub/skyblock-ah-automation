"""Static lookup tables for dungeon star essence upgrade costs."""

from __future__ import annotations

# Exact per-star costs for star levels 1 through 5:
# Wither Armor: 100, 200, 350, 600, 1000 Wither Essence
WITHER_ARMOR_STAR_COSTS = [100, 200, 350, 600, 1000]
CRIMSON_ARMOR_STAR_COSTS = [100, 200, 350, 600, 1000]
WITHER_WEAPONS_STAR_COSTS = [100, 200, 350, 600, 1000]
DEFAULT_DUNGEON_STAR_COSTS = [50, 100, 150, 200, 250]

# Wither armor sets
WITHER_ARMOR_IDS = {
    "WITHER_HELMET",
    "WITHER_CHESTPLATE",
    "WITHER_LEGGINGS",
    "WITHER_BOOTS",
    "NECRON_HELMET",
    "NECRON_CHESTPLATE",
    "NECRON_LEGGINGS",
    "NECRON_BOOTS",
    "STORM_HELMET",
    "STORM_CHESTPLATE",
    "STORM_LEGGINGS",
    "STORM_BOOTS",
    "MAXOR_HELMET",
    "MAXOR_CHESTPLATE",
    "MAXOR_LEGGINGS",
    "MAXOR_BOOTS",
    "GOLDOR_HELMET",
    "GOLDOR_CHESTPLATE",
    "GOLDOR_LEGGINGS",
    "GOLDOR_BOOTS",
}

# Wither blade weapons
WITHER_WEAPON_IDS = {
    "HYPERION",
    "VALKYRIE",
    "SCYLLA",
    "ASTRAEA",
    "NECRON_BLADE",
}

# Crimson Isle kuudra sets
CRIMSON_ARMOR_PREFIXES = (
    "CRIMSON_",
    "AURORA_",
    "TERROR_",
    "FERVOR_",
    "HOLLOW_",
    "HOT_CRIMSON_",
    "HOT_AURORA_",
    "HOT_TERROR_",
    "HOT_FERVOR_",
    "HOT_HOLLOW_",
    "BURNING_CRIMSON_",
    "BURNING_AURORA_",
    "BURNING_TERROR_",
    "BURNING_FERVOR_",
    "BURNING_HOLLOW_",
    "FIERY_CRIMSON_",
    "FIERY_AURORA_",
    "FIERY_TERROR_",
    "FIERY_FERVOR_",
    "FIERY_HOLLOW_",
    "INFERNAL_CRIMSON_",
    "INFERNAL_AURORA_",
    "INFERNAL_TERROR_",
    "INFERNAL_FERVOR_",
    "INFERNAL_HOLLOW_",
)

# Dragon sets
DRAGON_ARMOR_PREFIXES = (
    "SUPERIOR_DRAGON_",
    "STRONG_DRAGON_",
    "WISE_DRAGON_",
    "YOUNG_DRAGON_",
    "OLD_DRAGON_",
    "PROTECTOR_DRAGON_",
    "UNSTABLE_DRAGON_",
    "HOLY_DRAGON_",
)


def get_star_essence_cost(internal_id: str, stars: int) -> tuple[str, int]:
    """
    Lookup essence type and total cumulative quantity needed for the specified star level (1-5).

    Returns:
        tuple[str, int]: (essence_product_id, total_quantity)
    """
    if stars <= 0:
        return ("", 0)

    upper_id = internal_id.strip().upper()
    star_level = min(max(stars, 0), 5)

    # 1. Wither Armor
    if upper_id in WITHER_ARMOR_IDS or any(
        upper_id.endswith(suffix)
        for suffix in ("_HELMET", "_CHESTPLATE", "_LEGGINGS", "_BOOTS")
        if any(upper_id.startswith(p) for p in ("NECRON_", "STORM_", "MAXOR_", "GOLDOR_", "WITHER_"))
    ):
        return ("ESSENCE_WITHER", sum(WITHER_ARMOR_STAR_COSTS[:star_level]))

    # 2. Wither Weapons
    if upper_id in WITHER_WEAPON_IDS:
        return ("ESSENCE_WITHER", sum(WITHER_WEAPONS_STAR_COSTS[:star_level]))

    # 3. Crimson Isle Kuudra Armor
    if any(upper_id.startswith(prefix) for prefix in CRIMSON_ARMOR_PREFIXES):
        return ("ESSENCE_CRIMSON", sum(CRIMSON_ARMOR_STAR_COSTS[:star_level]))

    # 4. Dragon Gear
    if any(upper_id.startswith(prefix) for prefix in DRAGON_ARMOR_PREFIXES) or "DRAGON" in upper_id:
        return ("ESSENCE_DRAGON", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))

    # 5. Spider Gear
    if "SPIDER" in upper_id or "TARANTULA" in upper_id or "SCORPION" in upper_id:
        return ("ESSENCE_SPIDER", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))

    # 6. Ice Gear
    if "ICE" in upper_id or "FROZEN" in upper_id or "GLACIAL" in upper_id or "YETI" in upper_id:
        return ("ESSENCE_ICE", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))

    # 7. Gold Gear
    if "MIDAS" in upper_id or "GOLD" in upper_id:
        return ("ESSENCE_GOLD", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))

    # 8. Diamond Gear
    if "DIAMOND" in upper_id or "PERFECT" in upper_id:
        return ("ESSENCE_DIAMOND", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))

    # Default Dungeon Gear (Undead Essence: Livid Dagger, Shadow Assassin, Shadow Fury, etc.)
    return ("ESSENCE_UNDEAD", sum(DEFAULT_DUNGEON_STAR_COSTS[:star_level]))
