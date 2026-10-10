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


def test_backtest_reaction_delay_fill_filter(backtest_db: tuple[DatabaseManager, str]):
    """Verify fill model: alert fills only if time_to_sell_s > REACTION_DELAY_S (default 1.5s)."""
    manager, _ = backtest_db

    # Alert 1: time_to_sell_s = 0.8s (<= 1.5s) -> Missed by sniper reaction delay
    manager.log_alert(
        auction_uuid="alert-fast",
        internal_id="FAST_ITEM",
        buy_price=10_000_000,
        estimated_resell=15_000_000,
        projected_profit=4_500_000,
        margin_pct=45.0,
        timestamp=1000,
    )
    manager.record_sale(
        uuid="sale-fast",
        internal_id="FAST_ITEM",
        price=15_000_000,
        buyer_uuid="b1",
        seller_uuid="s1",
        timestamp=1800,
        time_to_sell_s=0.8,
    )

    # Alert 2: time_to_sell_s = 3.5s (> 1.5s) -> Successfully filled
    manager.log_alert(
        auction_uuid="alert-slow",
        internal_id="SLOW_ITEM",
        buy_price=10_000_000,
        estimated_resell=15_000_000,
        projected_profit=4_500_000,
        margin_pct=45.0,
        timestamp=3000,
    )
    manager.record_sale(
        uuid="sale-slow",
        internal_id="SLOW_ITEM",
        price=15_000_000,
        buyer_uuid="b2",
        seller_uuid="s2",
        timestamp=6500,
        time_to_sell_s=3.5,
    )

    engine = BacktestEngine(db=manager)
    results = engine.run_backtest(reaction_delay_s=1.5, initial_capital=50_000_000)

    assert results.total_alerts == 2
    assert results.matched_sales == 2
    assert results.filled_trades == 1  # Only the slow item filled
    assert results.fill_rate_pct == 50.0


def test_backtest_competing_sniper_determinism(backtest_db: tuple[DatabaseManager, str]):
    """Verify backtest determinism: same seed and inputs produce identical metrics."""
    manager, _ = backtest_db

    for i in range(10):
        manager.log_alert(
            auction_uuid=f"det-alert-{i}",
            internal_id=f"ITEM_{i}",
            buy_price=5_000_000 * (i + 1),
            estimated_resell=8_000_000 * (i + 1),
            projected_profit=2_500_000 * (i + 1),
            margin_pct=50.0,
            timestamp=1000 + i * 500,
        )
        manager.record_sale(
            uuid=f"det-sale-{i}",
            internal_id=f"ITEM_{i}",
            price=8_000_000 * (i + 1),
            buyer_uuid=f"buyer-{i}",
            seller_uuid=f"seller-{i}",
            timestamp=2000 + i * 500,
            time_to_sell_s=5.0,
        )

    engine = BacktestEngine(db=manager)
    res1 = engine.run_backtest(initial_capital=500_000_000, enable_competing_model=True, seed=12345)
    res2 = engine.run_backtest(initial_capital=500_000_000, enable_competing_model=True, seed=12345)

    assert res1.filled_trades == res2.filled_trades
    assert res1.total_simulated_profit == res2.total_simulated_profit
    assert res1.fill_rate_pct == res2.fill_rate_pct
    assert res1.max_drawdown_coins == res2.max_drawdown_coins
    assert res1.sharpe_ratio == res2.sharpe_ratio
    assert res1.final_capital == res2.final_capital


def test_backtest_capital_constraints_and_max_positions(backtest_db: tuple[DatabaseManager, str]):
    """Verify max open positions constraint and reinvestment of capital upon position close."""
    manager, _ = backtest_db

    # 3 simultaneous alerts at t=1000
    for i in range(3):
        manager.log_alert(
            auction_uuid=f"cap-alert-{i}",
            internal_id=f"CAP_ITEM_{i}",
            buy_price=20_000_000,
            estimated_resell=30_000_000,
            projected_profit=9_100_000,
            margin_pct=45.0,
            timestamp=1000,
        )
        manager.record_sale(
            uuid=f"cap-sale-{i}",
            internal_id=f"CAP_ITEM_{i}",
            price=30_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=5000,
            time_to_sell_s=4.0,
        )

    engine = BacktestEngine(db=manager)
    # With max_open_positions=2 and initial_capital=100M: only 2 of the 3 can be opened at t=1000
    results = engine.run_backtest(
        max_open_positions=2,
        initial_capital=100_000_000,
        reaction_delay_s=0.0,
    )

    assert results.total_alerts == 3
    assert results.filled_trades == 2  # 3rd was blocked by position limit


def test_backtest_lane_filtering(backtest_db: tuple[DatabaseManager, str]):
    """Verify --lane filter evaluates only alerts matching the requested lane."""
    manager, _ = backtest_db

    manager.log_alert(
        auction_uuid="alert-floor",
        internal_id="FLOOR_ITEM",
        buy_price=5_000_000,
        estimated_resell=8_000_000,
        projected_profit=2_700_000,
        margin_pct=50.0,
        timestamp=1000,
        lane="floor",
    )
    manager.log_alert(
        auction_uuid="alert-pets",
        internal_id="PET_ITEM",
        buy_price=10_000_000,
        estimated_resell=15_000_000,
        projected_profit=4_500_000,
        margin_pct=45.0,
        timestamp=2000,
        lane="pets",
    )

    engine = BacktestEngine(db=manager)
    results = engine.run_backtest(lane="floor")
    assert results.total_alerts == 1


def test_backtest_drawdown_and_sharpe_ratio(backtest_db: tuple[DatabaseManager, str]):
    """Verify drawdown and Sharpe ratio calculation across profitable and loss-making trades."""
    manager, _ = backtest_db

    # Trade 1: Profit (buy 10M, sell 20M) at t=1000, sells at t=2000
    manager.log_alert(
        auction_uuid="t1-alert",
        internal_id="ITEM_1",
        buy_price=10_000_000,
        estimated_resell=20_000_000,
        projected_profit=9_400_000,
        margin_pct=94.0,
        timestamp=1000,
    )
    manager.record_sale(
        uuid="t1-sale",
        internal_id="ITEM_1",
        price=20_000_000,
        buyer_uuid="b",
        seller_uuid="s",
        timestamp=2000,
        time_to_sell_s=2.0,
    )

    # Trade 2: Loss (buy 30M, sell 25M) at t=3000, sells at t=4000
    manager.log_alert(
        auction_uuid="t2-alert",
        internal_id="ITEM_2",
        buy_price=30_000_000,
        estimated_resell=40_000_000,
        projected_profit=8_800_000,
        margin_pct=25.0,
        timestamp=3000,
    )
    manager.record_sale(
        uuid="t2-sale",
        internal_id="ITEM_2",
        price=25_000_000,
        buyer_uuid="b",
        seller_uuid="s",
        timestamp=4000,
        time_to_sell_s=2.0,
    )

    engine = BacktestEngine(db=manager)
    results = engine.run_backtest(initial_capital=50_000_000, reaction_delay_s=0.0)

    assert results.filled_trades == 2
    assert results.max_drawdown_coins > 0
    assert results.max_drawdown_pct > 0.0
    assert results.sharpe_ratio != 0.0
