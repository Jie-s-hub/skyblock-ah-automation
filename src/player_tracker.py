"""Automated player portfolio and trade recognition engine for Hypixel SkyBlock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Optional

import aiohttp

from src.config import Settings
from src.constants.taxes import (
    calculate_progressive_ah_tax,
    calculate_progressive_net_profit,
    collection_fee,
    creation_fee,
    net_revenue,
)
from src.db import DatabaseManager, get_db
from src.nbt_decoder import decode_item_bytes

logger = logging.getLogger(__name__)


def normalize_uuid(uuid_str: Optional[str]) -> str:
    """Normalize UUID by removing dashes and lowercasing."""
    if not uuid_str:
        return ""
    return str(uuid_str).replace("-", "").strip().lower()


@dataclass
class CompletedTrade:
    """Data representation of a completed and matched flip trade."""

    auction_uuid: str
    internal_id: str
    item_name: str
    buy_price: int
    sale_price: int
    progressive_tax: int
    net_realized_pnl: int
    roi_pct: float
    buy_timestamp: int
    sell_timestamp: int
    hold_duration_seconds: float
    pnl_per_hour: float

    def format_duration(self) -> str:
        """Format hold duration to 'Xh Ym' string."""
        m, _ = divmod(int(self.hold_duration_seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m"


class PlayerTradeTracker:
    """
    Tracks portfolio performance, buy attribution, and sale matching
    for a target player username (e.g. 'ongvvkk').
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[Settings] = None,
        session: Optional[aiohttp.ClientSession] = None,
        notifier: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.config = config or Settings(DRY_RUN=True)
        self.tracked_ign = self.config.TRACKED_PLAYER_IGN
        self.tracked_uuid: Optional[str] = None
        self._session = session
        self._owns_session = False
        self._uuid_cache: dict[str, str] = {}
        self.notifier = notifier

    def set_tracked_uuid(self, uuid: str) -> None:
        """Explicitly set tracked player UUID (normalized without dashes)."""
        self.tracked_uuid = normalize_uuid(uuid)
        self._uuid_cache[self.tracked_ign.lower()] = self.tracked_uuid

    async def get_session(self) -> aiohttp.ClientSession:
        """Return or create aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close session if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def resolve_identity(self) -> Optional[str]:
        """
        Query the official Mojang API to resolve and cache player username to UUID.
        URL: https://api.mojang.com/users/by/username/{username}
        """
        ign_clean = self.tracked_ign.strip().lower()
        if ign_clean in self._uuid_cache:
            self.tracked_uuid = self._uuid_cache[ign_clean]
            return self.tracked_uuid

        url = f"https://api.mojang.com/users/by/username/{self.tracked_ign}"
        try:
            session = await self.get_session()
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw_id = data.get("id")
                    if raw_id:
                        self.tracked_uuid = normalize_uuid(raw_id)
                        self._uuid_cache[ign_clean] = self.tracked_uuid
                        logger.info("Resolved player '%s' to UUID: %s", self.tracked_ign, self.tracked_uuid)
                        return self.tracked_uuid
                else:
                    logger.warning("Mojang API returned HTTP %d for user '%s'", resp.status, self.tracked_ign)
        except Exception as err:
            logger.warning("Failed to resolve Mojang UUID for '%s': %s", self.tracked_ign, err)

        return self.tracked_uuid

    def process_ended_auctions(self, raw_auctions: list[dict[str, Any]]) -> list[CompletedTrade]:
        """
        Inspect ended auctions batch from /v2/skyblock/auctions_ended.
        - Identifies buys where buyer == tracked_uuid -> logs to purchases as 'HELD'.
        - Identifies sales where seller == tracked_uuid -> matches with 'HELD' purchases,
          computes progressive tax and net PnL, updates position to 'SOLD', and returns CompletedTrade events.
        """
        if not self.config.ENABLE_PLAYER_TRACKING or not self.tracked_uuid:
            return []

        completed_trades: list[CompletedTrade] = []
        target_uuid = self.tracked_uuid

        for auction in raw_auctions:
            if not isinstance(auction, dict):
                continue

            buyer_raw = auction.get("buyer")
            seller_raw = auction.get("seller")
            buyer_norm = normalize_uuid(buyer_raw)
            seller_norm = normalize_uuid(seller_raw)

            auction_id = str(auction.get("auction_id") or auction.get("uuid") or "").strip()
            price = int(auction.get("price") or auction.get("starting_bid") or 0)
            raw_ts = auction.get("timestamp")
            timestamp = int(float(raw_ts)) if raw_ts is not None else int(time.time() * 1000)

            item_bytes = auction.get("item_bytes")
            decoded: dict[str, Any] = {}
            if item_bytes:
                decoded = decode_item_bytes(item_bytes)
            elif "decoded" in auction:
                decoded = auction["decoded"]

            internal_id = decoded.get("internal_id") or auction.get("internal_id") or "UNKNOWN"
            item_name = decoded.get("item_name") or auction.get("item_name") or internal_id
            item_uuid = decoded.get("item_uuid") or auction.get("item_uuid")

            # ---------------------------------------------------------------
            # 1. Buy Attribution: buyer == tracked_uuid
            # ---------------------------------------------------------------
            if buyer_norm and buyer_norm == target_uuid:
                logger.info(
                    "Detected BUY for tracked player %s (%s): %s for %d coins",
                    self.tracked_ign,
                    target_uuid,
                    internal_id,
                    price,
                )
                self.db.record_purchase(
                    auction_uuid=auction_id,
                    internal_id=internal_id,
                    item_uuid=item_uuid,
                    buy_price=price,
                    buy_timestamp=timestamp,
                    status="HELD",
                    features_json=decoded,
                    notes=f"Auto-attributed buy for {self.tracked_ign}",
                )

            # ---------------------------------------------------------------
            # 2. Sale Attribution: seller == tracked_uuid
            # ---------------------------------------------------------------
            if seller_norm and seller_norm == target_uuid:
                logger.info(
                    "Detected SALE for tracked player %s (%s): %s for %d coins",
                    self.tracked_ign,
                    target_uuid,
                    internal_id,
                    price,
                )
                # Locate matching open position in purchases
                open_positions = self.db.get_purchases(status="HELD", limit=500)
                matched_purchase: Optional[dict[str, Any]] = None

                # Match Strategy 1: Exact NBT item_uuid match
                if item_uuid:
                    for pos in open_positions:
                        if pos.get("item_uuid") and pos["item_uuid"] == item_uuid:
                            matched_purchase = pos
                            break

                # Match Strategy 2: FIFO match by identical internal_id (oldest buy first)
                if not matched_purchase:
                    candidates = [p for p in open_positions if p.get("internal_id") == internal_id]
                    if candidates:
                        # Oldest buy timestamp first
                        candidates.sort(key=lambda p: int(p.get("buy_timestamp", 0)))
                        matched_purchase = candidates[0]

                if matched_purchase:
                    buy_price = int(matched_purchase["buy_price"])
                    buy_ts = int(matched_purchase.get("buy_timestamp", timestamp))
                    is_derpy = bool(matched_purchase.get("derpy_active", 0))
                    tax = creation_fee(price) + collection_fee(price, derpy_active=is_derpy)
                    net_pnl = net_revenue(price, derpy_active=is_derpy) - buy_price
                    roi_pct = (net_pnl / buy_price) * 100.0 if buy_price > 0 else 0.0
                    hold_sec = max(0.0, (timestamp - buy_ts) / 1000.0)
                    hours = hold_sec / 3600.0
                    pnl_per_hour = (net_pnl / hours) if hours > 0.05 else float(net_pnl)

                    # Update purchase record in DB
                    self.db.update_purchase_sale(
                        auction_uuid=matched_purchase["auction_uuid"],
                        realized_sell_price=price,
                        realized_sell_timestamp=timestamp,
                        net_realized_pnl=net_pnl,
                        status="SOLD",
                        notes=f"Auto-paired flip for {self.tracked_ign}",
                        derpy_active=1 if is_derpy else 0,
                        fee_model_version=2,
                    )

                    trade = CompletedTrade(
                        auction_uuid=matched_purchase["auction_uuid"],
                        internal_id=internal_id,
                        item_name=item_name,
                        buy_price=buy_price,
                        sale_price=price,
                        progressive_tax=tax,
                        net_realized_pnl=net_pnl,
                        roi_pct=round(roi_pct, 2),
                        buy_timestamp=buy_ts,
                        sell_timestamp=timestamp,
                        hold_duration_seconds=hold_sec,
                        pnl_per_hour=round(pnl_per_hour, 2),
                    )
                    completed_trades.append(trade)
                    logger.info(
                        "Successfully paired flip for %s: %s | PnL: +%d (%0.2f%%)",
                        self.tracked_ign,
                        item_name,
                        net_pnl,
                        roi_pct,
                    )
                else:
                    logger.warning(
                        "Sale by %s of %s (%d coins) had no matching HELD position in ledger",
                        self.tracked_ign,
                        internal_id,
                        price,
                    )

        return completed_trades

    def get_portfolio_metrics(self) -> dict[str, Any]:
        """
        Aggregate portfolio metrics for the tracked player:
        - Realized PnL: Daily (last 24h), Weekly (last 7d), All-Time
        - Unrealized inventory worth (held items, total invested capital)
        - Win rate, average profit per trade, average capital lock time
        """
        now_ms = int(time.time() * 1000)
        ms_in_day = 24 * 3600 * 1000
        ms_in_week = 7 * ms_in_day

        sold_trades = self.db.get_purchases(status="SOLD", limit=2000)
        held_items = self.db.get_purchases(status="HELD", limit=500)

        daily_pnl = 0
        weekly_pnl = 0
        all_time_pnl = 0
        win_count = 0
        total_hold_sec = 0.0

        for trade in sold_trades:
            pnl = int(trade.get("net_realized_pnl", 0))
            sell_ts = int(trade.get("realized_sell_timestamp", 0))
            buy_ts = int(trade.get("buy_timestamp", sell_ts))
            hold_sec = max(0.0, (sell_ts - buy_ts) / 1000.0)

            all_time_pnl += pnl
            total_hold_sec += hold_sec
            if pnl > 0:
                win_count += 1

            if sell_ts >= (now_ms - ms_in_day):
                daily_pnl += pnl
            if sell_ts >= (now_ms - ms_in_week):
                weekly_pnl += pnl

        n_trades = len(sold_trades)
        win_rate = (win_count / n_trades * 100.0) if n_trades > 0 else 0.0
        avg_profit = (all_time_pnl // n_trades) if n_trades > 0 else 0
        avg_hold_sec = (total_hold_sec / n_trades) if n_trades > 0 else 0.0

        unrealized_capital = sum(int(h.get("buy_price", 0)) for h in held_items)

        return {
            "player_ign": self.tracked_ign,
            "player_uuid": self.tracked_uuid or "Unresolved",
            "total_trades": n_trades,
            "win_rate_pct": round(win_rate, 2),
            "realized_pnl_daily": daily_pnl,
            "realized_pnl_weekly": weekly_pnl,
            "realized_pnl_all_time": all_time_pnl,
            "avg_profit_per_trade": avg_profit,
            "avg_hold_duration_sec": avg_hold_sec,
            "held_items_count": len(held_items),
            "unrealized_capital_deployed": unrealized_capital,
        }


# Convenience alias for tracker
PlayerTracker = PlayerTradeTracker
