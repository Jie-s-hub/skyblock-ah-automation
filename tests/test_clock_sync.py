"""
Tests for sub-second API clock synchronization, next poll delay calculation, and header tracking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.client import HypixelClient, calculate_next_poll_delay
from src.config import Settings


def test_calculate_next_poll_delay_normal_halfway():
    """Verify next poll delay when cache is halfway through 60s lifecycle."""
    current_time = 1000.0
    # last_updated was 30 seconds ago (970.0s -> 970,000 ms)
    last_updated_ms = 970_000
    # target_refresh = 970.0 + 60.0 = 1030.0
    # delay = (1030.0 - 1000.0) + 0.5 = 30.5s
    delay = calculate_next_poll_delay(last_updated_ms, current_time=current_time)
    assert delay == pytest.approx(30.5, abs=0.01)


def test_calculate_next_poll_delay_just_updated():
    """Verify next poll delay right after cache updated."""
    current_time = 1000.0
    # last_updated was 0.5s ago (999.5s -> 999,500 ms)
    last_updated_ms = 999_500
    # target_refresh = 999.5 + 60.0 = 1059.5
    # delay = (1059.5 - 1000.0) + 0.5 = 60.0s (clamped to 60.0)
    delay = calculate_next_poll_delay(last_updated_ms, current_time=current_time)
    assert delay == 60.0


def test_calculate_next_poll_delay_near_expiration():
    """Verify next poll delay when cache is 58 seconds old."""
    current_time = 1000.0
    # last_updated was 58 seconds ago (942.0s -> 942,000 ms)
    last_updated_ms = 942_000
    # target_refresh = 942.0 + 60.0 = 1002.0
    # delay = (1002.0 - 1000.0) + 0.5 = 2.5s
    delay = calculate_next_poll_delay(last_updated_ms, current_time=current_time)
    assert delay == pytest.approx(2.5, abs=0.01)


def test_calculate_next_poll_delay_already_expired_clamped_to_one():
    """Verify next poll delay clamps to minimum 1.0 second when cache is already expired."""
    current_time = 1000.0
    # last_updated was 70 seconds ago (930.0s -> 930,000 ms)
    last_updated_ms = 930_000
    # target_refresh = 930.0 + 60.0 = 990.0 (past)
    # delay = (990.0 - 1000.0) + 0.5 = -9.5s -> clamped to 1.0
    delay = calculate_next_poll_delay(last_updated_ms, current_time=current_time)
    assert delay == 1.0


def test_calculate_next_poll_delay_future_skew_clamped_to_sixty():
    """Verify next poll delay clamps to maximum 60.0 seconds on future clock drift."""
    current_time = 1000.0
    # last_updated is in the future
    last_updated_ms = 1_050_000
    delay = calculate_next_poll_delay(last_updated_ms, current_time=current_time)
    assert delay == 60.0


def test_calculate_next_poll_delay_zero_or_negative_defaults_to_sixty():
    """Verify invalid/zero timestamp safely returns default 60.0s."""
    assert calculate_next_poll_delay(0) == 60.0
    assert calculate_next_poll_delay(-500) == 60.0


@pytest.mark.asyncio
async def test_hypixel_client_tracks_last_updated_and_headers():
    """Verify HypixelClient populates last_updated_ms and captures HTTP date headers."""
    config = Settings(DRY_RUN=True)
    client = HypixelClient(config)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {
        "Last-Modified": "Fri, 09 Oct 2026 03:00:00 GMT",
        "Date": "Fri, 09 Oct 2026 03:00:15 GMT",
    }
    mock_resp.json = AsyncMock(
        return_value={
            "success": True,
            "page": 0,
            "totalPages": 1,
            "lastUpdated": 1_700_000_000_000,
            "auctions": [
                {"uuid": "bin-test", "bin": True, "starting_bid": 1_000_000},
            ],
        }
    )

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    client.get_session = AsyncMock(return_value=mock_session)

    res = await client.fetch_all_auctions()
    assert res is not None
    assert len(res) == 1
    assert client.last_updated == 1_700_000_000_000
    assert client.last_updated_ms == 1_700_000_000_000
    assert client.last_modified_header == "Fri, 09 Oct 2026 03:00:00 GMT"
    assert client.date_header == "Fri, 09 Oct 2026 03:00:15 GMT"
