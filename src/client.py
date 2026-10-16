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


def safe_int(val: Any, default: int = 0) -> int:
    """Safely convert a value to int with fallback default on None or conversion error."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


REQUIRED_AUCTION_FIELDS = (
    "uuid",
    "auctioneer",
    "profile_id",
    "coop",
    "start",
    "end",
    "bin",
    "starting_bid",
    "highest_bid_amount",
    "claimed",
    "claimed_bidders",
    "tier",
    "item_name",
    "category",
    "item_bytes",
)


def is_dead_listing(auction: dict[str, Any], now_ms: float) -> bool:
    """
    Early Dead-Listing Filter:
    Drop listings that are not BIN, already claimed, have bids/claimed bidders,
    or have already expired past the current timestamp.
    """
    if not auction.get("bin", False):
        return True
    if auction.get("claimed", False):
        return True
    if int(auction.get("highest_bid_amount", 0) or 0) > 0:
        return True
    claimed_bidders = auction.get("claimed_bidders")
    if claimed_bidders and len(claimed_bidders) > 0:
        return True
    end_ms = auction.get("end", 0)
    if end_ms and int(end_ms) <= now_ms:
        return True
    return False


def prune_auction_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract only required fields without allocating unused nested bid arrays."""
    return {k: raw[k] for k in REQUIRED_AUCTION_FIELDS if k in raw}


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
        self.stale_held_uuids: set[str] = set()
        self.page_uuids_by_page: dict[int, set[str]] = {}
        concurrency = getattr(self.config, "MAX_CONCURRENT_PAGES", 80)
        self._semaphore = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> "HypixelClient":
        await self.get_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def get_session(self) -> aiohttp.ClientSession:
        """Retrieve active ClientSession or instantiate one."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45.0, connect=15.0, sock_read=20.0)
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=100,
                keepalive_timeout=75.0,
                ttl_dns_cache=300,
            )
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

    async def close_idle_connections(self) -> None:
        """
        Close stale/idle keep-alive connections after long inter-cycle sleep.
        Guarantees next cycle starts with clean, pristine TCP/TLS connections.
        """
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def keep_connection_warm(self) -> bool:
        """
        Send a lightweight HEAD request to keep the TCP/TLS connection alive and hot.
        Avoids connection termination from Cloudflare idle timeouts while preventing cold handshakes.
        """
        try:
            session = await self.get_session()
            async with session.head(
                self.BASE_URL,
                params={"page": "0"},
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as r:
                if r.status == 200:
                    last_mod = r.headers.get("Last-Modified")
                    if last_mod:
                        self.last_modified_header = last_mod
                    return True
                return False
        except Exception as e:
            logger.debug("Failed keepalive probe: %s. Re-instantiating session.", e)
            await self.close_idle_connections()
            return False

    async def probe_cache_freshness(
        self,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> tuple[bool, Optional[str], int]:
        """
        Lightweight ~25ms HEAD request to detect Cloudflare edge cache updates with 0 bytes body.
        Returns:
            (is_fresh, last_modified_str, age_seconds)
        """
        active_session = session or await self.get_session()
        try:
            async with active_session.head(
                self.BASE_URL,
                params={"page": "0"},
                timeout=aiohttp.ClientTimeout(total=4.0),
            ) as resp:
                if resp.status == 200:
                    last_mod = resp.headers.get("Last-Modified")
                    age_str = resp.headers.get("Age", "0")
                    try:
                        age = int(age_str)
                    except (ValueError, TypeError):
                        age = 0

                    is_fresh = False
                    if self.last_modified_header is not None and last_mod is not None:
                        if last_mod != self.last_modified_header:
                            is_fresh = True
                    elif last_mod is not None:
                        is_fresh = True

                    return is_fresh, last_mod, age
        except Exception as err:
            logger.debug("HEAD cache probe encountered error: %s", err)
        return False, None, 0

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
                    cf_cache = response.headers.get("CF-Cache-Status", "UNKNOWN")
                    rl_remaining = response.headers.get("RateLimit-Remaining")
                    if rl_remaining is not None:
                        try:
                            rl_int = int(rl_remaining)
                            if rl_int < 10:
                                logger.warning("Hypixel API RateLimit-Remaining critically low (%d) on page %d", rl_int, page)
                        except (ValueError, TypeError):
                            pass
                    logger.debug("Page %d response: HTTP %d | CF-Cache-Status=%s | RateLimit-Remaining=%s", page, response.status, cf_cache, rl_remaining)

                    if response.status == 200:
                        self.last_modified_header = response.headers.get("Last-Modified")
                        self.date_header = response.headers.get("Date")
                        try:
                            import orjson

                            raw_bytes = await response.read()
                            data = orjson.loads(raw_bytes)
                        except Exception:
                            data = await response.json()

                        if isinstance(data, dict) and "auctions" in data:
                            now_ms = time.time() * 1000.0
                            raw_auctions = data["auctions"]
                            page_uuids: set[str] = set()
                            filtered_bin: list[dict[str, Any]] = []
                            p_max_start = 0
                            for a in raw_auctions:
                                uuid_str = str(a.get("uuid", "")).strip()
                                if uuid_str:
                                    page_uuids.add(uuid_str)
                                s_val = a.get("start", 0)
                                try:
                                    s_int = int(s_val)
                                    if s_int > p_max_start:
                                        p_max_start = s_int
                                except (ValueError, TypeError):
                                    pass
                                if is_dead_listing(a, now_ms):
                                    continue
                                filtered_bin.append(prune_auction_fields(a))
                            data["page_uuids"] = page_uuids
                            data["bin_auctions"] = filtered_bin
                            data["max_start_ms"] = p_max_start

                        return data

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
                    if response.status == 404 and page > 0:
                        logger.warning(
                            "Page %d returned 404 (total pages contracted during fetch). Returning empty page.",
                            page,
                        )
                        return {
                            "success": True,
                            "page": page,
                            "totalPages": page,
                            "totalAuctions": 0,
                            "lastUpdated": self.last_updated_ms or 0,
                            "auctions": [],
                            "bin_auctions": [],
                            "page_uuids": set(),
                            "max_start_ms": 0,
                        }

                    # Unhandled HTTP error status
                    text = await response.text()
                    raise HypixelClientError(f"Unexpected HTTP {response.status} on page {page}: {text[:200]}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt >= max_retries:
                    raise HypixelClientError(
                        f"Network failure on page {page} after {max_retries} retries: {err}"
                    ) from err

                is_stale_socket = (
                    isinstance(err, (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError))
                    or "TransferEncodingError" in str(err)
                )
                if is_stale_socket and attempt == 0:
                    wait_time = 0.05
                    logger.debug("Stale keep-alive socket detected on page %d (%s). Fast-retrying in 50ms...", page, err)
                else:
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

        total_pages = safe_int(page_zero.get("totalPages"), 1)
        last_updated = safe_int(page_zero.get("lastUpdated"), 0)

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
        page_indices = list(range(1, total_pages))
        if page_indices:
            tasks = [self._fetch_bounded_page(p, session) for p in page_indices]
            try:
                subsequent_pages = await asyncio.gather(*tasks)
            except Exception as err:
                logger.error("Irrecoverable error fetching auction pages: %s. Skipping cycle.", err)
                return None

            # Phase 1.3: Cloudflare Edge Cache Skew Recovery
            page_results_by_idx: dict[int, dict[str, Any]] = {0: page_zero}
            for p_idx, p_data in zip(page_indices, subsequent_pages):
                page_results_by_idx[p_idx] = p_data

            stale_pages = [
                p_idx for p_idx, p_data in page_results_by_idx.items()
                if isinstance(p_data, dict) and safe_int(p_data.get("lastUpdated"), 0) < last_updated
            ]

            if stale_pages:
                logger.info(
                    "Cloudflare edge cache skew detected on %d pages (%s < %d). Micro-retrying in 25ms...",
                    len(stale_pages),
                    [page_results_by_idx[p].get("lastUpdated") for p in stale_pages[:5]],
                    last_updated,
                )
                # Micro-retry 1 (+25ms)
                await asyncio.sleep(0.025)
                retry_tasks = [self._fetch_bounded_page(p, session) for p in stale_pages]
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                remaining_stale = []
                for p_idx, res in zip(stale_pages, retry_results):
                    if isinstance(res, dict):
                        page_results_by_idx[p_idx] = res
                        if safe_int(res.get("lastUpdated"), 0) < last_updated:
                            remaining_stale.append(p_idx)
                    else:
                        remaining_stale.append(p_idx)

                # Micro-retry 2 (+35ms, total 60ms)
                if remaining_stale:
                    logger.info("Micro-retrying %d stubborn stale pages in 35ms...", len(remaining_stale))
                    await asyncio.sleep(0.035)
                    retry2_tasks = [self._fetch_bounded_page(p, session) for p in remaining_stale]
                    retry2_results = await asyncio.gather(*retry2_tasks, return_exceptions=True)
                    unresolved_stale = []
                    for p_idx, res in zip(remaining_stale, retry2_results):
                        if isinstance(res, dict):
                            page_results_by_idx[p_idx] = res
                            if safe_int(res.get("lastUpdated"), 0) < last_updated:
                                unresolved_stale.append(p_idx)
                        else:
                            unresolved_stale.append(p_idx)
                    stale_pages = unresolved_stale
                else:
                    stale_pages = []

            # Hold UUIDs of pages that remained stale so they are not falsely pruned
            held_uuids: set[str] = set()
            for p_idx in stale_pages:
                held_uuids.update(self.page_uuids_by_page.get(p_idx, set()))
            self.stale_held_uuids = held_uuids

            all_page_results = [page_results_by_idx[p] for p in range(total_pages) if p in page_results_by_idx]

        # Step 4: Extract and filter Buy-It-Now (BIN) auctions with Early Dead-Listing Filter
        bin_auctions: list[dict[str, Any]] = []
        max_start_ms = self.last_auction_start_ms
        now_ms = time.time() * 1000.0
        new_page_uuids_by_page: dict[int, set[str]] = {}

        for p_idx, page_data in enumerate(all_page_results):
            if "bin_auctions" in page_data:
                new_page_uuids_by_page[p_idx] = page_data.get("page_uuids", set())
                p_max = page_data.get("max_start_ms", 0)
                if p_max > max_start_ms:
                    max_start_ms = p_max
                bin_auctions.extend(page_data.get("bin_auctions", []))
            else:
                auctions = page_data.get("auctions", [])
                fallback_uuids: set[str] = set()
                for auction in auctions:
                    uuid_str = str(auction.get("uuid", "")).strip()
                    if uuid_str:
                        fallback_uuids.add(uuid_str)

                    start_val = auction.get("start", 0)
                    try:
                        start_int = int(start_val)
                        if start_int > max_start_ms:
                            max_start_ms = start_int
                    except (ValueError, TypeError):
                        pass

                    if is_dead_listing(auction, now_ms):
                        continue

                    bin_auctions.append(prune_auction_fields(auction))
                new_page_uuids_by_page[p_idx] = fallback_uuids

        self.page_uuids_by_page = new_page_uuids_by_page
        self.last_auction_start_ms = max_start_ms
        self.last_updated = last_updated
        self.last_updated_ms = last_updated
        logger.info(
            "Successfully fetched and parsed %d active BIN auctions across %d pages (watermark=%d, held_stale=%d).",
            len(bin_auctions),
            total_pages,
            self.last_auction_start_ms,
            len(self.stale_held_uuids),
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
