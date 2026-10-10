#!/usr/bin/env python3
"""
CLI script to generate an outcome calibration report across valuation lanes,
confidence labels, and score deciles.

Usage:
    python scripts/calibration_report.py [--db PATH] [--csv PATH]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import get_settings
from src.constants.taxes import calculate_net_revenue
from src.db import DatabaseManager


def get_decile_bucket(score: float | None) -> str:
    """Bucket a confidence score into a decile interval string."""
    if score is None:
        return "N/A"
    s = float(score)
    if s >= 90.0:
        return "90-100"
    if s >= 80.0:
        return "80-89"
    if s >= 70.0:
        return "70-79"
    if s >= 60.0:
        return "60-69"
    if s >= 50.0:
        return "50-59"
    if s >= 40.0:
        return "40-49"
    if s >= 30.0:
        return "30-39"
    if s >= 20.0:
        return "20-29"
    if s >= 10.0:
        return "10-19"
    return "0-9"


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate outcome calibration metrics for a cluster of records."""
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "sold_fast_rate": 0.0,
            "target_hit_rate": 0.0,
            "mean_pred_margin": 0.0,
            "mean_real_margin": None,
        }

    sold_fast_count = sum(1 for r in records if r.get("status") == "SOLD_FAST")
    target_hit_count = sum(1 for r in records if r.get("target_hit") == 1)

    pred_margins: list[float] = []
    for r in records:
        pm = r.get("predicted_margin")
        if pm is not None:
            pred_margins.append(float(pm))
        else:
            bp = float(r.get("buy_price", 0))
            tp = float(r.get("target_price", 0))
            if bp > 0:
                net_rev = calculate_net_revenue(int(tp))
                pred_margins.append(((net_rev - bp) / bp) * 100.0)

    mean_pred = (sum(pred_margins) / len(pred_margins)) if pred_margins else 0.0

    realized_margins = [float(r["realized_net_margin"]) for r in records if r.get("realized_net_margin") is not None]
    mean_real = (sum(realized_margins) / len(realized_margins)) if realized_margins else None

    return {
        "n": n,
        "sold_fast_rate": round((sold_fast_count / n) * 100.0, 2),
        "target_hit_rate": round((target_hit_count / n) * 100.0, 2),
        "mean_pred_margin": round(mean_pred, 2),
        "mean_real_margin": round(mean_real, 2) if mean_real is not None else None,
    }


def fetch_outcomes_data(db: DatabaseManager) -> list[dict[str, Any]]:
    """Retrieve all alert outcome records joined with alerts metadata."""
    query = """
    SELECT
        o.alert_id,
        o.auction_uuid,
        o.lane,
        o.path,
        o.bucket_key,
        o.buy_price,
        o.target_price,
        o.confidence_score,
        o.label,
        o.alerted_at,
        o.status,
        o.listing_sold_after_s,
        o.target_hit,
        o.realized_net_margin,
        a.margin_pct AS predicted_margin
    FROM alert_outcomes o
    LEFT JOIN alerts a ON o.auction_uuid = a.auction_uuid
    ORDER BY o.evaluated_at DESC
    """
    conn = db._get_connection()
    cur = conn.execute(query)
    return [dict(r) for r in cur.fetchall()]


def generate_report(db: DatabaseManager, csv_path: str | None = None) -> None:
    """Generate and display calibration report, optionally exporting to CSV."""
    records = fetch_outcomes_data(db)

    print("=" * 86)
    print("                    ARBITRAGE OUTCOMES CALIBRATION REPORT                    ")
    print(f" Total Evaluated Alert Outcomes: {len(records):,}")
    print("=" * 86)

    # 1. Group by Lane x Confidence Label
    lane_label_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        lane = str(r.get("lane") or "unknown")
        label = str(r.get("label") or "UNKNOWN")
        lane_label_groups[(lane, label)].append(r)

    print("\n--- Breakdown: Lane x Confidence Label ---")
    header_fmt = "{:<20} {:<10} {:>8} {:>14} {:>14} {:>14} {:>14}"
    row_fmt = "{:<20} {:<10} {:>8} {:>13.1f}% {:>13.1f}% {:>13.2f}% {:>14}"

    print(header_fmt.format("Lane", "Label", "N", "SOLD_FAST %", "Target Hit %", "Mean Pred %", "Mean Real %"))
    print("-" * 105)

    csv_rows: list[dict[str, Any]] = []

    for (lane, label), group in sorted(lane_label_groups.items()):
        metrics = compute_metrics(group)
        real_str = f"{metrics['mean_real_margin']:.2f}%" if metrics["mean_real_margin"] is not None else "N/A"
        print(
            row_fmt.format(
                lane,
                label,
                metrics["n"],
                metrics["sold_fast_rate"],
                metrics["target_hit_rate"],
                metrics["mean_pred_margin"],
                real_str,
            )
        )

        csv_rows.append(
            {
                "dimension": "lane_label",
                "group_key": lane,
                "sub_key": label,
                "n": metrics["n"],
                "sold_fast_rate_pct": metrics["sold_fast_rate"],
                "target_hit_rate_pct": metrics["target_hit_rate"],
                "mean_predicted_margin_pct": metrics["mean_pred_margin"],
                "mean_realized_margin_pct": metrics["mean_real_margin"]
                if metrics["mean_real_margin"] is not None
                else "",
            }
        )

    # 2. Group by Score Decile
    decile_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        decile = get_decile_bucket(r.get("confidence_score"))
        decile_groups[decile].append(r)

    decile_order = ["90-100", "80-89", "70-79", "60-69", "50-59", "40-49", "30-39", "20-29", "10-19", "0-9", "N/A"]

    print("\n--- Breakdown: Confidence Score Decile ---")
    decile_header_fmt = "{:<16} {:>8} {:>14} {:>14} {:>14} {:>14}"
    decile_row_fmt = "{:<16} {:>8} {:>13.1f}% {:>13.1f}% {:>13.2f}% {:>14}"
    print(decile_header_fmt.format("Score Decile", "N", "SOLD_FAST %", "Target Hit %", "Mean Pred %", "Mean Real %"))
    print("-" * 88)

    for decile in decile_order:
        if decile in decile_groups:
            group = decile_groups[decile]
            metrics = compute_metrics(group)
            real_str = f"{metrics['mean_real_margin']:.2f}%" if metrics["mean_real_margin"] is not None else "N/A"
            print(
                decile_row_fmt.format(
                    decile,
                    metrics["n"],
                    metrics["sold_fast_rate"],
                    metrics["target_hit_rate"],
                    metrics["mean_pred_margin"],
                    real_str,
                )
            )

            csv_rows.append(
                {
                    "dimension": "score_decile",
                    "group_key": decile,
                    "sub_key": "",
                    "n": metrics["n"],
                    "sold_fast_rate_pct": metrics["sold_fast_rate"],
                    "target_hit_rate_pct": metrics["target_hit_rate"],
                    "mean_predicted_margin_pct": metrics["mean_pred_margin"],
                    "mean_realized_margin_pct": metrics["mean_real_margin"]
                    if metrics["mean_real_margin"] is not None
                    else "",
                }
            )

    print("=" * 86)

    # Optional CSV Export
    if csv_path:
        out_p = Path(csv_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "dimension",
            "group_key",
            "sub_key",
            "n",
            "sold_fast_rate_pct",
            "target_hit_rate_pct",
            "mean_predicted_margin_pct",
            "mean_realized_margin_pct",
        ]
        with open(out_p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"Calibration metrics successfully exported to CSV: {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate arbitrage alert outcomes calibration report.")
    parser.add_argument("--db", default=None, help="Path to SQLite database file")
    parser.add_argument("--csv", default=None, help="Optional output path to write CSV export")
    args = parser.parse_args()

    settings = get_settings(_env_file=None, DRY_RUN=True)
    db_path = args.db or settings.DB_PATH
    db = DatabaseManager(db_path)

    generate_report(db, csv_path=args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
