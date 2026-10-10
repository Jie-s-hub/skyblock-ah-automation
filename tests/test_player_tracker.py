"""Unit and integration tests for player trade recognition and portfolio tracking (Phase 2)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from scripts.ledger_cli import main as ledger_cli_main
from src.config import Settings
from src.db import DatabaseManager
from src.notifier import DiscordNotifier
from src.player_tracker import CompletedTrade, PlayerTradeTracker, normalize_uuid


@pytest.fixture
def tracker_fixture(tmp_path: Path) -> tuple[DatabaseManager, PlayerTradeTracker, str]:
    db_file = str(tmp_path / "player_tracker_test.db")
    db = DatabaseManager(db_path=db_file)
    cfg = Settings(DRY_RUN=True, TRACKED_PLAYER_IGN="ongvvkk", ENABLE_PLAYER_TRACKING=True)
    tracker = PlayerTradeTracker(db=db, config=cfg)
    # Target UUID (undashed) for ongvvkk: e.g. "069a79f444e94726a5befca90e38aaf5"
    tracker.set_tracked_uuid("069a79f4-44e9-4726-a5be-fca90e38aaf5")
    yield db, tracker, db_file
    db.close()


def test_normalize_uuid():
    """Verify UUID normalization strips dashes and lowercases."""
    assert normalize_uuid("069A79F4-44E9-4726-A5BE-FCA90E38AAF5") == "069a79f444e94726a5befca90e38aaf5"
    assert normalize_uuid("069a79f444e94726a5befca90e38aaf5") == "069a79f444e94726a5befca90e38aaf5"
    assert normalize_uuid("") == ""
    assert normalize_uuid(None) == ""


@pytest.mark.asyncio
async def test_resolve_identity_mojang_api(tmp_path: Path):
    """Verify Mojang API resolution and caching."""
    db_file = str(tmp_path / "mojang_test.db")
    db = DatabaseManager(db_path=db_file)
    cfg = Settings(DRY_RUN=True, TRACKED_PLAYER_IGN="ongvvkk")

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"id": "069a79f444e94726a5befca90e38aaf5", "name": "ongvvkk"})

    mock_session = MagicMock()
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    tracker = PlayerTradeTracker(db=db, config=cfg, session=mock_session)
    uuid = await tracker.resolve_identity()

    assert uuid == "069a79f444e94726a5befca90e38aaf5"
    assert tracker.tracked_uuid == "069a79f444e94726a5befca90e38aaf5"
    db.close()


def test_buy_attribution_to_held(tracker_fixture: tuple[DatabaseManager, PlayerTradeTracker, str]):
    """Verify when buyer == tracked_uuid, auction is logged as HELD in purchases."""
    db, tracker, _ = tracker_fixture

    ended_auctions = [
        {
            "auction_id": "buy-auction-1",
            "buyer": "069a79f444e94726a5befca90e38aaf5",  # ongvvkk
            "seller": "someone_else_uuid",
            "price": 45_000_000,
            "timestamp": 1000,
            "decoded": {
                "internal_id": "NECRON_CHESTPLATE",
                "item_name": "Necron's Chestplate",
                "item_uuid": "nbt-unique-item-uuid-1",
            },
        },
        {
            "auction_id": "unrelated-auction",
            "buyer": "unrelated_buyer",
            "seller": "unrelated_seller",
            "price": 10_000_000,
            "timestamp": 1000,
            "decoded": {"internal_id": "LIVID_DAGGER"},
        },
    ]

    completed = tracker.process_ended_auctions(ended_auctions)
    assert len(completed) == 0  # No sales completed yet, only buy

    # Check purchase in DB
    purchase = db.get_purchase("buy-auction-1")
    assert purchase is not None
    assert purchase["status"] == "HELD"
    assert purchase["buy_price"] == 45_000_000
    assert purchase["item_uuid"] == "nbt-unique-item-uuid-1"
    assert purchase["internal_id"] == "NECRON_CHESTPLATE"


def test_sale_attribution_and_matching_item_uuid(tracker_fixture: tuple[DatabaseManager, PlayerTradeTracker, str]):
    """Verify sale matching via exact item_uuid calculates progressive tax and ROI."""
    db, tracker, _ = tracker_fixture

    # 1. Pre-seed a HELD purchase with an item_uuid
    db.record_purchase(
        auction_uuid="pos-1",
        internal_id="HYPERION",
        buy_price=900_000_000,
        buy_timestamp=10_000,
        item_uuid="hyp-item-uuid-999",
        status="HELD",
    )

    # 2. ongvvkk sells this exact item at t=46,000 for 1,150,000,000 coins (1.15B)
    # Recomputed by hand per Phase 1 fee model (>=100M: 2.5% creation fee + 1.0% collection fee):
    # Creation fee: 1,150,000,000 * 0.025 = 28,750,000
    # Collection fee: 1,150,000,000 * 0.010 = 11,500,000
    # Total tax: 28,750,000 + 11,500,000 = 40,250,000
    # Net revenue: 1,150,000,000 - 40,250,000 = 1,109,750,000
    # Net profit: 1,109,750,000 - 900,000,000 = 209,750,000
    ended_auctions = [
        {
            "auction_id": "sell-auction-1",
            "seller": "069a79f444e94726a5befca90e38aaf5",  # ongvvkk sells!
            "buyer": "buyer_whale_uuid",
            "price": 1_150_000_000,
            "timestamp": 46_000,
            "decoded": {
                "internal_id": "HYPERION",
                "item_name": "Hyperion",
                "item_uuid": "hyp-item-uuid-999",
            },
        }
    ]

    completed_trades = tracker.process_ended_auctions(ended_auctions)
    assert len(completed_trades) == 1

    trade = completed_trades[0]
    assert trade.internal_id == "HYPERION"
    assert trade.buy_price == 900_000_000
    assert trade.sale_price == 1_150_000_000
    assert trade.progressive_tax == 40_250_000
    assert trade.net_realized_pnl == 209_750_000
    assert trade.roi_pct == round((209_750_000 / 900_000_000) * 100.0, 2)
    assert trade.hold_duration_seconds == 36.0  # (46000 - 10000) / 1000

    # Verify status in database updated to SOLD
    saved = db.get_purchase("pos-1")
    assert saved["status"] == "SOLD"
    assert saved["realized_sell_price"] == 1_150_000_000
    assert saved["net_realized_pnl"] == 209_750_000


def test_sale_attribution_fifo_fallback(tracker_fixture: tuple[DatabaseManager, PlayerTradeTracker, str]):
    """Verify FIFO fallback matches the oldest open HELD item when item_uuid is missing or changed."""
    db, tracker, _ = tracker_fixture

    # Pre-seed two purchases of JUJU_SHORTBOW at different times
    db.record_purchase(
        auction_uuid="juju-old",
        internal_id="JUJU_SHORTBOW",
        buy_price=20_000_000,
        buy_timestamp=1000,
        item_uuid=None,  # No item UUID
        status="HELD",
    )
    db.record_purchase(
        auction_uuid="juju-new",
        internal_id="JUJU_SHORTBOW",
        buy_price=22_000_000,
        buy_timestamp=5000,
        item_uuid=None,
        status="HELD",
    )

    # ongvvkk sells one JUJU_SHORTBOW for 30M
    # Recomputed by hand per Phase 1 fee model (10M-100M: 2% creation + 1% collection = 3%):
    # Tax on 30M: 30,000,000 * 0.03 = 900,000 -> net revenue 29,100,000
    # Oldest purchase is 'juju-old' (buy 20M) -> net PnL = 29,100,000 - 20,000,000 = 9,100,000
    ended = [
        {
            "auction_id": "sell-juju",
            "seller": "069a79f444e94726a5befca90e38aaf5",
            "buyer": "buyer_123",
            "price": 30_000_000,
            "timestamp": 10_000,
            "decoded": {
                "internal_id": "JUJU_SHORTBOW",
                "item_name": "Juju Shortbow",
            },
        }
    ]

    completed = tracker.process_ended_auctions(ended)
    assert len(completed) == 1
    assert completed[0].auction_uuid == "juju-old"
    assert completed[0].buy_price == 20_000_000
    assert completed[0].net_realized_pnl == 9_100_000

    # Verify 'juju-old' is SOLD and 'juju-new' is still HELD
    assert db.get_purchase("juju-old")["status"] == "SOLD"
    assert db.get_purchase("juju-new")["status"] == "HELD"


def test_discord_completed_trade_embed(tracker_fixture: tuple[DatabaseManager, PlayerTradeTracker, str]):
    """Verify Discord celebration embed formatting for completed flips."""
    _, _, _ = tracker_fixture
    notifier = DiscordNotifier(config=Settings(DRY_RUN=True))

    trade = CompletedTrade(
        auction_uuid="uuid-trade-123",
        internal_id="TERMINATOR",
        item_name="Terminator",
        buy_price=600_000_000,
        sale_price=800_000_000,
        progressive_tax=23_000_000,  # 2% of 800M (16M) + 1% of 700M (7M) = 23M
        net_realized_pnl=177_000_000,
        roi_pct=29.5,
        buy_timestamp=1000,
        sell_timestamp=7201000,
        hold_duration_seconds=7200.0,
        pnl_per_hour=88_500_000.0,
    )

    payload = notifier.build_completed_trade_embed(trade, player_ign="ongvvkk")
    embed = payload["embeds"][0]

    assert "ongvvkk flipped Terminator!" in embed["title"]
    field_map = {f["name"]: f["value"] for f in embed["fields"]}
    assert "600,000,000 coins" in field_map["Bought"]
    assert "800,000,000 coins" in field_map["Sold"]
    assert "+177,000,000 coins" in field_map["Net Realized PnL"]
    assert "2h 0m" in field_map["Hold Duration"]
    assert "88,500,000 coins/hr" in field_map["PnL / Hour"]


def test_portfolio_metrics_and_cli_player_stats(tracker_fixture: tuple[DatabaseManager, PlayerTradeTracker, str], capsys):
    """Verify get_portfolio_metrics and ledger_cli player-stats command."""
    db, tracker, db_file = tracker_fixture

    # Pre-seed 1 sold trade and 1 held position
    db.record_purchase(
        auction_uuid="p-sold-1",
        internal_id="DIVAN_CHESTPLATE",
        buy_price=40_000_000,
        buy_timestamp=1000,
    )
    db.update_purchase_sale(
        auction_uuid="p-sold-1",
        realized_sell_price=60_000_000,
        realized_sell_timestamp=2000,
        net_realized_pnl=18_800_000,
        status="SOLD",
    )

    db.record_purchase(
        auction_uuid="p-held-1",
        internal_id="CLOVER_HELMET",
        buy_price=25_000_000,
        buy_timestamp=3000,
        status="HELD",
    )

    metrics = tracker.get_portfolio_metrics()
    assert metrics["total_trades"] == 1
    assert metrics["win_rate_pct"] == 100.0
    assert metrics["realized_pnl_all_time"] == 18_800_000
    assert metrics["held_items_count"] == 1
    assert metrics["unrealized_capital_deployed"] == 25_000_000

    # Run player-stats CLI command
    exit_code = ledger_cli_main(["--db", db_file, "player-stats", "--player", "ongvvkk"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "PORTFOLIO & TRADE PERFORMANCE: ONGVVKK" in captured.out
    assert "+18,800,000 coins" in captured.out
    assert "Open Positions (HELD):  1" in captured.out
    assert "25,000,000 coins" in captured.out
