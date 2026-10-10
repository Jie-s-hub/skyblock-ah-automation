"""Hypixel SkyBlock Bazaar market client and live pricing cache."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import logging
import statistics
import time
from typing import Any, Optional

import aiohttp

from src.config import Settings

logger = logging.getLogger(__name__)


MASTER_STAR_PRODUCT_IDS: list[str] = [
    "FIRST_MASTER_STAR",
    "SECOND_MASTER_STAR",
    "THIRD_MASTER_STAR",
    "FOURTH_MASTER_STAR",
    "FIFTH_MASTER_STAR",
]

GEMSTONE_TIERS: list[str] = [
    "FLAWED",
    "FINE",
    "FLAWLESS",
    "PERFECT",
]

GEMSTONE_TYPES: list[str] = [
    "RUBY",
    "AMETHYST",
    "JASPER",
    "SAPPHIRE",
    "OPAL",
    "JADE",
    "AMBER",
    "TOPAZ",
]

ALL_GEMSTONE_PRODUCT_IDS: list[str] = [
    f"{tier}_{gtype}_GEM"
    for tier in GEMSTONE_TIERS
    for gtype in GEMSTONE_TYPES
]

SECONDARY_ADDON_PRODUCT_IDS: list[str] = [
    "GEMSTONE_CHAMBER",
    "THE_ART_OF_WAR",
    "WOOD_SINGULARITY",
    "TRANSMISSION_TUNER",
]


class BazaarCache:
    """
    Maintains an in-memory price cache of Hypixel Bazaar items with TTL expiration.
    Provides instant-buy and instant-sell pricing for upgrades (recombs, essence, enchants, stones).
    """

    BASE_URL = "https://api.hypixel.net/v2/skyblock/bazaar"

    def __init__(
        self,
        config: Optional[Settings] = None,
        session: Optional[aiohttp.ClientSession] = None,
        cache_ttl: float = 60.0,
    ) -> None:
        self.config = config
        self._session = session
        self._owns_session = session is None
        self.cache_ttl = cache_ttl
        self._last_updated: float = 0.0
        self._buy_prices: dict[str, float] = {}
        self._sell_prices: dict[str, float] = {}
        self._price_history: dict[str, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=10))

    async def __aenter__(self) -> "BazaarCache":
        await self.get_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def get_session(self) -> aiohttp.ClientSession:
        """Retrieve active ClientSession or instantiate one."""
        if self._session is None or getattr(self._session, "closed", False):
            timeout = aiohttp.ClientTimeout(total=20.0, connect=10.0)
            headers = {"User-Agent": "HypixelAHMonitor/1.0"}
            if self.config and self.config.HYPIXEL_API_KEY:
                headers["API-Key"] = self.config.HYPIXEL_API_KEY
            self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close active ClientSession if owned by this instance."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def is_cache_stale(self) -> bool:
        """Check if cached Bazaar data exceeds the TTL threshold."""
        return (time.time() - self._last_updated) > self.cache_ttl

    def get_buy_price(self, product_id: str) -> Optional[float]:
        """Return the instant-buy price for a product from the in-memory cache."""
        return self._buy_prices.get(product_id.strip().upper())

    def get_sell_price(self, product_id: str) -> Optional[float]:
        """Return the instant-sell price for a product from the in-memory cache."""
        return self._sell_prices.get(product_id.strip().upper())

    def get_enchant_price(self, enchant_name: str, level: int) -> int:
        """
        Lookup instant-buy price for an enchanted book at tier N with anvil compounding.
        1. Checks exact tier key (e.g. ENCHANTMENT_GROWTH_6, ENCHANTMENT_ULTIMATE_WISE_5).
        2. If exact tier is not found or <= 0, checks base Tier 1 book (ENCHANTMENT_{NAME}_1)
           and compounds: Book Value = 2^(N - 1) * Price(Tier 1).
        3. If neither exists, safely returns 0 without raising an exception.
        """
        from src.enchant_evaluator import get_enchant_bazaar_price

        return get_enchant_bazaar_price(self, enchant_name, level)

    def get_master_star_price(self, star_index: int) -> int:
        """
        Lookup instant-buy price for a master star by index (1 to 5).
        Star 1 = FIRST_MASTER_STAR, Star 5 = FIFTH_MASTER_STAR.
        Returns 0 if not found.
        """
        if 1 <= star_index <= len(MASTER_STAR_PRODUCT_IDS):
            prod_id = MASTER_STAR_PRODUCT_IDS[star_index - 1]
            price = self.get_buy_price(prod_id)
            if price and price > 0:
                return int(price)
        return 0

    def get_gemstone_price(self, gem_type: str, quality: str) -> int:
        """
        Lookup instant-buy price for a slotted gemstone ({QUALITY}_{TYPE}_GEM).
        Returns 0 if not found.
        """
        clean_type = gem_type.strip().upper()
        clean_quality = quality.strip().upper()
        prod_id = f"{clean_quality}_{clean_type}_GEM"
        price = self.get_buy_price(prod_id)
        if price and price > 0:
            return int(price)
        return 0

    def get_pet_item_price(self, item_id: str) -> int:
        """Lookup instant-buy price for a pet held item from Bazaar cache."""
        clean_id = item_id.strip().upper()
        price = self.get_buy_price(clean_id)
        if price and price > 0:
            return int(price)
        return 0

    def set_mock_prices(
        self,
        buy_prices: dict[str, float | int],
        sell_prices: Optional[dict[str, float | int]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """Populate mock prices for unit testing and record into rolling deque."""
        ts = timestamp if timestamp is not None else time.time()
        self._buy_prices = {k.strip().upper(): float(v) for k, v in buy_prices.items()}
        if sell_prices:
            self._sell_prices = {k.strip().upper(): float(v) for k, v in sell_prices.items()}
        else:
            self._sell_prices = dict(self._buy_prices)
        self._last_updated = ts
        for k, v in self._buy_prices.items():
            self._price_history[k].append((ts, v))

    def record_snapshot(
        self,
        buy_prices: dict[str, float | int],
        timestamp: Optional[float] = None,
    ) -> None:
        """
        Record a discrete instant-buy price snapshot into the 10-snapshot rolling deque.
        """
        ts = timestamp if timestamp is not None else time.time()
        for prod_id, price in buy_prices.items():
            clean_id = prod_id.strip().upper()
            flt_price = float(price)
            if flt_price > 0:
                self._price_history[clean_id].append((ts, flt_price))

    def get_rolling_median_buy_price(
        self,
        product_id: str,
        now: Optional[float] = None,
        max_age_seconds: Optional[float] = None,
    ) -> Optional[float]:
        """
        Compute the median instant-buy price for a product over recent snapshots within window.
        """
        clean_id = product_id.strip().upper()
        history = self._price_history.get(clean_id)
        if not history:
            return None

        current_time = now if now is not None else time.time()
        window = (
            max_age_seconds
            if max_age_seconds is not None
            else (getattr(self.config, "BAZAAR_SHOCK_WINDOW_SECONDS", 300.0) if self.config else 300.0)
        )

        valid_prices = [
            price for ts, price in history
            if (current_time - ts) <= window
        ]
        if not valid_prices:
            valid_prices = [price for _, price in history]

        if not valid_prices:
            return None

        return float(statistics.median(valid_prices))

    def is_price_shocked(
        self,
        product_id: str,
        current_price: Optional[float] = None,
        now: Optional[float] = None,
        threshold_pct: Optional[float] = None,
        min_snapshots: Optional[int] = None,
    ) -> bool:
        """
        Bazaar Shock Guard: Returns True if the current instant-buy price deviates
        by more than threshold_pct (>15%) from its rolling median price.
        """
        clean_id = product_id.strip().upper()
        history = self._price_history.get(clean_id)
        req_snaps = (
            min_snapshots
            if min_snapshots is not None
            else (getattr(self.config, "BAZAAR_SHOCK_MIN_SNAPSHOTS", 3) if self.config else 3)
        )
        if not history or len(history) < req_snaps:
            return False

        if current_price is None:
            current_price = self.get_buy_price(clean_id)

        if current_price is None or current_price <= 0:
            return False

        median_price = self.get_rolling_median_buy_price(clean_id, now=now)
        if median_price is None or median_price <= 0:
            return False

        shock_thresh = (
            threshold_pct
            if threshold_pct is not None
            else (getattr(self.config, "BAZAAR_SHOCK_THRESHOLD_PCT", 15.0) if self.config else 15.0)
        ) / 100.0

        deviation = abs(current_price - median_price) / median_price
        return deviation > shock_thresh

    def is_addon_shocked(self, product_id: str) -> bool:
        """Check if an addon product ID is currently experiencing a Bazaar price shock."""
        return self.is_price_shocked(product_id)

    async def fetch_bazaar_data(self, force: bool = False) -> bool:
        """
        Fetch latest Bazaar product summaries from Hypixel API and update price cache.

        Returns:
            bool: True if cache was updated or still fresh, False if error occurred.
        """
        if not force and not self.is_cache_stale() and self._buy_prices:
            return True

        session = await self.get_session()
        try:
            async with session.get(self.BASE_URL) as response:
                if response.status != 200:
                    logger.warning("Bazaar API returned HTTP %d. Retaining existing cache.", response.status)
                    return False

                data = await response.json()
                if not data.get("success"):
                    logger.warning("Bazaar API responded with success=False.")
                    return False

                products = data.get("products", {})
                new_buy: dict[str, float] = {}
                new_sell: dict[str, float] = {}

                for product_id, prod_data in products.items():
                    quick_status = prod_data.get("quick_status", {})
                    buy_price = quick_status.get("buyPrice")
                    sell_price = quick_status.get("sellPrice")

                    # Fallback to buy_summary / sell_summary if quick_status is 0 or absent
                    if not buy_price or buy_price <= 0:
                        buy_summary = prod_data.get("buy_summary", [])
                        if buy_summary:
                            buy_price = buy_summary[0].get("pricePerUnit", 0.0)

                    if not sell_price or sell_price <= 0:
                        sell_summary = prod_data.get("sell_summary", [])
                        if sell_summary:
                            sell_price = sell_summary[0].get("pricePerUnit", 0.0)

                    if buy_price and buy_price > 0:
                        new_buy[product_id.upper()] = float(buy_price)
                    if sell_price and sell_price > 0:
                        new_sell[product_id.upper()] = float(sell_price)

                self._buy_prices = new_buy
                self._sell_prices = new_sell
                self._last_updated = time.time()
                for k, v in new_buy.items():
                    self._price_history[k].append((self._last_updated, v))

                logger.info("Successfully updated Bazaar cache with %d products.", len(self._buy_prices))
                return True
        except Exception as err:
            logger.error("Failed to query Bazaar API: %s", err)
            return False
