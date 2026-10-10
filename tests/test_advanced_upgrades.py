"""
Tests for Slotted Gemstones, Master Stars (6-10), and Dynamic Retention Engine.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import nbtlib
import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.evaluator import FlipEvaluator, FlipResult
from src.nbt_decoder import (
    decode_item_bytes,
    parse_slotted_gemstones,
)
from src.networth_evaluator import (
    calculate_item_addons,
    get_dynamic_retention_rate,
)
from src.notifier import DiscordNotifier


def make_synthetic_nbt_with_gemstones(
    internal_id: str = "TERMINATOR",
    name: str = "§cTerminator §6✪✪✪§c✪✪",
    dungeon_stars: int = 8,
    rarity_upgrades: int = 1,
    modifier: str = "precise",
    gems: dict[str, Any] | None = None,
    enchantments: dict[str, int] | None = None,
) -> str:
    """Helper to build synthetic NBT payload with stars, gems, and enchants."""
    if enchantments is None:
        enchantments = {"overload": 5, "power": 7}

    extra_fields: dict[str, Any] = {
        "id": nbtlib.String(internal_id),
        "rarity_upgrades": nbtlib.Int(rarity_upgrades),
        "modifier": nbtlib.String(modifier),
        "dungeon_item_level": nbtlib.Int(dungeon_stars),
    }

    if gems:
        extra_fields["gems"] = nbtlib.Compound({k: nbtlib.String(v) for k, v in gems.items()})

    extra_fields["enchantments"] = nbtlib.Compound({k: nbtlib.Int(v) for k, v in enchantments.items()})

    tag = nbtlib.Compound(
        {
            "i": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "id": nbtlib.Short(261),
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


# ============================================================================
# 1. DYNAMIC RETENTION ENGINE TESTS
# ============================================================================


def test_dynamic_retention_rate_thresholds():
    """Verify tier-based dynamic retention rates across boundaries."""
    # Budget / Early-Mid Gear (< 50M) -> 50%
    assert get_dynamic_retention_rate(0) == 0.50
    assert get_dynamic_retention_rate(10_000_000) == 0.50
    assert get_dynamic_retention_rate(49_999_999) == 0.50

    # Mid-to-Late Gear (50M <= base < 500M) -> 70%
    assert get_dynamic_retention_rate(50_000_000) == 0.70
    assert get_dynamic_retention_rate(100_000_000) == 0.70
    assert get_dynamic_retention_rate(499_999_999) == 0.70

    # Endgame Staples (>= 500M) -> 85%
    assert get_dynamic_retention_rate(500_000_000) == 0.85
    assert get_dynamic_retention_rate(700_000_000) == 0.85
    assert get_dynamic_retention_rate(1_500_000_000) == 0.85


# ============================================================================
# 2. MASTER STAR (STARS 6-10) PARSING & PRICING TESTS
# ============================================================================


def test_master_star_extraction_from_nbt():
    """Verify stars 6-10 correctly map to master stars 1-5."""
    # 5 stars -> 0 master stars
    nbt_5 = make_synthetic_nbt_with_gemstones(dungeon_stars=5)
    dec_5 = decode_item_bytes(nbt_5)
    assert dec_5["dungeon_stars"] == 5
    assert dec_5["master_stars"] == 0

    # 6 stars -> 1 master star (First Master Star)
    nbt_6 = make_synthetic_nbt_with_gemstones(dungeon_stars=6)
    dec_6 = decode_item_bytes(nbt_6)
    assert dec_6["dungeon_stars"] == 6
    assert dec_6["master_stars"] == 1

    # 8 stars -> 3 master stars (First, Second, Third)
    nbt_8 = make_synthetic_nbt_with_gemstones(dungeon_stars=8)
    dec_8 = decode_item_bytes(nbt_8)
    assert dec_8["dungeon_stars"] == 8
    assert dec_8["master_stars"] == 3

    # 10 stars -> 5 master stars (All 5 master stars)
    nbt_10 = make_synthetic_nbt_with_gemstones(dungeon_stars=10)
    dec_10 = decode_item_bytes(nbt_10)
    assert dec_10["dungeon_stars"] == 10
    assert dec_10["master_stars"] == 5


def test_bazaar_master_star_pricing():
    """Verify BazaarCache master star pricing and compounding."""
    cache = BazaarCache()
    cache.set_mock_prices(
        {
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
            "FOURTH_MASTER_STAR": 45_000_000,
            "FIFTH_MASTER_STAR": 80_000_000,
        }
    )

    assert cache.get_master_star_price(1) == 5_000_000
    assert cache.get_master_star_price(2) == 12_000_000
    assert cache.get_master_star_price(3) == 25_000_000
    assert cache.get_master_star_price(4) == 45_000_000
    assert cache.get_master_star_price(5) == 80_000_000
    assert cache.get_master_star_price(6) == 0  # Out of range

    # Test networth calculation with 3 Master Stars (Star 8)
    # Sum of Star 1 + Star 2 + Star 3 = 5M + 12M + 25M = 42,000,000
    decoded = {
        "internal_id": "TERMINATOR",
        "dungeon_stars": 8,
        "master_stars": 3,
        "rarity_upgrades": 0,
        "gemstones": [],
        "enchantments": {},
    }
    addons, total_cost = calculate_item_addons(decoded, cache)
    ms_addon = next((a for a in addons if a["type"] == "master_star"), None)
    assert ms_addon is not None
    assert ms_addon["cost"] == 42_000_000
    assert ms_addon["name"] == "Master Stars: 3x Applied"


# ============================================================================
# 3. SLOTTED GEMSTONE EXTRACTION & PRICING TESTS
# ============================================================================


def test_parse_slotted_gemstones():
    """Verify slotted gemstone parsing handles various formats and rejects empty/locked slots."""
    # Standard slot index tags
    raw_gems = {
        "JASPER_0": "PERFECT",
        "RUBY_1": "FLAWLESS",
        "SAPPHIRE_2": "FINE",
        "TOPAZ_0": "FLAWED",
        "LOCKED_SLOTS": "3",  # Ignored
        "UNLOCKED_0": "true",  # Ignored
        "EMPTY_1": "empty",  # Ignored
        "AMETHYST_0": "LOCKED",  # Quality not in VALID_GEM_QUALITIES -> ignored
    }
    parsed = parse_slotted_gemstones(raw_gems)
    assert len(parsed) == 4
    types_quals = {(g["type"], g["quality"]) for g in parsed}
    assert ("JASPER", "PERFECT") in types_quals
    assert ("RUBY", "FLAWLESS") in types_quals
    assert ("SAPPHIRE", "FINE") in types_quals
    assert ("TOPAZ", "FLAWED") in types_quals


def test_parse_slotted_gemstones_companion_tags():
    """Verify companion type tags (UNIVERSAL_0 + UNIVERSAL_0_gem) are handled."""
    raw_gems = {
        "UNIVERSAL_0": "PERFECT",
        "UNIVERSAL_0_gem": "JASPER",
        "COMBAT_0": "FLAWLESS",
        "COMBAT_0_gem": "RUBY",
    }
    parsed = parse_slotted_gemstones(raw_gems)
    assert len(parsed) == 2
    types_quals = {(g["type"], g["quality"]) for g in parsed}
    assert ("JASPER", "PERFECT") in types_quals
    assert ("RUBY", "FLAWLESS") in types_quals


def test_bazaar_gemstone_pricing_and_breakdown():
    """Verify gemstone pricing from Bazaar and summary formatting in addon breakdown."""
    cache = BazaarCache()
    cache.set_mock_prices(
        {
            "PERFECT_JASPER_GEM": 18_000_000,
            "FLAWLESS_RUBY_GEM": 4_000_000,
        }
    )

    assert cache.get_gemstone_price("JASPER", "PERFECT") == 18_000_000
    assert cache.get_gemstone_price("RUBY", "FLAWLESS") == 4_000_000
    assert cache.get_gemstone_price("AMBER", "FINE") == 0  # Missing

    # 2x Perfect Jasper + 1x Flawless Ruby
    decoded = {
        "internal_id": "TERMINATOR",
        "dungeon_stars": 0,
        "master_stars": 0,
        "rarity_upgrades": 0,
        "gemstones": [
            {"type": "JASPER", "quality": "PERFECT"},
            {"type": "JASPER", "quality": "PERFECT"},
            {"type": "RUBY", "quality": "FLAWLESS"},
        ],
        "enchantments": {},
    }

    addons, total_cost = calculate_item_addons(decoded, cache)
    gem_addon = next((a for a in addons if a["type"] == "gemstones"), None)
    assert gem_addon is not None
    # 2 * 18M + 1 * 4M = 40,000,000
    assert gem_addon["cost"] == 40_000_000
    assert "2x Perfect Jasper" in gem_addon["name"]
    assert "1x Flawless Ruby" in gem_addon["name"]
    assert gem_addon["name"].startswith("Gemstones:")


# ============================================================================
# 4. END-TO-END VALUATION ENGINE WITH DYNAMIC RETENTION TESTS
# ============================================================================


def test_endgame_staple_valuation_85_percent_retention():
    """
    Test Endgame gear (base_lbin >= 500M):
    Terminator with 8 stars (3 Master Stars), 2x Perfect Jasper, Recomb, and Overload 5.
    Must apply 85% dynamic retention.
    """
    settings = Settings(
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=10_000_000,
        MIN_PROFIT_MARGIN_PCT=4.0,
        STAPLE_MIN_PROFIT_MARGIN_PCT=4.0,
        AH_TAX_RATE=0.02,
    )
    cache = BazaarCache()
    cache.set_mock_prices(
        {
            "RECOMBOBULATOR_3000": 10_000_000,
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
            "PERFECT_JASPER_GEM": 18_000_000,
            "ENCHANTMENT_OVERLOAD_5": 8_000_000,
        }
    )

    evaluator = FlipEvaluator(settings, bazaar_cache=cache)
    # Set clean price baseline for Terminator at 750,000,000 (>= 500M -> Endgame)
    evaluator.set_price_cache({"TERMINATOR": [750_000_000, 760_000_000, 770_000_000]})

    auction = {
        "uuid": "term-auction-uuid",
        "starting_bid": 770_000_000,
        "item_name": "Precise Terminator ✪✪✪✪✪✪✪✪",
    }
    decoded = {
        "internal_id": "TERMINATOR",
        "item_name": "Precise Terminator ✪✪✪✪✪✪✪✪",
        "dungeon_stars": 8,
        "master_stars": 3,
        "rarity_upgrades": 1,
        "modifier": "precise",
        "gemstones": [
            {"type": "JASPER", "quality": "PERFECT"},
            {"type": "JASPER", "quality": "PERFECT"},
        ],
        "enchantments": {
            "overload": 5,
        },
    }

    # Addons breakdown:
    # - Recomb: 10M
    # - Master stars (3x): 5M + 12M + 25M = 42M
    # - Gemstones (2x Perfect Jasper): 2 * 18M = 36M
    # - Enchants (Overload 5): 8M
    # Total addons: 10M + 42M + 36M + 8M = 96,000,000
    # Recomputed by hand per Phase 1 fee model (831.6M in >=100M tier: 2.5% creation + 1.0% collection = 3.5%):
    # Total fee = 831,600,000 * 0.035 = 29,106,000 -> Net revenue = 802,494,000
    # Buy price: 770,000,000 -> Net profit: 802,494,000 - 770,000,000 = 32,494,000 coins (margin 4.22% >= 4.0% staple threshold)

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is True
    assert flip.retention_rate == 0.85
    assert flip.master_stars == 3
    assert len(flip.gemstones) == 2
    assert flip.total_addon_cost == 96_000_000
    assert flip.intrinsic_worth == 750_000_000 + 96_000_000
    assert flip.target_resell_price == 831_600_000
    assert flip.net_profit == 32_494_000

    # Test Discord Embed contains Retention Rate Applied: 85%
    notifier = DiscordNotifier(settings)
    payload = notifier.build_embed_payload(flip)
    embed = payload["embeds"][0]
    field_map = {f["name"]: f["value"] for f in embed["fields"]}

    assert field_map["Retention Rate Applied"] == "85%"
    calc_str = field_map["Calculations"]
    assert "→ Master Stars: 3x Applied (42,000,000)" in calc_str
    assert "→ Gemstones: 2x Perfect Jasper (36,000,000)" in calc_str


def test_budget_gear_valuation_50_percent_retention():
    """
    Test Budget gear (base_lbin < 50M):
    Livid Dagger with Recomb and 1x Flawless Ruby.
    Must apply 50% dynamic retention.
    """
    settings = Settings(
        ENABLE_NETWORTH_VALUATION=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        AH_TAX_RATE=0.02,
    )
    cache = BazaarCache()
    cache.set_mock_prices(
        {
            "RECOMBOBULATOR_3000": 10_000_000,
            "FLAWLESS_RUBY_GEM": 4_000_000,
        }
    )

    evaluator = FlipEvaluator(settings, bazaar_cache=cache)
    # Clean baseline = 15,000,000 (< 50M -> Budget)
    evaluator.set_price_cache({"LIVID_DAGGER": [15_000_000, 16_000_000, 17_000_000]})

    auction = {
        "uuid": "livid-uuid",
        "starting_bid": 16_000_000,
        "item_name": "Fabled Livid Dagger",
    }
    decoded = {
        "internal_id": "LIVID_DAGGER",
        "item_name": "Fabled Livid Dagger",
        "dungeon_stars": 0,
        "master_stars": 0,
        "rarity_upgrades": 1,
        "modifier": "",
        "gemstones": [
            {"type": "RUBY", "quality": "FLAWLESS"},
        ],
        "enchantments": {},
    }

    # Total addons: 10M (recomb) + 4M (ruby) = 14,000,000
    # Dynamic Retention: 50% (0.50)
    # Resell baseline: 15,000,000 + round(14,000,000 * 0.50) = 15,000,000 + 7,000,000 = 22,000,000
    # Recomputed by hand per Phase 1 fee model (22M in 10M-100M tier: 2% creation + 1% collection = 3%):
    # Total fee = 22,000,000 * 0.03 = 660,000 -> Net revenue: 21,340,000
    # Net profit: 21,340,000 - 16,000,000 = 5,340,000 coins

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.is_networth_snipe is True
    assert flip.retention_rate == 0.50
    assert flip.target_resell_price == 22_000_000
    assert flip.net_profit == 5_340_000

    # Discord Embed verification
    notifier = DiscordNotifier(settings)
    payload = notifier.build_embed_payload(flip)
    embed = payload["embeds"][0]
    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert field_map["Retention Rate Applied"] == "50%"
    assert "→ Gemstones: 1x Flawless Ruby (4,000,000)" in field_map["Calculations"]


@pytest.mark.asyncio
async def test_dry_run_notifier_shows_master_stars_gems_and_retention(capsys: pytest.CaptureFixture):
    """Verify dry-run stdout formatting prints Master Stars, Gemstones, and Retention Rate Applied."""
    settings = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(settings)

    flip = FlipResult(
        auction_uuid="dry-term-uuid",
        internal_id="TERMINATOR",
        item_name="Precise Terminator ✪✪✪✪✪✪✪✪",
        buy_price=780_000_000,
        target_resell_price=831_600_000,
        net_profit=34_968_000,
        margin_pct=4.48,
        is_priority=True,
        staple_category="DUNGEON_WEAPON",
        is_networth_snipe=True,
        base_lbin=750_000_000,
        addon_breakdown=[
            {"type": "master_star", "name": "Master Stars: 3x Applied", "cost": 42_000_000},
            {"type": "gemstones", "name": "Gemstones: 2x Perfect Jasper", "cost": 36_000_000},
        ],
        total_addon_cost=78_000_000,
        intrinsic_worth=828_000_000,
        master_stars=3,
        gemstones=[{"type": "JASPER", "quality": "PERFECT"}, {"type": "JASPER", "quality": "PERFECT"}],
        retention_rate=0.85,
    )

    success = await notifier.send_alert(flip)
    assert success is True

    captured = capsys.readouterr()
    assert "[DRY RUN NETWORTH SNIPE]" in captured.out
    assert "Master Stars: 3x Applied (42,000,000)" in captured.out
    assert "Gemstones: 2x Perfect Jasper (36,000,000)" in captured.out
    assert "Retention Rate Applied: 85%" in captured.out
