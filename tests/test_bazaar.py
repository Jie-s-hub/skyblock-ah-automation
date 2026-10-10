"""Tests for BazaarCache market pricing, TTL handling, and HTTP fetching."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bazaar import BazaarCache
from src.config import Settings


def test_bazaar_cache_initialization():
    """Verify default initialization and configuration."""
    config = Settings(BAZAAR_CACHE_TTL_SECONDS=120)
    cache = BazaarCache(config=config, cache_ttl=120.0)
    assert cache.cache_ttl == 120.0
    assert cache.is_cache_stale() is True
    assert cache.get_buy_price("RECOMBOBULATOR_3000") is None
    assert cache.get_sell_price("RECOMBOBULATOR_3000") is None


def test_set_mock_prices_and_getters():
    """Verify set_mock_prices populates prices and normalizes product IDs."""
    cache = BazaarCache(cache_ttl=60.0)
    cache.set_mock_prices(
        buy_prices={
            "recombobulator_3000": 9_500_000,
            "  ESSENCE_WITHER  ": 3_500,
            "ENCHANTMENT_ULTIMATE_SOUL_EATER_5": 15_000_000,
        },
        sell_prices={
            "RECOMBOBULATOR_3000": 9_000_000,
        },
    )

    assert cache.is_cache_stale() is False
    assert cache.get_buy_price("RECOMBOBULATOR_3000") == 9_500_000.0
    assert cache.get_buy_price("recombobulator_3000") == 9_500_000.0
    assert cache.get_buy_price("ESSENCE_WITHER") == 3_500.0
    assert cache.get_buy_price("ENCHANTMENT_ULTIMATE_SOUL_EATER_5") == 15_000_000.0

    # Sell price check
    assert cache.get_sell_price("RECOMBOBULATOR_3000") == 9_000_000.0
    # Fallback to buy price if sell price wasn't separately specified for product
    assert cache.get_sell_price("ESSENCE_WITHER") is None

    # Missing product
    assert cache.get_buy_price("NON_EXISTENT_ITEM") is None


def test_cache_stale_after_ttl():
    """Verify is_cache_stale returns True after cache_ttl has elapsed."""
    cache = BazaarCache(cache_ttl=0.1)
    cache.set_mock_prices({"RECOMBOBULATOR_3000": 10_000_000})
    assert cache.is_cache_stale() is False

    time.sleep(0.15)
    assert cache.is_cache_stale() is True


@pytest.mark.asyncio
async def test_fetch_bazaar_data_success():
    """Verify fetch_bazaar_data parses products, quick_status, and fallbacks."""
    cache = BazaarCache(cache_ttl=60.0)

    mock_payload = {
        "success": True,
        "lastUpdated": 1700000000,
        "products": {
            "RECOMBOBULATOR_3000": {
                "product_id": "RECOMBOBULATOR_3000",
                "quick_status": {
                    "buyPrice": 9_800_000.0,
                    "sellPrice": 9_200_000.0,
                },
            },
            "ESSENCE_WITHER": {
                "product_id": "ESSENCE_WITHER",
                "quick_status": {
                    "buyPrice": 0.0,  # Test fallback to buy_summary
                    "sellPrice": 0.0,
                },
                "buy_summary": [{"pricePerUnit": 3_600.0}],
                "sell_summary": [{"pricePerUnit": 3_400.0}],
            },
        },
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=mock_payload)

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.get.return_value.__aenter__.return_value = mock_resp

    cache._session = mock_session
    cache._owns_session = False

    result = await cache.fetch_bazaar_data(force=True)
    assert result is True
    assert cache.get_buy_price("RECOMBOBULATOR_3000") == 9_800_000.0
    assert cache.get_sell_price("RECOMBOBULATOR_3000") == 9_200_000.0
    assert cache.get_buy_price("ESSENCE_WITHER") == 3_600.0
    assert cache.get_sell_price("ESSENCE_WITHER") == 3_400.0


@pytest.mark.asyncio
async def test_fetch_bazaar_data_skipped_when_fresh():
    """Verify fetch_bazaar_data skips network call when cache is not stale."""
    cache = BazaarCache(cache_ttl=300.0)
    cache.set_mock_prices({"RECOMBOBULATOR_3000": 9_500_000})

    mock_session = MagicMock()
    cache._session = mock_session

    result = await cache.fetch_bazaar_data(force=False)
    assert result is True
    mock_session.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_bazaar_data_http_error():
    """Verify HTTP error returns False and retains any previous cache."""
    cache = BazaarCache(cache_ttl=60.0)
    cache.set_mock_prices({"RECOMBOBULATOR_3000": 9_500_000})

    mock_resp = AsyncMock()
    mock_resp.status = 500

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    cache._session = mock_session
    cache._owns_session = False

    result = await cache.fetch_bazaar_data(force=True)
    assert result is False
    assert cache.get_buy_price("RECOMBOBULATOR_3000") == 9_500_000.0


@pytest.mark.asyncio
async def test_fetch_bazaar_data_success_false():
    """Verify API returning success=False returns False."""
    cache = BazaarCache(cache_ttl=60.0)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"success": False, "cause": "Rate limited"})

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    cache._session = mock_session
    cache._owns_session = False

    result = await cache.fetch_bazaar_data(force=True)
    assert result is False


@pytest.mark.asyncio
async def test_fetch_bazaar_data_exception_handling():
    """Verify network exceptions are caught and logged without crashing."""
    cache = BazaarCache(cache_ttl=60.0)

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.get.side_effect = Exception("Connection refused")
    cache._session = mock_session
    cache._owns_session = False

    result = await cache.fetch_bazaar_data(force=True)
    assert result is False


@pytest.mark.asyncio
async def test_bazaar_cache_context_manager_and_close():
    """Verify context manager and close() release resources."""
    mock_session = AsyncMock()
    mock_session.closed = False

    cache = BazaarCache()
    cache._session = mock_session
    cache._owns_session = True

    await cache.close()
    mock_session.close.assert_awaited_once()
