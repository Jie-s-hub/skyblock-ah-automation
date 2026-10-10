"""Mapping of equipment reforge modifiers to their corresponding Bazaar reforge stones."""

from __future__ import annotations

# Reforge modifier name (lowercase) -> Bazaar product ID
REFORGE_STONE_MAP: dict[str, str] = {
    "ancient": "PRECURSOR_GEAR",
    "withered": "WITHER_BLOOD",
    "fabled": "DRAGON_CLAW",
    "warped": "AOTE_STONE",
    "loving": "RED_SCARF",
    "jaded": "JADERALD",
    "giant": "GIANT_TOOTH",
    "submerged": "DEEP_SEA_ORB",
    "perfect": "DIAMOND_ATOM",
    "spiritual": "SPIRIT_DECOY",
    "precise": "OPTICAL_LENS",
    "blessed": "BLESSED_FRUIT",
    "renowned": "DRAGON_HORN",
    "necrotic": "NECROMANCER_BROOCH",
    "bulky": "BULKY_STONE",
    "searing": "SEARING_STONE",
    "jerry": "JERRY_STONE",
}

# Human-readable display names for reforge stones
REFORGE_STONE_DISPLAY_NAMES: dict[str, str] = {
    "ancient": "Precursor Gear",
    "withered": "Wither Blood",
    "fabled": "Dragon Claw",
    "warped": "Warped Stone",
    "loving": "Red Scarf",
    "jaded": "Jaderald",
    "giant": "Giant Tooth",
    "submerged": "Deep Sea Orb",
    "perfect": "Diamond Atom",
    "spiritual": "Spirit Decoy",
    "precise": "Optical Lens",
    "blessed": "Blessed Fruit",
    "renowned": "Dragon Horn",
    "necrotic": "Necromancer's Brooch",
    "bulky": "Bulky Stone",
    "searing": "Searing Stone",
    "jerry": "Jerry Stone",
}
