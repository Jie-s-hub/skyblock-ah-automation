"""Comprehensive tests for v2 capabilities:
1. Local Audio & TTS alerts with purse budget suppression.
2. Crimson Isle prestige armor tier grouping & benchmarking.
3. Divan's Armor Gemstone Chambers valuation.
4. Secondary flat addons (The Art of War, Wood Singularity, Transmission Tuners).
5. Stale auction age filtering (MAX_BENCHMARK_AGE_HOURS).
6. Purse budget gating (MAX_PURSE_BUDGET & OUT_OF_BUDGET_WEBHOOK_URL).
7. WebSocket feed ingestion and payload normalization.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bazaar import BazaarCache
from src.config import get_settings
from src.constants.prestige import (
    get_prestige_tier,
)
from src.evaluator import FlipEvaluator, FlipResult
from src.nbt_decoder import parse_divan_gemstone_chambers
from src.networth_evaluator import calculate_item_addons
from src.notifier import DiscordNotifier
from src.stream_client import WebSocketFeedClient, normalize_auction_payload

# =====================================================================
# 2. CRIMSON ISLE PRESTIGE ARMOR TIERS
# =====================================================================


def test_prestige_tier_detection():
    """Verify that get_prestige_tier correctly categorizes Crimson Isle tiers."""
    assert get_prestige_tier("INFERNAL_CRIMSON_CHESTPLATE") == "INFERNAL"
    assert get_prestige_tier("FIERY_AURORA_HELMET") == "FIERY"
    assert get_prestige_tier("BURNING_TERROR_BOOTS") == "BURNING"
    assert get_prestige_tier("HOT_FERVOR_LEGGINGS") == "HOT"
    assert get_prestige_tier("CRIMSON_CHESTPLATE") == "BASIC"
    assert get_prestige_tier("AURORA_BOOTS") == "BASIC"
    assert get_prestige_tier("HYPERION") == "BASIC"


def test_prestige_armor_benchmarked_strictly_against_same_tier():
    """
    Ensure a Burning Crimson Chestplate is strictly isolated and benchmarked against
    the Burning Crimson Chestplate LBIN, and never against Basic Crimson Chestplate.
    """
    config = get_settings(DRY_RUN=True, MIN_SAMPLE_SIZE=2, MIN_PROFIT_COINS=1_000_000)
    bz = BazaarCache(config)
    bz.set_mock_prices(
        {
            "RECOMBOBULATOR_3000": 10_000_000,
            "ESSENCE_CRIMSON": 4_000,
        }
    )

    evaluator = FlipEvaluator(config=config, bazaar_cache=bz)

    # Feed baseline listings:
    # Basic Crimson: 15M clean
    # Burning Crimson: 150M clean
    now_ms = 1_000_000_000_000
    start_time = now_ms - 60_000
    auctions = [
        # Basic Crimson listings
        (
            {"starting_bid": 15_000_000, "start": start_time},
            {"internal_id": "CRIMSON_CHESTPLATE", "dungeon_stars": 0, "rarity_upgrades": 0},
        ),
        (
            {"starting_bid": 16_000_000, "start": start_time},
            {"internal_id": "CRIMSON_CHESTPLATE", "dungeon_stars": 0, "rarity_upgrades": 0},
        ),
        # Burning Crimson listings
        (
            {"starting_bid": 150_000_000, "start": start_time},
            {"internal_id": "BURNING_CRIMSON_CHESTPLATE", "dungeon_stars": 0, "rarity_upgrades": 0},
        ),
        (
            {"starting_bid": 160_000_000, "start": start_time},
            {"internal_id": "BURNING_CRIMSON_CHESTPLATE", "dungeon_stars": 0, "rarity_upgrades": 0},
        ),
    ]

    evaluator.update_cache(auctions, now_ms=now_ms)

    # Basic base LBIN should be 15M
    assert evaluator.get_base_lbin("CRIMSON_CHESTPLATE") == 15_000_000
    # Burning base LBIN must be 150M, NOT 15M!
    assert evaluator.get_base_lbin("BURNING_CRIMSON_CHESTPLATE") == 150_000_000

    # Evaluate upgraded Burning Crimson piece listed at 155M with Recomb (worth ~150M base + 7M retained recomb)
    candidate_auction = {
        "uuid": "burning-cand-1",
        "starting_bid": 120_000_000,
        "item_name": "Burning Crimson Chestplate",
    }
    candidate_decoded = {
        "internal_id": "BURNING_CRIMSON_CHESTPLATE",
        "item_name": "Burning Crimson Chestplate",
        "rarity_upgrades": 1,
        "dungeon_stars": 0,
        "prestige_tier": "BURNING",
    }

    flip = evaluator.evaluate_listing(candidate_auction, candidate_decoded)
    assert flip is not None
    assert flip.internal_id == "BURNING_CRIMSON_CHESTPLATE"
    assert flip.prestige_tier == "BURNING"
    assert flip.base_lbin == 150_000_000  # Strictly matched to Burning tier!


# =====================================================================
# 3. DIVAN'S ARMOR GEMSTONE CHAMBERS VALUATION
# =====================================================================


def test_parse_divan_gemstone_chambers():
    """Verify parsing unlocked gemstone chambers on Divan's Armor."""
    # Test from explicit field
    assert parse_divan_gemstone_chambers("DIVAN_HELMET", {"gemstone_chambers": 4}) == 4
    assert parse_divan_gemstone_chambers("DIVAN_CHESTPLATE", {"chambers": 5}) == 5

    # Test from gems.unlocked_slots integer
    assert parse_divan_gemstone_chambers("DIVAN_LEGGINGS", {"gems": {"unlocked_slots": 3}}) == 3

    # Test from gems.unlocked_slots list
    assert parse_divan_gemstone_chambers("DIVAN_BOOTS", {"gems": {"unlocked_slots": ["TOPAZ_0", "JADE_0"]}}) == 2

    # Non-Divan piece should return 0
    assert parse_divan_gemstone_chambers("NECRON_CHESTPLATE", {"gems": {"unlocked_slots": 2}}) == 0


def test_divan_gemstone_chambers_networth_valuation():
    """Verify that unlocked gemstone chambers add correct Bazaar worth to Divan pieces."""
    config = get_settings(DRY_RUN=True)
    bz = BazaarCache(config)
    bz.set_mock_prices(
        {
            "GEMSTONE_CHAMBER": 10_000_000,
        }
    )

    decoded = {
        "internal_id": "DIVAN_CHESTPLATE",
        "gemstone_chambers": 4,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
    }

    breakdown, total_cost = calculate_item_addons(decoded, bz)
    assert total_cost == 40_000_000
    assert len(breakdown) == 1
    assert breakdown[0]["type"] == "gemstone_chambers"
    assert breakdown[0]["cost"] == 40_000_000
    assert breakdown[0]["count"] == 4


# =====================================================================
# 4. SECONDARY FLAT ADDONS (ART OF WAR, WOOD SINGULARITY, TUNERS)
# =====================================================================


def test_secondary_flat_addons_valuation():
    """Verify valuation of The Art of War, Wood Singularity, and Transmission Tuners."""
    config = get_settings(DRY_RUN=True)
    bz = BazaarCache(config)
    bz.set_mock_prices(
        {
            "THE_ART_OF_WAR": 6_500_000,
            "WOOD_SINGULARITY": 4_000_000,
            "TRANSMISSION_TUNER": 800_000,
        }
    )

    decoded = {
        "internal_id": "ASPECT_OF_THE_VOID",
        "art_of_war_count": 1,
        "wood_singularity_count": 1,
        "transmission_tuners": 3,
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
    }

    breakdown, total_cost = calculate_item_addons(decoded, bz)

    # 6.5M + 4.0M + (3 * 0.8M = 2.4M) = 12.9M total
    assert total_cost == 12_900_000
    types = {item["type"]: item["cost"] for item in breakdown}
    assert types["art_of_war"] == 6_500_000
    assert types["wood_singularity"] == 4_000_000
    assert types["transmission_tuners"] == 2_400_000


def test_is_upgraded_item_detects_secondary_addons():
    """Verify that FlipEvaluator.is_upgraded_item returns True when secondary addons exist."""
    config = get_settings(DRY_RUN=True)
    evaluator = FlipEvaluator(config)

    assert evaluator.is_upgraded_item({"art_of_war_count": 1}) is True
    assert evaluator.is_upgraded_item({"wood_singularity_count": 1}) is True
    assert evaluator.is_upgraded_item({"transmission_tuners": 2}) is True
    assert evaluator.is_upgraded_item({"gemstone_chambers": 3}) is True
    assert evaluator.is_upgraded_item({}) is False


# =====================================================================
# 5. STALE AUCTION AGE FILTERING (GHOST LISTINGS)
# =====================================================================


def test_stale_auction_filter_rejects_old_listings():
    """update_cache keeps old-but-live listings and drops only expired ones (end time passed)."""
    config = get_settings(DRY_RUN=True, MAX_BENCHMARK_AGE_HOURS=12.0)
    evaluator = FlipEvaluator(config)

    now_ms = 1_000_000_000_000  # Reference epoch
    hour_ms = 3_600_000
    auctions = [
        # 2h old, live -> keep
        (
            {"starting_bid": 10_000_000, "start": now_ms - (2 * hour_ms), "end": now_ms + (46 * hour_ms)},
            {"internal_id": "TEST_ITEM"},
        ),
        # 21h old, still live (48h auction) -> keep
        (
            {"starting_bid": 11_000_000, "start": now_ms - (21 * hour_ms), "end": now_ms + (27 * hour_ms)},
            {"internal_id": "TEST_ITEM"},
        ),
        # Already ended -> drop
        (
            {"starting_bid": 12_000_000, "start": now_ms - (50 * hour_ms), "end": now_ms - (2 * hour_ms)},
            {"internal_id": "TEST_ITEM"},
        ),
        # No end time known -> keep
        ({"starting_bid": 13_000_000, "start": now_ms - (30 * hour_ms)}, {"internal_id": "TEST_ITEM"}),
    ]

    evaluator.update_cache(auctions, now_ms=now_ms)

    assert evaluator._price_cache.get("TEST_ITEM", []) == [10_000_000, 11_000_000, 13_000_000]


def test_insert_listing_drops_only_expired():
    """insert_listing (stream path) uses the same expiry rule as update_cache."""
    evaluator = FlipEvaluator(get_settings(DRY_RUN=True))
    now_ms = 1_000_000_000_000
    hour_ms = 3_600_000
    evaluator.update_cache([], now_ms=now_ms)

    old_live = {"starting_bid": 5_000_000, "start": now_ms - 30 * hour_ms, "end": now_ms + hour_ms}
    expired = {"starting_bid": 6_000_000, "start": now_ms - 30 * hour_ms, "end": now_ms - hour_ms}
    evaluator.insert_listing(old_live, {"internal_id": "TEST_ITEM"}, now_ms=now_ms)
    evaluator.insert_listing(expired, {"internal_id": "TEST_ITEM"}, now_ms=now_ms)

    assert evaluator._price_cache.get("TEST_ITEM", []) == [5_000_000]


def test_florid_regression_clean_item_not_priced_off_starred_or_old_listings():
    """
    Regression (2026-10-10): a clean Florid Zombie Sword at 1.56M was alerted with a 3M target.
    Cause: 11 live 1.59M listings were >12h old and dropped, and Path A used a 5-star 3M listing
    as the reference. Expected: no flip (real clean floor is ~1.59M, a supply dump).
    """
    config = get_settings(
        DRY_RUN=True,
        MIN_SAMPLE_SIZE=3,
        MAX_TARGET_TO_BUY_RATIO=3.0,
        MAX_SNIPE_LISTING_AGE_SECONDS=0,
    )
    evaluator = FlipEvaluator(config)
    now_ms = 1_000_000_000_000
    hour_ms = 3_600_000
    clean = {"internal_id": "FLORID_ZOMBIE_SWORD"}
    starred = {"internal_id": "FLORID_ZOMBIE_SWORD", "dungeon_stars": 5}

    candidate = {"uuid": "cand", "starting_bid": 1_560_000, "start": now_ms - hour_ms, "end": now_ms + 15 * hour_ms}
    auctions = [(candidate, clean)]
    auctions += [
        (
            {
                "uuid": f"old-{i}",
                "starting_bid": 1_589_999,
                "start": now_ms - 21 * hour_ms,
                "end": now_ms + 27 * hour_ms,
            },
            clean,
        )
        for i in range(11)
    ]
    auctions.append(
        (
            {"uuid": "star5", "starting_bid": 3_000_000, "start": now_ms - 4 * hour_ms, "end": now_ms + 44 * hour_ms},
            starred,
        )
    )
    auctions.append(
        (
            {
                "uuid": "heroic",
                "starting_bid": 35_000_000,
                "start": now_ms - 5 * hour_ms,
                "end": now_ms + 330 * hour_ms,
            },
            starred,
        )
    )

    evaluator.update_cache(auctions, now_ms=now_ms)
    assert evaluator.evaluate_listing(candidate, clean, now_ms=now_ms) is None

    # Only starred competition: the 5-star listings must never become the reference (old code -> 3M target).
    evaluator.update_cache(
        [
            (candidate, clean),
            ({"uuid": "s1", "starting_bid": 3_000_000, "start": now_ms, "end": now_ms + hour_ms}, starred),
            ({"uuid": "s2", "starting_bid": 3_200_000, "start": now_ms, "end": now_ms + hour_ms}, starred),
        ],
        now_ms=now_ms,
    )
    assert evaluator.evaluate_listing(candidate, clean, now_ms=now_ms) is None

    # Genuine clean underpricing still flips, referenced off the clean floor (not the 4M starred one).
    evaluator.update_cache(
        [
            (candidate, clean),
            ({"uuid": "c2", "starting_bid": 3_000_000, "start": now_ms, "end": now_ms + hour_ms}, clean),
            ({"uuid": "c3", "starting_bid": 3_200_000, "start": now_ms, "end": now_ms + hour_ms}, clean),
            ({"uuid": "s1", "starting_bid": 4_000_000, "start": now_ms, "end": now_ms + hour_ms}, starred),
        ],
        now_ms=now_ms,
    )
    flip = evaluator.evaluate_listing(candidate, clean, now_ms=now_ms)
    assert flip is not None
    assert flip.valuation_path == "PATH_A"
    assert flip.target_resell_price <= 3_000_000
    assert flip.base_lbin == 1_560_000

    # A cheaper starred copy (1.8M) dominates the clean 3M floor, so it caps the target -> not profitable.
    evaluator.update_cache(
        [
            (candidate, clean),
            ({"uuid": "c2", "starting_bid": 3_000_000, "start": now_ms, "end": now_ms + hour_ms}, clean),
            ({"uuid": "c3", "starting_bid": 3_200_000, "start": now_ms, "end": now_ms + hour_ms}, clean),
            ({"uuid": "s1", "starting_bid": 1_800_000, "start": now_ms, "end": now_ms + hour_ms}, starred),
        ],
        now_ms=now_ms,
    )
    assert evaluator.evaluate_listing(candidate, clean, now_ms=now_ms) is None


# =====================================================================
# 6. PURSE BUDGET GATE & OUT_OF_BUDGET ROUTING
# =====================================================================


def test_evaluator_sets_is_out_of_budget():
    """Verify FlipResult correctly flags is_out_of_budget when buy_price > MAX_PURSE_BUDGET."""
    config = get_settings(
        DRY_RUN=True,
        MAX_PURSE_BUDGET=100_000_000,
        MIN_SAMPLE_SIZE=2,
        MIN_PROFIT_COINS=1_000_000,
    )
    evaluator = FlipEvaluator(config)

    now_ms = 1_000_000_000_000
    start_time = now_ms - 60_000
    auctions = [
        ({"starting_bid": 120_000_000, "start": start_time}, {"internal_id": "EXPENSIVE_ITEM"}),
        ({"starting_bid": 150_000_000, "start": start_time}, {"internal_id": "EXPENSIVE_ITEM"}),
    ]
    evaluator.update_cache(auctions, now_ms=now_ms)

    candidate = {"uuid": "u1", "starting_bid": 120_000_000}
    decoded = {"internal_id": "EXPENSIVE_ITEM"}

    flip = evaluator.evaluate_listing(candidate, decoded)
    assert flip is not None
    assert flip.is_out_of_budget is True


@pytest.mark.asyncio
async def test_discord_notifier_routes_out_of_budget_webhook():
    """Verify out-of-budget flips are routed to OUT_OF_BUDGET_WEBHOOK_URL when present."""
    config = get_settings(
        DRY_RUN=False,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/main",
        OUT_OF_BUDGET_WEBHOOK_URL="https://discord.com/api/webhooks/secondary_budget",
        MAX_PURSE_BUDGET=50_000_000,
    )

    mock_resp = AsyncMock()
    mock_resp.status = 200

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    notifier = DiscordNotifier(config, session=mock_session)

    flip = FlipResult(
        auction_uuid="out-budget-flip",
        internal_id="HYPERION",
        item_name="Hyperion",
        buy_price=600_000_000,
        target_resell_price=700_000_000,
        net_profit=86_000_000,
        margin_pct=14.3,
        is_out_of_budget=True,
    )

    success = await notifier.send_alert(flip)
    assert success is True

    # Assert POST was called with the secondary out-of-budget webhook!
    mock_session.post.assert_called_once()
    called_url = mock_session.post.call_args[0][0]
    assert called_url == "https://discord.com/api/webhooks/secondary_budget"


@pytest.mark.asyncio
async def test_discord_notifier_drops_out_of_budget_when_no_secondary_webhook():
    """Verify out-of-budget flips are skipped when OUT_OF_BUDGET_WEBHOOK_URL is None."""
    config = get_settings(
        DRY_RUN=False,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/main",
        OUT_OF_BUDGET_WEBHOOK_URL=None,
        MAX_PURSE_BUDGET=50_000_000,
    )

    mock_session = MagicMock()
    notifier = DiscordNotifier(config, session=mock_session)

    flip = FlipResult(
        auction_uuid="out-budget-flip-2",
        internal_id="TERMINATOR",
        item_name="Terminator",
        buy_price=500_000_000,
        target_resell_price=600_000_000,
        net_profit=88_000_000,
        margin_pct=17.6,
        is_out_of_budget=True,
    )

    success = await notifier.send_alert(flip)
    assert success is False
    mock_session.post.assert_not_called()


# =====================================================================
# 7. WEBSOCKET FIREHOSE INGESTION & NORMALIZATION
# =====================================================================


def test_normalize_auction_payload_coflnet_format():
    """Verify normalize_auction_payload parses Coflnet modsocket wrapper payloads."""
    coflnet_raw = {
        "type": "auction",
        "data": {
            "uuid": "cofl-1234",
            "startingBid": 12500000,
            "itemBytes": "H4sIC...",
            "itemName": "Shadow Assassin Chestplate",
            "bin": True,
            "startTime": 1600000000,
            "endTime": 1600086400,
        },
    }

    normalized = normalize_auction_payload(coflnet_raw)
    assert normalized is not None
    assert normalized["uuid"] == "cofl-1234"
    assert normalized["starting_bid"] == 12_500_000
    assert normalized["item_bytes"] == "H4sIC..."
    assert normalized["item_name"] == "Shadow Assassin Chestplate"
    assert normalized["bin"] is True
    assert normalized["start"] == 1600000000
    assert normalized["end"] == 1600086400


def test_normalize_auction_payload_skips_heartbeats_and_invalid():
    """Verify normalize_auction_payload cleanly ignores pings and non-auction messages."""
    assert normalize_auction_payload({"type": "ping"}) is None
    assert normalize_auction_payload({"type": "pong"}) is None
    assert normalize_auction_payload({"type": "handshake"}) is None
    assert normalize_auction_payload("invalid json string") is None
    assert normalize_auction_payload({}) is None


@pytest.mark.asyncio
async def test_websocket_feed_client_yields_normalized_batches():
    """Verify WebSocketFeedClient streams and yields normalized auction batches."""
    config = get_settings(DRY_RUN=True)
    client = WebSocketFeedClient(config, ws_url="wss://mock.stream")

    # Mock incoming WebSocket text message
    mock_msg = MagicMock()
    mock_msg.type = 1  # WSMsgType.TEXT in aiohttp
    mock_msg.data = (
        '{"type": "auction", "data": {"uuid": "ws-1", "startingBid": 2000000, "itemBytes": "abc", "itemName": "Test"}}'
    )

    async def mock_ws_iter():
        yield mock_msg

    mock_ws = MagicMock()
    mock_ws.__aiter__.side_effect = mock_ws_iter

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.ws_connect.return_value.__aenter__.return_value = mock_ws

    client._session = mock_session

    async def run_stream():
        results = []
        async for batch in client.stream_auctions():
            results.extend(batch)
            break
        return results

    batch = await run_stream()
    assert len(batch) == 1
    assert batch[0]["uuid"] == "ws-1"
    assert batch[0]["starting_bid"] == 2_000_000
    assert batch[0]["item_name"] == "Test"
