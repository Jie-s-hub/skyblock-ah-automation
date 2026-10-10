"""Unit and integration tests for SQLite persistence engine (src/db.py)."""

import asyncio
from pathlib import Path
import pytest

from src.db import DatabaseManager


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Fixture providing a cleanly isolated DatabaseManager on temporary storage."""
    db_file = tmp_path / "test_market_intelligence.db"
    manager = DatabaseManager(db_path=str(db_file))
    yield manager
    manager.close()


def test_db_wal_mode_and_tables_creation(temp_db: DatabaseManager):
    """Verify that SQLite connection enables WAL mode and creates all required tables."""
    conn = temp_db._get_connection()
    cur = conn.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"

    # Verify tables exist
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('alerts', 'purchases', 'sales_history', 'bazaar_snapshots', 'comps_cache', 'retention_params');"
    )
    tables = {r[0] for r in cur.fetchall()}
    assert tables == {"alerts", "purchases", "sales_history", "bazaar_snapshots", "comps_cache", "retention_params"}


def test_alerts_logging_and_querying(temp_db: DatabaseManager):
    """Verify alert insertion, duplicate ignore, and filtering."""
    success = temp_db.log_alert(
        auction_uuid="uuid-1",
        internal_id="HYPERION",
        buy_price=1_000_000_000,
        estimated_resell=1_200_000_000,
        projected_profit=165_000_000,
        margin_pct=16.5,
        confidence_label="HIGH",
        valuation_path="PATH_B",
        features_json={"stars": 5, "recomb": 1},
        timestamp=1000,
    )
    assert success is True

    # Duplicate auction_uuid should be ignored safely
    dup_success = temp_db.log_alert(
        auction_uuid="uuid-1",
        internal_id="HYPERION",
        buy_price=900_000_000,
        estimated_resell=1_100_000_000,
        projected_profit=150_000_000,
        margin_pct=15.0,
    )
    assert dup_success is False

    # Retrieve by UUID
    alert = temp_db.get_alert("uuid-1")
    assert alert is not None
    assert alert["internal_id"] == "HYPERION"
    assert alert["buy_price"] == 1_000_000_000
    assert alert["confidence_label"] == "HIGH"
    assert alert["valuation_path"] == "PATH_B"
    assert '"stars":5' in alert["features_json"]

    # Retrieve list with filters
    temp_db.log_alert(
        auction_uuid="uuid-2",
        internal_id="TERMINATOR",
        buy_price=500_000_000,
        estimated_resell=600_000_000,
        projected_profit=86_000_000,
        margin_pct=17.2,
        timestamp=2000,
    )

    all_alerts = temp_db.get_alerts()
    assert len(all_alerts) == 2

    hyp_alerts = temp_db.get_alerts(internal_id="HYPERION")
    assert len(hyp_alerts) == 1
    assert hyp_alerts[0]["auction_uuid"] == "uuid-1"

    time_filtered = temp_db.get_alerts(start_time=1500)
    assert len(time_filtered) == 1
    assert time_filtered[0]["auction_uuid"] == "uuid-2"


def test_purchases_ledger_lifecycle(temp_db: DatabaseManager):
    """Verify recording of purchase, update of sale with progressive tax, and status transitions."""
    # 1. Record buy (HELD)
    ok = temp_db.record_purchase(
        auction_uuid="buy-uuid-1",
        internal_id="NECRON_CHESTPLATE",
        buy_price=30_000_000,
        buy_timestamp=1000,
        item_uuid="nbt-item-uuid-123",
        notes="Sniped at 30M",
    )
    assert ok is True

    p = temp_db.get_purchase("buy-uuid-1")
    assert p is not None
    assert p["status"] == "HELD"
    assert p["buy_price"] == 30_000_000
    assert p["item_uuid"] == "nbt-item-uuid-123"

    # 2. Update sale (SOLD)
    # Sold for 45,000,000 (tax at 2% = 900,000 -> net revenue 44.1M -> net PnL = 14.1M)
    sold_ok = temp_db.update_purchase_sale(
        auction_uuid="buy-uuid-1",
        realized_sell_price=45_000_000,
        realized_sell_timestamp=5000,
        status="SOLD",
        notes="Flipped within 4 seconds",
    )
    assert sold_ok is True

    p_sold = temp_db.get_purchase("buy-uuid-1")
    assert p_sold["status"] == "SOLD"
    assert p_sold["realized_sell_price"] == 45_000_000
    assert p_sold["net_realized_pnl"] == 14_100_000  # 45M * 0.98 - 30M
    assert p_sold["notes"] == "Flipped within 4 seconds"

    # Query by status
    held_list = temp_db.get_purchases(status="HELD")
    assert len(held_list) == 0

    sold_list = temp_db.get_purchases(status="SOLD")
    assert len(sold_list) == 1


def test_sales_history_and_comps_cache(temp_db: DatabaseManager):
    """Verify sales history ingestion and comps cache operations."""
    # Record completed sales
    temp_db.record_sale(
        uuid="sale-uuid-1",
        internal_id="JUJU_SHORTBOW",
        price=25_000_000,
        buyer_uuid="buyer-1",
        seller_uuid="seller-1",
        timestamp=1000,
        features_json={"stars": 5},
        features_hash="hash-juju-5s",
    )

    sales = temp_db.get_sales(internal_id="JUJU_SHORTBOW")
    assert len(sales) == 1
    assert sales[0]["price"] == 25_000_000
    assert sales[0]["features_hash"] == "hash-juju-5s"

    # Comps cache upsert & retrieval
    temp_db.set_comps_cache(
        internal_id="JUJU_SHORTBOW",
        features_hash="hash-juju-5s",
        comp_count=12,
        median_price=24_500_000,
        iqr_spread=1_200_000,
        computed_at=2000,
    )

    cache_val = temp_db.get_comps_cache("JUJU_SHORTBOW", "hash-juju-5s")
    assert cache_val is not None
    assert cache_val["comp_count"] == 12
    assert cache_val["median_price"] == 24_500_000
    assert cache_val["iqr_spread"] == 1_200_000


def test_bazaar_snapshots_and_retention_params(temp_db: DatabaseManager):
    """Verify bazaar snapshot recording and empirical bayes retention storage."""
    # Bazaar snapshots
    temp_db.record_bazaar_snapshot(
        product_id="RECOMBOBULATOR_3000",
        buy_price=6_200_000.0,
        sell_price=5_900_000.0,
        buy_volume=150,
        sell_volume=220,
    )
    snaps = temp_db.get_bazaar_snapshots("RECOMBOBULATOR_3000")
    assert len(snaps) == 1
    assert snaps[0]["buy_price"] == 6_200_000.0

    # Retention params
    temp_db.set_retention_params(
        category="DUNGEON_WEAPON",
        sample_size=45,
        fitted_retention=0.685,
    )
    rp = temp_db.get_retention_params("DUNGEON_WEAPON")
    assert rp is not None
    assert rp["sample_size"] == 45
    assert abs(rp["fitted_retention"] - 0.685) < 1e-4


@pytest.mark.asyncio
async def test_async_database_methods(temp_db: DatabaseManager):
    """Verify asynchronous helpers run concurrently without blocking the event loop."""
    ok_alert = await temp_db.async_log_alert(
        auction_uuid="async-uuid-1",
        internal_id="WITHER_SHIELD_SCROLL",
        buy_price=100_000_000,
        estimated_resell=130_000_000,
        projected_profit=26_400_000,
        margin_pct=26.4,
    )
    assert ok_alert is True

    ok_buy = await temp_db.async_record_purchase(
        auction_uuid="async-uuid-1",
        internal_id="WITHER_SHIELD_SCROLL",
        buy_price=100_000_000,
    )
    assert ok_buy is True

    ok_sale = await temp_db.async_record_sale(
        uuid="async-sale-1",
        internal_id="WITHER_SHIELD_SCROLL",
        price=130_000_000,
        buyer_uuid="b-1",
        seller_uuid="s-1",
        timestamp=1000,
    )
    assert ok_sale is True

    ok_bz = await temp_db.async_record_bazaar_snapshot(
        product_id="WITHER_SHIELD_SCROLL",
        buy_price=128_000_000.0,
        sell_price=120_000_000.0,
    )
    assert ok_bz is True
