"""
Replay Backtest Script: Pet Sales Before vs After Pet Bucket Engine.
Evaluates historical pet scenarios through:
1. BEFORE: Legacy evaluator without merged-state floor, bucket isolation, or sold comps gate.
2. AFTER: Pet Bucket Valuation Engine with merged-state floor, discrete buckets, sold comps P75 ceiling, and anti-domination guards.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import time
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings
from src.constants.pet_levels import get_pet_bucket_key
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator


def build_scenario_dataset() -> list[dict[str, Any]]:
    """
    Returns a representative suite of pet market scenarios encountered in production:
    - Supply dump / batch floor lag (the Goblin symptom)
    - Level bracket mismatch (L1 pet vs L85 listing)
    - Candy pet bait (10/10 candy vs clean)
    - Tier boost held item isolation
    - Valid true positive snipes
    - Ghost / stale reference listings
    - Seller-dominated cartel floors
    - Thin comps / illiquid pets
    """
    now = time.time() * 1000.0
    stale_start = now - (18.0 * 3600.0 * 1000.0)  # 18 hours old

    scenarios = [
        # Scenario 1: Goblin Symptom (4x L1 at 4.99M, 1 at 9.2M, real market = 5M)
        {
            "name": "Goblin Symptom (Supply Dump at 4.99M)",
            "candidate": {
                "auction": {"uuid": "goblin-1", "starting_bid": 4_999_999, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_GOBLIN_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Goblin",
                    "is_pet": True,
                    "pet_type": "GOBLIN",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "goblin-1", "starting_bid": 4_999_999, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-2", "starting_bid": 4_999_999, "auctioneer": "sellerB", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-3", "starting_bid": 4_999_999, "auctioneer": "sellerC", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-4", "starting_bid": 4_999_999, "auctioneer": "sellerD", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-5", "starting_bid": 9_200_000, "auctioneer": "sellerE", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
            ],
            "comps": [5_000_000, 5_000_000, 5_100_000, 5_050_000, 4_999_999, 5_150_000],
            "realized_resale": 5_000_000,
        },
        # Scenario 2: Level Mismatch (L1 Wither Skeleton at 4.5M vs L85 at 20M)
        {
            "name": "Level Mismatch (L1 vs L85 listing)",
            "candidate": {
                "auction": {"uuid": "ws-l1", "starting_bid": 4_500_000, "auctioneer": "seller1", "start": now},
                "decoded": {
                    "internal_id": "PET_WITHER_SKELETON_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Wither Skeleton",
                    "is_pet": True,
                    "pet_type": "WITHER_SKELETON",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "ws-l1", "starting_bid": 4_500_000, "auctioneer": "seller1", "start": now}, {"internal_id": "PET_WITHER_SKELETON_LEGENDARY", "is_pet": True, "pet_type": "WITHER_SKELETON", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "ws-l85", "starting_bid": 20_000_000, "auctioneer": "seller2", "start": now}, {"internal_id": "PET_WITHER_SKELETON_LEGENDARY", "is_pet": True, "pet_type": "WITHER_SKELETON", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 85, "pet_exp": 12_000_000.0}),
            ],
            "comps": [4_600_000, 4_500_000, 4_700_000, 4_650_000, 4_550_000, 4_600_000],
            "realized_resale": 4_600_000,
        },
        # Scenario 3: True Positive Snipe (L100 Baby Yeti at 22M, ref 34M, comps 34M)
        {
            "name": "True Positive Snipe (L100 Baby Yeti)",
            "candidate": {
                "auction": {"uuid": "yeti-l100", "starting_bid": 22_000_000, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_BABY_YETI_LEGENDARY",
                    "item_name": "[Lvl 100] Legendary Baby Yeti",
                    "is_pet": True,
                    "pet_type": "BABY_YETI",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 100,
                    "pet_exp": 25_353_230.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "yeti-l100", "starting_bid": 22_000_000, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_BABY_YETI_LEGENDARY", "is_pet": True, "pet_type": "BABY_YETI", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 100, "pet_exp": 25_353_230.0}),
                ({"uuid": "yeti-ref-1", "starting_bid": 34_000_000, "auctioneer": "sellerB", "start": now}, {"internal_id": "PET_BABY_YETI_LEGENDARY", "is_pet": True, "pet_type": "BABY_YETI", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 100, "pet_exp": 25_353_230.0}),
                ({"uuid": "yeti-ref-2", "starting_bid": 35_000_000, "auctioneer": "sellerC", "start": now}, {"internal_id": "PET_BABY_YETI_LEGENDARY", "is_pet": True, "pet_type": "BABY_YETI", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 100, "pet_exp": 25_353_230.0}),
            ],
            "comps": [33_500_000, 34_000_000, 34_200_000, 34_000_000, 34_500_000, 34_100_000],
            "realized_resale": 34_000_000,
        },
        # Scenario 4: Candy Bait (10/10 Candy at 20M vs Clean at 25M)
        {
            "name": "Candy Bait (10/10 Candy Pet)",
            "candidate": {
                "auction": {"uuid": "candy-pet", "starting_bid": 20_000_000, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_BLACK_CAT_LEGENDARY",
                    "item_name": "[Lvl 100] Legendary Black Cat",
                    "is_pet": True,
                    "pet_type": "BLACK_CAT",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 100,
                    "pet_exp": 25_353_230.0,
                    "pet_candy_used": 10,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "candy-pet", "starting_bid": 20_000_000, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_BLACK_CAT_LEGENDARY", "is_pet": True, "pet_type": "BLACK_CAT", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 100, "pet_exp": 25_353_230.0, "pet_candy_used": 10}),
                ({"uuid": "clean-pet", "starting_bid": 25_000_000, "auctioneer": "sellerB", "start": now}, {"internal_id": "PET_BLACK_CAT_LEGENDARY", "is_pet": True, "pet_type": "BLACK_CAT", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 100, "pet_exp": 25_353_230.0, "pet_candy_used": 0}),
            ],
            # No candied comps exist -> BUCKET_NO_MATCH
            "comps": [],
            "realized_resale": 20_200_000,
        },
        # Scenario 5: Stale Ghost Reference (18h old listing at 20M, real comps 8M)
        {
            "name": "Stale Ghost Reference (18h old benchmark)",
            "candidate": {
                "auction": {"uuid": "ghost-cand", "starting_bid": 5_000_000, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_SHEEP_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Sheep",
                    "is_pet": True,
                    "pet_type": "SHEEP",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "ghost-cand", "starting_bid": 5_000_000, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_SHEEP_LEGENDARY", "is_pet": True, "pet_type": "SHEEP", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "ghost-ref", "starting_bid": 20_000_000, "auctioneer": "ghostSeller", "start": stale_start}, {"internal_id": "PET_SHEEP_LEGENDARY", "is_pet": True, "pet_type": "SHEEP", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
            ],
            "comps": [8_000_000, 8_100_000, 8_050_000, 8_200_000, 7_950_000, 8_150_000],
            "realized_resale": 8_100_000,
        },
        # Scenario 6: Cartel Seller Domination (3 of 3 floor listings owned by one seller)
        {
            "name": "Seller Dominated Floor (Cartel 3/3)",
            "candidate": {
                "auction": {"uuid": "dom-cand", "starting_bid": 5_000_000, "auctioneer": "snipeGuy", "start": now},
                "decoded": {
                    "internal_id": "PET_SILVERFISH_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Silverfish",
                    "is_pet": True,
                    "pet_type": "SILVERFISH",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "dom-cand", "starting_bid": 5_000_000, "auctioneer": "snipeGuy", "start": now}, {"internal_id": "PET_SILVERFISH_LEGENDARY", "is_pet": True, "pet_type": "SILVERFISH", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "dom-1", "starting_bid": 15_000_000, "auctioneer": "cartel", "start": now}, {"internal_id": "PET_SILVERFISH_LEGENDARY", "is_pet": True, "pet_type": "SILVERFISH", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "dom-2", "starting_bid": 15_500_000, "auctioneer": "cartel", "start": now}, {"internal_id": "PET_SILVERFISH_LEGENDARY", "is_pet": True, "pet_type": "SILVERFISH", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "dom-3", "starting_bid": 16_000_000, "auctioneer": "cartel", "start": now}, {"internal_id": "PET_SILVERFISH_LEGENDARY", "is_pet": True, "pet_type": "SILVERFISH", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
            ],
            "comps": [15_000_000, 15_200_000, 15_100_000, 15_000_000, 15_300_000, 15_100_000],
            "realized_resale": 5_200_000,  # Real market was 5.2M, 15M was artificial manipulation
        },
        # Scenario 7: Thin Comps Pet (Only 2 comps in 48h)
        {
            "name": "Thin Comps Pet (Only 2 sales in 48h)",
            "candidate": {
                "auction": {"uuid": "thin-cand", "starting_bid": 10_000_000, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_GIRAFFE_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Giraffe",
                    "is_pet": True,
                    "pet_type": "GIRAFFE",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "thin-cand", "starting_bid": 10_000_000, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_GIRAFFE_LEGENDARY", "is_pet": True, "pet_type": "GIRAFFE", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "thin-ref", "starting_bid": 18_000_000, "auctioneer": "sellerB", "start": now}, {"internal_id": "PET_GIRAFFE_LEGENDARY", "is_pet": True, "pet_type": "GIRAFFE", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
            ],
            "comps": [18_000_000, 18_500_000],  # only 2 comps
            "realized_resale": 10_500_000,
        },
        # Scenario 8: True Positive Snipe (L1 Goblin at 6.5M, next 8.7M, comps 8.6M)
        {
            "name": "True Positive Snipe (L1 Goblin)",
            "candidate": {
                "auction": {"uuid": "goblin-snipe", "starting_bid": 6_500_000, "auctioneer": "sellerA", "start": now},
                "decoded": {
                    "internal_id": "PET_GOBLIN_LEGENDARY",
                    "item_name": "[Lvl 1] Legendary Goblin",
                    "is_pet": True,
                    "pet_type": "GOBLIN",
                    "pet_tier": "LEGENDARY",
                    "effective_tier": "LEGENDARY",
                    "pet_level": 1,
                    "pet_exp": 0.0,
                    "pet_candy_used": 0,
                    "pet_skin": None,
                    "pet_held_item": "",
                },
            },
            "market_batch": [
                ({"uuid": "goblin-snipe", "starting_bid": 6_500_000, "auctioneer": "sellerA", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-ref1", "starting_bid": 8_700_000, "auctioneer": "sellerB", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
                ({"uuid": "goblin-ref2", "starting_bid": 9_500_000, "auctioneer": "sellerC", "start": now}, {"internal_id": "PET_GOBLIN_LEGENDARY", "is_pet": True, "pet_type": "GOBLIN", "pet_tier": "LEGENDARY", "effective_tier": "LEGENDARY", "pet_level": 1, "pet_exp": 0.0}),
            ],
            "comps": [8_500_000, 8_550_000, 8_600_000, 8_650_000, 8_700_000, 8_800_000],
            "realized_resale": 8_700_000,
        },
    ]
    return scenarios


def run_evaluation_pass(enable_bucket_engine: bool) -> dict[str, Any]:
    """Execute backtest pass across all scenarios under given engine mode."""
    db = DatabaseManager(":memory:")
    db.init_db()

    settings = Settings(
        ENABLE_PET_BUCKET_VALUATION=enable_bucket_engine,
        MIN_PROFIT_COINS=500_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
        PET_MIN_SOLD_COMPS_48H=5,
        MAX_SELLER_DOMINATION_RATIO=0.40,
        MAX_BENCHMARK_AGE_HOURS=12.0,
        PET_MARGIN_REVIEW_PCT=40.0,
    )
    evaluator = FlipEvaluator(settings, db=db)
    scenarios = build_scenario_dataset()

    total_scenarios = len(scenarios)
    alerts_fired = 0
    hits = 0
    errors: list[float] = []
    alert_details = []

    for sc in scenarios:
        cand = sc["candidate"]
        realized = sc["realized_resale"]

        # Populate sold comps in DB
        if sc["comps"]:
            for i, p in enumerate(sc["comps"]):
                cand_dec = dict(cand["decoded"])
                db.record_sale(
                    uuid=f"comp-{sc['name']}-{i}",
                    internal_id=cand_dec["internal_id"],
                    price=p,
                    buyer_uuid=f"buyer-{i}",
                    seller_uuid=f"seller-{i}",
                    timestamp=int(time.time() * 1000 - 3600 * 1000),
                    features_json=cand_dec,
                    features_hash="hash",
                )

        # Update market cache
        evaluator.update_cache(sc["market_batch"])

        # In legacy mode (BEFORE), emulate previous cycle's precomputed table (batch floor lag)
        if not enable_bucket_engine:
            # Emulate precomputed cache from prior snapshot (where candidate was not yet merged)
            prices = [a["starting_bid"] for a, _ in sc["market_batch"] if a["uuid"] != cand["auction"]["uuid"]]
            if prices:
                evaluator._price_cache[cand["decoded"]["internal_id"]] = sorted(prices)
            else:
                evaluator._price_cache[cand["decoded"]["internal_id"]] = [cand["auction"]["starting_bid"]]

        flip = evaluator.evaluate_listing(cand["auction"], cand["decoded"])

        if flip is not None:
            alerts_fired += 1
            est_target = flip.target_resell_price
            error = abs(est_target - realized)
            errors.append(error)
            is_hit = realized >= (est_target * 0.98)
            if is_hit:
                hits += 1
            alert_details.append({
                "scenario": sc["name"],
                "alerted": True,
                "target": est_target,
                "realized": realized,
                "error": error,
                "is_hit": is_hit,
                "rejection": None,
            })
        else:
            rejection = evaluator.get_last_pet_rejection_reason(cand["auction"]["uuid"])
            alert_details.append({
                "scenario": sc["name"],
                "alerted": False,
                "target": None,
                "realized": realized,
                "error": 0.0,
                "is_hit": False,
                "rejection": rejection or "FILTERED",
            })

    hit_rate = (hits / alerts_fired * 100.0) if alerts_fired > 0 else 0.0
    mae = (sum(errors) / len(errors)) if errors else 0.0
    rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors)) if errors else 0.0

    return {
        "engine_mode": "NEW (Bucket Engine)" if enable_bucket_engine else "OLD (Legacy Heuristic)",
        "total_scenarios": total_scenarios,
        "alerts_fired": alerts_fired,
        "hits": hits,
        "hit_rate_pct": round(hit_rate, 2),
        "mean_absolute_error": round(mae, 2),
        "root_mean_squared_error": round(rmse, 2),
        "details": alert_details,
    }


def main():
    print("=" * 80)
    print("           PET SALES BACKTEST REPLAY: BEFORE VS AFTER COMPARISON")
    print("=" * 80)

    before = run_evaluation_pass(enable_bucket_engine=False)
    after = run_evaluation_pass(enable_bucket_engine=True)

    print(f"\n{'Metric':<35} | {'BEFORE (Legacy)':<20} | {'AFTER (Bucket Engine)':<20}")
    print("-" * 80)
    print(f"{'Total Evaluated Scenarios':<35} | {before['total_scenarios']:<20} | {after['total_scenarios']:<20}")
    print(f"{'Total Alerts Fired':<35} | {before['alerts_fired']:<20} | {after['alerts_fired']:<20}")
    print(f"{'Successful Arbitrage Hits':<35} | {before['hits']:<20} | {after['hits']:<20}")
    print(f"{'Hit Rate (Realized >= Target)':<35} | {before['hit_rate_pct']}%{'':<14} | {after['hit_rate_pct']}%{'':<14}")
    print(f"{'Mean Absolute Error (coins)':<35} | {before['mean_absolute_error']:>14,.0f} coins | {after['mean_absolute_error']:>14,.0f} coins")
    print(f"{'Root Mean Sq Error (coins)':<35} | {before['root_mean_squared_error']:>14,.0f} coins | {after['root_mean_squared_error']:>14,.0f} coins")
    print("-" * 80)

    print("\nScenario Breakdown (AFTER):")
    for d in after["details"]:
        status = f"ALERT (Target: {d['target']:,}, Realized: {d['realized']:,})" if d["alerted"] else f"REJECTED [{d['rejection']}]"
        print(f"  - {d['scenario']:<45}: {status}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
