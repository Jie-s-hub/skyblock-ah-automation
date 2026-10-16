#!/usr/bin/env python3
"""
Standalone CLI command to dispatch the profile AH tracker and portfolio report
to the configured Discord webhook.

Usage:
    python scripts/post_profile_tracker.py
    python scripts/post_profile_tracker.py --player ongvvkk
    python scripts/post_profile_tracker.py --dry-run
    python scripts/post_profile_tracker.py --webhook-url https://discord.com/api/webhooks/...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

# Ensure Windows consoles don't crash on Minecraft symbols or emojis
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings
from src.db import DatabaseManager
from src.notifier import DiscordNotifier
from src.player_tracker import PlayerTradeTracker


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dispatch Hypixel SkyBlock Profile AH Tracker report to Discord webhook",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--player",
        type=str,
        default=None,
        help="Target player username (default: TRACKED_PLAYER_IGN from settings or 'ongvvkk')",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="data/market_intelligence.db",
        help="Path to SQLite intelligence database",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Discord Webhook URL (default: DISCORD_WEBHOOK_URL from settings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Print the formatted embed payload to console without dispatching to Discord",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum open positions and recent flips to include in embed",
    )
    return parser.parse_args(argv)


async def dispatch_profile_report(args: argparse.Namespace) -> int:
    overrides = {}
    if args.dry_run is not None:
        overrides["DRY_RUN"] = args.dry_run
    if args.webhook_url is not None:
        overrides["DISCORD_WEBHOOK_URL"] = args.webhook_url
    if args.player is not None:
        overrides["TRACKED_PLAYER_IGN"] = args.player

    settings = get_settings(**overrides)
    db = DatabaseManager(db_path=args.db)

    try:
        async with PlayerTradeTracker(db=db, config=settings) as tracker:
            await tracker.resolve_identity()

            metrics = tracker.get_portfolio_metrics()
            open_positions = tracker.get_open_positions(limit=args.limit)
            recent_trades = tracker.get_recent_trades(limit=args.limit)

            player_ign = metrics["player_ign"]
            print(f"[*] Aggregating AH Portfolio metrics for '{player_ign}' (UUID: {metrics['player_uuid']})...")
            print(f"[*] Found {metrics['total_trades']} closed trades, {metrics['held_items_count']} active open positions.")

            async with DiscordNotifier(config=settings) as notifier:
                success = await notifier.send_profile_tracker_report(
                    metrics=metrics,
                    open_positions=open_positions,
                    recent_trades=recent_trades,
                )

            if success:
                print(f"[+] Successfully dispatched profile AH tracker report for '{player_ign}' to Discord!")
                return 0
            else:
                print(f"[-] Failed to dispatch profile AH tracker report for '{player_ign}'. Check Discord webhook configuration.", file=sys.stderr)
                return 1
    finally:
        db.close()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return asyncio.run(dispatch_profile_report(args))


if __name__ == "__main__":
    sys.exit(main())
