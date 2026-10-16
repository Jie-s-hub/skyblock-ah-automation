"""Networth calculation engine, dynamic retention rates, and gemstone/master star pricing."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

from src.bazaar import MASTER_STAR_PRODUCT_IDS, BazaarCache
from src.constants.prestige import is_crimson_isle_item
from src.constants.reforge_stones import REFORGE_STONE_DISPLAY_NAMES, REFORGE_STONE_MAP
from src.constants.star_costs import get_star_essence_cost
from src.enchant_evaluator import get_enchant_bazaar_cost

logger = logging.getLogger(__name__)

# Known ultimate enchantments stems
ULTIMATE_ENCHANTS = {
    "SOUL_EATER",
    "WISDOM",
    "LEGION",
    "CHIMERA",
    "ONE_FOR_ALL",
    "SWARM",
    "FATAL_TEMPO",
    "REND",
    "COMBO",
    "BANK",
    "ULTIMATE_JERRY",
    "LAST_STAND",
    "NO_PAIN_NO_GAIN",
    "FLASH",
    "DUPLEX",
    "INFERNO",
    "HABANERO_TACTICS",
    "ULTIMATE_WISE",
    "WISE",
}


def get_dynamic_retention_rate(base_lbin: int) -> float:
    """
    Compute dynamic retention rate based on base item value:
    - Endgame Staples (base_lbin >= 500,000,000): 85% (0.85)
    - Mid-to-Late Gear (50,000,000 <= base_lbin < 500,000,000): 70% (0.70)
    - Budget / Early-Mid Gear (base_lbin < 50,000,000): 50% (0.50)
    """
    if base_lbin >= 500_000_000:
        return 0.85
    elif base_lbin >= 50_000_000:
        return 0.70
    else:
        return 0.50


def calculate_enchant_bazaar_cost(
    bazaar_cache: Optional[BazaarCache],
    enchant_name: str,
    level: int,
) -> tuple[str, int]:
    """
    Lookup instant-buy price for an enchanted book with progression safeguards,
    exact tier lookup, T6->T7 compounding, and downward anvil compounding.
    Delegates to src.enchant_evaluator.get_enchant_bazaar_cost.
    """
    return get_enchant_bazaar_cost(bazaar_cache, enchant_name, level)


def calculate_item_addons(
    decoded_data: dict[str, Any],
    bazaar_cache: Optional[BazaarCache],
    is_valuable_enchant_fn: Optional[Any] = None,
    format_enchant_fn: Optional[Any] = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Compute itemized breakdown of attached addons:
    - Recombobulator 3000
    - Master Stars (Stars 6-10 -> First through Fifth Master Star)
    - Slotted Gemstones ({QUALITY}_{TYPE}_GEM)
    - Enchantments with anvil book compounding
    - Essence Stars (1-5)
    - Reforge Stones

    Returns:
        tuple[list[dict[str, Any]], int]: (addon_breakdown_list, total_addon_cost)
    """
    if not bazaar_cache:
        return ([], 0)

    internal_id = decoded_data.get("internal_id", "")
    addon_breakdown: list[dict[str, Any]] = []

    # 1. Recombobulator
    if int(decoded_data.get("rarity_upgrades", 0)) >= 1:
        recomb_price = bazaar_cache.get_buy_price("RECOMBOBULATOR_3000")
        if recomb_price and recomb_price > 0:
            addon_breakdown.append(
                {
                    "type": "recomb",
                    "name": "Recombobulator 3000",
                    "cost": int(recomb_price),
                    "product_ids": ["RECOMBOBULATOR_3000"],
                }
            )

    # 2. Master Stars (Stars 6 through 10 -> 1 to 5 Master Stars on Dungeon items only)
    total_stars = int(decoded_data.get("dungeon_stars", 0))
    master_stars = int(decoded_data.get("master_stars", 0))

    # Crimson Isle items (Kuudra gear, crimson rods, etc.) never have Master Stars
    if is_crimson_isle_item(internal_id):
        master_stars = 0
    elif master_stars == 0 and total_stars > 5:
        master_stars = max(0, min(total_stars - 5, 5))

    if master_stars > 0:
        master_star_total_cost = 0
        ms_prod_ids = []
        for star_idx in range(1, master_stars + 1):
            ms_price = bazaar_cache.get_master_star_price(star_idx)
            master_star_total_cost += ms_price
            if star_idx <= len(MASTER_STAR_PRODUCT_IDS):
                ms_prod_ids.append(MASTER_STAR_PRODUCT_IDS[star_idx - 1])

        if master_star_total_cost > 0:
            addon_breakdown.append(
                {
                    "type": "master_star",
                    "name": f"Master Stars: {master_stars}x Applied",
                    "cost": master_star_total_cost,
                    "count": master_stars,
                    "product_ids": ms_prod_ids,
                }
            )

    # 3. Slotted Gemstones
    gemstones = decoded_data.get("gemstones", [])
    if gemstones:
        gemstones_total_cost = 0
        gem_counts: dict[tuple[str, str], int] = defaultdict(int)
        gem_prod_ids = []
        for gem in gemstones:
            gtype = str(gem.get("type", "")).strip().upper()
            gqual = str(gem.get("quality", "")).strip().upper()
            if gtype and gqual:
                gem_price = bazaar_cache.get_gemstone_price(gtype, gqual)
                gemstones_total_cost += gem_price
                gem_counts[(gqual, gtype)] += 1
                prod = f"{gqual}_{gtype}_GEM"
                if prod not in gem_prod_ids:
                    gem_prod_ids.append(prod)

        if gemstones_total_cost > 0:
            gem_desc_parts = [
                f"{cnt}x {qual.title()} {typ.title()}"
                for (qual, typ), cnt in sorted(gem_counts.items(), key=lambda x: (x[0][0], x[0][1]))
            ]
            gem_summary = ", ".join(gem_desc_parts)
            addon_breakdown.append(
                {
                    "type": "gemstones",
                    "name": f"Gemstones: {gem_summary}",
                    "cost": gemstones_total_cost,
                    "items": gemstones,
                    "product_ids": gem_prod_ids,
                }
            )

    # 4. Reforge Stone
    modifier = str(decoded_data.get("modifier", "")).strip().lower()
    if modifier in REFORGE_STONE_MAP:
        stone_id = REFORGE_STONE_MAP[modifier]
        if stone_id:
            stone_cost = bazaar_cache.get_buy_price(stone_id)
            if stone_cost and stone_cost > 0:
                stone_display = REFORGE_STONE_DISPLAY_NAMES.get(modifier, stone_id)
                addon_breakdown.append(
                    {
                        "type": "reforge",
                        "name": stone_display,
                        "cost": int(stone_cost),
                        "product_ids": [stone_id],
                    }
                )

    # 5. Enchantments with anvil compounding
    enchantments = decoded_data.get("enchantments", {})
    for ench_name, lvl in sorted(enchantments.items()):
        if is_valuable_enchant_fn and not is_valuable_enchant_fn(ench_name, lvl):
            continue
        book_id, book_cost = calculate_enchant_bazaar_cost(bazaar_cache, ench_name, lvl)
        if book_cost > 0:
            if format_enchant_fn:
                display_name = f"{format_enchant_fn(ench_name)} {lvl}"
            else:
                display_name = f"{ench_name.replace('_', ' ').title()} {lvl}"
            addon_breakdown.append(
                {
                    "type": "enchant",
                    "name": display_name,
                    "cost": book_cost,
                    "product_ids": [book_id] if book_id else [],
                }
            )

    # 6. Essence Stars (1-10 for Crimson Isle items, 1-5 for Dungeon items)
    if is_crimson_isle_item(internal_id):
        essence_stars = min(total_stars, 10)
    else:
        essence_stars = min(total_stars, 5)
    if essence_stars > 0:
        essence_type, essence_count = get_star_essence_cost(internal_id, essence_stars)
        if essence_type and essence_count > 0:
            unit_price = bazaar_cache.get_buy_price(essence_type)
            if unit_price and unit_price > 0:
                essence_cost = int(essence_count * unit_price)
                clean_essence_name = essence_type.replace("ESSENCE_", "").capitalize() + " Essence"
                addon_breakdown.append(
                    {
                        "type": "essence",
                        "name": f"{essence_count:,}x {clean_essence_name}",
                        "cost": essence_cost,
                        "product_ids": [essence_type],
                    }
                )

    # 7. Divan's Armor Gemstone Chambers
    gemstone_chambers = int(decoded_data.get("gemstone_chambers", 0))
    if gemstone_chambers > 0 and (internal_id.startswith("DIVAN_") or "DIVAN" in internal_id):
        chamber_price = bazaar_cache.get_buy_price("GEMSTONE_CHAMBER")
        if chamber_price and chamber_price > 0:
            chambers_cost = int(gemstone_chambers * chamber_price)
            addon_breakdown.append(
                {
                    "type": "gemstone_chambers",
                    "name": f"Gemstone Chambers: {gemstone_chambers}x Unlocked",
                    "cost": chambers_cost,
                    "count": gemstone_chambers,
                    "product_ids": ["GEMSTONE_CHAMBER"],
                }
            )

    # 8. The Art of War
    art_of_war_count = int(decoded_data.get("art_of_war_count", 0))
    if art_of_war_count >= 1:
        aow_price = bazaar_cache.get_buy_price("THE_ART_OF_WAR")
        if aow_price and aow_price > 0:
            addon_breakdown.append(
                {
                    "type": "art_of_war",
                    "name": "The Art of War (+5 ⚔)",
                    "cost": int(aow_price * art_of_war_count),
                    "count": art_of_war_count,
                    "product_ids": ["THE_ART_OF_WAR"],
                }
            )

    # 9. Wood Singularity
    wood_singularity_count = int(decoded_data.get("wood_singularity_count", 0))
    if wood_singularity_count >= 1:
        ws_price = bazaar_cache.get_buy_price("WOOD_SINGULARITY")
        if ws_price and ws_price > 0:
            addon_breakdown.append(
                {
                    "type": "wood_singularity",
                    "name": "Wood Singularity (+100 ⚔)",
                    "cost": int(ws_price * wood_singularity_count),
                    "count": wood_singularity_count,
                    "product_ids": ["WOOD_SINGULARITY"],
                }
            )

    # 10. Transmission Tuners
    transmission_tuners = int(decoded_data.get("transmission_tuners", 0))
    if transmission_tuners > 0:
        tt_price = bazaar_cache.get_buy_price("TRANSMISSION_TUNER")
        if tt_price and tt_price > 0:
            tuners_cost = int(transmission_tuners * tt_price)
            addon_breakdown.append(
                {
                    "type": "transmission_tuners",
                    "name": f"Transmission Tuners: {transmission_tuners}x Applied",
                    "cost": tuners_cost,
                    "count": transmission_tuners,
                    "product_ids": ["TRANSMISSION_TUNER"],
                }
            )

    # 11. Hot Potato & Fuming Potato Books (1-10 HPB, 11-15 FPB)
    hot_potato_count = int(decoded_data.get("hot_potato_count", 0))
    if hot_potato_count > 0:
        from src.addon_rules import calculate_potato_book_cost

        _, _, _, potato_addons = calculate_potato_book_cost(hot_potato_count, bazaar_cache)
        addon_breakdown.extend(potato_addons)

    # 12. Wither Scrolls
    ability_scrolls = decoded_data.get("ability_scrolls", [])
    if ability_scrolls:
        from src.addon_rules import calculate_wither_scrolls_cost

        _, scroll_addons = calculate_wither_scrolls_cost(ability_scrolls, bazaar_cache)
        addon_breakdown.extend(scroll_addons)

    # 13. Accessory Enrichment
    enrichment = decoded_data.get("talisman_enrichment")
    if enrichment:
        from src.addon_rules import calculate_enrichment_cost

        _, enrich_addons = calculate_enrichment_cost(enrichment, bazaar_cache)
        addon_breakdown.extend(enrich_addons)

    # 14. Drill Parts
    if any(decoded_data.get(k) for k in ("drill_part_engine", "drill_part_fuel_tank", "drill_part_upgrade_module")):
        from src.addon_rules import calculate_drill_parts_cost

        _, drill_addons = calculate_drill_parts_cost(decoded_data, bazaar_cache)
        addon_breakdown.extend(drill_addons)

    total_addon_cost = sum(a["cost"] for a in addon_breakdown)
    return (addon_breakdown, total_addon_cost)


def calculate_item_networth(
    base_lbin: int,
    decoded_data: dict[str, Any],
    bazaar_cache: Optional[BazaarCache],
    is_valuable_enchant_fn: Optional[Any] = None,
    format_enchant_fn: Optional[Any] = None,
    override_retention_rate: Optional[float] = None,
) -> tuple[list[dict[str, Any]], int, int, float, int]:
    """
    Calculate item addon breakdown, total addon worth, intrinsic networth,
    dynamic retention rate, and calculated resell baseline.

    Returns:
        tuple: (addon_breakdown, total_addon_cost, intrinsic_worth, retention_rate, resell_baseline)
    """
    addon_breakdown, total_addon_cost = calculate_item_addons(
        decoded_data=decoded_data,
        bazaar_cache=bazaar_cache,
        is_valuable_enchant_fn=is_valuable_enchant_fn,
        format_enchant_fn=format_enchant_fn,
    )
    intrinsic_worth = base_lbin + total_addon_cost
    if override_retention_rate is not None:
        retention_rate = float(override_retention_rate)
    else:
        retention_rate = get_dynamic_retention_rate(base_lbin)

    recomb_cost = sum(a["cost"] for a in addon_breakdown if a.get("type") == "recomb")
    other_addon_cost = total_addon_cost - recomb_cost

    # If base item value is below 1M, recombobulator adds 0 resale retention (auto-recomb dungeon drops/junk)
    if base_lbin < 1_000_000:
        retained_recomb = 0
    else:
        retained_recomb = int(round(recomb_cost * retention_rate))

    retained_other = int(round(other_addon_cost * retention_rate))
    total_retained_addons = retained_recomb + retained_other

    # For budget items (< 20M), cap total retained addon value at 2.0x base_lbin to prevent
    # low-tier junk from being inflated into multi-million coin targets
    if base_lbin < 20_000_000 and total_retained_addons > int(2.0 * base_lbin):
        total_retained_addons = int(2.0 * base_lbin)

    from src.constants.archetypes import calculate_coherence_multiplier

    coherence_mult, _ = calculate_coherence_multiplier(
        internal_id=decoded_data.get("internal_id", ""),
        base_lbin=base_lbin,
        decoded_data=decoded_data,
        addon_breakdown=addon_breakdown,
    )
    if coherence_mult != 1.0:
        total_retained_addons = int(round(total_retained_addons * coherence_mult))

    resell_baseline = base_lbin + total_retained_addons
    return addon_breakdown, total_addon_cost, intrinsic_worth, retention_rate, resell_baseline
