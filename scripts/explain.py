#!/usr/bin/env python3
"""
CLI tool to inspect and explain the persisted evaluation trace for an arbitrage alert.
Usage:
    python scripts/explain.py <alert_id|auction_uuid>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings
from src.db import DatabaseManager


def format_coins(coins: float | None) -> str:
    """Format coins with integer commas or N/A."""
    if coins is None:
        return "N/A"
    return f"{int(coins):,} coins"


def explain_trace(alert_record: dict[str, Any]) -> None:
    """Print a clean, structured diagnostic explanation of an alert's valuation trace."""
    auction_uuid = alert_record.get("auction_uuid", "Unknown")
    alert_id = alert_record.get("id", "N/A")
    internal_id = alert_record.get("internal_id", "UNKNOWN")
    val_path = alert_record.get("valuation_path", "UNKNOWN")
    conf_label = alert_record.get("confidence_label", "UNKNOWN")

    trace_raw = alert_record.get("trace_json")
    trace_data: dict[str, Any] = {}
    if trace_raw:
        try:
            trace_data = json.loads(trace_raw) if isinstance(trace_raw, str) else trace_raw
        except (json.JSONDecodeError, TypeError):
            trace_data = {}

    lane = trace_data.get("lane") or alert_record.get("lane") or "unknown"
    path = trace_data.get("path") or val_path
    accepted = trace_data.get("accepted", True)
    reasons = trace_data.get("reasons", [])
    intermediates = trace_data.get("intermediates", {})

    print("=" * 70)
    print(f" Arbitrage Decision Trace: Alert #{alert_id} ({internal_id})")
    print("=" * 70)
    print(f"Auction UUID:      {auction_uuid}")
    print(f"Canonical Lane:    {lane}")
    print(f"Valuation Path:    {path}")
    print(f"Status:            {'ACCEPTED' if accepted else 'REJECTED'}")
    if reasons:
        print(f"Rejection Reasons: {', '.join(reasons)}")
    print("-" * 70)
    print("Key Intermediates:")
    print(f"  Buy Price:             {format_coins(alert_record.get('buy_price'))}")
    print(f"  Estimated Resell:      {format_coins(alert_record.get('estimated_resell'))}")
    if intermediates.get("suggested_list_price"):
        print(
            f"  Suggested List Price:  {format_coins(intermediates['suggested_list_price'])} (fee cliff optimization)"
        )
    print(f"  Projected Net Profit:  {format_coins(alert_record.get('projected_profit'))}")
    print(f"  Net Margin:            {alert_record.get('margin_pct', 0.0):.2f}%")
    print(f"  Confidence:            {conf_label}")
    if "confidence_score" in intermediates:
        print(f"  Confidence Score:      {intermediates['confidence_score']:.1f}/100")
    if "comp_count" in intermediates:
        print(f"  Historical Comps:      {intermediates.get('comp_count')} sample(s)")
    if "comp_median" in intermediates and intermediates.get("comp_median"):
        print(f"  Comps Median:          {format_coins(intermediates.get('comp_median'))}")
    if "capital_efficiency" in intermediates:
        print(f"  Capital Efficiency:    {intermediates.get('capital_efficiency'):.2f}%/hr")
    if "estimated_hold_hours" in intermediates:
        print(f"  Estimated Hold:        {intermediates.get('estimated_hold_hours'):.1f} hours")
    if "craft_cost" in intermediates and intermediates.get("craft_cost"):
        print(
            f"  Craft Ceiling:         {format_coins(intermediates.get('craft_cost'))} (capped={intermediates.get('is_craft_capped')})"
        )
    print(f"  Derpy Active:          {bool(alert_record.get('derpy_active', 0))}")
    print(f"  Fee Model Version:     v{alert_record.get('fee_model_version', 2)}")
    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and explain an alert's valuation trace.")
    parser.add_argument("target", help="Alert ID (integer) or auction UUID")
    parser.add_argument("--db", default=None, help="Path to SQLite database file")
    args = parser.parse_args()

    target_identifier = args.target.strip()
    settings = get_settings(_env_file=None, DRY_RUN=True)
    db_path = args.db or settings.DB_PATH
    db = DatabaseManager(db_path)

    alert = db.get_alert(target_identifier)
    if not alert:
        print(f"Error: Alert '{target_identifier}' not found in database ({db_path}).", file=sys.stderr)
        return 1

    explain_trace(alert)
    return 0


if __name__ == "__main__":
    sys.exit(main())
