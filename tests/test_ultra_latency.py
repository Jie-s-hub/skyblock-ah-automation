"""
Unit and integration tests for ultra-low latency architecture:
- Bulk sales insertion (record_sales_batch)
- Lightweight cache probing (probe_cache_freshness)
- Active keep-alive warming (keep_connection_warm)
- Comps evaluator bucket memoization
- Auctions-first background housekeeping
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.client import HypixelClient
from src.comps_evaluator import CompsEvaluator
from src.config import Settings
from src.db import DatabaseManager
from src.main import run_background_housekeeping
from src.sales_ingestor import SalesIngestor


@pytest.fixture
def tmp_db(tmp_path) -> DatabaseManager:
    db_file = tmp_path / "test_ultra_latency.db"
    return DatabaseManager(str(db_file))


def test_bulk_sales_insertion(tmp_db: DatabaseManager):
    """Verify record_sales_batch inserts multiple sales atomically and returns rowcount."""
    sales = [
        {
            "uuid": f"sale-uuid-{i}",
            "internal_id": "HYPERION",
            "price": 1_000_000_000 + i * 1_000_000,
            "buyer_uuid": "buyer-123",
            "seller_uuid": "seller-456",
            "timestamp": 1700000000000 + i * 1000,
            "features_json": {"dungeon_stars": 5},
            "features_hash": "hash123",
            "normalized_tier": 5,
            "time_to_sell_s": 12.5,
        }
        for i in range(10)
    ]
    inserted = tmp_db.record_sales_batch(sales)
    assert inserted == 10

    # Verify rows in DB
    queried = tmp_db.get_sales(internal_id="HYPERION", limit=20)
    assert len(queried) == 10


def test_sales_ingestor_batches_sales(tmp_db: DatabaseManager):
    """Verify SalesIngestor batches raw auctions and delegates to record_sales_batch."""
    ingestor = SalesIngestor(tmp_db, Settings(DRY_RUN=True))
    raw_ended = [
        {
            "uuid": f"ended-uuid-{i}",
            "buyer": "buyer-abc",
            "seller": "seller-def",
            "price": 50_000_000,
            "bin": True,
            "timestamp": 1700000000000,
            "internal_id": "NECRON_CHESTPLATE",
            "item_bytes": None,
            "decoded": {"internal_id": "NECRON_CHESTPLATE", "dungeon_stars": 5},
        }
        for i in range(5)
    ]
    count = ingestor.ingest_ended_auctions(raw_ended)
    assert count == 5
    assert len(tmp_db.get_sales(internal_id="NECRON_CHESTPLATE")) == 5


@pytest.mark.asyncio
async def test_probe_cache_freshness():
    """Verify probe_cache_freshness parses Last-Modified and Age correctly."""
    client = HypixelClient(Settings(DRY_RUN=True))
    client.last_modified_header = "Wed, 14 Oct 2026 08:00:00 GMT"

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {
        "Last-Modified": "Wed, 14 Oct 2026 08:01:00 GMT",
        "Age": "1",
    }
    mock_head_ctx = AsyncMock()
    mock_head_ctx.__aenter__.return_value = mock_resp

    mock_session = MagicMock()
    mock_session.head.return_value = mock_head_ctx

    is_fresh, last_mod, age = await client.probe_cache_freshness(mock_session)
    assert is_fresh is True
    assert last_mod == "Wed, 14 Oct 2026 08:01:00 GMT"
    assert age == 1


@pytest.mark.asyncio
async def test_keep_connection_warm():
    """Verify keep_connection_warm sends lightweight HEAD request and returns True on 200."""
    client = HypixelClient(Settings(DRY_RUN=True))
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Last-Modified": "Wed, 14 Oct 2026 08:05:00 GMT"}
    mock_head_ctx = AsyncMock()
    mock_head_ctx.__aenter__.return_value = mock_resp

    mock_session = MagicMock()
    mock_session.head.return_value = mock_head_ctx

    with patch.object(client, "get_session", return_value=mock_session):
        success = await client.keep_connection_warm()
        assert success is True
        assert client.last_modified_header == "Wed, 14 Oct 2026 08:05:00 GMT"


def test_comps_evaluator_sales_memoization(tmp_db: DatabaseManager):
    """Verify CompsEvaluator memoizes recent sales queries across the same internal_id."""
    comps = CompsEvaluator(tmp_db, Settings(DRY_RUN=True))

    # Seed 5 sales
    tmp_db.record_sales_batch([
        {
            "uuid": f"test-comp-{i}",
            "internal_id": "DIVAN_DRILL",
            "price": 200_000_000 + i * 10_000_000,
            "buyer_uuid": f"b{i}",
            "seller_uuid": f"s{i}",
            "timestamp": 1700000000000,
            "features_json": {"dungeon_stars": 0},
            "features_hash": "hash_drill",
            "normalized_tier": 0,
        }
        for i in range(5)
    ])

    features_1 = {"internal_id": "DIVAN_DRILL", "dungeon_stars": 0}
    features_2 = {"internal_id": "DIVAN_DRILL", "dungeon_stars": 1}

    # First lookup populates the memoized sales cache
    res1 = comps.estimate_comps_value(features_1, now_ms=1700000010000)
    assert res1 is not None

    # Second lookup with different features but same internal_id reuses the memoized query
    assert len(comps._recent_sales_cache) >= 1
    res2 = comps.estimate_comps_value(features_2, now_ms=1700000010000)
    assert res2 is not None


@pytest.mark.asyncio
async def test_run_background_housekeeping():
    """Verify run_background_housekeeping executes without blocking main loop."""
    client = MagicMock()
    client.get_session = AsyncMock()
    client.fetch_ended_auctions = AsyncMock(return_value=[])

    bazaar = MagicMock()
    bazaar.fetch_bazaar_data = AsyncMock()

    velocity = MagicMock()
    election = MagicMock()
    election.update = AsyncMock(return_value=False)

    evaluator = MagicMock()
    notifier = MagicMock()
    settings = Settings(DRY_RUN=True, ENABLE_NETWORTH_VALUATION=True)

    await run_background_housekeeping(
        client=client,
        bazaar_cache=bazaar,
        velocity_tracker=velocity,
        sales_ingestor=None,
        player_tracker=None,
        election_tracker=election,
        evaluator=evaluator,
        notifier=notifier,
        settings=settings,
    )

    bazaar.fetch_bazaar_data.assert_awaited_once()
    election.update.assert_awaited_once()
    evaluator.set_derpy_active.assert_called_with(False)
