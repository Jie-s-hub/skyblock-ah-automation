"""Replay Backtesting Engine for Hypixel SkyBlock AH Arbitrage Listings."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from dataclasses import dataclass
from typing import Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.taxes import (
    calculate_progressive_net_profit,
    net_revenue,
)
from src.db import DatabaseManager, get_db
from src.evaluator import FlipEvaluator


@dataclass
class BacktestResults:
    """Metrics container for historical replay backtest execution."""

    total_alerts: int
    matched_sales: int
    hits: int
    hit_rate_pct: float
    mae: float
    rmse: float
    initial_capital: int
    final_capital: int
    capital_growth_pct: float
    total_simulated_profit: int

    def summary(self) -> str:
        """Format backtest metrics into a clean terminal report."""
        growth_sign = "+" if self.capital_growth_pct >= 0 else ""
        profit_sign = "+" if self.total_simulated_profit >= 0 else ""
        return (
            "\n"
            + "=" * 70
            + "\n"
            + "              HISTORICAL REPLAY BACKTEST REPORT\n"
            + "=" * 70
            + "\n"
            + f"  Total Evaluated Alerts:     {self.total_alerts}\n"
            + f"  Matched Realized Sales:     {self.matched_sales}\n"
            + f"  Successful Arbitrage Hits:  {self.hits}\n"
            + f"  Hit Rate (Realized >= Target): {self.hit_rate_pct:.2f}%\n"
            + "-" * 70
            + "\n"
            + f"  Mean Absolute Error (MAE):  {self.mae:,.2f} coins\n"
            + f"  Root Mean Sq Error (RMSE):  {self.rmse:,.2f} coins\n"
            + "-" * 70
            + "\n"
            + f"  Initial Simulated Capital:  {self.initial_capital:,} coins\n"
            + f"  Final Simulated Capital:    {self.final_capital:,} coins\n"
            + f"  Total Simulated Profit:     {profit_sign}{self.total_simulated_profit:,} coins\n"
            + f"  Capital Growth:             {growth_sign}{self.capital_growth_pct:.2f}%\n"
            + "=" * 70
            + "\n"
        )


class BacktestEngine:
    """Replays logged historical alerts against observed completed sales."""

    def __init__(
        self,
        db: DatabaseManager,
        evaluator: Optional[FlipEvaluator] = None,
    ) -> None:
        self.db = db
        self.evaluator = evaluator

    def run_backtest(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        sample_size: int = 100,
        target_window_hours: float = 72.0,
        initial_capital: int = 100_000_000,
        derpy_override: Optional[bool] = None,
    ) -> BacktestResults:
        """
        Execute historical replay backtest:
        1. Queries alerts within range.
        2. Correlates with subsequent sales from sales_history within target_window_hours.
        3. Computes Hit Rate, MAE, RMSE, and Simulated Capital Growth.
        """
        alerts = self.db.get_alerts(
            start_time=start_time,
            end_time=end_time,
            limit=sample_size,
        )

        if not alerts:
            return BacktestResults(
                total_alerts=0,
                matched_sales=0,
                hits=0,
                hit_rate_pct=0.0,
                mae=0.0,
                rmse=0.0,
                initial_capital=initial_capital,
                final_capital=initial_capital,
                capital_growth_pct=0.0,
                total_simulated_profit=0,
            )

        window_ms = int(target_window_hours * 3600.0 * 1000.0)
        current_capital = initial_capital
        total_profit = 0
        hits = 0
        matched_sales = 0
        absolute_errors: list[float] = []
        squared_errors: list[float] = []

        # Chronological order
        sorted_alerts = sorted(alerts, key=lambda a: int(a["timestamp"]))

        for alert in sorted_alerts:
            internal_id = alert["internal_id"]
            buy_price = int(alert["buy_price"])
            estimated_resell = int(alert["estimated_resell"])
            alert_ts = int(alert["timestamp"])

            # Query completed sales for this item that occurred within window
            sales = self.db.get_sales(
                internal_id=internal_id,
                start_time=alert_ts,
                end_time=alert_ts + window_ms,
                limit=10,
            )

            if not sales:
                continue

            matched_sales += 1
            # Pick the earliest subsequent sale as the realized proxy
            closest_sale = sorted(sales, key=lambda s: int(s["timestamp"]))[0]
            realized_price = int(closest_sale["price"])

            # Evaluate Hit: item realized at or above 98% of target resell
            if realized_price >= int(estimated_resell * 0.98):
                hits += 1

            # Resale prediction accuracy metrics
            diff = float(realized_price - estimated_resell)
            absolute_errors.append(abs(diff))
            squared_errors.append(diff ** 2)

            # Simulated capital execution
            if current_capital >= buy_price:
                # Realize trade with stored or overridden Derpy status
                is_derpy = derpy_override if derpy_override is not None else bool(alert.get("derpy_active", 0))
                net_pnl = net_revenue(realized_price, derpy_active=is_derpy) - buy_price
                current_capital += net_pnl
                total_profit += net_pnl

        n_matched = len(absolute_errors)
        hit_rate = (hits / matched_sales * 100.0) if matched_sales > 0 else 0.0
        mae = (sum(absolute_errors) / n_matched) if n_matched > 0 else 0.0
        rmse = math.sqrt(sum(squared_errors) / n_matched) if n_matched > 0 else 0.0
        growth_pct = ((current_capital - initial_capital) / initial_capital) * 100.0 if initial_capital > 0 else 0.0

        return BacktestResults(
            total_alerts=len(alerts),
            matched_sales=matched_sales,
            hits=hits,
            hit_rate_pct=hit_rate,
            mae=mae,
            rmse=rmse,
            initial_capital=initial_capital,
            final_capital=current_capital,
            capital_growth_pct=growth_pct,
            total_simulated_profit=total_profit,
        )


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for backtester."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.backtest",
        description="Hypixel SkyBlock AH Arbitrage Replay Backtesting Engine",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/market_intelligence.db",
        help="Path to SQLite intelligence database",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=250,
        help="Number of historical alerts to replay (default: 250)",
    )
    parser.add_argument(
        "--window-hours",
        type=float,
        default=72.0,
        help="Lookahead window in hours for matching sales (default: 72.0)",
    )
    parser.add_argument(
        "--capital",
        type=int,
        default=100_000_000,
        help="Initial capital in coins (default: 100,000,000)",
    )
    parser.add_argument(
        "--derpy",
        dest="derpy",
        action="store_true",
        default=None,
        help="Force simulate with Mayor Derpy collection fee active",
    )
    parser.add_argument(
        "--no-derpy",
        dest="derpy",
        action="store_false",
        help="Force simulate without Mayor Derpy active",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI execution entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    db = DatabaseManager(db_path=args.db)
    try:
        engine = BacktestEngine(db=db)
        results = engine.run_backtest(
            sample_size=args.sample_size,
            target_window_hours=args.window_hours,
            initial_capital=args.capital,
            derpy_override=args.derpy,
        )
        print(results.summary())
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
