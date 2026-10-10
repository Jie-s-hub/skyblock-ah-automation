"""Tests for advanced networth calculation: 8-star Necron's Chestplate with gems, enchants, and dynamic retention rates."""

from __future__ import annotations

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.networth_evaluator import (
    calculate_item_addons,
    calculate_item_networth,
    get_dynamic_retention_rate,
)


@pytest.fixture
def bz_cache() -> BazaarCache:
    config = Settings(DRY_RUN=True)
    cache = BazaarCache(config)
    cache.set_mock_prices(
        {
            "RECOMBOBULATOR_3000": 10_000_000,
            "ESSENCE_WITHER": 4_000,
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
            "PERFECT_JASPER_GEM": 20_000_000,
            "FLAWLESS_RUBY_GEM": 6_000_000,
            "ENCHANTMENT_ULTIMATE_WISDOM_1": 250_000,
            "PRECURSOR_GEAR": 8_000_000,
        }
    )
    return cache


def test_eight_star_recombed_gemstone_necron_chestplate_worth(bz_cache: BazaarCache):
    """
    Mock an 8-star Recombed Necron's Chestplate:
    - Stars: 8 (5 essence stars = 2,250 Wither Essence @ 4k = 9,000,000; 3 Master Stars = 5M + 12M + 25M = 42,000,000)
    - Recomb: 10,000,000
    - Gems: 2x Perfect Jasper (2 * 20M = 40M) + 1x Flawless Ruby (6M) = 46,000,000
    - Enchants: Wisdom 5 (16 * 250k = 4,000,000)
    - Reforge: Ancient (Precursor Gear = 8,000,000)

    Total Addon Cost: 9M + 42M + 10M + 46M + 4M + 8M = 119,000,000 coins.
    """
    decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "dungeon_stars": 8,
        "master_stars": 3,
        "rarity_upgrades": 1,
        "gemstones": [
            {"type": "JASPER", "quality": "PERFECT"},
            {"type": "JASPER", "quality": "PERFECT"},
            {"type": "RUBY", "quality": "FLAWLESS"},
        ],
        "enchantments": {
            "wisdom": 5,
        },
        "modifier": "ancient",
    }

    breakdown, total_cost = calculate_item_addons(decoded, bz_cache)
    assert total_cost == 119_000_000

    # Base LBIN = 70M (Tier: Mid-to-Late -> 70% retention)
    base_lbin = 70_000_000
    breakdown, total_addon_cost, intrinsic_worth, retention_rate, resell_baseline = calculate_item_networth(
        base_lbin=base_lbin,
        decoded_data=decoded,
        bazaar_cache=bz_cache,
    )

    assert total_addon_cost == 119_000_000
    assert intrinsic_worth == base_lbin + 119_000_000  # 189,000,000
    assert retention_rate == 0.70
    expected_resell = base_lbin + int(round(119_000_000 * 0.70))  # 70M + 83.3M = 153,300,000
    assert resell_baseline == expected_resell


def test_dynamic_retention_rate_thresholds():
    """Verify tier-based dynamic retention rates: 85% for >= 500M, 70% for 50M-500M, 50% for < 50M."""
    # Endgame (>= 500M)
    assert get_dynamic_retention_rate(500_000_000) == 0.85
    assert get_dynamic_retention_rate(1_500_000_000) == 0.85

    # Mid-to-Late (50M <= base < 500M)
    assert get_dynamic_retention_rate(499_999_999) == 0.70
    assert get_dynamic_retention_rate(50_000_000) == 0.70
    assert get_dynamic_retention_rate(100_000_000) == 0.70

    # Early-to-Mid (< 50M)
    assert get_dynamic_retention_rate(49_999_999) == 0.50
    assert get_dynamic_retention_rate(10_000_000) == 0.50
    assert get_dynamic_retention_rate(1_000_000) == 0.50
