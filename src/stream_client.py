"""WebSocket auction stream ingestion client and payload normalizer."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import json
import logging
from typing import Any, AsyncIterator, Optional, Union

import random
import aiohttp

from src.config import Settings

logger = logging.getLogger(__name__)


def normalize_auction_payload(raw: Union[dict[str, Any], str]) -> Optional[dict[str, Any]]:
    """
    Normalize raw incoming auction feed messages (such as Coflnet modsocket JSON)
    into the standard Hypixel Auction House schema:
    - uuid: str
    - item_bytes: str
    - starting_bid: int
    - bin: bool
    - start: int
    - end: int
    - item_name: str
    """
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception:
            return None
    elif isinstance(raw, dict):
        payload = raw
    else:
        return None

    # Handle wrapper formats: {"type": "auction", "data": {...}}
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]
    else:
        data = payload

    if not isinstance(data, dict):
        return None

    # Skip non-auction messages (e.g. pings, subscriptions, handshakes)
    msg_type = str(payload.get("type", "")).lower()
    if msg_type in ("ping", "pong", "handshake", "subscribed", "info"):
        return None

    uuid_val = data.get("uuid") or data.get("id") or data.get("auction_id")
    if not uuid_val:
        return None

    item_bytes = (
        data.get("item_bytes")
        or data.get("itemBytes")
        or data.get("bytes")
        or data.get("nbt")
        or ""
    )
    if isinstance(item_bytes, dict):
        item_bytes = item_bytes.get("data", "")

    # Normalize starting bid / price
    price_val = data.get("starting_bid")
    if price_val is None:
        price_val = data.get("startingBid")
    if price_val is None:
        price_val = data.get("price")
    if price_val is None:
        price_val = data.get("highestBid")

    try:
        starting_bid = int(price_val or 0)
    except (ValueError, TypeError):
        starting_bid = 0

    # BIN flag
    bin_val = data.get("bin")
    if bin_val is None:
        bin_val = data.get("isBin", True)
    bin_flag = bool(bin_val)

    # Timestamps
    start_ts = data.get("start") or data.get("timestamp") or data.get("startTime") or 0
    end_ts = data.get("end") or data.get("endTime") or 0
    try:
        start_int = int(start_ts)
    except (ValueError, TypeError):
        start_int = 0
    try:
        end_int = int(end_ts)
    except (ValueError, TypeError):
        end_int = 0

    item_name = data.get("item_name") or data.get("itemName") or data.get("name") or "Unknown Item"

    return {
        "uuid": str(uuid_val),
        "item_bytes": str(item_bytes),
        "starting_bid": starting_bid,
        "bin": bin_flag,
        "start": start_int,
        "end": end_int,
        "item_name": str(item_name),
    }


class AuctionFeedClient(ABC):
    """Abstract base class for streaming auction feeds."""

    @abstractmethod
    async def stream_auctions(self) -> AsyncIterator[list[dict[str, Any]]]:
        """Stream batches of normalized auctions continuously."""
        pass

    async def close(self) -> None:
        """Clean up network sessions or streams."""
        pass


class WebSocketFeedClient(AuctionFeedClient):
    """
    Ingests live auction events over a WebSocket connection (e.g. Coflnet modsocket),
    normalizing payloads into the standard Hypixel auction dict schema.
    """

    def __init__(
        self,
        config: Settings,
        ws_url: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None,
        reconnect_delay: float = 3.0,
        backoff_multiplier: float = 2.0,
        max_reconnect_delay: float = 32.0,
    ) -> None:
        self.config = config
        self.ws_url = ws_url or config.COFLNET_WS_URL
        self._session = session
        self._owns_session = session is None
        self.reconnect_delay = reconnect_delay
        self.initial_reconnect_delay = reconnect_delay
        self.backoff_multiplier = backoff_multiplier
        self.max_reconnect_delay = max_reconnect_delay
        self._running = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        self._running = False
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def stream_auctions(self) -> AsyncIterator[list[dict[str, Any]]]:
        """
        Continuously stream and yield lists of normalized auctions from the WebSocket feed.
        Automatically reconnects upon transient connection drops with exponential backoff and jitter.
        """
        self._running = True
        session = await self._get_session()
        current_delay = self.initial_reconnect_delay

        while self._running:
            try:
                logger.info("Connecting to auction WebSocket: %s", self.ws_url)
                async with session.ws_connect(self.ws_url) as ws:
                    logger.info("Connected to auction WebSocket.")
                    # Reset backoff upon successful connection
                    current_delay = self.initial_reconnect_delay
                    async for msg in ws:
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            normalized = normalize_auction_payload(msg.data)
                            if normalized:
                                # Connection is active and yielding valid messages
                                current_delay = self.initial_reconnect_delay
                                yield [normalized]
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("WebSocket closed or encountered error: %s", msg.data)
                            break
                    if not self._running:
                        break
                    # If socket closed normally and loop is still running, backoff before reconnecting
                    jitter = random.uniform(0.1, 0.5)
                    wait_time = min(current_delay + jitter, self.max_reconnect_delay)
                    logger.info("WebSocket disconnected. Reconnecting in %.2fs...", wait_time)
                    await asyncio.sleep(wait_time)
                    current_delay = min(current_delay * self.backoff_multiplier, self.max_reconnect_delay)
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as err:
                jitter = random.uniform(0.1, 0.5)
                wait_time = min(current_delay + jitter, self.max_reconnect_delay)
                logger.warning(
                    "WebSocket connection dropped: %s. Reconnecting in %.2fs (base=%.1fs)...",
                    err,
                    wait_time,
                    current_delay,
                )
                if not self._running:
                    break
                await asyncio.sleep(wait_time)
                current_delay = min(current_delay * self.backoff_multiplier, self.max_reconnect_delay)
