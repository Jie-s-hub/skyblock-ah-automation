"""Tests for star essence upgrade costs and reforge stone lookup tables."""

from __future__ import annotations

from src.constants.reforge_stones import REFORGE_STONE_DISPLAY_NAMES, REFORGE_STONE_MAP
from src.constants.star_costs import (
    DEFAULT_DUNGEON_STAR_COSTS,
    WITHER_ARMOR_STAR_COSTS,
    get_star_essence_cost,
)


def test_star_essence_cost_zero_or_negative():
    """Verify 0 or negative stars return 0 essence cost."""
    assert get_star_essence_cost("NECRON_CHESTPLATE", 0) == ("", 0)
    assert get_star_essence_cost("HYPERION", -1) == ("", 0)


def test_wither_armor_star_costs():
    """Verify Wither armor cumulative essence costs for stars 1 to 5."""
    assert sum(WITHER_ARMOR_STAR_COSTS[:1]) == 100
    assert sum(WITHER_ARMOR_STAR_COSTS[:2]) == 300
    assert sum(WITHER_ARMOR_STAR_COSTS[:3]) == 650
    assert sum(WITHER_ARMOR_STAR_COSTS[:4]) == 1250
    assert sum(WITHER_ARMOR_STAR_COSTS[:5]) == 2250

    for star in range(1, 6):
        expected_essence = sum(WITHER_ARMOR_STAR_COSTS[:star])
        assert get_star_essence_cost("NECRON_CHESTPLATE", star) == ("ESSENCE_WITHER", expected_essence)
        assert get_star_essence_cost("STORM_BOOTS", star) == ("ESSENCE_WITHER", expected_essence)
        assert get_star_essence_cost("MAXOR_LEGGINGS", star) == ("ESSENCE_WITHER", expected_essence)
        assert get_star_essence_cost("GOLDOR_HELMET", star) == ("ESSENCE_WITHER", expected_essence)


def test_wither_weapon_star_costs():
    """Verify Wither weapons use ESSENCE_WITHER and exact star costs."""
    assert get_star_essence_cost("HYPERION", 5) == ("ESSENCE_WITHER", 2250)
    assert get_star_essence_cost("VALKYRIE", 3) == ("ESSENCE_WITHER", 650)
    assert get_star_essence_cost("SCYLLA", 1) == ("ESSENCE_WITHER", 100)


def test_crimson_armor_star_costs():
    """Verify Crimson Isle Kuudra sets map to ESSENCE_CRIMSON."""
    assert get_star_essence_cost("CRIMSON_CHESTPLATE", 5) == ("ESSENCE_CRIMSON", 2250)
    assert get_star_essence_cost("AURORA_LEGGINGS", 3) == ("ESSENCE_CRIMSON", 650)
    assert get_star_essence_cost("TERROR_BOOTS", 1) == ("ESSENCE_CRIMSON", 100)
    assert get_star_essence_cost("BURNING_FERVOR_HELMET", 4) == ("ESSENCE_CRIMSON", 1250)


def test_dragon_gear_star_costs():
    """Verify Dragon armor maps to ESSENCE_DRAGON and default star costs."""
    expected_5_star = sum(DEFAULT_DUNGEON_STAR_COSTS[:5])  # 750
    assert get_star_essence_cost("SUPERIOR_DRAGON_CHESTPLATE", 5) == ("ESSENCE_DRAGON", expected_5_star)
    assert get_star_essence_cost("WISE_DRAGON_BOOTS", 2) == ("ESSENCE_DRAGON", 150)


def test_elemental_and_mob_gear_essence():
    """Verify Spider, Ice, Gold, and Diamond gear mapping."""
    # Spider
    assert get_star_essence_cost("TARANTULA_HELMET", 5) == ("ESSENCE_SPIDER", 750)
    # Ice
    assert get_star_essence_cost("FROZEN_SCYTHE", 5) == ("ESSENCE_ICE", 750)
    # Gold
    assert get_star_essence_cost("MIDAS_STAFF", 5) == ("ESSENCE_GOLD", 750)
    # Diamond
    assert get_star_essence_cost("PERFECT_CHESTPLATE", 5) == ("ESSENCE_DIAMOND", 750)


def test_default_undead_essence():
    """Verify standard dungeon weapons/armor default to ESSENCE_UNDEAD."""
    assert get_star_essence_cost("LIVID_DAGGER", 5) == ("ESSENCE_UNDEAD", 750)
    assert get_star_essence_cost("SHADOW_ASSASSIN_CHESTPLATE", 3) == ("ESSENCE_UNDEAD", 300)
    assert get_star_essence_cost("FELTHORN_REAPER", 1) == ("ESSENCE_UNDEAD", 50)


def test_star_clamping_above_five():
    """Verify stars above 5 are clamped to maximum 5-star essence."""
    assert get_star_essence_cost("HYPERION", 10) == ("ESSENCE_WITHER", 2250)


def test_reforge_stone_mappings():
    """Verify reforge stone map associates correct Bazaar IDs and display names."""
    assert REFORGE_STONE_MAP["ancient"] == "PRECURSOR_GEAR"
    assert REFORGE_STONE_MAP["withered"] == "WITHER_BLOOD"
    assert REFORGE_STONE_MAP["fabled"] == "DRAGON_CLAW"
    assert REFORGE_STONE_MAP["warped"] == "AOTE_STONE"
    assert REFORGE_STONE_MAP["loving"] == "RED_SCARF"
    assert REFORGE_STONE_MAP["jaded"] == "JADERALD"
    assert REFORGE_STONE_MAP["necrotic"] == "NECROMANCER_BROOCH"

    # Display names
    assert REFORGE_STONE_DISPLAY_NAMES["ancient"] == "Precursor Gear"
    assert REFORGE_STONE_DISPLAY_NAMES["jaded"] == "Jaderald"
    assert REFORGE_STONE_DISPLAY_NAMES["warped"] == "Warped Stone"
