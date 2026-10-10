"""Tests for CLI arguments and main loop runner."""

from __future__ import annotations

import argparse
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.main import (
    decode_auction_batch,
    load_configured_settings,
    run_monitor,
)


def test_decode_auction_batch():
    """Verify batch decode returns matched pairs of raw auction and decoded data."""
    sample_auctions = [
        {"uuid": "1", "item_bytes": None},
        {"uuid": "2", "item_bytes": ""},
    ]
    results = decode_auction_batch(sample_auctions)
    assert len(results) == 2
    assert results[0][0]["uuid"] == "1"
    assert results[0][1]["internal_id"] == "UNKNOWN"


def test_load_configured_settings_cli_overrides():
    """Verify command-line flags override defaults."""
    args = argparse.Namespace(
        dry_run=True,
        interval=30,
        min_profit=2_000_000,
        min_margin=25.0,
        api_key="test-key",
        webhook_url=None,
    )
    settings = load_configured_settings(args)
    assert settings.DRY_RUN is True
    assert settings.POLL_INTERVAL_SECONDS == 30
    assert settings.MIN_PROFIT_COINS == 2_000_000
    assert settings.MIN_PROFIT_MARGIN_PCT == 25.0
    assert settings.HYPIXEL_API_KEY == "test-key"


@pytest.mark.asyncio
async def test_run_monitor_single_cycle_graceful_shutdown():
    """Verify run_monitor executes a cycle and exits cleanly when stop_event is set."""
    from src.config import Settings

    settings = Settings(DRY_RUN=True, POLL_INTERVAL_SECONDS=1)
    stop_event = asyncio.Event()

    with patch("src.main.HypixelClient.fetch_all_auctions", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None  # Simulates unchanged or clean fetch

        # Set stop event after a brief moment to allow loop to run once
        async def trigger_stop():
            await asyncio.sleep(0.05)
            stop_event.set()

        asyncio.create_task(trigger_stop())
        await run_monitor(settings, stop_event)

        assert stop_event.is_set()


@pytest.mark.asyncio
async def test_run_monitor_cycle_with_auctions():
    """Verify run_monitor processes auction listings, updates cache, and handles flips."""
    from src.config import Settings

    settings = Settings(DRY_RUN=True, POLL_INTERVAL_SECONDS=1, ENABLE_NETWORTH_VALUATION=False)
    stop_event = asyncio.Event()

    sample_auctions = [
        {"uuid": "test-1", "starting_bid": 1_000_000, "item_name": "Test Item", "item_bytes": None},
    ]

    with (
        patch("src.main.HypixelClient.fetch_all_auctions", new_callable=AsyncMock) as mock_fetch,
        patch("src.main.HypixelClient.fetch_ended_auctions", new_callable=AsyncMock) as mock_ended,
    ):
        mock_fetch.return_value = sample_auctions
        mock_ended.return_value = []

        async def trigger_stop():
            await asyncio.sleep(0.05)
            stop_event.set()

        asyncio.create_task(trigger_stop())
        await run_monitor(settings, stop_event)

        assert stop_event.is_set()


@pytest.mark.asyncio
async def test_e2e_dry_run_pipeline():
    """Verify complete end-to-end dry run pipeline flow from ended auction tracking to alerting."""
    import time

    from src.config import Settings
    from src.evaluator import FlipEvaluator
    from src.notifier import DiscordNotifier
    from src.velocity import SalesVelocityTracker

    settings = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        STAPLE_MIN_PROFIT_COINS=500_000,
        STAPLE_MIN_PROFIT_MARGIN_PCT=8.0,
        COSMETIC_FILTER_ENABLED=True,
        ENABLE_VELOCITY_FILTER=True,
        MIN_SALES_PER_DAY=5,
        VELOCITY_CACHE_FILE=None,
    )

    velocity_tracker = SalesVelocityTracker(settings, auto_load=False)
    evaluator = FlipEvaluator(settings, velocity_tracker=velocity_tracker)
    notifier = DiscordNotifier(settings)

    # 1. Record ended auctions to establish turnover velocity
    now = time.time()
    ended_auctions = [
        # Necron's Chestplate (10 sales)
        *[
            {
                "auction_id": f"ended_necron_{i}",
                "buyer": f"buyer_{i}",
                "bin": True,
                "timestamp": int((now - i * 1800) * 1000),
                "internal_id": "NECRON_CHESTPLATE",
            }
            for i in range(10)
        ],
        # Livid Dagger (10 sales)
        *[
            {
                "auction_id": f"ended_livid_{i}",
                "buyer": f"buyer_{i}",
                "bin": True,
                "timestamp": int((now - i * 1800) * 1000),
                "internal_id": "LIVID_DAGGER",
            }
            for i in range(10)
        ],
        # Runaan's Bow (10 sales)
        *[
            {
                "auction_id": f"ended_runaan_{i}",
                "buyer": f"buyer_{i}",
                "bin": True,
                "timestamp": int((now - i * 1800) * 1000),
                "internal_id": "RUNAAN_BOW",
            }
            for i in range(10)
        ],
    ]
    recorded = velocity_tracker.record_ended_auctions(ended_auctions)
    assert recorded == 30
    assert velocity_tracker.is_velocity_sufficient("NECRON_CHESTPLATE") is True
    assert velocity_tracker.is_velocity_sufficient("LIVID_DAGGER") is True
    assert velocity_tracker.is_velocity_sufficient("RUNAAN_BOW") is True
    assert velocity_tracker.is_velocity_sufficient("OBSIDIAN_CHESTPLATE") is False

    # 2. Ingest market listings into cache
    market_batch = [
        # Necron's Chestplate (relaxed margin 10.25%)
        (
            {"starting_bid": 40_000_000, "uuid": "necron_0"},
            {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"},
        ),
        (
            {"starting_bid": 45_000_000, "uuid": "necron_1"},
            {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"},
        ),
        (
            {"starting_bid": 46_000_000, "uuid": "necron_2"},
            {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"},
        ),
        # Livid Dagger (33.0% margin)
        ({"starting_bid": 7_000_000, "uuid": "livid_0"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),
        ({"starting_bid": 9_500_000, "uuid": "livid_1"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),
        ({"starting_bid": 9_800_000, "uuid": "livid_2"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),
        # Runaan's Bow (37.2% margin)
        ({"starting_bid": 5_000_000, "uuid": "runaan_0"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),
        ({"starting_bid": 7_000_000, "uuid": "runaan_1"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),
        ({"starting_bid": 7_200_000, "uuid": "runaan_2"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),
        # Zero-velocity item
        (
            {"starting_bid": 2_000_000, "uuid": "obsidian_0"},
            {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"},
        ),
        (
            {"starting_bid": 6_000_000, "uuid": "obsidian_1"},
            {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"},
        ),
        (
            {"starting_bid": 6_500_000, "uuid": "obsidian_2"},
            {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"},
        ),
        # Cosmetic bait
        (
            {"starting_bid": 10_000_000, "uuid": "skin_0"},
            {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"},
        ),
        (
            {"starting_bid": 30_000_000, "uuid": "skin_1"},
            {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"},
        ),
        (
            {"starting_bid": 35_000_000, "uuid": "skin_2"},
            {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"},
        ),
        # Cosmetic dye
        ({"starting_bid": 50_000_000, "uuid": "dye_0"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
        ({"starting_bid": 150_000_000, "uuid": "dye_1"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
        ({"starting_bid": 160_000_000, "uuid": "dye_2"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
    ]
    evaluator.update_cache(market_batch)

    # 3. Verify cosmetics and low-velocity items are rejected
    assert evaluator.evaluate_listing(market_batch[12][0], market_batch[12][1]) is None
    assert evaluator.evaluate_listing(market_batch[15][0], market_batch[15][1]) is None
    assert evaluator.evaluate_listing(market_batch[9][0], market_batch[9][1]) is None

    # 4. Verify progression staples are accepted with priority tagging
    res_necron = evaluator.evaluate_listing(market_batch[0][0], market_batch[0][1])
    assert res_necron is not None
    assert res_necron.is_priority is True
    assert res_necron.staple_category == "WITHER_ARMOR"
    assert 8.0 <= res_necron.margin_pct < 15.0

    res_livid = evaluator.evaluate_listing(market_batch[3][0], market_batch[3][1])
    assert res_livid is not None
    assert res_livid.is_priority is True
    assert res_livid.staple_category == "MIDGAME_COMBAT"

    # 5. Verify core weapon is preserved without priority tagging
    res_runaan = evaluator.evaluate_listing(market_batch[6][0], market_batch[6][1])
    assert res_runaan is not None
    assert res_runaan.is_priority is False
    assert res_runaan.staple_category is None

    # 6. Verify embed formatting and dry-run alerting
    necron_embed = notifier.build_embed_payload(res_necron)["embeds"][0]
    assert necron_embed["title"] == "🔥 [PRIORITY STAPLE] Necron's Chestplate"
    assert necron_embed["color"] == 0xFF9900

    runaan_embed = notifier.build_embed_payload(res_runaan)["embeds"][0]
    assert runaan_embed["title"] == "[FLIP FOUND] Runaan's Bow"
    assert runaan_embed["color"] == 0x00FF00

    assert await notifier.send_alert(res_necron) is True
    assert await notifier.send_alert(res_livid) is True
    assert await notifier.send_alert(res_runaan) is True
    await notifier.close()


@pytest.mark.asyncio
async def test_graceful_shutdown_cleans_resources():
    """Verify run_monitor shuts down client, notifier, bazaar, broadcaster, and DB cleanly on stop."""
    from src.config import Settings
    from src.main import run_monitor

    settings = Settings(
        DRY_RUN=True,
        POLL_INTERVAL_SECONDS=1,
        ENABLE_LOCAL_WS=True,
        LOCAL_WS_HOST="127.0.0.1",
        LOCAL_WS_PORT=0,
        ENABLE_NETWORTH_VALUATION=False,
    )
    stop_event = asyncio.Event()

    with (
        patch("src.main.HypixelClient") as MockClient,
        patch("src.main.DiscordNotifier") as MockNotifier,
        patch("src.main.BazaarCache") as MockBazaar,
        patch("src.main.LocalWebSocketBroadcaster") as MockWS,
    ):
        mock_client_inst = AsyncMock()
        mock_client_inst.fetch_all_auctions.return_value = None
        mock_client_inst.last_updated_ms = None
        mock_client_inst.get_session.return_value = AsyncMock()
        MockClient.return_value = mock_client_inst

        mock_notifier_inst = AsyncMock()
        MockNotifier.return_value = mock_notifier_inst

        mock_bazaar_inst = AsyncMock()
        MockBazaar.return_value = mock_bazaar_inst

        mock_ws_inst = AsyncMock()
        MockWS.return_value = mock_ws_inst

        # Trigger shutdown immediately after cycle starts
        async def trigger_shutdown():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.create_task(trigger_shutdown())
        await run_monitor(settings, stop_event)

        assert stop_event.is_set()
        mock_client_inst.close.assert_awaited_once()
        mock_notifier_inst.close.assert_awaited_once()
        mock_bazaar_inst.close.assert_awaited_once()
        mock_ws_inst.stop.assert_awaited_once()
