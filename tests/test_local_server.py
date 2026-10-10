"""Tests for the local WebSocket broadcaster (ws://127.0.0.1:8765) and Fabric mod payload format."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from src.evaluator import FlipResult
from src.local_server import LocalWebSocketBroadcaster, build_flip_payload


def test_build_flip_payload_clean():
    """Verify JSON structure for standard clean flip."""
    flip = FlipResult(
        auction_uuid="test-uuid-clean",
        internal_id="HYPERION",
        item_name="Hyperion",
        buy_price=500_000_000,
        target_resell_price=550_000_000,
        net_profit=39_000_000,
        margin_pct=7.8,
        staple_category="WITHER_IMPACT",
    )

    payload = build_flip_payload(flip)
    assert payload["uuid"] == "test-uuid-clean"
    assert payload["name"] == "Hyperion"
    assert payload["buy_price"] == 500_000_000
    assert payload["target_resell"] == 550_000_000
    assert payload["profit"] == 39_000_000
    assert payload["margin_pct"] == 7.8
    assert payload["category"] == "WITHER_IMPACT"
    assert payload["is_out_of_budget"] is False
    assert isinstance(payload["calculations"], list)
    assert payload["calculations"] == []
    assert "timestamp" in payload


def test_build_flip_payload_with_addons():
    """Verify calculations breakdown for upgraded items with addons."""
    flip = FlipResult(
        auction_uuid="test-uuid-addons",
        internal_id="NECRON_CHESTPLATE",
        item_name="Necron's Chestplate",
        buy_price=80_000_000,
        target_resell_price=120_000_000,
        net_profit=37_600_000,
        margin_pct=47.0,
        is_networth_snipe=True,
        addon_breakdown=[
            {"type": "recombobulator", "name": "Recombobulator 3000", "cost": 10_000_000},
            {"type": "dungeon_stars", "name": "Stars 1-5 (Wither)", "cost": 4_000_000},
            {"type": "master_stars", "name": "Master Star 8", "cost": 18_000_000},
            {"type": "gemstones", "name": "PERFECT JASPER (Applied)", "cost": 22_000_000},
        ],
    )

    payload = build_flip_payload(flip)
    assert payload["category"] == "NETWORTH"
    calcs = payload["calculations"]
    assert len(calcs) == 4
    assert any("Recombobulator 3000 (10,000,000)" in c for c in calcs)
    assert any("Master Star 8 (18,000,000)" in c for c in calcs)
    assert any("PERFECT JASPER (Applied) (22,000,000)" in c for c in calcs)


def test_build_flip_payload_pet():
    """Verify calculations breakdown for pet flips."""
    flip = FlipResult(
        auction_uuid="test-uuid-pet",
        internal_id="GOLDEN_DRAGON",
        item_name="Golden Dragon",
        buy_price=600_000_000,
        target_resell_price=750_000_000,
        net_profit=135_000_000,
        margin_pct=22.5,
        is_pet=True,
        pet_level=200,
        pet_held_item_name="Minos Relic",
        held_item_value=40_000_000,
    )

    payload = build_flip_payload(flip)
    assert payload["category"] == "PET"
    calcs = payload["calculations"]
    assert any("Level: 200" in c for c in calcs)
    assert any("Held Item: Minos Relic (40,000,000)" in c for c in calcs)


def test_build_flip_payload_attributes():
    """Verify calculations breakdown for Kuudra attribute rolls."""
    flip = FlipResult(
        auction_uuid="test-uuid-attrs",
        internal_id="CRIMSON_CHESTPLATE",
        item_name="Crimson Chestplate",
        buy_price=30_000_000,
        target_resell_price=80_000_000,
        net_profit=48_400_000,
        margin_pct=161.33,
        is_attribute_snipe=True,
        attributes={"mana_pool": 6, "veteran": 5},
    )

    payload = build_flip_payload(flip)
    assert payload["category"] == "ATTRIBUTE"
    calcs = payload["calculations"]
    assert any("Mana Pool 6" in c for c in calcs)
    assert any("Veteran 5" in c for c in calcs)


@pytest.mark.asyncio
async def test_websocket_broadcaster_e2e():
    """Verify starting local WebSocket server, connecting client, receiving broadcast, and stopping."""
    broadcaster = LocalWebSocketBroadcaster(host="127.0.0.1", port=0)
    await broadcaster.start()
    assert broadcaster.actual_port > 0

    ws_url = f"ws://127.0.0.1:{broadcaster.actual_port}"

    try:
        async with websockets.connect(ws_url) as client_ws:
            # Allow time for server handler registration
            await asyncio.sleep(0.05)
            assert len(broadcaster.clients) == 1

            # Prepare flip
            flip = FlipResult(
                auction_uuid="uuid-ws-test-1",
                internal_id="TERMINATOR",
                item_name="Terminator",
                buy_price=450_000_000,
                target_resell_price=520_000_000,
                net_profit=59_400_000,
                margin_pct=13.2,
                staple_category="COMBAT_STAPLE",
            )

            # Broadcast flip
            sent_count = await broadcaster.broadcast_flip(flip)
            assert sent_count == 1

            # Receive payload on client
            raw_msg = await asyncio.wait_for(client_ws.recv(), timeout=2.0)
            data = json.loads(raw_msg)

            assert data["uuid"] == "uuid-ws-test-1"
            assert data["name"] == "Terminator"
            assert data["buy_price"] == 450_000_000
            assert data["target_resell"] == 520_000_000
            assert data["profit"] == 59_400_000
            assert data["margin_pct"] == 13.2
            assert data["category"] == "COMBAT_STAPLE"

    finally:
        await broadcaster.stop()
        assert len(broadcaster.clients) == 0


@pytest.mark.asyncio
async def test_websocket_broadcaster_zero_clients():
    """Verify broadcasting with 0 connected clients returns 0 without error."""
    broadcaster = LocalWebSocketBroadcaster(host="127.0.0.1", port=0)
    await broadcaster.start()

    try:
        flip = FlipResult(
            auction_uuid="uuid-0",
            internal_id="TEST",
            item_name="Test Item",
            buy_price=1_000_000,
            target_resell_price=2_000_000,
            net_profit=960_000,
            margin_pct=96.0,
        )
        sent_count = await broadcaster.broadcast_flip(flip)
        assert sent_count == 0
    finally:
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_websocket_broadcaster_multiple_clients():
    """Verify multiple connected Fabric mod clients all receive broadcasts."""
    broadcaster = LocalWebSocketBroadcaster(host="127.0.0.1", port=0)
    await broadcaster.start()
    ws_url = f"ws://127.0.0.1:{broadcaster.actual_port}"

    try:
        async with websockets.connect(ws_url) as ws1, websockets.connect(ws_url) as ws2:
            await asyncio.sleep(0.05)
            assert len(broadcaster.clients) == 2

            flip = FlipResult(
                auction_uuid="uuid-multi",
                internal_id="DIVAN_CHESTPLATE",
                item_name="Divan's Chestplate",
                buy_price=100_000_000,
                target_resell_price=140_000_000,
                net_profit=37_200_000,
                margin_pct=37.2,
            )

            sent = await broadcaster.broadcast_flip(flip)
            assert sent == 2

            msg1 = json.loads(await asyncio.wait_for(ws1.recv(), timeout=2.0))
            msg2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=2.0))

            assert msg1["uuid"] == "uuid-multi"
            assert msg2["uuid"] == "uuid-multi"
    finally:
        await broadcaster.stop()


def test_websocket_refuses_non_loopback():
    """Verify LocalWebSocketBroadcaster refuses binding to non-loopback addresses."""
    with pytest.raises(ValueError, match="only binds to loopback"):
        LocalWebSocketBroadcaster(host="0.0.0.0", port=8765)

    with pytest.raises(ValueError, match="only binds to loopback"):
        LocalWebSocketBroadcaster(host="192.168.1.50", port=8765)

    # Valid loopbacks should not raise
    b1 = LocalWebSocketBroadcaster(host="127.0.0.1", port=8765)
    assert b1.host == "127.0.0.1"
    b2 = LocalWebSocketBroadcaster(host="localhost", port=8765)
    assert b2.host == "localhost"
    b3 = LocalWebSocketBroadcaster(host="::1", port=8765)
    assert b3.host == "::1"


@pytest.mark.asyncio
async def test_websocket_origin_filtering():
    """Verify handshake rejects unapproved Origin and allows approved or absent Origin."""
    broadcaster = LocalWebSocketBroadcaster(
        host="127.0.0.1",
        port=0,
        allowed_origins=["http://localhost:3000", "http://app.local"],
    )
    await broadcaster.start()
    ws_url = f"ws://127.0.0.1:{broadcaster.actual_port}"

    try:
        # 1. Disallowed origin should be rejected (403 Forbidden)
        try:
            connect_kwargs = {"additional_headers": {"Origin": "http://evil.com"}}
            async with websockets.connect(ws_url, **connect_kwargs):
                pytest.fail("Disallowed Origin should have been rejected")
        except websockets.exceptions.InvalidStatus as exc:
            assert exc.response.status_code == 403
        except websockets.exceptions.InvalidStatusCode as exc:
            assert exc.status_code == 403

        # 2. Allowed origin should connect
        connect_kwargs = {"additional_headers": {"Origin": "http://localhost:3000"}}
        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            assert ws is not None

        # 3. Absent origin (e.g. native client mod) should connect
        async with websockets.connect(ws_url) as ws:
            assert ws is not None
    finally:
        await broadcaster.stop()


@pytest.mark.asyncio
async def test_websocket_token_authentication():
    """Verify handshake rejects missing or invalid ?token= and accepts valid token."""
    secret_token = "my-secure-token-xyz"
    broadcaster = LocalWebSocketBroadcaster(
        host="127.0.0.1",
        port=0,
        token=secret_token,
    )
    await broadcaster.start()
    port = broadcaster.actual_port

    try:
        # 1. Missing token -> 401 Unauthorized
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}"):
                pytest.fail("Connection without token should have been rejected")
        except websockets.exceptions.InvalidStatus as exc:
            assert exc.response.status_code == 401
        except websockets.exceptions.InvalidStatusCode as exc:
            assert exc.status_code == 401

        # 2. Invalid token -> 401 Unauthorized
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}?token=bad-token"):
                pytest.fail("Connection with bad token should have been rejected")
        except websockets.exceptions.InvalidStatus as exc:
            assert exc.response.status_code == 401
        except websockets.exceptions.InvalidStatusCode as exc:
            assert exc.status_code == 401

        # 3. Valid token -> Success
        async with websockets.connect(f"ws://127.0.0.1:{port}?token={secret_token}") as ws:
            assert ws is not None
    finally:
        await broadcaster.stop()
