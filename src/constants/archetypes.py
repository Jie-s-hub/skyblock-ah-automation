"""Archetype definitions, incomplete item penalties, and meta completion synergy bonuses."""

from __future__ import annotations

from typing import Any, Optional

WITHER_BLADE_IDS = {"HYPERION", "VALKYRIE", "SCYLLA", "ASTRAEA"}
ALL_WITHER_SCROLLS = {"WITHER_SHIELD_SCROLL", "IMPLOSION_SCROLL", "SHADOW_WARP_SCROLL"}

EXPENSIVE_ULTIMATES = {"CHIMERA", "FATAL_TEMPO", "DUPLEX", "INFERNO"}


def calculate_coherence_multiplier(
    internal_id: str,
    base_lbin: int,
    decoded_data: dict[str, Any],
    addon_breakdown: list[dict[str, Any]],
) -> tuple[float, Optional[str]]:
    """
    Compute archetype coherence multiplier (gamma) for upgraded items:
    - Incomplete High-End Penalty (gamma = 0.50 to 0.70):
      Applies when expensive addons are attached to incomplete bases:
      * Wither Blades with 1 or 2 scrolls instead of all 3 (gamma = 0.60)
      * Tier II-IV expensive ultimate enchants (Chimera, Fatal Tempo, Duplex) (gamma = 0.65)
      * Master Stars on non-recombobulated or < 5-star gear (gamma = 0.50)
    - Meta Completion Synergy Bonus (gamma = 1.05 to 1.10):
      Applies when an item satisfies 100% of canonical endgame template:
      * 3-scroll Wither Blade + Recomb + 5 Dungeon Stars + T6/T7 enchants (gamma = 1.08)

    Returns:
        tuple[float, Optional[str]]: (multiplier, reason_code)
    """
    clean_id = (internal_id or "").strip().upper()
    total_stars = int(decoded_data.get("dungeon_stars", 0))
    master_stars = int(decoded_data.get("master_stars", 0))
    recomb = int(decoded_data.get("rarity_upgrades", 0))
    ability_scrolls = [str(s).strip().upper() for s in decoded_data.get("ability_scrolls", [])]
    enchantments = decoded_data.get("enchantments", {})

    # Check 1: Master Stars on incomplete base (<5 stars or no recomb)
    if master_stars > 0:
        if recomb < 1 or (total_stars - master_stars) < 5:
            return 0.50, "INCOMPLETE_MASTER_STARS_PENALTY"

    # Check 2: Wither Blade Scrolls
    if clean_id in WITHER_BLADE_IDS:
        norm_scrolls = set()
        for s in ability_scrolls:
            norm = s if s.endswith("_SCROLL") else f"{s}_SCROLL"
            norm_scrolls.add(norm)

        scroll_count = len(norm_scrolls.intersection(ALL_WITHER_SCROLLS))
        if 1 <= scroll_count < 3:
            # Missing 1 or 2 scrolls: heavy penalty on incomplete blade
            return 0.60, "INCOMPLETE_SCROLLS_PENALTY"

        if scroll_count == 3:
            # Check meta completion: 3 scrolls + Recomb + 5 stars + enchants
            has_recomb = recomb >= 1
            has_5_stars = total_stars >= 5
            has_high_enchants = any(lvl >= 6 for lvl in enchantments.values())
            if has_recomb and has_5_stars and has_high_enchants:
                return 1.08, "META_COMPLETION_WITHER_BLADE_BONUS"

    # Check 3: Intermediate expensive ultimate enchants (Tier II, III, IV)
    for ench_name, lvl in enchantments.items():
        name_clean = ench_name.strip().upper().replace("ULTIMATE_", "")
        if name_clean in EXPENSIVE_ULTIMATES:
            if 2 <= lvl <= 4:
                return 0.65, "INTERMEDIATE_ULTIMATE_PENALTY"

    return 1.0, None
