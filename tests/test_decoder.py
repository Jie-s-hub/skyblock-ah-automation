"""Tests for NBT decoder and color code stripping."""

from __future__ import annotations

import base64
import io
from typing import Any

import nbtlib

from src.nbt_decoder import decode_item_bytes, strip_minecraft_color_codes


def make_synthetic_nbt(
    internal_id: str = "HYPERION",
    name: str = "§6§lHyperion§r",
    rarity_upgrades: int = 1,
    dungeon_stars: int = 5,
    upgrade_level: int = 0,
    enchantments: dict[str, int] | None = None,
) -> str:
    """Helper to generate base64-encoded, gzip-compressed synthetic NBT payload."""
    if enchantments is None:
        enchantments = {"sharpness": 7, "critical": 6}

    extra_fields: dict[str, Any] = {
        "id": nbtlib.String(internal_id),
        "rarity_upgrades": nbtlib.Int(rarity_upgrades),
    }
    if dungeon_stars > 0:
        extra_fields["dungeon_item_level"] = nbtlib.Int(dungeon_stars)
    if upgrade_level > 0:
        extra_fields["upgrade_level"] = nbtlib.Int(upgrade_level)

    extra_fields["enchantments"] = nbtlib.Compound({k: nbtlib.Int(v) for k, v in enchantments.items()})

    tag = nbtlib.Compound(
        {
            "i": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "id": nbtlib.Short(276),
                            "Count": nbtlib.Byte(1),
                            "tag": nbtlib.Compound(
                                {
                                    "display": nbtlib.Compound({"Name": nbtlib.String(name)}),
                                    "ExtraAttributes": nbtlib.Compound(extra_fields),
                                }
                            ),
                        }
                    )
                ]
            )
        }
    )
    nbt_file = nbtlib.File(tag)
    buf = io.BytesIO()
    nbt_file.save(buf, gzipped=True)
    return base64.b64encode(buf.getvalue()).decode()


def test_strip_minecraft_color_codes():
    """Verify Minecraft color codes §0-9 and §a-f, §k-r are properly removed."""
    assert strip_minecraft_color_codes("§6§lHyperion§r") == "Hyperion"
    assert strip_minecraft_color_codes("§cAncient §bNecron's Chestplate") == "Ancient Necron's Chestplate"
    assert strip_minecraft_color_codes("Clean Item") == "Clean Item"
    assert strip_minecraft_color_codes("") == ""


def test_decode_valid_item_nbt():
    """Verify complete extraction of internal ID, name, stars, recomb, and enchants."""
    raw_b64 = make_synthetic_nbt(
        internal_id="HYPERION",
        name="§6Heroic Hyperion ✪✪✪✪✪§r",
        rarity_upgrades=1,
        dungeon_stars=5,
        enchantments={"sharpness": 7, "smite": 7, "chimera": 5},
    )

    result = decode_item_bytes(raw_b64)

    assert result["internal_id"] == "HYPERION"
    assert result["item_name"] == "Heroic Hyperion ✪✪✪✪✪"
    assert result["rarity_upgrades"] == 1
    assert result["dungeon_stars"] == 5
    assert result["enchantments"] == {"sharpness": 7, "smite": 7, "chimera": 5}


def test_decode_upgrade_level_fallback():
    """Verify stars extraction falls back to upgrade_level when dungeon_item_level is absent."""
    raw_b64 = make_synthetic_nbt(
        internal_id="JUJU_SHORTBOW",
        name="§5Juju Shortbow",
        rarity_upgrades=0,
        dungeon_stars=0,
        upgrade_level=3,
        enchantments={"power": 6},
    )

    result = decode_item_bytes(raw_b64)

    assert result["internal_id"] == "JUJU_SHORTBOW"
    assert result["dungeon_stars"] == 3
    assert result["rarity_upgrades"] == 0
    assert result["enchantments"] == {"power": 6}


def test_decode_dict_wrapper():
    """Verify decode_item_bytes handles dict input {'data': '<base64>'}."""
    raw_b64 = make_synthetic_nbt(internal_id="TERMINATOR", name="§6Terminator")
    wrapped = {"data": raw_b64}

    result = decode_item_bytes(wrapped)
    assert result["internal_id"] == "TERMINATOR"
    assert result["item_name"] == "Terminator"


def test_decode_malformed_and_empty_inputs():
    """Verify robust fallback for empty, non-base64, or invalid gzip data."""
    for bad_input in [None, "", "not_base64_!@#$", base64.b64encode(b"not gzip").decode()]:
        result = decode_item_bytes(bad_input)
        assert result["internal_id"] == "UNKNOWN"
        assert result["item_name"] == "Unknown Item"
        assert result["rarity_upgrades"] == 0
        assert result["dungeon_stars"] == 0
        assert result["enchantments"] == {}


def test_decode_missing_extra_attributes():
    """Verify safe fallback when NBT does not contain ExtraAttributes."""
    tag = nbtlib.Compound(
        {
            "i": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "id": nbtlib.Short(1),
                            "Count": nbtlib.Byte(64),
                            "tag": nbtlib.Compound(
                                {
                                    "display": nbtlib.Compound({"Name": nbtlib.String("§fStone")}),
                                }
                            ),
                        }
                    )
                ]
            )
        }
    )
    nbt_file = nbtlib.File(tag)
    buf = io.BytesIO()
    nbt_file.save(buf, gzipped=True)
    raw_b64 = base64.b64encode(buf.getvalue()).decode()

    result = decode_item_bytes(raw_b64)
    assert result["internal_id"] == "UNKNOWN"
    assert result["item_name"] == "Stone"
    assert result["rarity_upgrades"] == 0
    assert result["dungeon_stars"] == 0
    assert result["enchantments"] == {}


def test_decode_crimson_isle_stars_never_master_stars():
    """Verify Crimson Isle armor/gear with 6-10 stars decodes master_stars as 0."""
    # 10-star Aurora Chestplate using upgrade_level
    raw_b64 = make_synthetic_nbt(
        internal_id="AURORA_CHESTPLATE",
        name="§cAurora Chestplate ✪✪✪✪✪",
        dungeon_stars=0,
        upgrade_level=10,
    )
    result = decode_item_bytes(raw_b64)
    assert result["internal_id"] == "AURORA_CHESTPLATE"
    assert result["dungeon_stars"] == 10
    assert result["master_stars"] == 0

    # 7-star Hot Crimson Helmet with dungeon_item_level set synthetically
    raw_b64_hot = make_synthetic_nbt(
        internal_id="HOT_CRIMSON_HELMET",
        name="§cHot Crimson Helmet ✪✪",
        dungeon_stars=7,
    )
    result_hot = decode_item_bytes(raw_b64_hot)
    assert result_hot["internal_id"] == "HOT_CRIMSON_HELMET"
    assert result_hot["dungeon_stars"] == 7
    assert result_hot["master_stars"] == 0

    # 8-star Slug Boots (Crimson equipment)
    raw_b64_slug = make_synthetic_nbt(
        internal_id="SLUG_BOOTS",
        name="§cSlug Boots ✪✪✪",
        dungeon_stars=0,
        upgrade_level=8,
    )
    result_slug = decode_item_bytes(raw_b64_slug)
    assert result_slug["internal_id"] == "SLUG_BOOTS"
    assert result_slug["dungeon_stars"] == 8
    assert result_slug["master_stars"] == 0


def test_decode_non_dungeon_without_dungeon_level_has_zero_master_stars():
    """Verify non-dungeon item with upgrade_level > 5 never gets master stars."""
    raw_b64 = make_synthetic_nbt(
        internal_id="BALLOON_SNAKE",
        name="§6Balloon Snake",
        dungeon_stars=0,
        upgrade_level=8,
    )
    result = decode_item_bytes(raw_b64)
    assert result["internal_id"] == "BALLOON_SNAKE"
    assert result["dungeon_stars"] == 8
    assert result["master_stars"] == 0
