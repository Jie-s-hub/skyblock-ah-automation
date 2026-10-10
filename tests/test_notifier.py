"""Tests for Discord notifier embed formatting and webhook delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.evaluator import FlipResult
from src.notifier import DiscordNotifier, format_upgrades


@pytest.fixture
def sample_flip() -> FlipResult:
    return FlipResult(
        auction_uuid="abc-123-uuid",
        internal_id="HYPERION",
        item_name="Hyperion ✪✪✪✪✪",
        buy_price=10_000_000,
        target_resell_price=15_000_000,
        net_profit=4_700_000,
        margin_pct=47.0,
        dungeon_stars=5,
        rarity_upgrades=1,
        enchantments={"sharpness": 7, "critical": 6},
    )


def test_format_upgrades(sample_flip: FlipResult):
    """Verify upgrades formatting includes stars, recombobulator flag, and enchants."""
    formatted = format_upgrades(sample_flip)
    assert "⭐ 5 Stars" in formatted
    assert "✅ Yes" in formatted
    assert "Critical 6" in formatted
    assert "Sharpness 7" in formatted


def test_build_embed_payload(sample_flip: FlipResult):
    """Verify embed structure and fields adhere to Discord webhook specifications."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    payload = notifier.build_embed_payload(sample_flip)
    assert "embeds" in payload
    assert len(payload["embeds"]) == 1

    embed = payload["embeds"][0]
    assert embed["title"] == "[FLIP FOUND] Hyperion ✪✪✪✪✪"
    assert embed["color"] == 0x00FF00

    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert "10,000,000 coins" in field_map["Buy Price"]
    assert "15,000,000 coins" in field_map["Target Resell"]
    assert "+4,700,000 coins (47.0%)" in field_map["Net Profit"]
    assert "```/viewauction abc-123-uuid```" in field_map["Quick Command"]


@pytest.mark.asyncio
async def test_send_alert_dry_run(sample_flip: FlipResult, capsys: pytest.CaptureFixture):
    """Verify DRY_RUN mode prints formatted alert to stdout and skips HTTP request."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    success = await notifier.send_alert(sample_flip)
    assert success is True

    captured = capsys.readouterr()
    assert "[DRY RUN FLIP] Hyperion ✪✪✪✪✪" in captured.out
    assert "Buy Price:     10,000,000 coins" in captured.out
    assert "/viewauction abc-123-uuid" in captured.out


@pytest.mark.asyncio
async def test_send_alert_discord_success(sample_flip: FlipResult):
    """Verify send_alert delivers JSON to Discord webhook when configured."""
    config = Settings(
        DRY_RUN=False,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/valid/test",
    )

    mock_resp = AsyncMock()
    mock_resp.status = 204

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    with patch("asyncio.sleep", new_callable=AsyncMock):
        notifier = DiscordNotifier(config, session=mock_session)
        success = await notifier.send_alert(sample_flip)

    assert success is True
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert call_args[0][0] == "https://discord.com/api/webhooks/valid/test"


def test_build_embed_payload_priority_staple():
    """Verify priority staple embed produces orange/gold color, flame title, and priority field."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    priority_flip = FlipResult(
        auction_uuid="priority-uuid-1",
        internal_id="NECRON_CHESTPLATE",
        item_name="Necron's Chestplate",
        buy_price=20_000_000,
        target_resell_price=23_000_000,
        net_profit=2_540_000,
        margin_pct=12.7,
        is_priority=True,
        staple_category="WITHER_ARMOR",
    )

    payload = notifier.build_embed_payload(priority_flip)
    assert "embeds" in payload
    embed = payload["embeds"][0]

    # Priority styling assertions
    assert embed["title"] == "🔥 [PRIORITY STAPLE] Necron's Chestplate"
    assert embed["color"] == 0xFF9900  # Flame Gold / Orange

    # Dedicated priority classification field
    field_map = {f["name"]: f for f in embed["fields"]}
    assert "⚡ Priority Classification" in field_map
    priority_field = field_map["⚡ Priority Classification"]
    assert priority_field["value"] == "**WITHER_ARMOR** *(High Velocity Demand)*"
    assert priority_field["inline"] is False


def test_build_embed_payload_standard_item_preserves_green_embed():
    """Verify non-priority item preserves standard title, emerald green color, and no priority field."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    standard_flip = FlipResult(
        auction_uuid="standard-uuid-1",
        internal_id="SUPER_CLEAVER",
        item_name="Super Cleaver",
        buy_price=1_000_000,
        target_resell_price=3_000_000,
        net_profit=1_940_000,
        margin_pct=194.0,
        is_priority=False,
        staple_category=None,
    )

    payload = notifier.build_embed_payload(standard_flip)
    embed = payload["embeds"][0]

    assert embed["title"] == "[FLIP FOUND] Super Cleaver"
    assert embed["color"] == 0x00FF00  # Standard Emerald Green
    field_names = [f["name"] for f in embed["fields"]]
    assert "⚡ Priority Classification" not in field_names


@pytest.mark.asyncio
async def test_send_alert_dry_run_priority_badge(capsys: pytest.CaptureFixture):
    """Verify dry-run console output includes priority badge for priority staples."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    priority_flip = FlipResult(
        auction_uuid="priority-dry-uuid",
        internal_id="LIVID_DAGGER",
        item_name="Livid Dagger",
        buy_price=6_000_000,
        target_resell_price=6_700_000,
        net_profit=566_000,
        margin_pct=9.43,
        is_priority=True,
        staple_category="MIDGAME_COMBAT",
    )

    success = await notifier.send_alert(priority_flip)
    assert success is True

    captured = capsys.readouterr()
    assert "🔥 [PRIORITY: MIDGAME_COMBAT]" in captured.out
    assert "Livid Dagger" in captured.out
    assert "566,000 coins" in captured.out


@pytest.mark.asyncio
async def test_send_alert_dry_run_standard_flip_no_priority_badge(capsys: pytest.CaptureFixture):
    """Verify dry-run console output does not include priority badge for non-priority flips."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    standard_flip = FlipResult(
        auction_uuid="std-dry-uuid",
        internal_id="NORMAL_ITEM",
        item_name="Normal Sword",
        buy_price=2_000_000,
        target_resell_price=3_000_000,
        net_profit=940_000,
        margin_pct=47.0,
        is_priority=False,
        staple_category=None,
    )

    success = await notifier.send_alert(standard_flip)
    assert success is True

    captured = capsys.readouterr()
    assert "🔥 [PRIORITY:" not in captured.out
    assert "[DRY RUN FLIP] Normal Sword (NORMAL_ITEM)" in captured.out


def test_build_embed_payload_networth_snipe():
    """Verify Format B networth embed displays royal purple color, itemized tree, and M formatting."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    networth_flip = FlipResult(
        auction_uuid="networth-uuid-1",
        internal_id="NECRON_CHESTPLATE",
        item_name="Ancient Necron's Chestplate ✪✪✪✪✪",
        buy_price=25_000_000,
        target_resell_price=52_200_000,
        net_profit=26_156_000,
        margin_pct=104.6,
        is_priority=False,
        staple_category=None,
        is_networth_snipe=True,
        base_lbin=20_000_000,
        modifier="ancient",
        addon_breakdown=[
            {"type": "recomb", "name": "Recombobulator 3000", "cost": 10_000_000},
            {"type": "essence", "name": "2250x ESSENCE_WITHER", "cost": 9_000_000},
            {"type": "reforge", "name": "Precursor Gear", "cost": 7_000_000},
            {"type": "enchant", "name": "Ultimate Legion 5", "cost": 20_000_000},
        ],
        total_addon_cost=46_000_000,
        intrinsic_worth=66_000_000,
    )

    payload = notifier.build_embed_payload(networth_flip)
    assert "embeds" in payload
    embed = payload["embeds"][0]

    assert embed["title"] == "[NETWORTH SNIPE] Ancient Necron's Chestplate ✪✪✪✪✪"
    assert embed["color"] == 0x9B59B6  # Royal purple

    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert "25,000,000 (25.00M)" in field_map["Price"]
    assert "20,000,000 (20.00M)" in field_map["Base Price"]
    assert field_map["Retention Rate Applied"] == "70%"
    assert "52,200,000 coins" in field_map["Estimated Resell Value"]
    assert "+26,156,000 coins (104.6%)" in field_map["Net Profit"]

    # Verify Calculations itemized tree
    calc_val = field_map["Calculations"]
    assert "→ Recombobulator 3000 (10,000,000)" in calc_val
    assert "→ 2250x ESSENCE_WITHER (9,000,000)" in calc_val
    assert "→ Precursor Gear (7,000,000)" in calc_val
    assert "→ Ultimate Legion 5 (20,000,000)" in calc_val


def test_build_embed_payload_priority_networth_snipe():
    """Verify priority networth snipe displays flame title and flame gold color."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    priority_networth_flip = FlipResult(
        auction_uuid="priority-networth-uuid",
        internal_id="NECRON_CHESTPLATE",
        item_name="Ancient Necron's Chestplate ✪✪✪✪✪",
        buy_price=25_000_000,
        target_resell_price=52_200_000,
        net_profit=26_156_000,
        margin_pct=104.6,
        is_priority=True,
        staple_category="WITHER_ARMOR",
        is_networth_snipe=True,
        base_lbin=20_000_000,
        modifier="ancient",
        addon_breakdown=[
            {"type": "recomb", "name": "Recombobulator 3000", "cost": 10_000_000},
        ],
        total_addon_cost=10_000_000,
        intrinsic_worth=30_000_000,
    )

    payload = notifier.build_embed_payload(priority_networth_flip)
    embed = payload["embeds"][0]

    assert embed["title"] == "🔥 [PRIORITY NETWORTH SNIPE] Ancient Necron's Chestplate ✪✪✪✪✪"
    assert embed["color"] == 0xFF9900  # Priority flame


@pytest.mark.asyncio
async def test_send_alert_dry_run_networth_snipe(capsys: pytest.CaptureFixture):
    """Verify dry-run console output for networth snipe shows calculations tree."""
    config = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(config)

    networth_flip = FlipResult(
        auction_uuid="dry-networth-uuid",
        internal_id="HYPERION",
        item_name="Hyperion ✪✪✪✪✪",
        buy_price=1_500_000_000,
        target_resell_price=1_800_000_000,
        net_profit=264_000_000,
        margin_pct=17.6,
        is_priority=True,
        staple_category="WITHER_WEAPON",
        is_networth_snipe=True,
        base_lbin=1_400_000_000,
        modifier="withered",
        addon_breakdown=[
            {"type": "recomb", "name": "Recombobulator 3000", "cost": 10_000_000},
            {"type": "reforge", "name": "Wither Blood", "cost": 6_000_000},
        ],
        total_addon_cost=16_000_000,
        intrinsic_worth=1_416_000_000,
    )

    success = await notifier.send_alert(networth_flip)
    assert success is True

    captured = capsys.readouterr()
    assert "[DRY RUN NETWORTH SNIPE]" in captured.out
    assert "Hyperion ✪✪✪✪✪" in captured.out
    assert "Base Price:             1,400,000,000 coins" in captured.out
    assert "Calculations:" in captured.out
    assert "→ Recombobulator 3000 (10,000,000)" in captured.out
    assert "→ Wither Blood (6,000,000)" in captured.out
    assert "Retention Rate Applied: 70%" in captured.out


@pytest.mark.asyncio
async def test_send_alert_broadcasts_to_ws_broadcaster(sample_flip: FlipResult):
    """Verify that DiscordNotifier broadcasts qualifying flips to ws_broadcaster."""
    config = Settings(DRY_RUN=True)
    mock_broadcaster = AsyncMock()
    notifier = DiscordNotifier(config, ws_broadcaster=mock_broadcaster)

    success = await notifier.send_alert(sample_flip)
    assert success is True
    mock_broadcaster.broadcast_flip.assert_awaited_once_with(sample_flip)
