"""Tests for SalesVelocityTracker, turnover rate calculation, deduplication, and evaluator integration."""

from __future__ import annotations

import time
import pytest
from pathlib import Path

from src.config import Settings
from src.evaluator import FlipEvaluator, FlipResult
from src.velocity import SalesVelocityTracker
from tests.test_decoder import make_synthetic_nbt


@pytest.fixture
def base_config() -> Settings:
    return Settings(
        DRY_RUN=True,
        ENABLE_VELOCITY_FILTER=True,
        MIN_SALES_PER_DAY=5,
        MAX_HOURS_WITHOUT_SALE=24.0,
        VELOCITY_WINDOW_HOURS=24,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        MIN_SAMPLE_SIZE=3,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )


def test_record_sale_and_daily_velocity_calculation(base_config: Settings):
    """Verify record_sale properly logs timestamps and calculates 24h daily velocity."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # Record 8 sales distributed across the past 12 hours
    for i in range(8):
        tracker.record_sale("HYPERION", now - (i * 3600.0))

    velocity = tracker.get_daily_velocity("HYPERION", now=now)
    assert velocity == 8.0

    # Unknown item has 0.0 velocity
    assert tracker.get_daily_velocity("UNKNOWN_ITEM", now=now) == 0.0

    # Invalid internal_id handled safely
    tracker.record_sale("", now)
    tracker.record_sale("UNKNOWN", now)
    assert tracker.get_daily_velocity("UNKNOWN", now=now) == 0.0


def test_daily_velocity_extrapolation_with_custom_window(base_config: Settings):
    """Verify daily velocity extrapolates properly when a non-24h window is specified."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # 4 sales within a 6-hour window -> expected daily rate is 4 * (24 / 6) = 16.0
    for i in range(4):
        tracker.record_sale("JUJU_SHORTBOW", now - (i * 3600.0))

    extrapolated_rate = tracker.get_daily_velocity("JUJU_SHORTBOW", window_hours=6.0, now=now)
    assert extrapolated_rate == 16.0


def test_record_ended_auctions_decoding_and_filtering(base_config: Settings):
    """Verify record_ended_auctions decodes NBT, checks buyer and bin flags, and converts timestamps."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now_ms = int(time.time() * 1000)

    nbt_hyp = make_synthetic_nbt(internal_id="HYPERION", name="Hyperion")
    nbt_necron = make_synthetic_nbt(internal_id="NECRON_CHESTPLATE", name="Necron's Chestplate")

    raw_auctions = [
        # 1. Valid BIN sale with buyer
        {
            "auction_id": "sale-1",
            "buyer": "buyer-uuid-1",
            "bin": True,
            "timestamp": now_ms,
            "item_bytes": nbt_hyp,
        },
        # 2. Expired auction without buyer
        {
            "auction_id": "sale-2",
            "buyer": None,
            "bin": True,
            "timestamp": now_ms,
            "item_bytes": nbt_hyp,
        },
        # 3. Regular bidding auction (not BIN)
        {
            "auction_id": "sale-3",
            "buyer": "buyer-uuid-2",
            "bin": False,
            "timestamp": now_ms,
            "item_bytes": nbt_hyp,
        },
        # 4. Malformed / empty item_bytes
        {
            "auction_id": "sale-4",
            "buyer": "buyer-uuid-3",
            "bin": True,
            "timestamp": now_ms,
            "item_bytes": "",
        },
        # 5. Second valid BIN sale for different item
        {
            "auction_id": "sale-5",
            "buyer": "buyer-uuid-4",
            "bin": True,
            "timestamp": now_ms,
            "item_bytes": nbt_necron,
        },
    ]

    recorded_count = tracker.record_ended_auctions(raw_auctions)
    assert recorded_count == 2
    assert tracker.get_daily_velocity("HYPERION") == 1.0
    assert tracker.get_daily_velocity("NECRON_CHESTPLATE") == 1.0


def test_deduplication_of_auction_ids(base_config: Settings):
    """Verify processing the same auction_id multiple times does not duplicate counts."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now_ms = int(time.time() * 1000)
    nbt = make_synthetic_nbt(internal_id="SHADOW_FURY")

    auction_data = [
        {
            "auction_id": "unique-ah-1",
            "buyer": "buyer-1",
            "bin": True,
            "timestamp": now_ms,
            "item_bytes": nbt,
        }
    ]

    first_run = tracker.record_ended_auctions(auction_data)
    assert first_run == 1

    second_run = tracker.record_ended_auctions(auction_data)
    assert second_run == 0

    assert tracker.get_daily_velocity("SHADOW_FURY") == 1.0


def test_pruning_dedup_cache_older_than_2_hours(base_config: Settings):
    """Verify deduplication cache evicts entries older than 2 hours."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # Manually seed an entry from 3 hours ago (10800s ago)
    old_ts = now - 10800.0
    tracker._processed_auction_ids.add("ancient-sale")
    tracker._auction_id_timestamps.append(("ancient-sale", old_ts))

    # Seed a recent entry from 30 minutes ago
    recent_ts = now - 1800.0
    tracker._processed_auction_ids.add("recent-sale")
    tracker._auction_id_timestamps.append(("recent-sale", recent_ts))

    tracker._prune_dedup_cache(now, max_age_seconds=7200.0)

    assert "ancient-sale" not in tracker._processed_auction_ids
    assert "recent-sale" in tracker._processed_auction_ids


def test_rolling_window_pruning(base_config: Settings):
    """Verify sales outside the 24-hour window are excluded from daily velocity calculation."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # 2 sales older than 24 hours (25h and 26h ago)
    tracker.record_sale("LIVID_DAGGER", now - (25.0 * 3600.0))
    tracker.record_sale("LIVID_DAGGER", now - (26.0 * 3600.0))

    # 3 sales within last 24 hours
    tracker.record_sale("LIVID_DAGGER", now - (2.0 * 3600.0))
    tracker.record_sale("LIVID_DAGGER", now - (5.0 * 3600.0))
    tracker.record_sale("LIVID_DAGGER", now - (10.0 * 3600.0))

    velocity = tracker.get_daily_velocity("LIVID_DAGGER", window_hours=24.0, now=now)
    assert velocity == 3.0
    # Confirm the 2 expired sales were pruned from internal deque
    assert len(tracker.sales["LIVID_DAGGER"]) == 3


def test_is_velocity_sufficient_thresholds(base_config: Settings):
    """Verify is_velocity_sufficient correctly applies minimum volume and staleness rules."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # 1. Item with >= 5 sales/day and recent transaction passes
    for i in range(6):
        tracker.record_sale("HIGH_VELOCITY_ITEM", now - (i * 3600.0))
    assert tracker.is_velocity_sufficient("HIGH_VELOCITY_ITEM", now=now) is True

    # 2. Item with 3 sales/day (< MIN_SALES_PER_DAY=5) is rejected
    for i in range(3):
        tracker.record_sale("LOW_VELOCITY_ITEM", now - (i * 3600.0))
    assert tracker.is_velocity_sufficient("LOW_VELOCITY_ITEM", now=now) is False

    # 3. Item with zero sales is rejected
    assert tracker.is_velocity_sufficient("ZERO_SALES_ITEM", now=now) is False

    # 4. Item with 10 sales, but most recent sale is stale (> 24 hours ago)
    for i in range(10):
        tracker.record_sale("STALE_ITEM", now - ((25.0 + i) * 3600.0))
    assert tracker.is_velocity_sufficient("STALE_ITEM", now=now) is False

    # 5. Custom threshold overrides
    tracker.record_sale("CUSTOM_ITEM", now - 3600.0)
    tracker.record_sale("CUSTOM_ITEM", now - 7200.0)
    # Fails default 5 sales/day
    assert tracker.is_velocity_sufficient("CUSTOM_ITEM", now=now) is False
    # Passes with custom min_daily_sales=2 and max_stale_hours=3.0
    assert (
        tracker.is_velocity_sufficient(
            "CUSTOM_ITEM", min_daily_sales=2, max_stale_hours=3.0, now=now
        )
        is True
    )
    # Fails if max_stale_hours is 0.5 (most recent sale was 1.0h ago)
    assert (
        tracker.is_velocity_sufficient(
            "CUSTOM_ITEM", min_daily_sales=2, max_stale_hours=0.5, now=now
        )
        is False
    )


def test_disk_persistence_save_and_load(tmp_path: Path, base_config: Settings):
    """Verify saving and restoring sales velocity state to/from JSON."""
    cache_file = tmp_path / "velocity_test.json"
    tracker1 = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    tracker1.record_sale("HYPERION", now - 1000.0)
    tracker1.record_sale("HYPERION", now - 500.0)
    tracker1.record_sale("VALKYRIE", now - 2000.0)

    tracker1.save_to_disk(cache_file)
    assert cache_file.exists()

    tracker2 = SalesVelocityTracker(base_config, auto_load=False)
    restored_count = tracker2.load_from_disk(cache_file)

    assert restored_count == 2
    assert tracker2.get_daily_velocity("HYPERION", now=now) == 2.0
    assert tracker2.get_daily_velocity("VALKYRIE", now=now) == 1.0


def test_flip_evaluator_integration_accepted_with_high_velocity(base_config: Settings):
    """Verify FlipEvaluator accepts profitable flip when sales velocity meets threshold."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # Record 8 sales in past 24 hours (>= 5 required)
    for i in range(8):
        tracker.record_sale("HYPERION", now - (i * 1800.0))

    evaluator = FlipEvaluator(base_config, velocity_tracker=tracker)
    evaluator.set_price_cache({"HYPERION": [10_000_000, 15_000_000, 16_000_000]})

    auction = {"uuid": "test-uuid-high-vel", "starting_bid": 10_000_000}
    decoded = {
        "internal_id": "HYPERION",
        "item_name": "Hyperion",
        "dungeon_stars": 5,
        "rarity_upgrades": 1,
    }

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert isinstance(result, FlipResult)
    assert result.internal_id == "HYPERION"
    # Recomputed by hand per Phase 1 fee model (15M in 10M-100M tier: 2% creation + 1% collection = 3%):
    # Net revenue = 15,000,000 * 0.97 = 14,550,000 -> net profit = 4,550,000
    assert result.net_profit == 4_550_000


def test_flip_evaluator_integration_rejected_with_low_or_zero_velocity(base_config: Settings):
    """Verify FlipEvaluator rejects profitable flips that have insufficient (<5) or zero sales."""
    tracker = SalesVelocityTracker(base_config, auto_load=False)
    now = time.time()

    # 1. Low velocity item: 2 sales in past 24h (< 5)
    for i in range(2):
        tracker.record_sale("TRAP_SWORD", now - (i * 3600.0))

    evaluator = FlipEvaluator(base_config, velocity_tracker=tracker)
    evaluator.set_price_cache({
        "TRAP_SWORD": [10_000_000, 20_000_000, 22_000_000],
        "ZERO_SALE_ITEM": [5_000_000, 10_000_000, 11_000_000],
    })

    # Test candidate with low velocity
    low_vel_auction = {"uuid": "test-uuid-low", "starting_bid": 10_000_000}
    low_vel_decoded = {"internal_id": "TRAP_SWORD", "item_name": "Trap Sword"}
    assert evaluator.evaluate_listing(low_vel_auction, low_vel_decoded) is None

    # Test candidate with zero sales
    zero_vel_auction = {"uuid": "test-uuid-zero", "starting_bid": 5_000_000}
    zero_vel_decoded = {"internal_id": "ZERO_SALE_ITEM", "item_name": "Zero Sale Item"}
    assert evaluator.evaluate_listing(zero_vel_auction, zero_vel_decoded) is None


def test_flip_evaluator_integration_config_toggle_bypasses_velocity():
    """Verify setting ENABLE_VELOCITY_FILTER=False allows flips even with 0 or low sales."""
    bypass_config = Settings(
        DRY_RUN=True,
        ENABLE_VELOCITY_FILTER=False,
        MIN_SALES_PER_DAY=5,
        MIN_PROFIT_COINS=1_000_000,
        MIN_PROFIT_MARGIN_PCT=15.0,
        MIN_SAMPLE_SIZE=3,
        MAX_TARGET_TO_BUY_RATIO=3.0,
    )
    tracker = SalesVelocityTracker(bypass_config, auto_load=False)
    # Zero sales in tracker for HYPERION

    evaluator = FlipEvaluator(bypass_config, velocity_tracker=tracker)
    evaluator.set_price_cache({"HYPERION": [10_000_000, 15_000_000, 16_000_000]})

    auction = {"uuid": "test-uuid-bypassed", "starting_bid": 10_000_000}
    decoded = {"internal_id": "HYPERION", "item_name": "Hyperion"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.internal_id == "HYPERION"
    assert result.daily_velocity == 0.0
