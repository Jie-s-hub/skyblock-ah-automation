"""Unit and CLI integration tests for scripts/ledger_cli.py."""

from pathlib import Path

import pytest

from scripts.ledger_cli import main
from src.db import DatabaseManager


@pytest.fixture
def cli_db(tmp_path: Path) -> tuple[DatabaseManager, str]:
    db_file = str(tmp_path / "ledger_test.db")
    manager = DatabaseManager(db_path=db_file)
    yield manager, db_file
    manager.close()


def test_cli_mark_bought_with_alert(cli_db: tuple[DatabaseManager, str], capsys):
    """Test mark-bought inherits buy price and internal_id from recorded alert."""
    manager, db_file = cli_db
    manager.log_alert(
        auction_uuid="uuid-alert-1",
        internal_id="TERMINATOR",
        buy_price=650_000_000,
        estimated_resell=800_000_000,
        projected_profit=126_000_000,
        margin_pct=19.4,
    )

    exit_code = main(["--db", db_file, "mark-bought", "uuid-alert-1"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Marked auction 'uuid-alert-1' (TERMINATOR) as BOUGHT for 650,000,000 coins" in captured.out

    purchase = manager.get_purchase("uuid-alert-1")
    assert purchase is not None
    assert purchase["buy_price"] == 650_000_000
    assert purchase["status"] == "HELD"


def test_cli_mark_bought_explicit_price(cli_db: tuple[DatabaseManager, str], capsys):
    """Test mark-bought with manual --price when alert does not exist."""
    manager, db_file = cli_db

    exit_code = main(
        ["--db", db_file, "mark-bought", "manual-uuid-2", "--price", "15000000", "--notes", "Manual snipe"]
    )
    assert exit_code == 0

    purchase = manager.get_purchase("manual-uuid-2")
    assert purchase is not None
    assert purchase["buy_price"] == 15_000_000
    assert purchase["notes"] == "Manual snipe"


def test_cli_mark_bought_missing_price_error(cli_db: tuple[DatabaseManager, str], capsys):
    """Test error when mark-bought has no alert and no explicit --price."""
    _, db_file = cli_db
    exit_code = main(["--db", db_file, "mark-bought", "nonexistent-uuid"])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error: Auction 'nonexistent-uuid' was not found in alerts" in captured.err


def test_cli_mark_sold_and_report(cli_db: tuple[DatabaseManager, str], capsys):
    """Test mark-sold calculates progressive tax, PnL, and report displays clean summary."""
    manager, db_file = cli_db
    # Record buy
    manager.record_purchase(
        auction_uuid="flip-1",
        internal_id="HYPERION",
        buy_price=1_000_000_000,
        buy_timestamp=1000,
    )

    # Mark sold at 1.2B
    # Recomputed by hand per Phase 1 fee model (>=100M: 2.5% creation fee + 1.0% collection fee = 3.5%):
    # Tax on 1.2B: 1.2B * 0.035 = 42,000,000 -> net revenue 1,158,000,000 -> PnL = +158,000,000 coins
    exit_code = main(["--db", db_file, "mark-sold", "flip-1", "--price", "1200000000", "--timestamp", "15000"])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "TRADE RECORDED" in captured.out
    assert "158,000,000 coins" in captured.out

    # Run report command
    exit_code_rep = main(["--db", db_file, "report"])
    assert exit_code_rep == 0

    rep_captured = capsys.readouterr()
    assert "HYPIXEL AH ARBITRAGE PERFORMANCE LEDGER" in rep_captured.out
    assert "HYPERION" in rep_captured.out
    assert "+158,000,000" in rep_captured.out
    assert "Win Rate:               100.0%" in rep_captured.out
