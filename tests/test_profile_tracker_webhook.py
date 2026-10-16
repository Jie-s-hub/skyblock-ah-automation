"""Unit and integration tests for Profile AH Tracker webhook command and Discord notifications."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.ledger_cli import main as ledger_cli_main
from scripts.post_profile_tracker import main as post_tracker_main
from src.config import Settings
from src.db import DatabaseManager
from src.notifier import DiscordNotifier
from src.player_tracker import PlayerTradeTracker


@pytest.fixture
def db_with_trades(tmp_path: Path) -> DatabaseManager:
    """Fixture providing a temporary database populated with open and completed trades."""
    db_file = str(tmp_path / "test_tracker_webhook.db")
    db = DatabaseManager(db_path=db_file)

    # 1. Insert open position (HELD)
    db.record_purchase(
        auction_uuid="open-pos-1",
        internal_id="HYPERION",
        item_uuid="inst-hyp-1",
        buy_price=1_200_000_000,
        buy_timestamp=1700000000000,
        status="HELD",
        features_json={"item_name": "Hyperion"},
    )

    # 2. Insert completed trade (SOLD)
    db.record_purchase(
        auction_uuid="sold-pos-1",
        internal_id="TERMINATOR",
        item_uuid="inst-term-1",
        buy_price=650_000_000,
        buy_timestamp=1700000000000,
        status="SOLD",
        features_json={"item_name": "Terminator"},
    )
    db.update_purchase_sale(
        auction_uuid="sold-pos-1",
        realized_sell_price=750_000_000,
        realized_sell_timestamp=1700003600000,
        net_realized_pnl=85_000_000,
        status="SOLD",
    )

    yield db
    db.close()


def test_build_profile_tracker_embed_empty():
    """Verify embed format when no trades exist."""
    cfg = Settings(DRY_RUN=True, DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/dummy")
    notifier = DiscordNotifier(cfg)
    metrics = {
        "player_ign": "ongvvkk",
        "player_uuid": "Unresolved",
        "total_trades": 0,
        "win_rate_pct": 0.0,
        "realized_pnl_daily": 0,
        "realized_pnl_weekly": 0,
        "realized_pnl_all_time": 0,
        "avg_profit_per_trade": 0,
        "avg_hold_duration_sec": 0.0,
        "held_items_count": 0,
        "unrealized_capital_deployed": 0,
    }

    payload = notifier.build_profile_tracker_embed(metrics, open_positions=[], recent_trades=[])
    assert "embeds" in payload
    embed = payload["embeds"][0]
    assert "ongvvkk" in embed["title"]
    assert embed["color"] == 0xF1C40F  # Neutral gold


def test_build_profile_tracker_embed_with_data(db_with_trades: DatabaseManager):
    """Verify embed contains active positions, closed trades, and green color for profit."""
    cfg = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(cfg)
    tracker = PlayerTradeTracker(db=db_with_trades, config=cfg)
    tracker.set_tracked_uuid("test-player-uuid-1234567890123456")

    metrics = tracker.get_portfolio_metrics()
    open_positions = tracker.get_open_positions(limit=5)
    recent_trades = tracker.get_recent_trades(limit=5)

    payload = notifier.build_profile_tracker_embed(
        metrics=metrics,
        open_positions=open_positions,
        recent_trades=recent_trades,
    )
    embed = payload["embeds"][0]

    assert embed["color"] == 0x2ECC71  # Positive emerald green
    field_names = [f["name"] for f in embed["fields"]]
    assert any("Open Positions" in name for name in field_names)
    assert any("Recent Closed Flips" in name for name in field_names)

    # Check that item names are reflected
    open_field = next(f for f in embed["fields"] if "Open Positions" in f["name"])
    assert "Hyperion" in open_field["value"]
    closed_field = next(f for f in embed["fields"] if "Recent Closed Flips" in f["name"])
    assert "Terminator" in closed_field["value"]


@pytest.mark.asyncio
async def test_send_profile_tracker_report_dry_run():
    """Verify send_profile_tracker_report returns True in dry-run mode."""
    cfg = Settings(DRY_RUN=True)
    notifier = DiscordNotifier(cfg)
    metrics = {"player_ign": "test_player", "total_trades": 0}
    result = await notifier.send_profile_tracker_report(metrics)
    assert result is True


@pytest.mark.asyncio
async def test_send_profile_tracker_report_dispatch_mock():
    """Verify webhook HTTP POST is called when DRY_RUN=False."""
    cfg = Settings(DRY_RUN=False, DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test-123")

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="ok")

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    notifier = DiscordNotifier(cfg, session=mock_session)
    metrics = {
        "player_ign": "ongvvkk",
        "player_uuid": "069a79f444e94726a5befca90e38aaf5",
        "total_trades": 1,
        "win_rate_pct": 100.0,
        "realized_pnl_daily": 10_000_000,
        "realized_pnl_weekly": 10_000_000,
        "realized_pnl_all_time": 10_000_000,
        "avg_profit_per_trade": 10_000_000,
        "avg_hold_duration_sec": 3600.0,
        "held_items_count": 0,
        "unrealized_capital_deployed": 0,
    }

    result = await notifier.send_profile_tracker_report(metrics)
    assert result is True
    assert mock_session.post.called
    args, kwargs = mock_session.post.call_args
    assert args[0] == "https://discord.com/api/webhooks/test-123"
    assert "embeds" in kwargs["json"]


def test_post_profile_tracker_cli(db_with_trades: DatabaseManager, monkeypatch: pytest.MonkeyPatch):
    """Test standalone scripts/post_profile_tracker.py CLI command in dry-run mode."""
    exit_code = post_tracker_main([
        "--db", db_with_trades.db_path,
        "--dry-run",
        "--player", "ongvvkk",
    ])
    assert exit_code == 0


def test_ledger_cli_webhook_report(db_with_trades: DatabaseManager):
    """Test scripts/ledger_cli.py webhook-report command in dry-run mode."""
    exit_code = ledger_cli_main([
        "--db", db_with_trades.db_path,
        "webhook-report",
        "--dry-run",
        "--player", "ongvvkk",
    ])
    assert exit_code == 0


def test_ledger_cli_player_stats_webhook(db_with_trades: DatabaseManager):
    """Test scripts/ledger_cli.py player-stats with --webhook flag in dry-run mode."""
    exit_code = ledger_cli_main([
        "--db", db_with_trades.db_path,
        "player-stats",
        "--webhook",
        "--dry-run",
        "--player", "ongvvkk",
    ])
    assert exit_code == 0


@pytest.mark.asyncio
async def test_main_dispatch_profile_report(db_with_trades: DatabaseManager):
    """Test dispatch_profile_report routine from src.main."""
    from src.main import dispatch_profile_report

    cfg = Settings(
        DRY_RUN=True,
        DB_PATH=db_with_trades.db_path,
        TRACKED_PLAYER_IGN="ongvvkk",
        TRACKED_PLAYER_UUID="069a79f444e94726a5befca90e38aaf5",
    )
    exit_code = await dispatch_profile_report(cfg)
    assert exit_code == 0
