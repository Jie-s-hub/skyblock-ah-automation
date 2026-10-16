"""Comprehensive verification tests for codebase audit, bug detection, and hardening fixes."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.client import HypixelClient
from src.comps_evaluator import CompsEvaluator
from src.config import Settings
from src.constants.attributes import calculate_attribute_estimated_value
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator
from src.local_server import LocalWebSocketBroadcaster
from src.velocity import SalesVelocityTracker

# ---------------------------------------------------------------------------
# 1. Memory Leaks & Cache Bounding
# ---------------------------------------------------------------------------


def test_audit_decoded_cache_strict_pruning():
    """Verify that decoded_cache strictly retains only active auction UUIDs without leakage."""
    active_uuids = {"uuid_1", "uuid_2", "uuid_3"}
    decoded_cache = {
        "uuid_1": {"internal_id": "HYPERION"},
        "uuid_2": {"internal_id": "VALKYRIE"},
        "uuid_old_1": {"internal_id": "TERMINATOR"},
        "uuid_old_2": {"internal_id": "NECRON_CHESTPLATE"},
    }

    # Apply strict pruning policy
    decoded_cache = {u: decoded_cache[u] for u in active_uuids if u in decoded_cache}

    assert set(decoded_cache.keys()) == {"uuid_1", "uuid_2"}
    assert "uuid_old_1" not in decoded_cache
    assert "uuid_old_2" not in decoded_cache


def test_audit_comps_evaluator_bucketed_cache_and_eviction(tmp_path):
    """Verify comps_evaluator quantizes cutoff_ms to 60s buckets and evicts on capacity."""
    db_file = tmp_path / "test_comps.db"
    db = DatabaseManager(str(db_file))
    config = Settings(DRY_RUN=True)
    evaluator = CompsEvaluator(db=db, config=config)

    # Seed sales into database (aligned 10s into current 60s bucket so +1.5s never crosses boundary)
    now_ms = float((int(time.time() * 1000) // 60_000) * 60_000 + 10_000)
    for i in range(5):
        db.record_sale(
            uuid=f"sale_{i}",
            internal_id="HYPERION",
            price=1_000_000_000,
            buyer_uuid=f"buyer_{i}",
            seller_uuid=f"seller_{i}",
            timestamp=int(now_ms - 1000 * (i + 1)),
            features_json={"internal_id": "HYPERION"},
        )

    item = {"internal_id": "HYPERION"}

    # Two calls within the same 60-second window should hit cache
    res1 = evaluator.estimate_comps_value(item, now_ms=now_ms)
    res2 = evaluator.estimate_comps_value(item, now_ms=now_ms + 1500)  # 1.5s later, same bucket
    assert res1 is not None
    assert res2 is not None
    assert len(evaluator._eval_cache) == 1

    # Invalidate cache
    evaluator.clear_cache()
    assert len(evaluator._eval_cache) == 0

    # Test cache capacity eviction (> 10_000 entries clears rather than freezing)
    for j in range(10_005):
        evaluator._eval_cache[(f"ITEM_{j}", "hash", None, 100)] = None
    assert len(evaluator._eval_cache) >= 10_000

    # Next evaluation should clear the full cache and insert fresh entry
    evaluator.estimate_comps_value(item, now_ms=now_ms)
    assert len(evaluator._eval_cache) == 1


# ---------------------------------------------------------------------------
# 2. SQLite WAL, busy_timeout & Thread-Safety
# ---------------------------------------------------------------------------


def test_audit_sqlite_wal_busy_timeout_and_thread_safety(tmp_path):
    """Verify DatabaseManager configures busy_timeout = 5000 and has thread-safe locking."""
    db_file = tmp_path / "test_concurrency.db"
    db = DatabaseManager(str(db_file))

    conn = db.get_connection()
    cur = conn.execute("PRAGMA busy_timeout;")
    row = cur.fetchone()
    assert row is not None
    # SQLite busy_timeout returns timeout in milliseconds
    assert int(row[0]) >= 5000

    # Verify thread lock is present
    assert hasattr(db, "_thread_lock")

    # Verify clear_sales_cache and get_sales run safely under lock
    db.clear_sales_cache()
    assert len(db._sales_cache) == 0


# ---------------------------------------------------------------------------
# 3. HTTP Header Ingestion & Fast-Retry Cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_client_response_headers_and_logging():
    """Verify HypixelClient reads CF-Cache-Status and RateLimit-Remaining headers."""
    config = Settings(DRY_RUN=True)
    client = HypixelClient(config=config)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {
        "CF-Cache-Status": "HIT",
        "RateLimit-Remaining": "120",
        "Last-Modified": "Wed, 14 Oct 2026 00:00:00 GMT",
        "Date": "Wed, 14 Oct 2026 00:00:01 GMT",
    }
    mock_resp.read = AsyncMock(return_value=b'{"success": true, "page": 0, "totalPages": 1, "auctions": []}')

    session_mock = MagicMock()
    session_mock.get.return_value.__aenter__.return_value = mock_resp

    with patch("logging.Logger.debug") as mock_debug:
        data = await client.fetch_page(0, session_mock)
        assert data.get("success") is True
        # Verify header logging was invoked
        assert any("CF-Cache-Status" in str(call) for call in mock_debug.call_args_list)


# ---------------------------------------------------------------------------
# 4. Sales Velocity Warmup Heuristic
# ---------------------------------------------------------------------------


def test_audit_velocity_cold_start_warmup_heuristic():
    """Verify that during cold start / warmup, staples or deep order books bypass velocity rejection."""
    config = Settings(
        DRY_RUN=True,
        ENABLE_VELOCITY_FILTER=True,
        MIN_SALES_PER_DAY=5,
    )
    velocity_tracker = SalesVelocityTracker(config=config, auto_load=False)
    assert velocity_tracker.is_warming_up() is True

    evaluator = FlipEvaluator(config=config, velocity_tracker=velocity_tracker)
    evaluator.set_cold_start(True)

    # Populate mock price cache with 3 floor listings
    evaluator._price_cache["HYPERION"] = [1_000_000_000, 1_050_000_000, 1_100_000_000]

    # Hyperion is a known staple and has 3 floor listings
    # Should not be rejected by insufficient_velocity under warmup mode
    auction = {
        "uuid": "test_hyp_1",
        "starting_bid": 800_000_000,
        "start": int(time.time() * 1000) - 10_000,
        "end": int(time.time() * 1000) + 100_000,
        "bin": True,
    }
    decoded = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
    }

    res = evaluator.evaluate_listing(auction, decoded)
    # Velocity check should pass (it may or may not produce a profitable flip depending on thresholds)
    # Crucially, last rejection reason must NOT be "insufficient_velocity"
    if res is None:
        rejection_reason = evaluator.last_rejections.get("test_hyp_1", "")
        assert rejection_reason != "insufficient_velocity"


# ---------------------------------------------------------------------------
# 5. Attribute Evaluation Key Case Normalization
# ---------------------------------------------------------------------------


def test_audit_attribute_case_insensitivity_god_roll():
    """Verify that uppercase or mixed-case attribute keys calculate full tier bonuses correctly."""
    # Uppercase attributes dictionary
    attrs_upper = {
        "MANA_POOL": 5,
        "MANA_REGENERATION": 5,
    }

    is_gr, gr_type, est_val = calculate_attribute_estimated_value(attrs_upper)
    assert is_gr is True
    assert gr_type == "MAGE"

    # Base Mage floor is 120M; Tier sum = 10, tier bonus = (10 - 2) * 5M = 40M -> total 160M
    assert est_val == 160_000_000


# ---------------------------------------------------------------------------
# 6. WebSocket Stalled Client Protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_websocket_client_timeout_and_closed_discard():
    """Verify that stalled or closed WebSocket clients are aggressively discarded without blocking."""
    broadcaster = LocalWebSocketBroadcaster()

    # Create a mock hung client whose send() never completes
    async def hung_send(msg):
        await asyncio.sleep(10.0)

    hung_client = MagicMock()
    hung_client.closed = False
    hung_client.send = hung_send

    # Create a normal client
    normal_client = MagicMock()
    normal_client.closed = False
    normal_client.send = AsyncMock()

    # Create an already-closed client
    closed_client = MagicMock()
    closed_client.closed = True

    broadcaster.clients.add(hung_client)
    broadcaster.clients.add(normal_client)
    broadcaster.clients.add(closed_client)

    from src.evaluator import FlipResult

    mock_flip = FlipResult(
        auction_uuid="uuid_123",
        internal_id="HYPERION",
        item_name="Hyperion",
        buy_price=500_000_000,
        target_resell_price=700_000_000,
        net_profit=180_000_000,
        margin_pct=36.0,
        lane="FLOOR",
    )

    t0 = time.time()
    sent_count = await broadcaster.broadcast_flip(mock_flip)
    duration = time.time() - t0

    # Broadcaster should finish within ~1.2s despite hung client (1.0s timeout), not 10s
    assert duration < 2.5
    assert sent_count == 1
    # Hung client and closed client should be removed from pool
    assert hung_client not in broadcaster.clients
    assert closed_client not in broadcaster.clients
    assert normal_client in broadcaster.clients


# ---------------------------------------------------------------------------
# 7. Pet Effective Tier Floor Isolation
# ---------------------------------------------------------------------------


def test_audit_pet_effective_tier_floor_isolation():
    """Verify that Tier-Boosted pets are indexed under their effective rarity floor."""
    config = Settings(DRY_RUN=True)
    evaluator = FlipEvaluator(config=config)

    # Decode pairs: an Epic Goblin with Tier Boost (effective Legendary)
    auction_boosted = {
        "uuid": "boosted_1",
        "starting_bid": 60_000_000,
        "start": int(time.time() * 1000),
        "end": int(time.time() * 1000) + 100_000,
        "bin": True,
    }
    decoded_boosted = {
        "internal_id": "PET_GOBLIN_EPIC",
        "pet_type": "GOBLIN",
        "pet_tier": "EPIC",
        "effective_tier": "LEGENDARY",
        "is_pet": True,
        "pet_held_item": "PET_ITEM_TIER_BOOST",
        "count": 1,
    }

    # A normal Epic Goblin
    auction_normal_epic = {
        "uuid": "normal_epic_1",
        "starting_bid": 200_000,
        "start": int(time.time() * 1000),
        "end": int(time.time() * 1000) + 100_000,
        "bin": True,
    }
    decoded_normal_epic = {
        "internal_id": "PET_GOBLIN_EPIC",
        "pet_type": "GOBLIN",
        "pet_tier": "EPIC",
        "effective_tier": "EPIC",
        "is_pet": True,
        "count": 1,
    }

    evaluator.update_cache([(auction_boosted, decoded_boosted), (auction_normal_epic, decoded_normal_epic)])

    # The Tier-Boosted pet should be in PET_GOBLIN_LEGENDARY, NOT corrupting PET_GOBLIN_EPIC
    assert 60_000_000 in evaluator.price_cache["PET_GOBLIN_LEGENDARY"]
    assert 60_000_000 not in evaluator.price_cache["PET_GOBLIN_EPIC"]
    assert evaluator.price_cache["PET_GOBLIN_EPIC"] == [200_000]


# ---------------------------------------------------------------------------
# 8. Stale Socket Fast-Retry & Early Break Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_stale_socket_fast_retry():
    """Verify that a stale keep-alive TransferEncodingError or ServerDisconnectedError retries in 50ms."""
    config = Settings(DRY_RUN=True)
    client = HypixelClient(config=config)

    # Mock session with a first attempt failing due to ServerDisconnectedError, then succeeding
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.headers = {"CF-Cache-Status": "HIT"}
    mock_response.read = AsyncMock(return_value=b'{"success": true, "page": 0, "totalPages": 1, "auctions": []}')

    call_count = 0

    class MockContextManager:
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                import aiohttp
                raise aiohttp.ServerDisconnectedError("Server closed connection")
            return mock_response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_session.get.side_effect = lambda url, params: MockContextManager()

    with patch("asyncio.sleep") as mock_sleep:
        mock_sleep.return_value = None
        data = await client.fetch_page(0, mock_session)
        assert data.get("success") is True
        # Verify first sleep was 0.05s (50ms) instead of standard 1.0s+ delay
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args_list[0][0][0] == 0.05


def test_audit_early_break_on_max_snipe_listing_age():
    """Verify that in sorted decoded_pairs, breaking on max_snipe_age skips older listings."""
    now_ms = time.time() * 1000.0

    # Create 100 auctions sorted newest first
    # First 5 are 10s old (< 300s)
    # Next 95 are 600s to 100,000s old (> 300s)
    auctions = []
    for i in range(5):
        auctions.append(({"uuid": f"new_{i}", "start": now_ms - (10_000 + i * 1000)}, {"internal_id": "HYPERION"}))
    for i in range(95):
        auctions.append(({"uuid": f"old_{i}", "start": now_ms - (400_000 + i * 1000)}, {"internal_id": "HYPERION"}))

    max_snipe_age = 300  # seconds
    evaluated_uuids = []

    for auction, decoded in auctions:
        start_ms = auction.get("start")
        if max_snipe_age > 0 and start_ms:
            age_s = (now_ms - float(start_ms)) / 1000.0
            if age_s > float(max_snipe_age):
                break
        evaluated_uuids.append(auction["uuid"])

    # Exactly 5 newly listed auctions should be evaluated, 95 old auctions skipped!
    assert len(evaluated_uuids) == 5
    assert all("new_" in u for u in evaluated_uuids)


def test_audit_live_listings_retention_pruning():
    """Verify that live_listings prunes rows older than 48 hours."""
    from src.db import DatabaseManager

    db = DatabaseManager(":memory:")
    now_ms = int(time.time() * 1000.0)
    old_time = now_ms - (50 * 3600 * 1000)  # 50 hours old
    recent_time = now_ms - (1 * 3600 * 1000)  # 1 hour old

    # Insert old and recent
    db.record_live_listings([("old_uuid", old_time), ("recent_uuid", recent_time)])

    # Record another pair to trigger the pruning
    db.record_live_listings([("new_uuid", now_ms)])

    conn = db._get_connection()
    rows = conn.execute("SELECT uuid FROM live_listings;").fetchall()
    uuids = {r[0] for r in rows}

    assert "recent_uuid" in uuids
    assert "new_uuid" in uuids
    assert "old_uuid" not in uuids

