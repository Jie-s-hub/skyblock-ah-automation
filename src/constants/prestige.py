"""Crimson Isle armor prestige tiers, Divan's armor chambers, and secondary addon constants."""

from __future__ import annotations

# Crimson Isle prestige tiers ordered by ascending tier
PRESTIGE_TIERS: list[str] = [
    "BASIC",
    "HOT",
    "BURNING",
    "FIERY",
    "INFERNAL",
]

# Kuudra armor base names
KUUDRA_ARMOR_BASES: set[str] = {
    "CRIMSON",
    "AURORA",
    "TERROR",
    "FERVOR",
    "HOLLOW",
}

# Divan armor pieces
DIVAN_ARMOR_PIECES: set[str] = {
    "DIVAN_HELMET",
    "DIVAN_CHESTPLATE",
    "DIVAN_LEGGINGS",
    "DIVAN_BOOTS",
}

# Product IDs for secondary addons in Bazaar
GEMSTONE_CHAMBER_PRODUCT_ID: str = "GEMSTONE_CHAMBER"
THE_ART_OF_WAR_PRODUCT_ID: str = "THE_ART_OF_WAR"
WOOD_SINGULARITY_PRODUCT_ID: str = "WOOD_SINGULARITY"
TRANSMISSION_TUNER_PRODUCT_ID: str = "TRANSMISSION_TUNER"


def get_prestige_tier(internal_id: str) -> str:
    """
    Determine the Crimson Isle prestige tier from an item's internal ID.
    Examples:
    - INFERNAL_CRIMSON_CHESTPLATE -> 'INFERNAL'
    - FIERY_AURORA_LEGGINGS -> 'FIERY'
    - BURNING_TERROR_BOOTS -> 'BURNING'
    - HOT_FERVOR_HELMET -> 'HOT'
    - CRIMSON_CHESTPLATE -> 'BASIC'
    - HYPERION -> 'BASIC' (or non-prestige)
    """
    clean_id = (internal_id or "").strip().upper()

    if clean_id.startswith("INFERNAL_"):
        return "INFERNAL"
    if clean_id.startswith("FIERY_"):
        return "FIERY"
    if clean_id.startswith("BURNING_"):
        return "BURNING"
    if clean_id.startswith("HOT_"):
        return "HOT"

    # Check if it is a basic Kuudra armor piece
    for base in KUUDRA_ARMOR_BASES:
        if clean_id.startswith(f"{base}_"):
            return "BASIC"

    return "BASIC"


def is_kuudra_armor(internal_id: str) -> bool:
    """Return True if the internal ID corresponds to a Kuudra armor piece."""
    clean_id = (internal_id or "").strip().upper()
    for base in KUUDRA_ARMOR_BASES:
        if f"_{base}_" in clean_id or clean_id.startswith(f"{base}_"):
            return True
    return False


def is_divan_piece(internal_id: str) -> bool:
    """Return True if the item is a Divan's Armor piece."""
    clean_id = (internal_id or "").strip().upper()
    return clean_id in DIVAN_ARMOR_PIECES or clean_id.startswith("DIVAN_")


# Crimson Isle equipment & secondary items that use Crimson stars (1-10)
CRIMSON_ISLE_EQUIPMENT_PREFIXES: tuple[str, ...] = (
    "MOLTEN_",
    "VANQUISHED_",
    "RAMPART_",
    "GILLSPLASH_",
    "THUNDER_",
    "MAGMA_LORD_",
)

CRIMSON_ISLE_ITEMS: set[str] = {
    "LAVA_SHELL_NECKLACE",
    "DELIRIUM_NECKLACE",
    "TAURUS_HELMET",
    "FLAMING_CHESTPLATE",
    "FLAMING_FIST",
    "MOOGMA_LEGGINGS",
    "SLUG_BOOTS",
    "GAUNTLET_OF_CONTAGION",
    "IMPLOSION_BELT",
    "SCOVILLE_BELT",
    "GHAST_CLOAK",
    "MAGMA_NECKLACE",
    "GLOWSTONE_GAUNTLET",
    "BLAZE_BELT",
    "MAGMA_ROD",
    "INFERNO_ROD",
    "HELLFIRE_ROD",
    "BLADE_OF_THE_VOLCANO",
    "STAFF_OF_THE_VOLCANO",
    "FIRE_VEIL_WAND",
    "RAGNAROCK_AXE",
}


def is_crimson_isle_item(internal_id: str) -> bool:
    """
    Return True if the internal ID corresponds to a Crimson Isle item
    (Kuudra armor, Crimson Isle equipment, Lava fishing gear/rods, or volcanic weapons)
    whose stars (1-10) are Crimson Stars upgraded with Crimson Essence,
    NOT Catacombs Master Stars.
    """
    if not internal_id:
        return False
    clean_id = internal_id.strip().upper()
    if is_kuudra_armor(clean_id):
        return True
    if clean_id in CRIMSON_ISLE_ITEMS:
        return True
    if any(clean_id.startswith(p) for p in CRIMSON_ISLE_EQUIPMENT_PREFIXES):
        return True
    return False
