"""Administrative and manual trade reconciliation CLI for the SkyBlock Arbitrage Ledger."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.taxes import (
    calculate_progressive_ah_tax,
    calculate_progressive_net_profit,
    calculate_progressive_net_revenue,
)
from src.db import DatabaseManager, get_db


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string (e.g. '2h 15m' or '45m 12s')."""
    if seconds < 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def parse_timestamp_arg(val: Optional[str]) -> int:
    """Parse integer epoch ms, epoch seconds, or ISO 8601 string into epoch ms."""
    if not val:
        return int(time.time() * 1000)
    try:
        f_val = float(val)
        # If less than 10^11, assume seconds and convert to ms
        if f_val < 1e11:
            return int(f_val * 1000)
        return int(f_val)
    except ValueError:
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return int(time.time() * 1000)


def handle_mark_bought(args: argparse.Namespace, db: DatabaseManager) -> int:
    """Execute mark-bought command."""
    uuid = args.auction_uuid.strip()
    buy_price = args.price

    # Look up alert if present to populate metadata
    alert = db.get_alert(uuid)
    internal_id = "UNKNOWN"
    features_json = None

    if alert:
        internal_id = alert.get("internal_id", "UNKNOWN")
        features_json = alert.get("features_json")
        if buy_price is None:
            buy_price = int(alert["buy_price"])
    elif buy_price is None:
        print(f"❌ Error: Auction '{uuid}' was not found in alerts. You must specify --price <coins>.", file=sys.stderr)
        return 1

    success = db.record_purchase(
        auction_uuid=uuid,
        internal_id=internal_id,
        buy_price=int(buy_price),
        buy_timestamp=int(time.time() * 1000),
        notes=args.notes,
        features_json=features_json,
        status="HELD",
    )

    if success:
        print(f"✅ Marked auction '{uuid}' ({internal_id}) as BOUGHT for {int(buy_price):,} coins (Status: HELD).")
        return 0
    else:
        print(f"❌ Failed to record purchase for '{uuid}'.", file=sys.stderr)
        return 1


def handle_mark_sold(args: argparse.Namespace, db: DatabaseManager) -> int:
    """Execute mark-sold command."""
    uuid = args.auction_uuid.strip()
    sell_price = int(args.price)
    sell_ts = parse_timestamp_arg(args.timestamp)

    purchase = db.get_purchase(uuid)
    if not purchase:
        print(f"❌ Error: Auction '{uuid}' not found in purchases ledger.", file=sys.stderr)
        return 1

    buy_price = int(purchase["buy_price"])
    buy_ts = int(purchase.get("buy_timestamp", sell_ts))
    internal_id = purchase.get("internal_id", "UNKNOWN")

    tax = calculate_progressive_ah_tax(sell_price)
    net_pnl = calculate_progressive_net_profit(sell_price, buy_price)
    roi_pct = (net_pnl / buy_price) * 100.0 if buy_price > 0 else 0.0
    hold_sec = max(0.0, (sell_ts - buy_ts) / 1000.0)
    effective_tax_pct = (tax / sell_price) * 100.0 if sell_price > 0 else 0.0

    success = db.update_purchase_sale(
        auction_uuid=uuid,
        realized_sell_price=sell_price,
        realized_sell_timestamp=sell_ts,
        net_realized_pnl=net_pnl,
        status="SOLD",
        notes=args.notes,
    )

    if success:
        pnl_sign = "+" if net_pnl >= 0 else ""
        print("\n=======================================================")
        print(f"  ✅ [TRADE RECORDED] {internal_id} ({uuid})")
        print("=======================================================")
        print(f"  Buy Price:          {buy_price:,} coins")
        print(f"  Realized Sale Price: {sell_price:,} coins")
        print(f"  Progressive AH Tax: {tax:,} coins ({effective_tax_pct:.2f}%)")
        print(f"  Net Realized PnL:   {pnl_sign}{net_pnl:,} coins ({roi_pct:.2f}%)")
        print(f"  Hold Duration:      {format_duration(hold_sec)}")
        print("=======================================================\n")
        return 0
    else:
        print(f"❌ Failed to update sale for '{uuid}'.", file=sys.stderr)
        return 1


def handle_report(args: argparse.Namespace, db: DatabaseManager) -> int:
    """Generate tabular performance summary of realized trades."""
    purchases = db.get_purchases(status="SOLD", limit=args.limit)
    if not purchases:
        print("\n[Ledger Report] No completed (SOLD) trades recorded yet.\n")
        return 0

    total_realized_pnl = 0
    total_capital_deployed = 0
    total_sales_revenue = 0
    win_count = 0
    total_hold_time_sec = 0.0

    # Group by category / internal_id prefix
    category_stats: dict[str, dict[str, Any]] = {}

    for p in purchases:
        buy_p = int(p.get("buy_price", 0))
        sell_p = int(p.get("realized_sell_price", 0))
        pnl = int(p.get("net_realized_pnl", 0))
        buy_ts = int(p.get("buy_timestamp", 0))
        sell_ts = int(p.get("realized_sell_timestamp", buy_ts))
        hold_sec = max(0.0, (sell_ts - buy_ts) / 1000.0)

        total_capital_deployed += buy_p
        total_sales_revenue += sell_p
        total_realized_pnl += pnl
        total_hold_time_sec += hold_sec
        if pnl > 0:
            win_count += 1

        cat = p.get("internal_id", "UNKNOWN").split("_")[0]
        if cat not in category_stats:
            category_stats[cat] = {
                "trades": 0,
                "wins": 0,
                "pnl": 0,
                "capital": 0,
                "hold_sec": 0.0,
            }
        category_stats[cat]["trades"] += 1
        if pnl > 0:
            category_stats[cat]["wins"] += 1
        category_stats[cat]["pnl"] += pnl
        category_stats[cat]["capital"] += buy_p
        category_stats[cat]["hold_sec"] += hold_sec

    n_trades = len(purchases)
    overall_win_rate = (win_count / n_trades) * 100.0 if n_trades > 0 else 0.0
    avg_hold_sec = total_hold_time_sec / n_trades if n_trades > 0 else 0.0
    avg_profit_trade = total_realized_pnl // n_trades if n_trades > 0 else 0

    print("\n" + "=" * 84)
    print("                      HYPIXEL AH ARBITRAGE PERFORMANCE LEDGER")
    print("=" * 84)
    print(
        f"{'Category':<16} | {'Trades':<6} | {'Win %':<7} | {'Total Realized PnL':<20} | {'Avg PnL/Trade':<14} | {'Avg Hold':<10}"
    )
    print("-" * 84)

    for cat, stats in sorted(category_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        c_trades = stats["trades"]
        c_win_rate = (stats["wins"] / c_trades) * 100.0 if c_trades > 0 else 0.0
        c_pnl = stats["pnl"]
        c_avg_pnl = c_pnl // c_trades if c_trades > 0 else 0
        c_avg_hold = format_duration(stats["hold_sec"] / c_trades) if c_trades > 0 else "0s"
        pnl_str = f"+{c_pnl:,}" if c_pnl >= 0 else f"{c_pnl:,}"
        avg_str = f"+{c_avg_pnl:,}" if c_avg_pnl >= 0 else f"{c_avg_pnl:,}"
        print(f"{cat:<16} | {c_trades:<6} | {c_win_rate:>5.1f}% | {pnl_str:>20} | {avg_str:>14} | {c_avg_hold:<10}")

    print("=" * 84)
    print(f"Total Trades:           {n_trades}")
    print(f"Win Rate:               {overall_win_rate:.1f}% ({win_count}/{n_trades} profitable)")
    print(f"Total Capital Deployed: {total_capital_deployed:,} coins")
    print(f"Total Net Realized PnL: {'+' if total_realized_pnl >= 0 else ''}{total_realized_pnl:,} coins")
    print(f"Average Profit / Trade: {'+' if avg_profit_trade >= 0 else ''}{avg_profit_trade:,} coins")
    print(f"Average Hold Duration:  {format_duration(avg_hold_sec)}")
    print("=" * 84 + "\n")
    return 0


def handle_player_stats(args: argparse.Namespace, db: DatabaseManager) -> int:
    """Display real-time portfolio metrics for tracked player."""
    from src.config import Settings
    from src.player_tracker import PlayerTradeTracker

    config = Settings(DRY_RUN=True, TRACKED_PLAYER_IGN=args.player or "ongvvkk")
    tracker = PlayerTradeTracker(db=db, config=config)
    metrics = tracker.get_portfolio_metrics()

    daily_sign = "+" if metrics["realized_pnl_daily"] >= 0 else ""
    weekly_sign = "+" if metrics["realized_pnl_weekly"] >= 0 else ""
    all_time_sign = "+" if metrics["realized_pnl_all_time"] >= 0 else ""
    avg_sign = "+" if metrics["avg_profit_per_trade"] >= 0 else ""

    print("\n" + "=" * 60)
    print(f"       PORTFOLIO & TRADE PERFORMANCE: {metrics['player_ign'].upper()}")
    print("=" * 60)
    print(f"  Tracked Player:         {metrics['player_ign']} (UUID: {metrics['player_uuid']})")
    print(f"  Total Trades Closed:    {metrics['total_trades']}")
    print(f"  Win Rate:               {metrics['win_rate_pct']:.1f}%")
    print("-" * 60)
    print(f"  Realized PnL (Daily):   {daily_sign}{metrics['realized_pnl_daily']:,} coins")
    print(f"  Realized PnL (Weekly):  {weekly_sign}{metrics['realized_pnl_weekly']:,} coins")
    print(f"  Realized PnL (All-Time):{all_time_sign}{metrics['realized_pnl_all_time']:,} coins")
    print(f"  Avg Profit / Trade:     {avg_sign}{metrics['avg_profit_per_trade']:,} coins")
    print(f"  Avg Capital Lock Time:  {format_duration(metrics['avg_hold_duration_sec'])}")
    print("-" * 60)
    print(f"  Open Positions (HELD):  {metrics['held_items_count']}")
    print(f"  Capital Deployed (HELD):{metrics['unrealized_capital_deployed']:,} coins")
    print("=" * 60 + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.ledger_cli",
        description="Hypixel SkyBlock AH Arbitrage Manual Reconciliation & Trade Ledger",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/market_intelligence.db",
        help="Path to SQLite intelligence database (default: data/market_intelligence.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: mark-bought
    p_bought = subparsers.add_parser("mark-bought", help="Record an auction alert as bought/held")
    p_bought.add_argument("auction_uuid", type=str, help="UUID of the auction listing")
    p_bought.add_argument("--price", type=int, default=None, help="Purchase price in coins (default: from alert)")
    p_bought.add_argument("--notes", type=str, default=None, help="Optional trade notes")

    # Subcommand: mark-sold
    p_sold = subparsers.add_parser("mark-sold", help="Record a completed flip sale and compute realized PnL")
    p_sold.add_argument("auction_uuid", type=str, help="UUID of the auction listing")
    p_sold.add_argument("--price", type=int, required=True, help="Sale price in coins")
    p_sold.add_argument("--timestamp", type=str, default=None, help="Sale timestamp (ISO 8601 or epoch ms)")
    p_sold.add_argument("--notes", type=str, default=None, help="Optional sale notes")

    # Subcommand: report
    p_report = subparsers.add_parser("report", help="Display performance summary of realized trades")
    p_report.add_argument("--limit", type=int, default=500, help="Maximum completed trades to analyze")

    # Subcommand: player-stats
    p_pstats = subparsers.add_parser("player-stats", help="Display real-time player portfolio metrics")
    p_pstats.add_argument("--player", type=str, default="ongvvkk", help="Tracked player username (default: ongvvkk)")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    db = DatabaseManager(db_path=args.db)
    try:
        if args.command == "mark-bought":
            return handle_mark_bought(args, db)
        elif args.command == "mark-sold":
            return handle_mark_sold(args, db)
        elif args.command == "report":
            return handle_report(args, db)
        elif args.command == "player-stats":
            return handle_player_stats(args, db)
        else:
            parser.print_help()
            return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
