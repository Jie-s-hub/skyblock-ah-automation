"""Addon coverage engine and vanity sold-comps routing (Phase 7)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/addon_rules.json"


def load_addon_rules(config_path: str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load addon rules JSON configuration."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to parse %s: %s. Using default fallback rules.", config_path, e)

    return {
        "hot_potato_book": {"product_id": "HOT_POTATO_BOOK", "max_count": 10},
        "fuming_potato_book": {"product_id": "FUMING_POTATO_BOOK", "max_count": 5},
        "wither_scrolls": {
            "WITHER_SHIELD_SCROLL": {"name": "Wither Shield", "product_id": "WITHER_SHIELD_SCROLL"},
            "IMPLOSION_SCROLL": {"name": "Implosion", "product_id": "IMPLOSION_SCROLL"},
            "SHADOW_WARP_SCROLL": {"name": "Shadow Warp", "product_id": "SHADOW_WARP_SCROLL"},
        },
        "pet_candy_penalty": {"penalty_per_candy": 0.015, "max_candy": 10},
        "vanity_rules": {
            "min_comps_sales": 3,
            "allowed_prefixes": ["PET_SKIN_"],
            "allowed_substrings": ["RUNE"],
            "permanent_blacklist": ["DYE_", "PORTAL", "FURNITURE", "MUSIC_DISC"],
        },
    }


def calculate_potato_book_cost(
    hot_potato_count: int,
    bazaar_cache: Any,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    """
    Calculate Hot Potato Book (1-10) and Fuming Potato Book (11-15) costs from Bazaar.

    Returns:
        tuple[int, int, int, list[dict]]: (hpb_count, fpb_count, total_cost, addon_items)
    """
    if hot_potato_count <= 0 or not bazaar_cache:
        return 0, 0, 0, []

    hpb_count = min(hot_potato_count, 10)
    fpb_count = max(0, min(hot_potato_count - 10, 5))

    total_cost = 0
    addon_items: list[dict[str, Any]] = []

    if hpb_count > 0:
        hpb_price = bazaar_cache.get_buy_price("HOT_POTATO_BOOK")
        if hpb_price and hpb_price > 0:
            cost = int(hpb_count * hpb_price)
            total_cost += cost
            addon_items.append({
                "type": "hot_potato_book",
                "name": f"Hot Potato Books ({hpb_count}/10)",
                "cost": cost,
                "count": hpb_count,
                "product_ids": ["HOT_POTATO_BOOK"],
            })

    if fpb_count > 0:
        fpb_price = bazaar_cache.get_buy_price("FUMING_POTATO_BOOK")
        if fpb_price and fpb_price > 0:
            cost = int(fpb_count * fpb_price)
            total_cost += cost
            addon_items.append({
                "type": "fuming_potato_book",
                "name": f"Fuming Potato Books ({fpb_count}/5)",
                "cost": cost,
                "count": fpb_count,
                "product_ids": ["FUMING_POTATO_BOOK"],
            })

    return hpb_count, fpb_count, total_cost, addon_items


def calculate_wither_scrolls_cost(
    ability_scrolls: list[str],
    bazaar_cache: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """
    Calculate the Bazaar value of applied Wither Scrolls (Wither Shield, Implosion, Shadow Warp).
    """
    if not ability_scrolls or not bazaar_cache:
        return 0, []

    total_cost = 0
    addon_items: list[dict[str, Any]] = []

    scroll_map = {
        "WITHER_SHIELD_SCROLL": ("Wither Shield Scroll", "WITHER_SHIELD_SCROLL"),
        "IMPLOSION_SCROLL": ("Implosion Scroll", "IMPLOSION_SCROLL"),
        "SHADOW_WARP_SCROLL": ("Shadow Warp Scroll", "SHADOW_WARP_SCROLL"),
    }

    for scroll in ability_scrolls:
        clean = scroll.strip().upper()
        # Normalization if names don't end in _SCROLL
        if not clean.endswith("_SCROLL") and clean in ("WITHER_SHIELD", "IMPLOSION", "SHADOW_WARP"):
            clean = f"{clean}_SCROLL"

        if clean in scroll_map:
            display_name, prod_id = scroll_map[clean]
            price = bazaar_cache.get_buy_price(prod_id)
            if price and price > 0:
                cost = int(price)
                total_cost += cost
                addon_items.append({
                    "type": "wither_scroll",
                    "name": display_name,
                    "cost": cost,
                    "product_ids": [prod_id],
                })

    return total_cost, addon_items


def calculate_enrichment_cost(
    enrichment: Optional[str],
    bazaar_cache: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Calculate the Bazaar value of an accessory enrichment."""
    if not enrichment or not bazaar_cache:
        return 0, []

    clean = enrichment.strip().upper()
    if not clean.startswith("TALISMAN_ENRICHMENT_"):
        clean = f"TALISMAN_ENRICHMENT_{clean}"

    price = bazaar_cache.get_buy_price(clean)
    if price and price > 0:
        cost = int(price)
        return cost, [{
            "type": "enrichment",
            "name": f"Enrichment: {clean.replace('TALISMAN_ENRICHMENT_', '').title()}",
            "cost": cost,
            "product_ids": [clean],
        }]

    return 0, []


def calculate_drill_parts_cost(
    decoded_data: dict[str, Any],
    bazaar_cache: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Calculate the Bazaar value of installed drill components."""
    if not bazaar_cache:
        return 0, []

    total_cost = 0
    addon_items: list[dict[str, Any]] = []

    for part_slot in ("drill_part_engine", "drill_part_fuel_tank", "drill_part_upgrade_module"):
        part_id = decoded_data.get(part_slot)
        if part_id and isinstance(part_id, str):
            clean_id = part_id.strip().upper()
            price = bazaar_cache.get_buy_price(clean_id)
            if price and price > 0:
                cost = int(price)
                total_cost += cost
                addon_items.append({
                    "type": "drill_part",
                    "name": f"Drill Part: {clean_id.replace('_', ' ').title()}",
                    "cost": cost,
                    "product_ids": [clean_id],
                })

    return total_cost, addon_items


def calculate_pet_candy_penalty(
    candy_count: int,
    penalty_per_candy: float = 0.015,
) -> float:
    """
    Calculate the percentage price penalty for a pet that has consumed candy.
    Formula: min(0.15, candy_count * penalty_per_candy) (default: -1.5% per candy, max -15%).
    """
    clamped_candy = max(0, min(10, candy_count))
    return round(clamped_candy * penalty_per_candy, 4)


def is_vanity_item(internal_id: str, item_name: str = "") -> bool:
    """Check if an item is a vanity item (pet skin or rune)."""
    clean_id = (internal_id or "").strip().upper()
    if clean_id.startswith("PET_SKIN_") or "PET_SKIN" in clean_id:
        return True
    if clean_id == "RUNE" or clean_id.startswith("RUNE_") or clean_id.endswith("_RUNE") or "_RUNE_" in clean_id:
        return True
    if item_name and " Rune " in f" {item_name} ":
        return True
    return False


def is_permanently_blacklisted(internal_id: str, item_name: str = "") -> bool:
    """Check if an item is under the non-negotiable permanent cosmetic blacklist."""
    clean_id = (internal_id or "").strip().upper()
    if "DYE_" in clean_id or clean_id.startswith("DYE_"):
        return True
    if "PORTAL" in clean_id or "FURNITURE" in clean_id or "MUSIC_DISC" in clean_id:
        return True
    clean_name = (item_name or "").strip().upper()
    if "DYE" in clean_name or "PORTAL SCROLL" in clean_name or "MUSIC DISC" in clean_name:
        return True
    return False
