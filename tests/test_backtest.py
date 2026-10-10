"""Unit tests for the Replay Backtesting Engine (scripts/backtest.py)."""

from pathlib import Path
import pytest

from scripts.backtest import BacktestEngine, main
from src.db import DatabaseManager


@pytest.fixture
def backtest_db(tmp_path: Path) -> tuple[DatabaseManager, str]:
    db_file = str(tmp_path / "backtest_test.db")
    manager = DatabaseManager(db_path=db_file)
    yield manager, db_file
    manager.close()


def test_backtest_empty_database(backtest_db: tuple[DatabaseManager, str]):
    """Verify backtester returns empty metrics safely when no historical alerts exist."""
    manager, _ = backtest_db
    engine = BacktestEngine(db=manager)
    results = engine.run_backtest(initial_capital=50_000_000)

    assert results.total_alerts == 0
    assert results.matched_sales == 0
    assert results.hits == 0
    assert results.hit_rate_pct == 0.0
    assert results.mae == 0.0
    assert results.rmse == 0.0
    assert results.final_capital == 50_000_000
    assert results.capital_growth_pct == 0.0


def test_backtest_hit_rate_and_error_metrics(backtest_db: tuple[DatabaseManager, str]):
    """
    Verify mathematical calculation of Hit Rate, MAE, RMSE, and Capital Growth.
    Alert 1: Buy 20M, Estimated 30M at t=1000.
             Sale occurs at t=2000 for 30M. (Hit! Error: 0)
             Realized PnL: 30M * 0.98 - 20M = 9.4M
    Alert 2: Buy 40M, Estimated 60M at t=3000.
             Sale occurs at t=4000 for 50M. (Miss! Realized < 60M * 0.98. Error: |50M - 60M| = 10M)
             Realized PnL: 50M * 0.98 - 40M = 9.0M
    """
    manager, db_file = backtest_db

    # Alert 1
    manager.log_alert(
        auction_uuid="alert-1",
        internal_id="DIVAN_HELMET",
        buy_price=20_000_000,
        estimated_resell=30_000_000,
        projected_profit=9_400_000,
        margin_pct=47.0,
        timestamp=1000,
    )
    # Sale 1
    manager.record_sale(
        uuid="sale-1",
        internal_id="DIVAN_HELMET",
        price=30_000_000,
        buyer_uuid="b1",
        seller_uuid="s1",
        timestamp=2000,
    )

    # Alert 2
    manager.log_alert(
        auction_uuid="alert-2",
        internal_id="NECRON_HELMET",
        buy_price=40_000_000,
        estimated_resell=60_000_000,
        projected_profit=18_800_000,
        margin_pct=47.0,
        timestamp=3000,
    )
    # Sale 2
    manager.record_sale(
        uuid="sale-2",
        internal_id="NECRON_HELMET",
        price=50_000_000,
        buyer_uuid="b2",
        seller_uuid="s2",
        timestamp=4000,
    )

    engine = BacktestEngine(db=manager)
    results = engine.run_backtest(initial_capital=100_000_000)

    assert results.total_alerts == 2
    assert results.matched_sales == 2
    assert results.hits == 1
    assert results.hit_rate_pct == 50.0  # 1 hit out of 2 matched
    # MAE = (0 + 10,000,000) / 2 = 5,000,000
    assert results.mae == 5_000_000.0
    # RMSE = sqrt((0^2 + 10M^2) / 2) = sqrt(50,000,000,000,000) ≈ 7,071,067.81
    assert pytest.approx(results.rmse, rel=1e-3) == 7_071_067.81
    # Recomputed by hand per Phase 1 fee model (10M-100M: 2% creation + 1% collection = 3%):
    # Sale 1 (30M): fee = 900k -> net revenue = 29.1M -> profit = 29.1M - 20M = 9.1M
    # Sale 2 (50M): fee = 1.5M -> net revenue = 48.5M -> profit = 48.5M - 40M = 8.5M
    # Total profit = 9.1M + 8.5M = 17.6M
    assert results.total_simulated_profit == 17_600_000
    assert results.final_capital == 100_000_000 + 17_600_000
    assert results.capital_growth_pct == pytest.approx(17.6)

    summary_text = results.summary()
    assert "HISTORICAL REPLAY BACKTEST REPORT" in summary_text
    assert "50.00%" in summary_text
    assert "+17.60%" in summary_text


def test_backtest_cli_main(backtest_db: tuple[DatabaseManager, str], capsys):
    """Test CLI invocation of scripts.backtest."""
    manager, db_file = backtest_db
    manager.log_alert(
        auction_uuid="cli-alert",
        internal_id="STORM_HELMET",
        buy_price=10_000_000,
        estimated_resell=15_000_000,
        projected_profit=4_700_000,
        margin_pct=47.0,
        timestamp=1000,
    )
    manager.record_sale(
        uuid="cli-sale",
        internal_id="STORM_HELMET",
        price=15_000_000,
        buyer_uuid="b",
        seller_uuid="s",
        timestamp=2000,
    )

    exit_code = main(["--db", db_file, "--sample-size", "10", "--capital", "50000000"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "HISTORICAL REPLAY BACKTEST REPORT" in captured.out
    assert "100.00%" in captured.out
