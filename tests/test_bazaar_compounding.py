"""Tests for Bazaar enchanted book anvil compounding math and Master Star lookups."""

from __future__ import annotations

import pytest

from src.bazaar import BazaarCache
from src.config import Settings


@pytest.fixture
def bz_cache() -> BazaarCache:
    config = Settings(DRY_RUN=True)
    cache = BazaarCache(config)
    cache.set_mock_prices(
        {
            # Tier 1 books traded on Bazaar
            "ENCHANTMENT_ULTIMATE_WISDOM_1": 250_000,
            "ENCHANTMENT_ULTIMATE_WISE_1": 150_000,
            "ENCHANTMENT_REJUVENATE_1": 50_000,
            # Exact higher tier book
            "ENCHANTMENT_GROWTH_6": 12_000_000,
            "ENCHANTMENT_GROWTH_1": 100_000,
            # Master Stars
            "FIRST_MASTER_STAR": 5_000_000,
            "SECOND_MASTER_STAR": 12_000_000,
            "THIRD_MASTER_STAR": 25_000_000,
            "FOURTH_MASTER_STAR": 55_000_000,
            "FIFTH_MASTER_STAR": 110_000_000,
        }
    )
    return cache


def test_wisdom_5_compounding(bz_cache: BazaarCache):
    """
    Wisdom 5 traded as Wisdom 1 on Bazaar:
    Cost = 2^(5 - 1) * Price(Wisdom 1) = 16 * 250,000 = 4,000,000
    """
    cost = bz_cache.get_enchant_price("wisdom", 5)
    assert cost == 4_000_000


def test_ultimate_wise_5_compounding(bz_cache: BazaarCache):
    """
    Ultimate Wise 5 traded as Ultimate Wise 1 on Bazaar:
    Cost = 2^(5 - 1) * Price(Ultimate Wise 1) = 16 * 150,000 = 2,400,000
    """
    cost = bz_cache.get_enchant_price("ultimate_wise", 5)
    assert cost == 2_400_000


def test_exact_tier_takes_precedence_over_compounding(bz_cache: BazaarCache):
    """
    Growth 6 has an exact key (12,000,000). It should use the exact price
    instead of compounding 2^(6 - 1) * 100,000 = 3,200,000.
    """
    cost = bz_cache.get_enchant_price("growth", 6)
    assert cost == 12_000_000


def test_missing_enchant_returns_zero_safely(bz_cache: BazaarCache):
    """Verify that a completely missing enchantment safely resolves to 0 without crashing."""
    cost = bz_cache.get_enchant_price("non_existent_enchant", 5)
    assert cost == 0


def test_master_star_lookups(bz_cache: BazaarCache):
    """Verify Master Star index 1-5 lookups."""
    assert bz_cache.get_master_star_price(1) == 5_000_000
    assert bz_cache.get_master_star_price(2) == 12_000_000
    assert bz_cache.get_master_star_price(3) == 25_000_000
    assert bz_cache.get_master_star_price(4) == 55_000_000
    assert bz_cache.get_master_star_price(5) == 110_000_000
    # Out of range stars
    assert bz_cache.get_master_star_price(0) == 0
    assert bz_cache.get_master_star_price(6) == 0
