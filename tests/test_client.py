"""Tests for HypixelClient pagination, filtering, and backoff handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.client import HypixelClient, HypixelClientError
from src.config import Settings


@pytest.fixture
def base_config() -> Settings:
    return Settings(DRY_RUN=True)


@pytest.mark.asyncio
async def test_fetch_all_auctions_pagination_and_bin_filter(base_config: Settings):
    """Test fetch_all_auctions correctly paginates across all pages and filters BIN listings."""
    client = HypixelClient(base_config)

    page_0_data = {
        "success": True,
        "page": 0,
        "totalPages": 2,
        "lastUpdated": 1000,
        "auctions": [
            {"uuid": "bin-1", "bin": True, "starting_bid": 5_000_000},
            {"uuid": "auction-1", "bin": False, "starting_bid": 1_000_000},
        ],
    }
    page_1_data = {
        "success": True,
        "page": 1,
        "totalPages": 2,
        "lastUpdated": 1000,
        "auctions": [
            {"uuid": "bin-2", "bin": True, "starting_bid": 8_000_000},
            {"uuid": "auction-2", "bin": False, "starting_bid": 2_000_000},
        ],
    }

    mock_session = MagicMock()

    async def fake_fetch_page(page, session, **kwargs):
        if page == 0:
            return page_0_data
        return page_1_data

    client.get_session = AsyncMock(return_value=mock_session)
    client.fetch_page = AsyncMock(side_effect=fake_fetch_page)

    result = await client.fetch_all_auctions()

    assert result is not None
    assert len(result) == 2
    assert [a["uuid"] for a in result] == ["bin-1", "bin-2"]
    assert client.last_updated == 1000


@pytest.mark.asyncio
async def test_fetch_all_auctions_unchanged_cycle(base_config: Settings):
    """Test that fetch_all_auctions returns None early if lastUpdated matches previous cycle."""
    client = HypixelClient(base_config)
    client.last_updated = 1000

    page_0_data = {
        "success": True,
        "page": 0,
        "totalPages": 2,
        "lastUpdated": 1000,
        "auctions": [],
    }

    mock_session = MagicMock()
    client.get_session = AsyncMock(return_value=mock_session)
    client.fetch_page = AsyncMock(return_value=page_0_data)

    result = await client.fetch_all_auctions()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_page_rate_limit_backoff(base_config: Settings):
    """Test fetch_page handles 429 response by waiting and retrying."""
    client = HypixelClient(base_config)

    resp_429 = MagicMock()
    resp_429.status = 429
    resp_429.headers = {"Retry-After": "0.1"}

    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.headers = {}
    resp_200.json = AsyncMock(return_value={"success": True, "totalPages": 1})

    cm_429 = MagicMock()
    cm_429.__aenter__ = AsyncMock(return_value=resp_429)
    cm_429.__aexit__ = AsyncMock(return_value=None)

    cm_200 = MagicMock()
    cm_200.__aenter__ = AsyncMock(return_value=resp_200)
    cm_200.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.side_effect = [cm_429, cm_200]

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        data = await client.fetch_page(0, mock_session, max_retries=2, initial_delay=0.1)

    assert data["success"] is True
    assert mock_session.get.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_ended_auctions_success(base_config: Settings):
    """Test fetch_ended_auctions successfully retrieves and returns ended auctions."""
    client = HypixelClient(base_config)

    ended_payload = {
        "success": True,
        "lastUpdated": 1791477006275,
        "auctions": [
            {
                "auction_id": "ended-1",
                "seller": "seller-1",
                "buyer": "buyer-1",
                "timestamp": 1791476942672,
                "price": 10_000_000,
                "bin": True,
                "item_bytes": "test_bytes_1",
            },
            {
                "auction_id": "ended-2",
                "seller": "seller-2",
                "buyer": "buyer-2",
                "timestamp": 1791476950000,
                "price": 25_000_000,
                "bin": True,
                "item_bytes": "test_bytes_2",
            },
        ],
    }

    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.json = AsyncMock(return_value=ended_payload)

    cm_200 = MagicMock()
    cm_200.__aenter__ = AsyncMock(return_value=resp_200)
    cm_200.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = cm_200
    client.get_session = AsyncMock(return_value=mock_session)

    results = await client.fetch_ended_auctions()

    assert len(results) == 2
    assert results[0]["auction_id"] == "ended-1"
    assert results[1]["auction_id"] == "ended-2"
    mock_session.get.assert_called_once_with(client.ENDED_AUCTIONS_URL)


@pytest.mark.asyncio
async def test_fetch_ended_auctions_rate_limit_backoff(base_config: Settings):
    """Test fetch_ended_auctions handles 429 rate limiting with retry and exponential backoff."""
    client = HypixelClient(base_config)

    resp_429 = MagicMock()
    resp_429.status = 429
    resp_429.headers = {"Retry-After": "0.1"}

    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.json = AsyncMock(return_value={"success": True, "auctions": [{"auction_id": "retry-success"}]})

    cm_429 = MagicMock()
    cm_429.__aenter__ = AsyncMock(return_value=resp_429)
    cm_429.__aexit__ = AsyncMock(return_value=None)

    cm_200 = MagicMock()
    cm_200.__aenter__ = AsyncMock(return_value=resp_200)
    cm_200.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.side_effect = [cm_429, cm_200]
    client.get_session = AsyncMock(return_value=mock_session)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        results = await client.fetch_ended_auctions(max_retries=2, initial_delay=0.1)

    assert len(results) == 1
    assert results[0]["auction_id"] == "retry-success"
    assert mock_session.get.call_count == 2
    mock_sleep.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_ended_auctions_exhausts_retries_raises_client_error(base_config: Settings):
    """Test fetch_ended_auctions raises HypixelClientError when retries are exhausted."""
    client = HypixelClient(base_config)

    resp_500 = AsyncMock()
    resp_500.status = 500

    cm_500 = MagicMock()
    cm_500.__aenter__ = AsyncMock(return_value=resp_500)
    cm_500.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = cm_500
    client.get_session = AsyncMock(return_value=mock_session)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(HypixelClientError, match="Exceeded max retries"):
            await client.fetch_ended_auctions(max_retries=2, initial_delay=0.01)
