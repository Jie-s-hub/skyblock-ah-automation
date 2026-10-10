"""
End-to-End Dry-Run Demonstration and Pipeline Verification Script.

Demonstrates and verifies:
1. Sales velocity tracking via ended auctions (/v2/skyblock/auctions_ended).
2. Cosmetic & vanity filtering discarding cosmetic baits (PET_SKIN_*, DYE_*).
3. Low sales turnover rejection via the SalesVelocityTracker.
4. Progression staple prioritization with relaxed profit margins (NECRON_CHESTPLATE with ~10% margin).
5. Midgame progression staple priority tagging (LIVID_DAGGER).
6. Core weapon retention without false-positive dropping (RUNAAN_BOW).
7. Dry-run alert dispatching and Discord embed formatting (colors, titles, priority badges).
"""

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Windows consoles don't crash on Minecraft symbols (e.g. ✪ \u272a, 🔥 \U0001f525)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

from src.config import Settings
from src.evaluator import FlipEvaluator
from src.notifier import DiscordNotifier
from src.velocity import SalesVelocityTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VerifyE2EDryRun")


async def run_e2e_verification() -> None:
    print("=" * 80)
    print(" HYPIXEL SKYBLOCK AH ARBITRAGE - END-TO-END DRY-RUN VERIFICATION")
    print("=" * 80)

    # 1. Configuration: DRY_RUN mode active, velocity filter active, cosmetic filter active
    print("\n[Step 1] Initializing Settings with DRY_RUN=True...")
    settings = Settings(
        DRY_RUN=True,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        STAPLE_MIN_PROFIT_COINS=500_000,
        STAPLE_MIN_PROFIT_MARGIN_PCT=8.0,
        COSMETIC_FILTER_ENABLED=True,
        ENABLE_VELOCITY_FILTER=True,
        MIN_SALES_PER_DAY=5,
        VELOCITY_CACHE_FILE=None,  # Isolated memory session
    )

    assert settings.DRY_RUN is True, "DRY_RUN must be enabled"
    assert settings.COSMETIC_FILTER_ENABLED is True, "COSMETIC_FILTER_ENABLED must be enabled"
    assert settings.ENABLE_VELOCITY_FILTER is True, "ENABLE_VELOCITY_FILTER must be enabled"
    print("  -> Configuration verified: DRY_RUN=True, strict cosmetic & velocity filters enabled.")

    # 2. Initialize Core Components
    print("\n[Step 2] Initializing Core Engines (VelocityTracker, FlipEvaluator, DiscordNotifier)...")
    velocity_tracker = SalesVelocityTracker(settings, auto_load=False)
    evaluator = FlipEvaluator(settings, velocity_tracker=velocity_tracker)
    notifier = DiscordNotifier(settings)

    # 3. Populate Sales Velocity via Ended Auctions
    print("\n[Step 3] Ingesting Ended Auctions into SalesVelocityTracker...")
    now = time.time()
    ended_auctions: list[dict[str, Any]] = []

    # High-velocity progression staple: NECRON_CHESTPLATE (12 sales in last 6 hours)
    for i in range(12):
        ended_auctions.append({
            "auction_id": f"ended_necron_{i}",
            "buyer": f"buyer_necron_{i}",
            "bin": True,
            "timestamp": int((now - (i * 1800)) * 1000),
            "internal_id": "NECRON_CHESTPLATE",
        })

    # High-velocity progression staple: LIVID_DAGGER (15 sales in last 5 hours)
    for i in range(15):
        ended_auctions.append({
            "auction_id": f"ended_livid_{i}",
            "buyer": f"buyer_livid_{i}",
            "bin": True,
            "timestamp": int((now - (i * 1200)) * 1000),
            "internal_id": "LIVID_DAGGER",
        })

    # High-velocity core weapon: RUNAAN_BOW (10 sales in last 5 hours)
    for i in range(10):
        ended_auctions.append({
            "auction_id": f"ended_runaan_{i}",
            "buyer": f"buyer_runaan_{i}",
            "bin": True,
            "timestamp": int((now - (i * 1800)) * 1000),
            "internal_id": "RUNAAN_BOW",
        })

    # High-velocity cosmetic items (to demonstrate cosmetic filter drops them despite high velocity)
    for i in range(8):
        ended_auctions.append({
            "auction_id": f"ended_yeti_skin_{i}",
            "buyer": f"buyer_yeti_{i}",
            "bin": True,
            "timestamp": int((now - (i * 2000)) * 1000),
            "internal_id": "PET_SKIN_BABY_YETI_ICE",
        })
    for i in range(8):
        ended_auctions.append({
            "auction_id": f"ended_dye_{i}",
            "buyer": f"buyer_dye_{i}",
            "bin": True,
            "timestamp": int((now - (i * 2000)) * 1000),
            "internal_id": "DYE_NECRON",
        })

    recorded = velocity_tracker.record_ended_auctions(ended_auctions)
    assert recorded == len(ended_auctions), f"Expected {len(ended_auctions)} sales recorded, got {recorded}"

    necron_vel = velocity_tracker.get_daily_velocity("NECRON_CHESTPLATE")
    livid_vel = velocity_tracker.get_daily_velocity("LIVID_DAGGER")
    runaan_vel = velocity_tracker.get_daily_velocity("RUNAAN_BOW")
    obsidian_vel = velocity_tracker.get_daily_velocity("OBSIDIAN_CHESTPLATE")

    print(f"  -> Recorded {recorded} sales across tracked items.")
    print(f"  -> NECRON_CHESTPLATE velocity: {necron_vel:.1f} sales/day (sufficient: {velocity_tracker.is_velocity_sufficient('NECRON_CHESTPLATE')})")
    print(f"  -> LIVID_DAGGER velocity:      {livid_vel:.1f} sales/day (sufficient: {velocity_tracker.is_velocity_sufficient('LIVID_DAGGER')})")
    print(f"  -> RUNAAN_BOW velocity:         {runaan_vel:.1f} sales/day (sufficient: {velocity_tracker.is_velocity_sufficient('RUNAAN_BOW')})")
    print(f"  -> OBSIDIAN_CHESTPLATE velocity:{obsidian_vel:.1f} sales/day (sufficient: {velocity_tracker.is_velocity_sufficient('OBSIDIAN_CHESTPLATE')})")

    assert velocity_tracker.is_velocity_sufficient("NECRON_CHESTPLATE") is True
    assert velocity_tracker.is_velocity_sufficient("LIVID_DAGGER") is True
    assert velocity_tracker.is_velocity_sufficient("RUNAAN_BOW") is True
    assert velocity_tracker.is_velocity_sufficient("OBSIDIAN_CHESTPLATE") is False, "0-sales item must not be sufficient"

    # 4. Ingest Active Market Listings into Evaluator Cache
    print("\n[Step 4] Updating Evaluator Market Cache with Active Listings...")
    market_batch: list[tuple[dict[str, Any], dict[str, Any]]] = [
        # Necron's Chestplate: buy 40M, resell 45M (net profit 4.1M, margin 10.25%)
        ({"starting_bid": 40_000_000, "uuid": "necron_candidate"}, {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}),
        ({"starting_bid": 45_000_000, "uuid": "necron_resell_1"}, {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}),
        ({"starting_bid": 46_000_000, "uuid": "necron_resell_2"}, {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}),
        ({"starting_bid": 48_000_000, "uuid": "necron_resell_3"}, {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}),

        # Livid Dagger: buy 7M, resell 9.5M (net profit 2.31M, margin 33.0%)
        ({"starting_bid": 7_000_000, "uuid": "livid_candidate"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),
        ({"starting_bid": 9_500_000, "uuid": "livid_resell_1"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),
        ({"starting_bid": 9_800_000, "uuid": "livid_resell_2"}, {"internal_id": "LIVID_DAGGER", "item_name": "Livid Dagger"}),

        # Runaan's Bow: buy 5M, resell 7M (net profit 1.86M, margin 37.2%)
        ({"starting_bid": 5_000_000, "uuid": "runaan_candidate"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),
        ({"starting_bid": 7_000_000, "uuid": "runaan_resell_1"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),
        ({"starting_bid": 7_200_000, "uuid": "runaan_resell_2"}, {"internal_id": "RUNAAN_BOW", "item_name": "Runaan's Bow"}),

        # Zero-velocity item: OBSIDIAN_CHESTPLATE (buy 2M, resell 6M -> apparent margin 194%, but 0 sales)
        ({"starting_bid": 2_000_000, "uuid": "obsidian_candidate"}, {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"}),
        ({"starting_bid": 6_000_000, "uuid": "obsidian_resell_1"}, {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"}),
        ({"starting_bid": 6_500_000, "uuid": "obsidian_resell_2"}, {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"}),

        # Cosmetic Bait: PET_SKIN_BABY_YETI_ICE (buy 10M, resell 30M -> huge margin bait)
        ({"starting_bid": 10_000_000, "uuid": "yeti_skin_candidate"}, {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"}),
        ({"starting_bid": 30_000_000, "uuid": "yeti_skin_resell_1"}, {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"}),
        ({"starting_bid": 35_000_000, "uuid": "yeti_skin_resell_2"}, {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"}),

        # Cosmetic Dye: DYE_NECRON (buy 50M, resell 150M -> huge margin bait)
        ({"starting_bid": 50_000_000, "uuid": "dye_candidate"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
        ({"starting_bid": 150_000_000, "uuid": "dye_resell_1"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
        ({"starting_bid": 160_000_000, "uuid": "dye_resell_2"}, {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}),
    ]

    evaluator.update_cache(market_batch)

    # Verify cosmetics were excluded from cache building
    assert "PET_SKIN_BABY_YETI_ICE" not in evaluator.price_cache, "Cosmetics must be excluded from price cache"
    assert "DYE_NECRON" not in evaluator.price_cache, "Dyes must be excluded from price cache"
    assert "NECRON_CHESTPLATE" in evaluator.price_cache, "Staples must be present in price cache"
    assert "LIVID_DAGGER" in evaluator.price_cache, "Staples must be present in price cache"
    assert "RUNAAN_BOW" in evaluator.price_cache, "Core weapons must be present in price cache"
    assert "OBSIDIAN_CHESTPLATE" in evaluator.price_cache, "Normal items must be present in price cache"
    print("  -> Market cache verified: cosmetics excluded, valid equipment cached.")

    # 5. Candidate Evaluation & Scenario Verification
    print("\n[Step 5] Evaluating Candidate Listings Against Business Logic...")

    # Scenario 1: Cosmetic bait (PET_SKIN_BABY_YETI_ICE)
    cosmetic_auction = {"starting_bid": 10_000_000, "uuid": "yeti_skin_candidate"}
    cosmetic_decoded = {"internal_id": "PET_SKIN_BABY_YETI_ICE", "item_name": "Baby Yeti Pet Skin (Ice)"}
    assert evaluator.is_blacklisted_cosmetic("PET_SKIN_BABY_YETI_ICE", "Baby Yeti Pet Skin (Ice)") is True
    res_cosmetic = evaluator.evaluate_listing(cosmetic_auction, cosmetic_decoded)
    assert res_cosmetic is None, "Cosmetic bait must be rejected"
    print("  [PASS] Scenario 1: Cosmetic bait (PET_SKIN_BABY_YETI_ICE) discarded by cosmetic filter.")

    # Scenario 2: Cosmetic dye (DYE_NECRON)
    dye_auction = {"starting_bid": 50_000_000, "uuid": "dye_candidate"}
    dye_decoded = {"internal_id": "DYE_NECRON", "item_name": "Necron Dye"}
    assert evaluator.is_blacklisted_cosmetic("DYE_NECRON", "Necron Dye") is True
    res_dye = evaluator.evaluate_listing(dye_auction, dye_decoded)
    assert res_dye is None, "Cosmetic dye must be rejected"
    print("  [PASS] Scenario 2: Cosmetic dye (DYE_NECRON) discarded by cosmetic filter.")

    # Scenario 3: Low-velocity item with 0 sales (OBSIDIAN_CHESTPLATE)
    obsidian_auction = {"starting_bid": 2_000_000, "uuid": "obsidian_candidate"}
    obsidian_decoded = {"internal_id": "OBSIDIAN_CHESTPLATE", "item_name": "Obsidian Chestplate"}
    res_obsidian = evaluator.evaluate_listing(obsidian_auction, obsidian_decoded)
    assert res_obsidian is None, "0-velocity item must be rejected despite huge margin"
    print("  [PASS] Scenario 3: Zero-velocity item (OBSIDIAN_CHESTPLATE) rejected by velocity engine.")

    # Scenario 4: Progression staple Wither armor (NECRON_CHESTPLATE) with 10% margin
    necron_auction = {"starting_bid": 40_000_000, "uuid": "necron_candidate"}
    necron_decoded = {
        "internal_id": "NECRON_CHESTPLATE",
        "item_name": "Necron's Chestplate",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
        "enchantments": {"growth": 6, "protection": 6},
    }
    res_necron = evaluator.evaluate_listing(necron_auction, necron_decoded)
    assert res_necron is not None, "Necron Chestplate must qualify under relaxed staple thresholds"
    assert res_necron.is_priority is True, "Necron Chestplate must be flagged is_priority=True"
    assert res_necron.staple_category == "WITHER_ARMOR", "Staple category must be WITHER_ARMOR"
    assert 8.0 <= res_necron.margin_pct < 15.0, f"Expected margin in [8.0%, 15.0%), got {res_necron.margin_pct}%"
    assert res_necron.net_profit == 4_100_000, f"Expected 4,100,000 coins profit, got {res_necron.net_profit}"
    print(f"  [PASS] Scenario 4: Progression staple Wither armor (NECRON_CHESTPLATE) accepted at {res_necron.margin_pct:.2f}% margin (relaxed threshold >= 8.0%, standard threshold >= 15.0%).")

    # Scenario 5: Midgame progression staple (LIVID_DAGGER)
    livid_auction = {"starting_bid": 7_000_000, "uuid": "livid_candidate"}
    livid_decoded = {
        "internal_id": "LIVID_DAGGER",
        "item_name": "Livid Dagger",
        "dungeon_stars": 5,
        "rarity_upgrades": 0,
        "enchantments": {"sharpness": 5, "first_strike": 4},
    }
    res_livid = evaluator.evaluate_listing(livid_auction, livid_decoded)
    assert res_livid is not None, "Livid Dagger must qualify"
    assert res_livid.is_priority is True, "Livid Dagger must be flagged is_priority=True"
    assert res_livid.staple_category == "MIDGAME_COMBAT", "Staple category must be MIDGAME_COMBAT"
    print(f"  [PASS] Scenario 5: Midgame progression staple (LIVID_DAGGER) accepted with category {res_livid.staple_category}.")

    # Scenario 6: Core weapon (RUNAAN_BOW) preserved and not dropped
    runaan_auction = {"starting_bid": 5_000_000, "uuid": "runaan_candidate"}
    runaan_decoded = {
        "internal_id": "RUNAAN_BOW",
        "item_name": "Runaan's Bow",
        "dungeon_stars": 0,
        "rarity_upgrades": 0,
        "enchantments": {"power": 5, "aiming": 5},
    }
    assert evaluator.is_blacklisted_cosmetic("RUNAAN_BOW", "Runaan's Bow") is False, "RUNAAN_BOW must not be blacklisted"
    res_runaan = evaluator.evaluate_listing(runaan_auction, runaan_decoded)
    assert res_runaan is not None, "Runaan's Bow must qualify"
    assert res_runaan.is_priority is False, "Standard item must not have priority"
    assert res_runaan.staple_category is None, "Standard item must have staple_category=None"
    assert res_runaan.margin_pct >= 15.0, "Standard item requires >= 15.0% margin"
    print(f"  [PASS] Scenario 6: Core weapon (RUNAAN_BOW) preserved as standard flip ({res_runaan.margin_pct:.1f}% margin).")

    # 6. Embed Payload Styling Validation
    print("\n[Step 6] Validating Discord Embed Styling and Visual Attributes...")

    # Check Necron embed
    necron_embed = notifier.build_embed_payload(res_necron)["embeds"][0]
    assert necron_embed["title"] == "🔥 [PRIORITY STAPLE] Necron's Chestplate"
    assert necron_embed["color"] == 0xFF9900, "Priority staple must use flame gold color 0xFF9900"
    priority_field = next((f for f in necron_embed["fields"] if f["name"] == "⚡ Priority Classification"), None)
    assert priority_field is not None, "Priority classification field must exist in embed"
    assert "WITHER_ARMOR" in priority_field["value"]
    print("  -> Necron's Chestplate embed styling verified: Flame title, color 0xFF9900, priority classification field.")

    # Check Livid Dagger embed
    livid_embed = notifier.build_embed_payload(res_livid)["embeds"][0]
    assert livid_embed["title"] == "🔥 [PRIORITY STAPLE] Livid Dagger"
    assert livid_embed["color"] == 0xFF9900
    livid_p_field = next((f for f in livid_embed["fields"] if f["name"] == "⚡ Priority Classification"), None)
    assert livid_p_field is not None
    assert "MIDGAME_COMBAT" in livid_p_field["value"]
    print("  -> Livid Dagger embed styling verified: Flame title, color 0xFF9900, priority classification field.")

    # Check Runaan's Bow embed
    runaan_embed = notifier.build_embed_payload(res_runaan)["embeds"][0]
    assert runaan_embed["title"] == "[FLIP FOUND] Runaan's Bow"
    assert runaan_embed["color"] == 0x00FF00, "Standard flip must use green color 0x00FF00"
    runaan_p_field = next((f for f in runaan_embed["fields"] if f["name"] == "⚡ Priority Classification"), None)
    assert runaan_p_field is None, "Standard flip must not have priority classification field"
    print("  -> Runaan's Bow embed styling verified: Standard title, color 0x00FF00, no priority field.")

    # 7. Dispatch Alerts in DRY_RUN Mode
    print("\n[Step 7] Dispatching Alerts via DiscordNotifier.send_alert (DRY_RUN)...")
    alerts_to_send = [res_necron, res_livid, res_runaan]
    for flip in alerts_to_send:
        dispatched = await notifier.send_alert(flip)
        assert dispatched is True, f"Failed to dispatch dry-run alert for {flip.item_name}"

    await notifier.close()

    print("\n" + "=" * 80)
    print(" ALL END-TO-END DRY-RUN ACCEPTANCE CRITERIA VERIFIED SUCCESSFULLY! ")
    print("=" * 80)


def main() -> None:
    try:
        asyncio.run(run_e2e_verification())
        sys.exit(0)
    except Exception:
        logger.exception("E2E Verification failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
