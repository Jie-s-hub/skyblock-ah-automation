"""
Tests for Crimson Isle / Nether gear attributes, God Roll detection, and valuation.
"""

from __future__ import annotations

import base64
import io
from typing import Any
import nbtlib
import pytest

from src.config import Settings
from src.constants.attributes import (
    calculate_attribute_estimated_value,
    check_god_roll,
    format_attributes,
    to_roman_numeral,
    GOD_ROLL_BASE_VALUES,
)
from src.evaluator import FlipEvaluator, FlipResult
from src.nbt_decoder import decode_item_bytes
from src.notifier import DiscordNotifier


def make_synthetic_attribute_nbt(
    internal_id: str = "AURORA_CHESTPLATE",
    name: str = "§6Aurora Chestplate",
    attributes: dict[str, int] | None = None,
    stars: int = 0,
) -> str:
    """Helper to construct synthetic NBT with ExtraAttributes.attributes compound."""
    if attributes is None:
        attributes = {"mana_pool": 5, "mana_regeneration": 5}

    extra_fields: dict[str, Any] = {
        "id": nbtlib.String(internal_id),
        "attributes": nbtlib.Compound({
            k: nbtlib.Int(v) for k, v in attributes.items()
        }),
    }
    if stars > 0:
        extra_fields["dungeon_item_level"] = nbtlib.Int(stars)

    tag = nbtlib.Compound({
        "i": nbtlib.List[nbtlib.Compound]([
            nbtlib.Compound({
                "id": nbtlib.Short(311),
                "Count": nbtlib.Byte(1),
                "tag": nbtlib.Compound({
                    "display": nbtlib.Compound({"Name": nbtlib.String(name)}),
                    "ExtraAttributes": nbtlib.Compound(extra_fields),
                }),
            })
        ])
    })
    nbt_file = nbtlib.File(tag)
    buf = io.BytesIO()
    nbt_file.save(buf, gzipped=True)
    return base64.b64encode(buf.getvalue()).decode()


def test_roman_numeral_conversion():
    """Verify conversion of integer tiers to Roman numerals."""
    assert to_roman_numeral(1) == "I"
    assert to_roman_numeral(3) == "III"
    assert to_roman_numeral(4) == "IV"
    assert to_roman_numeral(5) == "V"
    assert to_roman_numeral(7) == "VII"
    assert to_roman_numeral(10) == "X"


def test_nbt_attribute_decoding():
    """Verify attributes compound tag is decoded into a clean dictionary."""
    nbt_b64 = make_synthetic_attribute_nbt(
        internal_id="CRIMSON_CHESTPLATE",
        attributes={"mana_pool": 5, "mana_regeneration": 5, "experience": 3},
    )
    decoded = decode_item_bytes(nbt_b64)
    assert "attributes" in decoded
    assert decoded["attributes"] == {
        "mana_pool": 5,
        "mana_regeneration": 5,
        "experience": 3,
    }


def test_check_god_roll_pairings():
    """Verify God Roll detection for Mage, Slayer, and Survivability pairings."""
    # Mage God Roll: mana_pool + mana_regeneration
    is_gr, gr_type, _ = check_god_roll({"mana_pool": 5, "mana_regeneration": 5})
    assert is_gr is True
    assert gr_type == "MAGE"

    # Slayer / Combat God Roll: magic_find + veteran
    is_gr, gr_type, _ = check_god_roll({"magic_find": 3, "veteran": 4})
    assert is_gr is True
    assert gr_type == "SLAYER"

    # Tank / Survivability God Roll: vitality + dominance
    is_gr, gr_type, _ = check_god_roll({"vitality": 6, "dominance": 6})
    assert is_gr is True
    assert gr_type == "SURVIVABILITY"

    # Non-god-roll pairings
    is_gr, gr_type, _ = check_god_roll({"mana_pool": 5, "speed": 4})
    assert is_gr is False
    assert gr_type is None

    is_gr, gr_type, _ = check_god_roll({"experience": 1})
    assert is_gr is False


def test_format_attributes_string():
    """Verify formatted string includes Roman numerals and flags God Rolls."""
    text_gr = format_attributes({"mana_pool": 5, "mana_regeneration": 5})
    assert "✦ Mana Pool V" in text_gr
    assert "✦ Mana Regeneration V" in text_gr
    assert "(GOD ROLL)" in text_gr

    text_regular = format_attributes({"speed": 3, "veteran": 2})
    assert "✦ Speed III" in text_regular
    assert "✦ Veteran II" in text_regular
    assert "(GOD ROLL)" not in text_regular


def test_calculate_attribute_estimated_value():
    """Verify floor pricing and tier level bonus scaling."""
    # Mage floor is 120M for Tier 1 pair (sum 2)
    # Tier 5 pair has sum 10 -> (10 - 2) * 5M = +40M -> total 160M
    is_gr, gr_type, est_val = calculate_attribute_estimated_value(
        {"mana_pool": 5, "mana_regeneration": 5},
        base_lbin=3_000_000,
    )
    assert is_gr is True
    assert gr_type == "MAGE"
    assert est_val == 160_000_000

    # High base LBIN (e.g. 70M) -> 2.5x multiplier = 175M > floor
    _, _, est_high = calculate_attribute_estimated_value(
        {"vitality": 1, "dominance": 1},
        base_lbin=70_000_000,
    )
    assert est_high == 175_000_000


def test_evaluator_god_roll_alert_detected():
    """
    Verify Kuudra armor with God Roll attributes listed at 35M is caught as a snipe,
    even though clean trash-roll LBIN is only 3M.
    """
    settings = Settings(
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=5_000_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings)
    # Trash-roll market baseline for Aurora Chestplate is 3M, 3.2M, 3.5M
    evaluator.set_price_cache({"AURORA_CHESTPLATE": [3_000_000, 3_200_000, 3_500_000]})

    auction = {
        "uuid": "gr-auction-uuid",
        "starting_bid": 35_000_000,
        "item_name": "Aurora Chestplate",
    }
    decoded = {
        "internal_id": "AURORA_CHESTPLATE",
        "item_name": "Aurora Chestplate",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
        "attributes": {
            "mana_pool": 5,
            "mana_regeneration": 5,
        },
    }

    # Estimated worth is 160,000,000 coins
    # Recomputed by hand per Phase 1 fee model (160M in >=100M tier: 2.5% creation + 1% collection = 3.5%):
    # Total fee = 160,000,000 * 0.035 = 5,600,000 -> Net revenue = 154,400,000
    # Net profit: 154,400,000 - 35,000,000 = 119,400,000 coins
    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_attribute_snipe is True
    assert flip.is_god_roll is True
    assert flip.god_roll_type == "MAGE"
    assert flip.target_resell_price == 160_000_000
    assert flip.net_profit == 119_400_000
    assert flip.is_priority is True


def test_evaluator_non_god_roll_trash_attributes_rejected():
    """Verify Kuudra armor with trash attributes listed at 35M is rejected."""
    settings = Settings(
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings)
    evaluator.set_price_cache({"AURORA_CHESTPLATE": [3_000_000, 3_200_000, 3_500_000]})

    auction = {
        "uuid": "trash-attr-uuid",
        "starting_bid": 35_000_000,
        "item_name": "Aurora Chestplate",
    }
    decoded = {
        "internal_id": "AURORA_CHESTPLATE",
        "item_name": "Aurora Chestplate",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
        "attributes": {
            "speed": 1,
            "experience": 1,
        },
    }

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is None


def test_attribute_discord_embed_formatting():
    """Verify Discord notifier formats Format A attribute embed correctly."""
    settings = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(settings)

    flip = FlipResult(
        auction_uuid="attr-uuid-123",
        internal_id="CRIMSON_CHESTPLATE",
        item_name="Crimson Chestplate",
        buy_price=35_000_000,
        target_resell_price=160_000_000,
        net_profit=121_800_000,
        margin_pct=348.0,
        is_attribute_snipe=True,
        is_god_roll=True,
        god_roll_type="MAGE",
        attributes={"mana_pool": 5, "mana_regeneration": 5},
    )

    payload = notifier.build_embed_payload(flip)
    assert "embeds" in payload
    embed = payload["embeds"][0]

    assert embed["title"] == "[ATTRIBUTE SNIPE] Crimson Chestplate"
    assert embed["color"] == 0xFF5500

    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert "✦ Mana Pool V" in field_map["Attributes"]
    assert "(GOD ROLL)" in field_map["Attributes"]
    assert "35,000,000 (35.00M)" in field_map["Price"]
    assert "160,000,000 (160.00M)" in field_map["Estimated Market Value"]
    assert "+121,800,000 coins (348.0%)" in field_map["Net Profit"]
    assert "```/viewauction attr-uuid-123```" in field_map["Quick Command"]


@pytest.mark.asyncio
async def test_dry_run_notifier_attribute_snipe(capsys: pytest.CaptureFixture):
    """Verify dry-run stdout logs formatted attribute snipe message."""
    settings = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(settings)

    flip = FlipResult(
        auction_uuid="attr-uuid-dry",
        internal_id="CRIMSON_CHESTPLATE",
        item_name="Crimson Chestplate",
        buy_price=35_000_000,
        target_resell_price=160_000_000,
        net_profit=121_800_000,
        margin_pct=348.0,
        is_attribute_snipe=True,
        is_god_roll=True,
        god_roll_type="MAGE",
        attributes={"mana_pool": 5, "mana_regeneration": 5},
    )

    success = await notifier.send_alert(flip)
    assert success is True

    captured = capsys.readouterr()
    assert "[DRY RUN ATTRIBUTE SNIPE] Crimson Chestplate" in captured.out
    assert "Mana Pool V" in captured.out
    assert "(GOD ROLL)" in captured.out
    assert "160,000,000 coins" in captured.out
