"""Sales velocity and volume validation engine tracking Hypixel SkyBlock ended auctions."""

from __future__ import annotations

from collections import defaultdict, deque
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional

from src.config import Settings
from src.nbt_decoder import decode_item_bytes

logger = logging.getLogger(__name__)


class SalesVelocityTracker:
    """
    Maintains in-memory rolling sales counters and turnover metrics per internal_id
    by monitoring and ingesting Hypixel ended auctions.
    """

    def __init__(
        self,
        config: Optional[Settings] = None,
        auto_load: bool = True,
    ) -> None:
        self.config = config or Settings(DRY_RUN=True)
        # Mapping: internal_id -> deque of sale timestamps (epoch seconds as float)
        self.sales: dict[str, deque[float]] = defaultdict(deque)
        # Deduplication tracking for auction IDs
        self._processed_auction_ids: set[str] = set()
        self._auction_id_timestamps: deque[tuple[str, float]] = deque()
        self.start_time: float = time.time()

        if auto_load and self.config.VELOCITY_CACHE_FILE:
            cache_path = Path(self.config.VELOCITY_CACHE_FILE)
            if cache_path.is_file():
                try:
                    self.load_from_disk(cache_path)
                except Exception as err:
                    logger.warning("Could not auto-load velocity cache from %s: %s", cache_path, err)

    def record_sale(self, internal_id: str, timestamp: float) -> None:
        """
        Record a single sale timestamp for an internal_id.
        Helper for direct updates and deterministic unit testing.
        """
        if not internal_id or internal_id == "UNKNOWN":
            return

        ts = float(timestamp)
        dq = self.sales[internal_id]
        if dq and ts < dq[-1]:
            # Keep timestamps monotonically non-decreasing
            dq.append(ts)
            self.sales[internal_id] = deque(sorted(dq))
        else:
            dq.append(ts)

    def _prune_dedup_cache(self, now: float, max_age_seconds: float = 7200.0) -> None:
        """Prune deduplication cache entries older than 2 hours (default 7200s)."""
        cutoff = now - max_age_seconds
        while self._auction_id_timestamps and self._auction_id_timestamps[0][1] < cutoff:
            old_id, _ = self._auction_id_timestamps.popleft()
            self._processed_auction_ids.discard(old_id)

    def _prune_sales(self, internal_id: str, now: float, window_seconds: float) -> None:
        """Remove timestamps outside the rolling time window for an internal_id."""
        dq = self.sales.get(internal_id)
        if not dq:
            return
        cutoff = now - window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()

    def record_ended_auctions(self, raw_auctions: list[dict[str, Any]]) -> int:
        """
        Process a batch of raw ended auctions from /v2/skyblock/auctions_ended.

        Filters for completed BIN sales (buyer is not None, bin is True), decodes item_bytes
        to retrieve internal_id, deduplicates by auction_id, and records sale timestamps.

        Returns:
            int: The number of new valid sales recorded.
        """
        now = time.time()
        self._prune_dedup_cache(now)
        recorded_count = 0

        for auction in raw_auctions:
            if not isinstance(auction, dict):
                continue

            # Must have a buyer (completed sale, not expired or cancelled)
            if auction.get("buyer") is None:
                continue

            # Must be a Buy-It-Now sale
            if auction.get("bin") is not True:
                continue

            auction_id = str(auction.get("auction_id", "")).strip()
            if not auction_id:
                auction_id = str(auction.get("uuid", "")).strip()
            if not auction_id:
                continue

            # Deduplication
            if auction_id in self._processed_auction_ids:
                continue

            # Parse timestamp (Hypixel timestamps are in epoch milliseconds)
            ts_raw = auction.get("timestamp")
            if ts_raw is not None:
                try:
                    ts_float = float(ts_raw)
                    # Convert ms to seconds if timestamp > 10^11
                    ts = ts_float / 1000.0 if ts_float > 1e11 else ts_float
                except (ValueError, TypeError):
                    ts = now
            else:
                ts = now

            # Decode item_bytes
            item_bytes = auction.get("item_bytes")
            decoded = decode_item_bytes(item_bytes) if item_bytes else {}
            internal_id = decoded.get("internal_id")
            if not internal_id or internal_id == "UNKNOWN":
                # Fallback to direct internal_id if provided in auction dict (e.g. mock tests)
                internal_id = auction.get("internal_id")

            if not internal_id or internal_id == "UNKNOWN":
                continue

            self.record_sale(internal_id, ts)
            self._processed_auction_ids.add(auction_id)
            self._auction_id_timestamps.append((auction_id, ts))
            recorded_count += 1

        return recorded_count

    def get_daily_velocity(
        self,
        internal_id: str,
        window_hours: Optional[float] = None,
        now: Optional[float] = None,
    ) -> float:
        """
        Calculate the estimated daily sales volume (sales per 24 hours) for internal_id.

        Prunes timestamps outside the rolling window and computes/extrapolates the 24h rate.
        """
        if not internal_id or internal_id not in self.sales:
            return 0.0

        w_hours = float(
            window_hours if window_hours is not None else self.config.VELOCITY_WINDOW_HOURS
        )
        if w_hours <= 0:
            w_hours = 24.0

        current_time = float(now if now is not None else time.time())
        window_seconds = w_hours * 3600.0

        self._prune_sales(internal_id, current_time, window_seconds)
        sales_in_window = len(self.sales[internal_id])
        if sales_in_window == 0:
            return 0.0

        # Daily rate normalized to 24 hours: sales * (24.0 / w_hours)
        daily_rate = (sales_in_window / w_hours) * 24.0
        return round(daily_rate, 2)

    def is_velocity_sufficient(
        self,
        internal_id: str,
        min_daily_sales: Optional[int] = None,
        max_stale_hours: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """
        Validate whether an internal_id satisfies turnover threshold requirements.

        Rejects candidates with insufficient daily velocity (< min_daily_sales),
        zero recorded sales, or whose last sale is older than max_stale_hours.
        """
        if not internal_id or internal_id not in self.sales or not self.sales[internal_id]:
            return False

        min_sales = (
            min_daily_sales
            if min_daily_sales is not None
            else self.config.MIN_SALES_PER_DAY
        )
        stale_hours = (
            max_stale_hours
            if max_stale_hours is not None
            else self.config.MAX_HOURS_WITHOUT_SALE
        )
        current_time = float(now if now is not None else time.time())

        # Check rolling daily velocity
        daily_velocity = self.get_daily_velocity(internal_id, now=current_time)
        if daily_velocity < min_sales:
            return False

        # Check staleness: hours elapsed since most recent sale
        if stale_hours is not None:
            last_sale_time = self.sales[internal_id][-1]
            if (current_time - last_sale_time) > (stale_hours * 3600.0):
                return False

        return True

    def save_to_disk(self, file_path: Optional[str | Path] = None) -> None:
        """Persist sales timestamps to disk in JSON format."""
        target = Path(file_path or self.config.VELOCITY_CACHE_FILE or "data/sales_velocity.json")
        target.parent.mkdir(parents=True, exist_ok=True)

        now = time.time()
        window_seconds = float(self.config.VELOCITY_WINDOW_HOURS) * 3600.0
        data_to_save: dict[str, list[float]] = {}
        for internal_id in list(self.sales.keys()):
            self._prune_sales(internal_id, now, window_seconds)
            if self.sales[internal_id]:
                data_to_save[internal_id] = list(self.sales[internal_id])

        payload = {
            "version": 1,
            "saved_at": now,
            "sales": data_to_save,
        }
        temp_file = target.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        temp_file.replace(target)
        logger.debug("Saved sales velocity cache to %s (%d items)", target, len(data_to_save))

    def load_from_disk(self, file_path: Optional[str | Path] = None) -> int:
        """Load sales timestamps from disk. Returns count of restored items."""
        target = Path(file_path or self.config.VELOCITY_CACHE_FILE or "data/sales_velocity.json")
        if not target.is_file():
            return 0

        with open(target, "r", encoding="utf-8") as f:
            payload = json.load(f)

        sales_data = payload.get("sales", {})
        now = time.time()
        window_seconds = float(self.config.VELOCITY_WINDOW_HOURS) * 3600.0

        restored_items = 0
        for internal_id, timestamps in sales_data.items():
            if not isinstance(timestamps, list):
                continue
            dq = deque(sorted(float(t) for t in timestamps))
            self.sales[internal_id] = dq
            self._prune_sales(internal_id, now, window_seconds)
            if self.sales[internal_id]:
                restored_items += 1

        logger.info("Loaded sales velocity cache from %s (%d active items)", target, restored_items)
        return restored_items
