"""Replay Backtesting Engine for Hypixel SkyBlock AH Arbitrage Listings."""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.taxes import net_revenue  # noqa: E402
from src.db import DatabaseManager  # noqa: E402
from src.evaluator import FlipEvaluator  # noqa: E402


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
    filled_trades: int = 0
    fill_rate_pct: float = 0.0
    simulated_roi_pct: float = 0.0
    max_drawdown_coins: int = 0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0

    def summary(self) -> str:
        """Format backtest metrics into a clean terminal report."""
        growth_sign = "+" if self.capital_growth_pct >= 0 else ""
        profit_sign = "+" if self.total_simulated_profit >= 0 else ""
        roi_sign = "+" if self.simulated_roi_pct >= 0 else ""
        return (
            "\n"
            + "=" * 70
            + "\n"
            + "              HISTORICAL REPLAY BACKTEST REPORT\n"
            + "=" * 70
            + "\n"
            + f"  Total Evaluated Alerts:     {self.total_alerts}\n"
            + f"  Matched Realized Sales:     {self.matched_sales}\n"
            + f"  Filled Simulated Trades:    {self.filled_trades}\n"
            + f"  Simulated Fill Rate:        {self.fill_rate_pct:.2f}%\n"
            + f"  Arbitrage Hit Rate:         {self.hit_rate_pct:.2f}%\n"
            + "-" * 70
            + "\n"
            + f"  Initial Simulated Capital:  {self.initial_capital:,} coins\n"
            + f"  Final Simulated Capital:    {self.final_capital:,} coins\n"
            + f"  Total Simulated Profit:     {profit_sign}{self.total_simulated_profit:,} coins\n"
            + f"  Simulated ROI:              {roi_sign}{self.simulated_roi_pct:.2f}%\n"
            + f"  Capital Growth:             {growth_sign}{self.capital_growth_pct:.2f}%\n"
            + f"  Max Drawdown:               -{self.max_drawdown_coins:,} coins (-{self.max_drawdown_pct:.2f}%)\n"
            + f"  Sharpe-like Ratio:          {self.sharpe_ratio:.2f}\n"
            + "-" * 70
            + "\n"
            + f"  Mean Absolute Error (MAE):  {self.mae:,.2f} coins\n"
            + f"  Root Mean Sq Error (RMSE):  {self.rmse:,.2f} coins\n"
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
        lane: Optional[str] = None,
        reaction_delay_s: float = 0.0,
        max_open_positions: int = 10,
        enable_competing_model: bool = False,
        seed: int = 42,
    ) -> BacktestResults:
        """
        Execute historical replay backtest:
        1. Queries alerts within range (and optional lane filter).
        2. Correlates with subsequent sales from sales_history within target_window_hours.
        3. Simulates realistic execution:
           - Fill model: time_to_sell_s > reaction_delay_s.
           - Competing sniper decay model based on discount depth.
           - Capital constraints with max_open_positions and reinvestment upon sale.
        4. Computes Hit Rate, MAE, RMSE, Drawdown, and Sharpe-like ratio.
        """
        rng = random.Random(seed)

        alerts = self.db.get_alerts(
            lane=lane,
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
                filled_trades=0,
                fill_rate_pct=0.0,
                simulated_roi_pct=0.0,
                max_drawdown_coins=0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
            )

        window_ms = int(target_window_hours * 3600.0 * 1000.0)
        available_cash = initial_capital
        peak_equity = float(initial_capital)
        max_drawdown_coins = 0
        max_drawdown_pct = 0.0

        hits = 0
        matched_sales = 0
        filled_trades = 0
        absolute_errors: list[float] = []
        squared_errors: list[float] = []
        trade_profits: list[int] = []

        # Chronological order of alerts
        sorted_alerts = sorted(alerts, key=lambda a: int(a["timestamp"]))

        # Track active open positions: [{buy_price, sale_price, sale_ts, is_derpy}]
        open_positions: list[dict[str, Any]] = []

        def record_equity_sample(equity: float) -> None:
            nonlocal peak_equity, max_drawdown_coins, max_drawdown_pct
            if equity > peak_equity:
                peak_equity = equity
            dd = int(peak_equity - equity)
            if dd > max_drawdown_coins:
                max_drawdown_coins = dd
            pct = (dd / peak_equity * 100.0) if peak_equity > 0 else 0.0
            if pct > max_drawdown_pct:
                max_drawdown_pct = pct

        for alert in sorted_alerts:
            internal_id = alert["internal_id"]
            buy_price = int(alert["buy_price"])
            estimated_resell = int(alert["estimated_resell"])
            alert_ts = int(alert["timestamp"])

            # 1. Close/reinvest open positions whose sales occurred prior to this alert
            remaining_positions: list[dict[str, Any]] = []
            for pos in sorted(open_positions, key=lambda p: p["sale_ts"]):
                if pos["sale_ts"] <= alert_ts:
                    # Realize sale proceeds and reinvest
                    proceeds = net_revenue(pos["sale_price"], derpy_active=pos["is_derpy"])
                    available_cash += proceeds
                    pnl = proceeds - pos["buy_price"]
                    trade_profits.append(pnl)
                    current_equity = available_cash + sum(p["buy_price"] for p in remaining_positions)
                    record_equity_sample(current_equity)
                else:
                    remaining_positions.append(pos)
            open_positions = remaining_positions

            # Update equity and drawdown metrics
            current_equity = available_cash + sum(p["buy_price"] for p in open_positions)
            record_equity_sample(current_equity)

            # 2. Query completed sales within the forward window
            sales = self.db.get_sales(
                internal_id=internal_id,
                start_time=alert_ts,
                end_time=alert_ts + window_ms,
                limit=10,
            )

            if not sales:
                continue

            matched_sales += 1
            # Earliest subsequent sale as realized proxy
            closest_sale = sorted(sales, key=lambda s: int(s["timestamp"]))[0]
            realized_price = int(closest_sale["price"])
            sale_ts = int(closest_sale["timestamp"])

            # Evaluate Hit: realized at or above 98% of target resell
            if realized_price >= int(estimated_resell * 0.98):
                hits += 1

            # Prediction accuracy metrics
            diff = float(realized_price - estimated_resell)
            absolute_errors.append(abs(diff))
            squared_errors.append(diff**2)

            # 3. Fill Model: check reaction delay
            # If time_to_sell_s is present, candidate fills only if time_to_sell_s > reaction_delay_s
            tts_s: Optional[float] = closest_sale.get("time_to_sell_s")
            if tts_s is None and alert.get("listing_start"):
                tts_s = max(0.0, (sale_ts - int(alert["listing_start"])) / 1000.0)

            if reaction_delay_s > 0 and tts_s is not None and tts_s <= reaction_delay_s:
                # Missed: sniped before reaction delay
                continue

            # 4. Competing-sniper model: probability of fill decays with discount depth
            if enable_competing_model:
                discount_depth = max(0.0, float(estimated_resell - buy_price) / max(1.0, float(estimated_resell)))
                fill_prob = math.exp(-2.0 * discount_depth)
                fill_prob = max(0.05, min(1.0, fill_prob))
                if rng.random() > fill_prob:
                    # Missed: competing sniper captured the flip
                    continue

            # 5. Capital Constraint: position limits and available coins
            if len(open_positions) >= max_open_positions:
                continue

            if available_cash >= buy_price:
                available_cash -= buy_price
                is_derpy = derpy_override if derpy_override is not None else bool(alert.get("derpy_active", 0))
                open_positions.append(
                    {
                        "buy_price": buy_price,
                        "sale_price": realized_price,
                        "sale_ts": sale_ts,
                        "is_derpy": is_derpy,
                    }
                )
                filled_trades += 1
                current_equity = available_cash + sum(p["buy_price"] for p in open_positions)
                record_equity_sample(current_equity)

        # Liquidate remaining open positions at simulation end in order of sale_ts
        sorted_remaining = sorted(open_positions, key=lambda p: p["sale_ts"])
        for idx, pos in enumerate(sorted_remaining):
            proceeds = net_revenue(pos["sale_price"], derpy_active=pos["is_derpy"])
            available_cash += proceeds
            pnl = proceeds - pos["buy_price"]
            trade_profits.append(pnl)
            unclosed_value = sum(p["buy_price"] for p in sorted_remaining[idx + 1 :])
            record_equity_sample(available_cash + unclosed_value)

        final_capital = available_cash
        total_simulated_profit = sum(trade_profits)

        n_matched = len(absolute_errors)
        hit_rate = (hits / matched_sales * 100.0) if matched_sales > 0 else 0.0
        fill_rate = (filled_trades / len(alerts) * 100.0) if alerts else 0.0
        mae = (sum(absolute_errors) / n_matched) if n_matched > 0 else 0.0
        rmse = math.sqrt(sum(squared_errors) / n_matched) if n_matched > 0 else 0.0
        growth_pct = ((final_capital - initial_capital) / initial_capital) * 100.0 if initial_capital > 0 else 0.0
        roi_pct = growth_pct

        # Sharpe-like ratio: mean profit per filled trade / stddev of profit
        sharpe_ratio = 0.0
        if trade_profits:
            mean_p = sum(trade_profits) / len(trade_profits)
            var_p = sum((p - mean_p) ** 2 for p in trade_profits) / len(trade_profits)
            std_p = math.sqrt(var_p)
            sharpe_ratio = (mean_p / std_p) if std_p > 0 else 0.0

        return BacktestResults(
            total_alerts=len(alerts),
            matched_sales=matched_sales,
            hits=hits,
            hit_rate_pct=hit_rate,
            mae=mae,
            rmse=rmse,
            initial_capital=initial_capital,
            final_capital=final_capital,
            capital_growth_pct=growth_pct,
            total_simulated_profit=total_simulated_profit,
            filled_trades=filled_trades,
            fill_rate_pct=fill_rate,
            simulated_roi_pct=roi_pct,
            max_drawdown_coins=max_drawdown_coins,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
        )


def parse_date_arg(val: Optional[str]) -> Optional[int]:
    """Parse user date input into epoch milliseconds (accepts integer ms or YYYY-MM-DD)."""
    if not val:
        return None
    val = val.strip()
    try:
        return int(val)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(val, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date format '{val}': use YYYY-MM-DD or ms timestamp") from exc


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
        "--lane",
        type=str,
        default=None,
        help="Filter by canonical lane (floor, upgraded_networth, sold_comps, pets, god_roll)",
    )
    parser.add_argument(
        "--sample-size",
        "--limit",
        dest="sample_size",
        type=int,
        default=250,
        help="Number of historical alerts to replay (default: 250)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Replay start boundary (YYYY-MM-DD or ms epoch)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Replay end boundary (YYYY-MM-DD or ms epoch)",
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
        "--reaction-delay",
        type=float,
        default=1.5,
        help="Reaction delay threshold in seconds (default: 1.5s)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=10,
        help="Maximum concurrent open positions allowed (default: 10)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for deterministic competing sniper simulation (default: 42)",
    )
    parser.add_argument(
        "--competing-snipers",
        dest="competing_snipers",
        action="store_true",
        default=True,
        help="Enable discount-depth competing sniper fill decay model (default: True)",
    )
    parser.add_argument(
        "--no-competing-snipers",
        dest="competing_snipers",
        action="store_false",
        help="Disable competing sniper fill decay model",
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

    start_ts = parse_date_arg(args.start_date)
    end_ts = parse_date_arg(args.end_date)

    db = DatabaseManager(db_path=args.db)
    try:
        engine = BacktestEngine(db=db)
        results = engine.run_backtest(
            start_time=start_ts,
            end_time=end_ts,
            sample_size=args.sample_size,
            target_window_hours=args.window_hours,
            initial_capital=args.capital,
            derpy_override=args.derpy,
            lane=args.lane,
            reaction_delay_s=args.reaction_delay,
            max_open_positions=args.max_positions,
            enable_competing_model=args.competing_snipers,
            seed=args.seed,
        )
        print(results.summary())
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
