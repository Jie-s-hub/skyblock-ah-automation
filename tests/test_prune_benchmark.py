import time

from src.config import Settings
from src.evaluator import FlipEvaluator


def test_prune_vanished_auctions_benchmark():
    """
    Benchmark bulk indexed pruning on 60,000 active listings with 3,000 vanished UUIDs.
    Target: < 50ms elapsed time (previously took ~9,585ms due to O(V * K) linear scans).
    """
    config = Settings(DRY_RUN=True)
    evaluator = FlipEvaluator(config=config)

    TOTAL_LISTINGS = 60_000
    VANISHED_COUNT = 3_000
    NUM_DISTINCT_ITEMS = 1_500  # average 40 listings per item ID

    # Generate 60,000 synthetic listings
    raw_auctions = []
    decoded_auctions = []
    all_uuids = []
    active_auctions_map = {}

    now_ms = int(time.time() * 1000)

    for i in range(TOTAL_LISTINGS):
        item_idx = i % NUM_DISTINCT_ITEMS
        internal_id = f"ITEM_{item_idx}"
        item_uuid = f"uuid-{i:06d}"
        price = 1_000_000 + (i % 50) * 10_000
        start_ms = now_ms - (i % 3600) * 1000

        auction = {
            "uuid": item_uuid,
            "starting_bid": price,
            "item_name": f"Mock Item {item_idx}",
            "start": start_ms,
            "end": now_ms + 86400_000,
            "auctioneer": f"seller_{i % 500}",
            "profile_id": f"prof_{i % 500}",
            "coop": [],
            "bin": True,
        }
        decoded = {
            "internal_id": internal_id,
            "item_name": f"Mock Item {item_idx}",
            "dungeon_stars": 0,
            "rarity_upgrades": 0,
            "is_pet": False,
        }

        raw_auctions.append(auction)
        decoded_auctions.append((auction, decoded))
        all_uuids.append(item_uuid)
        active_auctions_map[item_uuid] = (auction, decoded)

    # Populate evaluator cache
    evaluator.update_cache(decoded_auctions, now_ms=now_ms)
    assert len(evaluator._listings_by_uuid) == TOTAL_LISTINGS

    # Pick 3,000 vanished UUIDs evenly distributed
    vanished_uuids = set(all_uuids[:: (TOTAL_LISTINGS // VANISHED_COUNT)][:VANISHED_COUNT])
    assert len(vanished_uuids) == VANISHED_COUNT

    # Time the bulk indexed pruning
    t0 = time.perf_counter()
    evaluator.prune_vanished_auctions(vanished_uuids, active_auctions_map)
    elapsed = time.perf_counter() - t0

    # Verify listings were pruned correctly
    assert len(evaluator._listings_by_uuid) == TOTAL_LISTINGS - VANISHED_COUNT
    for v_uuid in list(vanished_uuids)[:50]:
        assert v_uuid not in evaluator._listings_by_uuid

    print(f"\n[BENCHMARK] Pruned {VANISHED_COUNT} listings out of {TOTAL_LISTINGS} in {elapsed * 1000:.2f} ms")

    # Assert under 50ms (0.050 seconds)
    assert elapsed < 0.050, f"Pruning took {elapsed * 1000:.2f} ms, exceeding 50ms budget!"
