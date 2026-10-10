"""NBT decoding utilities for Hypixel SkyBlock compressed item bytes."""

from __future__ import annotations

import base64
import gzip
import io
import json
import re
from typing import Any, Union

import nbtlib

from src.constants.pet_levels import calculate_pet_level
from src.constants.prestige import get_prestige_tier

COLOR_CODE_PATTERN = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)


def strip_minecraft_color_codes(text: str) -> str:
    """Strip Minecraft color and formatting codes (e.g. §6, §l, §r) from a string."""
    if not text:
        return ""
    return COLOR_CODE_PATTERN.sub("", text).strip()


VALID_GEM_QUALITIES = {"ROUGH", "FLAWED", "FINE", "FLAWLESS", "PERFECT"}
VALID_GEM_TYPES = {"RUBY", "AMETHYST", "JASPER", "SAPPHIRE", "OPAL", "JADE", "AMBER", "TOPAZ"}


def parse_slotted_gemstones(gems_tag: Any) -> list[dict[str, str]]:
    """
    Parse the `gems` compound tag inside ExtraAttributes to extract applied gemstones.
    Keys often follow formats like {TYPE}_{SLOT_INDEX} (e.g. JASPER_0: 'PERFECT', RUBY_1: 'FLAWLESS').
    Ignores locked slot strings or unlocked empty slot indicators.

    Returns:
        list[dict[str, str]]: E.g. [{"type": "JASPER", "quality": "PERFECT"}, ...]
    """
    if not gems_tag or not hasattr(gems_tag, "items"):
        return []

    gemstones: list[dict[str, str]] = []
    gem_dict: dict[str, str] = {}

    for k, v in gems_tag.items():
        if isinstance(v, (str, bytes)):
            val_str = str(v.decode("utf-8") if isinstance(v, bytes) else v).strip().upper()
            gem_dict[str(k).strip().upper()] = val_str
        elif isinstance(v, dict) or hasattr(v, "get"):
            q = str(v.get("quality", "")).strip().upper()
            t = str(v.get("type", "")).strip().upper()
            if q in VALID_GEM_QUALITIES and t in VALID_GEM_TYPES:
                gemstones.append({"type": t, "quality": q})
        else:
            val_str = str(v).strip().upper()
            gem_dict[str(k).strip().upper()] = val_str

    for key, val in gem_dict.items():
        if key in ("UNLOCKED_SLOTS", "UNLOCKED") or val in ("LOCKED", "UNLOCKED", "EMPTY", "NONE"):
            continue

        if key.endswith("_GEM"):
            continue

        if val in VALID_GEM_QUALITIES:
            parts = key.split("_")
            gem_type = parts[0]
            if gem_type in VALID_GEM_TYPES:
                gemstones.append({"type": gem_type, "quality": val})
            else:
                gem_type_override = gem_dict.get(f"{key}_GEM")
                if gem_type_override and gem_type_override in VALID_GEM_TYPES:
                    gemstones.append({"type": gem_type_override, "quality": val})
                elif len(parts) >= 2 and parts[1] in VALID_GEM_TYPES:
                    gemstones.append({"type": parts[1], "quality": val})

    return gemstones


def parse_divan_gemstone_chambers(internal_id: str, extra_attrs: Any) -> int:
    """
    Parse the count of unlocked gemstone chambers on Divan's Armor pieces.
    Handles explicit count fields (chambers, unlocked_chambers, gemstone_chambers)
    and ExtraAttributes.gems.unlocked_slots (int or list).
    """
    clean_id = (internal_id or "").strip().upper()
    if not clean_id.startswith("DIVAN_"):
        return 0
    if not hasattr(extra_attrs, "get"):
        return 0

    for field in ("gemstone_chambers", "chambers", "unlocked_chambers", "gemstone_slots"):
        val = extra_attrs.get(field)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass

    gems_tag = extra_attrs.get("gems")
    if hasattr(gems_tag, "get"):
        unlocked = gems_tag.get("unlocked_slots")
        if unlocked is not None:
            if isinstance(unlocked, (int, float)):
                return int(unlocked)
            if isinstance(unlocked, (list, tuple, set)):
                return len(unlocked)
            try:
                return int(unlocked)
            except (ValueError, TypeError):
                pass

    return 0


def decode_item_bytes(raw_b64: Union[str, dict[str, Any], None]) -> dict[str, Any]:
    """
    Decode Hypixel base64-encoded, gzip-compressed item NBT bytes.

    Extracts:
        - internal_id: SkyBlock internal item ID (e.g., 'HYPERION')
        - item_name: Display name stripped of color codes
        - modifier: Item reforge modifier
        - rarity_upgrades: Count of Recombobulators applied
        - dungeon_stars: Total star upgrade level (1-10)
        - master_stars: Master stars applied (stars 6-10 -> 1-5)
        - gemstones: List of applied gemstones [{"type": "...", "quality": "..."}]
        - enchantments: Mapping of enchant name to level
        - attributes: Mapping of Crimson Isle attribute name to level
        - is_pet: True if item is a SkyBlock pet
        - pet_level: Calculated pet level
        - pet_held_item: Held item ID applied to the pet
        - prestige_tier: Crimson Isle prestige tier ('BASIC', 'HOT', 'BURNING', 'FIERY', 'INFERNAL')
        - gemstone_chambers: Unlocked chambers on Divan's Armor pieces (0-5)
        - art_of_war_count: The Art of War applied (0 or 1)
        - wood_singularity_count: Wood Singularity applied (0 or 1)
        - transmission_tuners: Transmission Tuners applied (0-4)
    """
    default_payload = {
        "internal_id": "UNKNOWN",
        "item_name": "Unknown Item",
        "modifier": "",
        "rarity_upgrades": 0,
        "dungeon_stars": 0,
        "master_stars": 0,
        "gemstones": [],
        "enchantments": {},
        "attributes": {},
        "is_pet": False,
        "pet_type": "",
        "pet_tier": "",
        "effective_tier": "",
        "pet_level": 1,
        "pet_exp": 0.0,
        "pet_held_item": "",
        "pet_skin": None,
        "prestige_tier": "BASIC",
        "gemstone_chambers": 0,
        "art_of_war_count": 0,
        "wood_singularity_count": 0,
        "transmission_tuners": 0,
        "hot_potato_count": 0,
        "pet_candy_used": 0,
        "ability_scrolls": [],
        "drill_part_engine": None,
        "drill_part_fuel_tank": None,
        "drill_part_upgrade_module": None,
        "talisman_enrichment": None,
        "count": 1,
    }

    if not raw_b64:
        return default_payload

    # Handle dictionary wrapper if present (e.g., {"data": "..."})
    if isinstance(raw_b64, dict):
        raw_b64 = raw_b64.get("data", "")
        if not raw_b64:
            return default_payload

    try:
        compressed_bytes = base64.b64decode(raw_b64)
        nbt_bytes = gzip.decompress(compressed_bytes)
        nbt_file = nbtlib.File.from_fileobj(io.BytesIO(nbt_bytes))
    except Exception:
        return default_payload

    try:
        root_tag = nbt_file.root if hasattr(nbt_file, "root") else nbt_file
        items_list = root_tag.get("i", [])
        if not items_list or len(items_list) == 0:
            return default_payload

        first_item = items_list[0]
        item_tag = first_item.get("tag", {})
        if not item_tag:
            return default_payload

        item_count = 1
        count_tag = first_item.get("Count")
        if count_tag is not None:
            try:
                item_count = max(1, int(count_tag))
            except (ValueError, TypeError):
                item_count = 1

        # Display name extraction
        display = item_tag.get("display", {})
        raw_name = str(display.get("Name", "Unknown Item"))
        clean_name = strip_minecraft_color_codes(raw_name)

        # ExtraAttributes extraction
        extra_attrs = item_tag.get("ExtraAttributes", {})
        internal_id = str(extra_attrs.get("id", "UNKNOWN"))
        modifier = str(extra_attrs.get("modifier", "")).strip().lower()

        # Recombobulator upgrades
        rarity_upgrades = int(extra_attrs.get("rarity_upgrades", 0))

        # Dungeon stars / upgrade level
        stars = 0
        if "dungeon_item_level" in extra_attrs:
            try:
                stars = max(stars, int(extra_attrs["dungeon_item_level"]))
            except (ValueError, TypeError):
                pass
        if "upgrade_level" in extra_attrs:
            try:
                stars = max(stars, int(extra_attrs["upgrade_level"]))
            except (ValueError, TypeError):
                pass

        # Master stars: Stars 6-10 correspond to Master Stars 1-5
        master_stars = max(0, min(stars - 5, 5)) if stars > 5 else 0

        # Slotted gemstones extraction
        gems_raw = extra_attrs.get("gems", {})
        gemstones = parse_slotted_gemstones(gems_raw)

        # Enchantments
        enchantments: dict[str, int] = {}
        enchants_raw = extra_attrs.get("enchantments", {})
        if hasattr(enchants_raw, "items"):
            for enchant_name, level in enchants_raw.items():
                try:
                    enchantments[str(enchant_name)] = int(level)
                except (ValueError, TypeError):
                    continue

        # Attributes extraction (Kuudra / Crimson Isle gear)
        attributes: dict[str, int] = {}
        attrs_raw = extra_attrs.get("attributes", {})
        if hasattr(attrs_raw, "items"):
            for attr_name, tier in attrs_raw.items():
                try:
                    attributes[str(attr_name).strip().lower()] = int(tier)
                except (ValueError, TypeError):
                    continue

        # Pet data extraction (serialized petInfo JSON)
        is_pet = False
        pet_type = ""
        pet_tier = ""
        effective_tier = ""
        pet_level = 1
        pet_exp = 0.0
        pet_held_item = ""
        pet_skin = None

        pet_raw_info = extra_attrs.get("petInfo")
        if pet_raw_info or internal_id == "PET":
            is_pet = True
            if pet_raw_info:
                try:
                    if isinstance(pet_raw_info, str):
                        pet_data = json.loads(pet_raw_info)
                    elif isinstance(pet_raw_info, dict):
                        pet_data = pet_raw_info
                    else:
                        pet_data = json.loads(str(pet_raw_info))
                    pet_type = str(pet_data.get("type", "UNKNOWN")).strip().upper()
                    pet_tier = str(pet_data.get("tier", "COMMON")).strip().upper()
                    pet_exp = float(pet_data.get("exp", 0.0))
                    pet_held_item = str(pet_data.get("heldItem", "")).strip().upper()
                    if pet_held_item in ("NONE", "NULL", ""):
                        pet_held_item = ""
                    skin_raw = str(
                        pet_data.get("skin", "") or extra_attrs.get("pet_skin", "") or extra_attrs.get("skin", "")
                    ).strip()
                    if skin_raw and skin_raw.upper() not in ("NONE", "NULL", ""):
                        pet_skin = skin_raw
                    else:
                        pet_skin = None
                    pet_level = calculate_pet_level(pet_type, pet_tier, pet_exp)
                except Exception:
                    pass

            effective_tier = pet_tier
            if pet_held_item == "PET_ITEM_TIER_BOOST":
                rarity_order = ["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC"]
                if pet_tier in rarity_order:
                    idx = rarity_order.index(pet_tier)
                    if idx < len(rarity_order) - 1:
                        effective_tier = rarity_order[idx + 1]

            if pet_type and pet_tier:
                internal_id = f"PET_{pet_type}_{pet_tier}"
                if clean_name in ("Pet", "Unknown Item", ""):
                    clean_name = f"[Lvl {pet_level}] {pet_tier.title()} {pet_type.replace('_', ' ').title()}"

        # Divan's armor gemstone chambers
        gemstone_chambers = parse_divan_gemstone_chambers(internal_id, extra_attrs)

        # Secondary flat addons
        art_of_war_count = 0
        for k in ("art_of_war_count", "art_of_war", "the_art_of_war"):
            if k in extra_attrs:
                try:
                    art_of_war_count = max(art_of_war_count, int(extra_attrs[k]))
                except (ValueError, TypeError):
                    pass

        wood_singularity_count = 0
        for k in ("wood_singularity_count", "wood_singularity"):
            if k in extra_attrs:
                try:
                    wood_singularity_count = max(wood_singularity_count, int(extra_attrs[k]))
                except (ValueError, TypeError):
                    pass

        transmission_tuners = 0
        for k in ("transmission_tuners", "tuners"):
            if k in extra_attrs:
                try:
                    transmission_tuners = max(transmission_tuners, int(extra_attrs[k]))
                except (ValueError, TypeError):
                    pass

        # Hot Potato / Fuming Potato Books (0-15)
        hot_potato_count = 0
        for k in ("hot_potato_count", "hot_potato_books"):
            if k in extra_attrs:
                try:
                    hot_potato_count = max(hot_potato_count, int(extra_attrs[k]))
                except (ValueError, TypeError):
                    pass

        # Pet candy used (0-10)
        pet_candy_used = 0
        if is_pet and pet_raw_info:
            try:
                pet_candy_used = int(pet_data.get("candyUsed", 0))
            except Exception:
                pass
        for k in ("pet_candy_used", "candyUsed"):
            if k in extra_attrs:
                try:
                    pet_candy_used = max(pet_candy_used, int(extra_attrs[k]))
                except (ValueError, TypeError):
                    pass

        # Wither Scrolls
        ability_scrolls = []
        raw_scrolls = extra_attrs.get("ability_scroll") or extra_attrs.get("ability_scrolls")
        if isinstance(raw_scrolls, list):
            ability_scrolls = [str(s).strip() for s in raw_scrolls if s]
        elif isinstance(raw_scrolls, str) and raw_scrolls.strip():
            ability_scrolls = [raw_scrolls.strip()]

        # Drill parts & Accessory Enrichments
        drill_part_engine = str(extra_attrs.get("drill_part_engine", "")).strip() or None
        drill_part_fuel_tank = str(extra_attrs.get("drill_part_fuel_tank", "")).strip() or None
        drill_part_upgrade_module = str(extra_attrs.get("drill_part_upgrade_module", "")).strip() or None
        talisman_enrichment = (
            str(extra_attrs.get("talisman_enrichment") or extra_attrs.get("enrichment") or "").strip() or None
        )

        prestige_tier = get_prestige_tier(internal_id)
        item_uuid = str(extra_attrs.get("uuid", "")).strip() or None

        return {
            "internal_id": internal_id,
            "item_name": clean_name or "Unknown Item",
            "modifier": modifier,
            "item_uuid": item_uuid,
            "rarity_upgrades": rarity_upgrades,
            "dungeon_stars": stars,
            "master_stars": master_stars,
            "gemstones": gemstones,
            "enchantments": enchantments,
            "attributes": attributes,
            "is_pet": is_pet,
            "pet_type": pet_type,
            "pet_tier": pet_tier,
            "effective_tier": effective_tier,
            "pet_level": pet_level,
            "pet_exp": pet_exp,
            "pet_held_item": pet_held_item,
            "pet_skin": pet_skin,
            "prestige_tier": prestige_tier,
            "gemstone_chambers": gemstone_chambers,
            "art_of_war_count": art_of_war_count,
            "wood_singularity_count": wood_singularity_count,
            "transmission_tuners": transmission_tuners,
            "hot_potato_count": hot_potato_count,
            "pet_candy_used": pet_candy_used,
            "ability_scrolls": ability_scrolls,
            "drill_part_engine": drill_part_engine,
            "drill_part_fuel_tank": drill_part_fuel_tank,
            "drill_part_upgrade_module": drill_part_upgrade_module,
            "talisman_enrichment": talisman_enrichment,
            "count": item_count,
        }
    except Exception:
        return default_payload
