"""Asynchronous Hypixel API client with pagination, concurrency bounding, and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Optional

import aiohttp

from src.config import Settings

logger = logging.getLogger(__name__)


def calculate_next_poll_delay(
    last_updated_ms: int,
    current_time: Optional[float] = None,
    padding: float = 0.5,
) -> float:
    """
    Calculate dynamic sleep duration until the next Hypixel cache refresh.

    Hypixel's /v2/skyblock/auctions endpoint is cached on Cloudflare/CDN edges
    and refreshes roughly every 60 seconds.
    - Target next cycle timestamp: (last_updated_ms / 1000.0) + 60.0
    - Remaining sleep duration: target_refresh - now
    - Adds safety padding (+0.5s) to avoid requesting right before cache expiration.
    - Clamped between [1.0, 60.0] seconds to prevent negative sleeps or runaway stalls.
    """
    if last_updated_ms <= 0:
        return 60.0

    now = current_time if current_time is not None else time.time()
    target_refresh = (last_updated_ms / 1000.0) + 60.0
    delay = (target_refresh - now) + padding
    return max(1.0, min(delay, 60.0))


class HypixelClientError(Exception):
    """Raised when an irrecoverable error occurs while querying the Hypixel API."""


class HypixelClient:
    """Async client for fetching and paginating Hypixel SkyBlock auctions."""

    BASE_URL = "https://api.hypixel.net/v2/skyblock/auctions"
    ENDED_AUCTIONS_URL = "https://api.hypixel.net/v2/skyblock/auctions_ended"

    def __init__(self, config: Settings, session: Optional[aiohttp.ClientSession] = None) -> None:
        self.config = config
        self._session = session
        self._owns_session = session is None
        self.last_updated: Optional[int] = None
        self.last_updated_ms: Optional[int] = None
        self.last_modified_header: Optional[str] = None
        self.date_header: Optional[str] = None
        self.last_auction_start_ms: int = 0
        concurrency = getattr(self.config, "MAX_CONCURRENT_PAGES", 15)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> "HypixelClient":
        await self.get_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def get_session(self) -> aiohttp.ClientSession:
        """Retrieve active ClientSession or instantiate one."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45.0, connect=15.0)
            connector = aiohttp.TCPConnector(limit=15, enable_cleanup_closed=True)
            headers = {"User-Agent": "HypixelAHMonitor/1.0"}
            if self.config.HYPIXEL_API_KEY:
                headers["API-Key"] = self.config.HYPIXEL_API_KEY
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close active ClientSession if owned by this client instance."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def fetch_page(
        self,
        page: int,
        session: aiohttp.ClientSession,
        max_retries: int = 4,
        initial_delay: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 16.0,
    ) -> dict[str, Any]:
        """
        Fetch a single auction page with exponential backoff on 429 and 5xx responses.
        """
        params = {"page": str(page)}
        delay = initial_delay

        for attempt in range(max_retries + 1):
            try:
                async with session.get(self.BASE_URL, params=params) as response:
                    if response.status == 200:
                        self.last_modified_header = response.headers.get("Last-Modified")
                        self.date_header = response.headers.get("Date")
                        return await response.json()

                    if response.status == 429:
                        retry_after_hdr = response.headers.get("Retry-After")
                        if retry_after_hdr:
                            try:
                                wait_time = float(retry_after_hdr) + random.uniform(0.1, 0.5)
                            except ValueError:
                                wait_time = delay + random.uniform(0.1, 0.5)
                        else:
                            wait_time = delay + random.uniform(0.1, 0.5)

                        logger.warning(
                            "Hypixel rate limit (429) on page %d (attempt %d/%d). Sleeping %.2fs...",
                            page,
                            attempt + 1,
                            max_retries,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        delay = min(delay * backoff_multiplier, max_delay)
                        continue

                    if 500 <= response.status < 600:
                        wait_time = delay + random.uniform(0.1, 0.5)
                        logger.warning(
                            "Hypixel server error (%d) on page %d (attempt %d/%d). Sleeping %.2fs...",
                            response.status,
                            page,
                            attempt + 1,
                            max_retries,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        delay = min(delay * backoff_multiplier, max_delay)
                        continue

                    # Unhandled HTTP error status
                    text = await response.text()
                    raise HypixelClientError(f"Unexpected HTTP {response.status} on page {page}: {text[:200]}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt >= max_retries:
                    raise HypixelClientError(
                        f"Network failure on page {page} after {max_retries} retries: {err}"
                    ) from err

                wait_time = delay + random.uniform(0.1, 0.5)
                logger.warning(
                    "Network error on page %d (%s). Retrying in %.2fs (attempt %d/%d)...",
                    page,
                    err,
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                delay = min(delay * backoff_multiplier, max_delay)

        raise HypixelClientError(f"Exceeded max retries ({max_retries}) fetching page {page}")

    async def _fetch_bounded_page(
        self,
        page: int,
        session: aiohttp.ClientSession,
    ) -> dict[str, Any]:
        """Fetch a page guarded by the concurrency-bounding semaphore."""
        async with self._semaphore:
            return await self.fetch_page(page, session)

    async def fetch_all_auctions(self) -> Optional[list[dict[str, Any]]]:
        """
        Fetch all active BIN auctions across all pages.

        Returns:
            list[dict]: Filtered BIN auction objects if new cycle data is available.
            None: If the data has not updated (lastUpdated match) or if a fetch error occurred.
        """
        session = await self.get_session()

        # Step 1: Fetch page 0 to discover total pages & lastUpdated timestamp
        try:
            page_zero = await self.fetch_page(0, session)
        except Exception as err:
            logger.error("Failed to fetch initial page 0: %s", err)
            return None

        total_pages = int(page_zero.get("totalPages", 1))
        last_updated = int(page_zero.get("lastUpdated", 0))

        # Step 2: Skip cycle if auction data has not changed
        if self.last_updated is not None and last_updated == self.last_updated:
            logger.info("Market data unchanged (lastUpdated=%d). Skipping cycle.", last_updated)
            return None

        logger.info(
            "Discovered %d auction pages (lastUpdated=%d). Fetching concurrent pages...",
            total_pages,
            last_updated,
        )

        all_page_results: list[dict[str, Any]] = [page_zero]

        # Step 3: Concurrently fetch pages 1 to total_pages - 1
        if total_pages > 1:
            tasks = [self._fetch_bounded_page(p, session) for p in range(1, total_pages)]
            try:
                subsequent_pages = await asyncio.gather(*tasks)
                all_page_results.extend(subsequent_pages)
            except Exception as err:
                logger.error("Irrecoverable error fetching auction pages: %s. Skipping cycle.", err)
                return None

        # Step 4: Extract and filter Buy-It-Now (BIN) auctions only
        bin_auctions: list[dict[str, Any]] = []
        max_start_ms = self.last_auction_start_ms
        for page_data in all_page_results:
            auctions = page_data.get("auctions", [])
            for auction in auctions:
                start_val = auction.get("start", 0)
                try:
                    start_int = int(start_val)
                    if start_int > max_start_ms:
                        max_start_ms = start_int
                except (ValueError, TypeError):
                    pass

                if auction.get("bin") is True:
                    bin_auctions.append(auction)

        self.last_auction_start_ms = max_start_ms
        self.last_updated = last_updated
        self.last_updated_ms = last_updated
        logger.info(
            "Successfully fetched and parsed %d active BIN auctions across %d pages (watermark=%d).",
            len(bin_auctions),
            total_pages,
            self.last_auction_start_ms,
        )
        return bin_auctions

    def get_watermark(self) -> int:
        """Return the newest auction start timestamp observed across ingested cycles."""
        return self.last_auction_start_ms

    def set_watermark(self, watermark_ms: int) -> None:
        """Explicitly set the cursor pagination watermark."""
        self.last_auction_start_ms = watermark_ms

    async def fetch_ended_auctions(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        max_retries: int = 4,
        initial_delay: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 16.0,
    ) -> list[dict[str, Any]]:
        """
        Fetch recently ended auctions from Hypixel API with retry and exponential backoff.

        Queries https://api.hypixel.net/v2/skyblock/auctions_ended and returns the list of
        ended auction dictionaries.
        """
        active_session = session or await self.get_session()
        delay = initial_delay

        for attempt in range(max_retries + 1):
            try:
                async with active_session.get(self.ENDED_AUCTIONS_URL) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, dict) and data.get("success"):
                            auctions = data.get("auctions", [])
                            if isinstance(auctions, list):
                                return auctions
                        return []

                    if response.status == 429:
                        retry_after_hdr = response.headers.get("Retry-After")
                        if retry_after_hdr:
                            try:
                                wait_time = float(retry_after_hdr) + random.uniform(0.1, 0.5)
                            except ValueError:
                                wait_time = delay + random.uniform(0.1, 0.5)
                        else:
                            wait_time = delay + random.uniform(0.1, 0.5)

                        logger.warning(
                            "Hypixel rate limit (429) fetching ended auctions (attempt %d/%d). Sleeping %.2fs...",
                            attempt + 1,
                            max_retries,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        delay = min(delay * backoff_multiplier, max_delay)
                        continue

                    if 500 <= response.status < 600:
                        wait_time = delay + random.uniform(0.1, 0.5)
                        logger.warning(
                            "Hypixel server error (%d) fetching ended auctions (attempt %d/%d). Sleeping %.2fs...",
                            response.status,
                            attempt + 1,
                            max_retries,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        delay = min(delay * backoff_multiplier, max_delay)
                        continue

                    text = await response.text()
                    raise HypixelClientError(f"Unexpected HTTP {response.status} fetching ended auctions: {text[:200]}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt >= max_retries:
                    raise HypixelClientError(
                        f"Network failure fetching ended auctions after {max_retries} retries: {err}"
                    ) from err

                wait_time = delay + random.uniform(0.1, 0.5)
                logger.warning(
                    "Network error fetching ended auctions (%s). Retrying in %.2fs (attempt %d/%d)...",
                    err,
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                delay = min(delay * backoff_multiplier, max_delay)

        raise HypixelClientError(f"Exceeded max retries ({max_retries}) fetching ended auctions")
