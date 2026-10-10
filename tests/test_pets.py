"""
Tests for Pet Valuation Engine: NBT decoding, level calculations, held item valuations, and Discord embeds.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any
import nbtlib
import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.constants.pet_levels import (
    calculate_pet_level,
    get_pet_held_item_info,
    get_pet_level_premium,
    GDRAG_MAX_XP,
    PET_MAX_XP,
)
from src.evaluator import FlipEvaluator, FlipResult
from src.nbt_decoder import decode_item_bytes
from src.notifier import DiscordNotifier


def make_synthetic_pet_nbt(
    pet_type: str = "ENDER_DRAGON",
    tier: str = "LEGENDARY",
    exp: float = 25_353_230.0,
    held_item: str = "PET_ITEM_TIER_BOOST",
) -> str:
    """Helper to build synthetic NBT with ExtraAttributes.petInfo JSON string."""
    pet_dict = {
        "type": pet_type,
        "tier": tier,
        "exp": exp,
        "heldItem": held_item,
        "candyUsed": 0,
    }
    pet_json_str = json.dumps(pet_dict)

    extra_fields: dict[str, Any] = {
        "id": nbtlib.String("PET"),
        "petInfo": nbtlib.String(pet_json_str),
    }

    tag = nbtlib.Compound({
        "i": nbtlib.List[nbtlib.Compound]([
            nbtlib.Compound({
                "id": nbtlib.Short(397),
                "Count": nbtlib.Byte(1),
                "tag": nbtlib.Compound({
                    "display": nbtlib.Compound({"Name": nbtlib.String(f"§7[Lvl 100] §6{pet_type.title()}")}),
                    "ExtraAttributes": nbtlib.Compound(extra_fields),
                }),
            })
        ])
    })
    nbt_file = nbtlib.File(tag)
    buf = io.BytesIO()
    nbt_file.save(buf, gzipped=True)
    return base64.b64encode(buf.getvalue()).decode()


def test_decode_pet_nbt_extracts_pet_info():
    """Verify NBT decoder extracts deserialized petInfo and sets internal_id."""
    nbt_b64 = make_synthetic_pet_nbt(
        pet_type="GOLDEN_DRAGON",
        tier="LEGENDARY",
        exp=GDRAG_MAX_XP,
        held_item="PET_ITEM_MINOS_RELIC",
    )
    decoded = decode_item_bytes(nbt_b64)

    assert decoded["is_pet"] is True
    assert decoded["pet_type"] == "GOLDEN_DRAGON"
    assert decoded["pet_tier"] == "LEGENDARY"
    assert decoded["pet_level"] == 200
    assert decoded["pet_held_item"] == "PET_ITEM_MINOS_RELIC"
    assert decoded["internal_id"] == "PET_GOLDEN_DRAGON_LEGENDARY"


def test_calculate_pet_level_standard_pets():
    """Verify level calculations for standard 100-level pets across rarities."""
    # Level 1 on zero XP
    assert calculate_pet_level("SHEEP", "LEGENDARY", 0.0) == 1
    assert calculate_pet_level("SHEEP", "COMMON", -10.0) == 1

    # Max level on max XP
    assert calculate_pet_level("ENDER_DRAGON", "LEGENDARY", 25_353_230.0) == 100
    assert calculate_pet_level("ENDER_DRAGON", "LEGENDARY", 50_000_000.0) == 100  # Capped at 100
    assert calculate_pet_level("SHEEP", "COMMON", 5_624_785.0) == 100
    assert calculate_pet_level("BABY_YETI", "EPIC", 18_608_500.0) == 100

    # Intermediate levels
    lvl_mid = calculate_pet_level("ENDER_DRAGON", "LEGENDARY", 12_000_000.0)
    assert 60 <= lvl_mid <= 85


def test_calculate_pet_level_golden_dragon_200():
    """Verify Golden Dragon calculates up to Level 200."""
    assert calculate_pet_level("GOLDEN_DRAGON", "LEGENDARY", 0.0) == 1
    assert calculate_pet_level("GOLDEN_DRAGON", "LEGENDARY", 25_353_230.0) == 100
    assert calculate_pet_level("GOLDEN_DRAGON", "LEGENDARY", 117_853_230.0) == 150
    assert calculate_pet_level("GOLDEN_DRAGON", "LEGENDARY", GDRAG_MAX_XP) == 200
    assert calculate_pet_level("GOLDEN_DRAGON", "LEGENDARY", 300_000_000.0) == 200  # Capped at 200


def test_get_pet_held_item_info():
    """Verify pet held item name lookup and market valuation."""
    name, val = get_pet_held_item_info("PET_ITEM_TIER_BOOST")
    assert name == "Tier Boost"
    assert val == 50_000_000

    name, val = get_pet_held_item_info("PET_ITEM_MINOS_RELIC")
    assert name == "Minos Relic"
    assert val == 75_000_000

    name, val = get_pet_held_item_info("PET_ITEM_QUICK_CLAW")
    assert name == "Quick Claw"
    assert val == 60_000_000

    name, val = get_pet_held_item_info("")
    assert name == "None"
    assert val == 0

    # BazaarCache dynamic price override
    cache = BazaarCache()
    cache.set_mock_prices({"PET_ITEM_MINOS_RELIC": 85_000_000})
    name_bz, val_bz = get_pet_held_item_info("PET_ITEM_MINOS_RELIC", cache)
    assert name_bz == "Minos Relic"
    assert val_bz == 85_000_000


def test_get_pet_level_premium():
    """Verify level premiums for max-level meta pets."""
    # Golden dragon
    assert get_pet_level_premium("GOLDEN_DRAGON", "LEGENDARY", 200) == 400_000_000
    assert get_pet_level_premium("GOLDEN_DRAGON", "LEGENDARY", 150) == 200_000_000
    assert get_pet_level_premium("GOLDEN_DRAGON", "LEGENDARY", 100) == 0

    # Ender dragon
    assert get_pet_level_premium("ENDER_DRAGON", "LEGENDARY", 100) == 80_000_000
    assert get_pet_level_premium("ENDER_DRAGON", "LEGENDARY", 90) == 40_000_000
    assert get_pet_level_premium("ENDER_DRAGON", "LEGENDARY", 1) == 0

    # Baby yeti
    assert get_pet_level_premium("BABY_YETI", "EPIC", 100) == 15_000_000


def test_evaluator_pet_snipe_with_level_and_held_item():
    """
    Verify Level 100 Legendary Ender Dragon with Tier Boost listed at 350M
    is caught as a pet snipe when Level 1 clean base is 300M.
    Estimated worth = 300M + 80M premium + 50M tier boost = 430M.
    """
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=False,
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=10_000_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        AH_TAX_RATE=0.02,
    )
    cache = BazaarCache()
    cache.set_mock_prices({"PET_ITEM_TIER_BOOST": 50_000_000})

    evaluator = FlipEvaluator(settings, bazaar_cache=cache)
    # Set clean Level 1 base price for Legendary Ender Dragon at 300M
    evaluator.set_price_cache({"PET_ENDER_DRAGON_LEGENDARY": [300_000_000, 310_000_000, 320_000_000]})

    auction = {
        "uuid": "edrag-uuid",
        "starting_bid": 350_000_000,
        "item_name": "[Lvl 100] Legendary Ender Dragon",
    }
    decoded = {
        "internal_id": "PET_ENDER_DRAGON_LEGENDARY",
        "item_name": "[Lvl 100] Legendary Ender Dragon",
        "is_pet": True,
        "pet_type": "ENDER_DRAGON",
        "pet_tier": "LEGENDARY",
        "pet_level": 100,
        "pet_exp": 25_353_230.0,
        "pet_held_item": "PET_ITEM_TIER_BOOST",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }

    # Estimated worth: 300M + 80M (Lvl 100) + 50M (Tier Boost) = 430,000,000
    # Recomputed by hand per Phase 1 fee model (430M in >=100M tier: 2.5% creation + 1.0% collection = 3.5%):
    # Total fee = 430,000,000 * 0.035 = 15,050,000 -> Net revenue = 414,950,000
    # Net profit: 414,950,000 - 350,000,000 = 64,950,000 coins
    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_pet is True
    assert flip.pet_level == 100
    assert flip.pet_tier == "LEGENDARY"
    assert flip.pet_type == "ENDER_DRAGON"
    assert flip.held_item_value == 50_000_000
    assert flip.target_resell_price == 430_000_000
    assert flip.net_profit == 64_950_000
    assert flip.margin_pct == pytest.approx(18.56, abs=0.1)


def test_evaluator_clean_lvl_1_pet_no_held_item_falls_through_to_clean():
    """Verify Level 1 pet with no held item is evaluated via standard clean 2nd LBIN logic."""
    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=False,
        MIN_PROFIT_COINS=5_000_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        AH_TAX_RATE=0.02,
    )
    evaluator = FlipEvaluator(settings)
    # Market baseline: listed at 20M, next LBIN is 30M
    evaluator.set_price_cache({"PET_SHEEP_LEGENDARY": [20_000_000, 30_000_000, 35_000_000]})

    auction = {
        "uuid": "sheep-clean-uuid",
        "starting_bid": 20_000_000,
        "item_name": "[Lvl 1] Legendary Sheep",
    }
    decoded = {
        "internal_id": "PET_SHEEP_LEGENDARY",
        "item_name": "[Lvl 1] Legendary Sheep",
        "is_pet": True,
        "pet_type": "SHEEP",
        "pet_tier": "LEGENDARY",
        "pet_level": 1,
        "pet_exp": 0.0,
        "pet_held_item": "",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "modifier": "",
        "enchantments": {},
    }

    # Should evaluate as clean flip against 30M target resell
    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is False
    # Recomputed by hand per Phase 1 fee model (30M in 10M-100M tier: 2% creation + 1% collection = 3%):
    # Total fee = 30,000,000 * 0.03 = 900,000 -> net profit: 29.1M - 20M = 9.1M
    assert flip.net_profit == 9_100_000


def test_pet_discord_embed_formatting():
    """Verify Discord notifier formats Format B Pet Snipe embed correctly."""
    settings = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(settings)

    flip = FlipResult(
        auction_uuid="pet-uuid-456",
        internal_id="PET_ENDER_DRAGON_LEGENDARY",
        item_name="[Lvl 100] Legendary Ender Dragon",
        buy_price=350_000_000,
        target_resell_price=430_000_000,
        net_profit=71_400_000,
        margin_pct=20.4,
        is_pet=True,
        pet_type="ENDER_DRAGON",
        pet_tier="LEGENDARY",
        pet_level=100,
        pet_held_item="PET_ITEM_TIER_BOOST",
        pet_held_item_name="Tier Boost",
        held_item_value=50_000_000,
        base_lvl_1_price=300_000_000,
    )

    payload = notifier.build_embed_payload(flip)
    assert "embeds" in payload
    embed = payload["embeds"][0]

    assert embed["title"] == "[PET SNIPE] [Lvl 100] Legendary Ender Dragon"
    assert embed["color"] == 0x3498DB  # Sky blue

    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert "✦ Tier Boost (50,000,000)" in field_map["Held Item"]
    assert "350,000,000 (350.00M)" in field_map["Price"]
    assert "300,000,000 (300.00M)" in field_map["Base Lvl 1 Price"]
    assert "430,000,000 (430.00M)" in field_map["Estimated Worth"]
    assert "+71,400,000 coins (20.4%)" in field_map["Net Profit"]
    assert "```/viewauction pet-uuid-456```" in field_map["Quick Command"]


@pytest.mark.asyncio
async def test_dry_run_notifier_pet_snipe(capsys: pytest.CaptureFixture):
    """Verify dry-run stdout logs formatted pet snipe alert."""
    settings = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(settings)

    flip = FlipResult(
        auction_uuid="pet-dry-789",
        internal_id="PET_ENDER_DRAGON_LEGENDARY",
        item_name="[Lvl 100] Legendary Ender Dragon",
        buy_price=350_000_000,
        target_resell_price=430_000_000,
        net_profit=71_400_000,
        margin_pct=20.4,
        is_pet=True,
        pet_type="ENDER_DRAGON",
        pet_tier="LEGENDARY",
        pet_level=100,
        pet_held_item="PET_ITEM_TIER_BOOST",
        pet_held_item_name="Tier Boost",
        held_item_value=50_000_000,
        base_lvl_1_price=300_000_000,
    )

    success = await notifier.send_alert(flip)
    assert success is True

    captured = capsys.readouterr()
    assert "[DRY RUN PET SNIPE] [Lvl 100] Legendary Ender Dragon" in captured.out
    assert "Held Item:        ✦ Tier Boost (50,000,000)" in captured.out
    assert "Base Lvl 1 Price: 300,000,000 coins" in captured.out
    assert "Estimated Worth:  430,000,000 coins" in captured.out
    assert "+71,400,000 coins (20.4%)" in captured.out
