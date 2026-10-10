"""Asynchronous and thread-safe SQLite persistence engine for market intelligence."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
-- 1. Generated Arbitrage Alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_uuid TEXT NOT NULL UNIQUE,
    timestamp INTEGER NOT NULL,
    internal_id TEXT NOT NULL,
    buy_price INTEGER NOT NULL,
    estimated_resell INTEGER NOT NULL,
    projected_profit INTEGER NOT NULL,
    margin_pct REAL NOT NULL,
    confidence_label TEXT NOT NULL,
    valuation_path TEXT NOT NULL,
    features_json TEXT NOT NULL,
    resolved_status TEXT DEFAULT 'PENDING',
    survival_seconds INTEGER DEFAULT 0,
    survival_duration_seconds REAL DEFAULT 0.0,
    last_checked INTEGER DEFAULT 0,
    last_checked_timestamp INTEGER DEFAULT 0,
    derpy_active INTEGER DEFAULT 0,
    fee_model_version INTEGER DEFAULT 2,
    trace_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_item_time ON alerts(internal_id, timestamp);

-- 2. Player Portfolio & Trade Ledger
CREATE TABLE IF NOT EXISTS purchases (
    auction_uuid TEXT PRIMARY KEY,
    internal_id TEXT NOT NULL,
    item_uuid TEXT,
    buy_price INTEGER NOT NULL,
    buy_timestamp INTEGER NOT NULL,
    realized_sell_price INTEGER,
    realized_sell_timestamp INTEGER,
    net_realized_pnl INTEGER,
    status TEXT NOT NULL,
    notes TEXT,
    features_json TEXT,
    derpy_active INTEGER DEFAULT 0,
    fee_model_version INTEGER DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_purchases_status ON purchases(status);
CREATE INDEX IF NOT EXISTS idx_purchases_item_uuid ON purchases(item_uuid);

-- 3. Completed Auction House Sales History
CREATE TABLE IF NOT EXISTS sales_history (
    uuid TEXT PRIMARY KEY,
    internal_id TEXT NOT NULL,
    price INTEGER NOT NULL,
    buyer_uuid TEXT,
    seller_uuid TEXT,
    timestamp INTEGER NOT NULL,
    features_json TEXT NOT NULL,
    features_hash TEXT NOT NULL,
    normalized_tier INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sales_item_time ON sales_history(internal_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_buyer_time ON sales_history(buyer_uuid, timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_seller_time ON sales_history(seller_uuid, timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_hash ON sales_history(features_hash);

-- 4. Bazaar Rolling Price Snapshots
CREATE TABLE IF NOT EXISTS bazaar_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    buy_price REAL NOT NULL,
    sell_price REAL NOT NULL,
    buy_volume INTEGER NOT NULL DEFAULT 0,
    sell_volume INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bazaar_prod_time ON bazaar_snapshots(product_id, timestamp);

-- 5. Pre-Aggregated Sold-Comps Statistics
CREATE TABLE IF NOT EXISTS comps_cache (
    internal_id TEXT NOT NULL,
    features_hash TEXT NOT NULL,
    comp_count INTEGER NOT NULL,
    median_price INTEGER NOT NULL,
    iqr_spread INTEGER NOT NULL,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (internal_id, features_hash)
);

-- 6. Empirical Bayes Retention Parameters
CREATE TABLE IF NOT EXISTS retention_params (
    category TEXT PRIMARY KEY,
    sample_size INTEGER NOT NULL,
    fitted_retention REAL NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


class DatabaseManager:
    """Thread-safe, WAL-enabled SQLite persistence manager."""

    def __init__(self, db_path: str = "data/market_intelligence.db") -> None:
        self.db_path = str(db_path)
        self._init_dir()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self.init_db()

    def _init_dir(self) -> None:
        """Ensure the parent directory for the SQLite file exists."""
        p = Path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """Open or return existing SQLite connection with WAL mode."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for high concurrency
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
        return self._conn

    def init_db(self) -> None:
        """Create tables and indexes if they do not already exist."""
        conn = self._get_connection()
        with conn:
            conn.executescript(SCHEMA_SQL)
            # Lightweight schema migration for existing databases
            for col_name, col_type in [
                ("resolved_status", "TEXT DEFAULT 'PENDING'"),
                ("survival_seconds", "INTEGER DEFAULT 0"),
                ("survival_duration_seconds", "REAL DEFAULT 0.0"),
                ("last_checked", "INTEGER DEFAULT 0"),
                ("last_checked_timestamp", "INTEGER DEFAULT 0"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE alerts ADD COLUMN {col_name} {col_type};")
                except sqlite3.OperationalError:
                    pass

            # Phase 1 Migration: Add derpy_active and fee_model_version (old rows keep version 1)
            cur = conn.execute("PRAGMA user_version;")
            ver_row = cur.fetchone()
            curr_version = ver_row[0] if ver_row else 0
            if curr_version < 2:
                for tbl in ("alerts", "purchases"):
                    for col_name, col_type in [
                        ("derpy_active", "INTEGER DEFAULT 0"),
                        ("fee_model_version", "INTEGER DEFAULT 1"),
                    ]:
                        try:
                            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_name} {col_type};")
                        except sqlite3.OperationalError:
                            pass
                conn.execute("PRAGMA user_version = 2;")

            # Phase 3 Migration: Add trace_json column to alerts table
            if curr_version < 3:
                try:
                    conn.execute("ALTER TABLE alerts ADD COLUMN trace_json TEXT;")
                except sqlite3.OperationalError:
                    pass
                conn.execute("PRAGMA user_version = 3;")
        logger.debug("Database initialized with WAL mode at: %s", self.db_path)

    def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -----------------------------------------------------------------------
    # 1. Alerts Table Operations
    # -----------------------------------------------------------------------

    def log_alert(
        self,
        auction_uuid: str,
        internal_id: str,
        buy_price: int,
        estimated_resell: Optional[int] = None,
        projected_profit: Optional[int] = None,
        margin_pct: float = 0.0,
        confidence_label: str = "MEDIUM",
        valuation_path: str = "PATH_A",
        features_json: Optional[str | dict[str, Any]] = None,
        timestamp: Optional[int] = None,
        *,
        target_resell_price: Optional[int] = None,
        net_profit: Optional[int] = None,
        derpy_active: int = 0,
        fee_model_version: int = 2,
        trace_json: Optional[str | dict[str, Any]] = None,
    ) -> bool:
        """Insert or ignore an arbitrage alert."""
        conn = self._get_connection()
        ts = timestamp if timestamp is not None else int(time.time() * 1000)
        resell_val = estimated_resell if estimated_resell is not None else target_resell_price
        profit_val = projected_profit if projected_profit is not None else net_profit
        if resell_val is None:
            resell_val = 0
        if profit_val is None:
            profit_val = 0
        feat_str = (
            features_json
            if isinstance(features_json, str)
            else json.dumps(features_json or {}, separators=(",", ":"))
        )
        trace_str = (
            trace_json
            if isinstance(trace_json, str)
            else json.dumps(trace_json, separators=(",", ":")) if trace_json is not None
            else None
        )

        query = """
        INSERT OR IGNORE INTO alerts (
            auction_uuid, timestamp, internal_id, buy_price,
            estimated_resell, projected_profit, margin_pct,
            confidence_label, valuation_path, features_json,
            derpy_active, fee_model_version, trace_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with conn:
                cur = conn.execute(
                    query,
                    (
                        auction_uuid,
                        ts,
                        internal_id,
                        int(buy_price),
                        int(resell_val),
                        int(profit_val),
                        float(margin_pct),
                        confidence_label,
                        valuation_path,
                        feat_str,
                        int(derpy_active),
                        int(fee_model_version),
                        trace_str,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("Failed to log alert %s: %s", auction_uuid, e)
            return False

    async def async_log_alert(self, **kwargs: Any) -> bool:
        """Async wrapper for log_alert."""
        return await asyncio.to_thread(self.log_alert, **kwargs)

    def get_alert(self, identifier: str | int) -> Optional[dict[str, Any]]:
        """Retrieve alert by auction UUID or integer ID."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM alerts WHERE auction_uuid = ? OR id = ?", (str(identifier), str(identifier)))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_total_sales_count(self) -> int:
        """Return total number of recorded sales in sales_history."""
        conn = self._get_connection()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM sales_history;")
            row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as err:
            logger.debug("Failed to count sales_history: %s", err)
            return 0

    def get_alerts(
        self,
        internal_id: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query historical alerts."""
        conn = self._get_connection()
        clauses = []
        params: list[Any] = []

        if internal_id:
            clauses.append("internal_id = ?")
            params.append(internal_id)
        if start_time is not None:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("timestamp <= ?")
            params.append(end_time)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"SELECT * FROM alerts {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(query, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def update_alert_survival(
        self,
        auction_uuid: str,
        status: str,
        duration_seconds: float,
        checked_timestamp: Optional[int] = None,
    ) -> bool:
        """Update market survival resolution status for an alert."""
        conn = self._get_connection()
        ts = checked_timestamp if checked_timestamp is not None else int(time.time() * 1000)
        query = """
        UPDATE alerts
        SET resolved_status = ?,
            survival_duration_seconds = ?,
            survival_seconds = ?,
            last_checked_timestamp = ?,
            last_checked = ?
        WHERE auction_uuid = ?
        """
        try:
            with conn:
                cur = conn.execute(
                    query,
                    (
                        status,
                        float(duration_seconds),
                        int(round(duration_seconds)),
                        ts,
                        ts,
                        auction_uuid,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("Failed to update alert survival for %s: %s", auction_uuid, e)
            return False

    async def async_update_alert_survival(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.update_alert_survival, **kwargs)

    def get_pending_alerts(self) -> list[dict[str, Any]]:
        """Retrieve alerts that are currently PENDING market validation."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM alerts WHERE resolved_status = 'PENDING' ORDER BY timestamp ASC")
        return [dict(r) for r in cur.fetchall()]

    async def async_get_pending_alerts(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.get_pending_alerts)

    def get_lane_conversion_stats(
        self,
        window_hours: float = 24.0,
        now_ms: Optional[float] = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Aggregate conversion metrics grouped by valuation_path within window_hours.
        Returns mapping: valuation_path -> {
            "total": int,
            "sniped": int,
            "stale": int,
            "pending": int,
            "disappeared": int,
            "conversion_rate": float
        }
        """
        conn = self._get_connection()
        current_now = now_ms if now_ms is not None else (time.time() * 1000.0)
        cutoff_ms = int(current_now - (window_hours * 3600.0 * 1000.0))

        cur = conn.execute(
            """
            SELECT valuation_path, resolved_status, count(*) as cnt
            FROM alerts
            WHERE timestamp >= ?
            GROUP BY valuation_path, resolved_status
            """,
            (cutoff_ms,),
        )
        stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"total": 0, "sniped": 0, "stale": 0, "pending": 0, "disappeared": 0, "conversion_rate": 1.0}
        )
        for row in cur.fetchall():
            path = str(row["valuation_path"])
            status = str(row["resolved_status"]).upper()
            cnt = int(row["cnt"])
            stats[path]["total"] += cnt
            if status == "SNIPED_BY_MARKET":
                stats[path]["sniped"] += cnt
            elif status == "STALE":
                stats[path]["stale"] += cnt
            elif status == "PENDING":
                stats[path]["pending"] += cnt
            else:
                stats[path]["disappeared"] += cnt

        for path, data in stats.items():
            resolved = data["sniped"] + data["stale"]
            if resolved > 0:
                data["conversion_rate"] = round(data["sniped"] / float(resolved), 4)
            else:
                data["conversion_rate"] = 1.0  # default optimistic until tested

        return dict(stats)

    # -----------------------------------------------------------------------
    # 2. Purchases Table Operations
    # -----------------------------------------------------------------------

    def record_purchase(
        self,
        auction_uuid: str,
        internal_id: str,
        buy_price: int,
        buy_timestamp: Optional[int] = None,
        item_uuid: Optional[str] = None,
        notes: Optional[str] = None,
        features_json: Optional[str | dict[str, Any]] = None,
        status: str = "HELD",
        derpy_active: int = 0,
        fee_model_version: int = 2,
    ) -> bool:
        """Record or update a purchased item into the portfolio ledger."""
        conn = self._get_connection()
        ts = buy_timestamp if buy_timestamp is not None else int(time.time() * 1000)
        feat_str = (
            features_json
            if isinstance(features_json, str)
            else json.dumps(features_json or {}, separators=(",", ":"))
        )

        query = """
        INSERT INTO purchases (
            auction_uuid, internal_id, item_uuid, buy_price,
            buy_timestamp, status, notes, features_json,
            derpy_active, fee_model_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(auction_uuid) DO UPDATE SET
            buy_price = excluded.buy_price,
            status = excluded.status,
            notes = coalesce(excluded.notes, purchases.notes),
            derpy_active = excluded.derpy_active,
            fee_model_version = excluded.fee_model_version
        """
        try:
            with conn:
                conn.execute(
                    query,
                    (
                        auction_uuid,
                        internal_id,
                        item_uuid,
                        int(buy_price),
                        ts,
                        status,
                        notes,
                        feat_str,
                        int(derpy_active),
                        int(fee_model_version),
                    ),
                )
                return True
        except Exception as e:
            logger.error("Failed to record purchase %s: %s", auction_uuid, e)
            return False

    async def async_record_purchase(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.record_purchase, **kwargs)

    def update_purchase_sale(
        self,
        auction_uuid: str,
        realized_sell_price: int,
        realized_sell_timestamp: Optional[int] = None,
        net_realized_pnl: Optional[int] = None,
        status: str = "SOLD",
        notes: Optional[str] = None,
        derpy_active: Optional[int] = None,
        fee_model_version: Optional[int] = None,
    ) -> bool:
        """Mark a purchase as sold, recording realized proceeds and net PnL."""
        conn = self._get_connection()
        ts = realized_sell_timestamp if realized_sell_timestamp is not None else int(time.time() * 1000)

        existing = self.get_purchase(auction_uuid) if (net_realized_pnl is None or derpy_active is None) else None
        is_derpy = bool(derpy_active if derpy_active is not None else (existing.get("derpy_active", 0) if existing else 0))
        model_ver = fee_model_version if fee_model_version is not None else (existing.get("fee_model_version", 2) if existing else 2)

        # If net_realized_pnl is omitted, look up original buy_price and compute with net_revenue
        if net_realized_pnl is None:
            if existing:
                from src.constants.taxes import net_revenue
                buy_p = int(existing["buy_price"])
                net_realized_pnl = net_revenue(realized_sell_price, derpy_active=is_derpy) - buy_p
            else:
                net_realized_pnl = 0

        query = """
        UPDATE purchases SET
            realized_sell_price = ?,
            realized_sell_timestamp = ?,
            net_realized_pnl = ?,
            status = ?,
            notes = coalesce(?, notes),
            derpy_active = ?,
            fee_model_version = ?
        WHERE auction_uuid = ?
        """
        try:
            with conn:
                cur = conn.execute(
                    query,
                    (
                        int(realized_sell_price),
                        ts,
                        int(net_realized_pnl),
                        status,
                        notes,
                        int(is_derpy),
                        int(model_ver),
                        auction_uuid,
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("Failed to update purchase sale %s: %s", auction_uuid, e)
            return False

    async def async_update_purchase_sale(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.update_purchase_sale, **kwargs)

    def get_purchase(self, auction_uuid: str) -> Optional[dict[str, Any]]:
        """Retrieve purchase by auction UUID."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM purchases WHERE auction_uuid = ?", (auction_uuid,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_purchases(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve purchases by status (e.g. 'HELD', 'SOLD', 'ABANDONED')."""
        conn = self._get_connection()
        if status:
            cur = conn.execute(
                "SELECT * FROM purchases WHERE status = ? ORDER BY buy_timestamp DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM purchases ORDER BY buy_timestamp DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]

    # -----------------------------------------------------------------------
    # 3. Sales History Table Operations
    # -----------------------------------------------------------------------

    def record_sale(
        self,
        uuid: str,
        internal_id: str,
        price: int,
        buyer_uuid: Optional[str],
        seller_uuid: Optional[str],
        timestamp: int,
        features_json: Optional[str | dict[str, Any]] = None,
        features_hash: str = "",
        normalized_tier: int = 0,
    ) -> bool:
        """Insert or ignore a completed auction from auctions_ended."""
        conn = self._get_connection()
        feat_str = (
            features_json
            if isinstance(features_json, str)
            else json.dumps(features_json or {}, separators=(",", ":"))
        )

        query = """
        INSERT OR IGNORE INTO sales_history (
            uuid, internal_id, price, buyer_uuid, seller_uuid,
            timestamp, features_json, features_hash, normalized_tier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with conn:
                cur = conn.execute(
                    query,
                    (
                        uuid,
                        internal_id,
                        int(price),
                        buyer_uuid,
                        seller_uuid,
                        int(timestamp),
                        feat_str,
                        features_hash,
                        int(normalized_tier),
                    ),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("Failed to record sale %s: %s", uuid, e)
            return False

    async def async_record_sale(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.record_sale, **kwargs)

    def get_sales(
        self,
        internal_id: Optional[str] = None,
        features_hash: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Retrieve completed sales by internal ID or feature hash."""
        conn = self._get_connection()
        clauses = []
        params: list[Any] = []

        if internal_id:
            clauses.append("internal_id = ?")
            params.append(internal_id)
        if features_hash:
            clauses.append("features_hash = ?")
            params.append(features_hash)
        if start_time is not None:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("timestamp <= ?")
            params.append(end_time)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"SELECT * FROM sales_history {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(query, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    # -----------------------------------------------------------------------
    # 4. Bazaar Snapshots Operations
    # -----------------------------------------------------------------------

    def record_bazaar_snapshot(
        self,
        product_id: str,
        buy_price: float,
        sell_price: float,
        buy_volume: int = 0,
        sell_volume: int = 0,
        timestamp: Optional[int] = None,
    ) -> bool:
        """Log a Bazaar snapshot timestamped in epoch milliseconds."""
        conn = self._get_connection()
        ts = timestamp if timestamp is not None else int(time.time() * 1000)
        query = """
        INSERT INTO bazaar_snapshots (
            timestamp, product_id, buy_price, sell_price, buy_volume, sell_volume
        ) VALUES (?, ?, ?, ?, ?, ?)
        """
        try:
            with conn:
                conn.execute(
                    query,
                    (
                        ts,
                        product_id,
                        float(buy_price),
                        float(sell_price),
                        int(buy_volume),
                        int(sell_volume),
                    ),
                )
                return True
        except Exception as e:
            logger.error("Failed to record bazaar snapshot for %s: %s", product_id, e)
            return False

    async def async_record_bazaar_snapshot(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.record_bazaar_snapshot, **kwargs)

    def get_bazaar_snapshots(
        self,
        product_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve recent snapshots for a Bazaar product."""
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT * FROM bazaar_snapshots WHERE product_id = ? ORDER BY timestamp DESC LIMIT ?",
            (product_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    # -----------------------------------------------------------------------
    # 5. Comps Cache Operations
    # -----------------------------------------------------------------------

    def set_comps_cache(
        self,
        internal_id: str,
        features_hash: str,
        comp_count: int,
        median_price: int,
        iqr_spread: int,
        computed_at: Optional[int] = None,
    ) -> bool:
        """Upsert aggregated comps statistics."""
        conn = self._get_connection()
        ts = computed_at if computed_at is not None else int(time.time() * 1000)
        query = """
        INSERT INTO comps_cache (
            internal_id, features_hash, comp_count, median_price, iqr_spread, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(internal_id, features_hash) DO UPDATE SET
            comp_count = excluded.comp_count,
            median_price = excluded.median_price,
            iqr_spread = excluded.iqr_spread,
            computed_at = excluded.computed_at
        """
        try:
            with conn:
                conn.execute(
                    query,
                    (
                        internal_id,
                        features_hash,
                        int(comp_count),
                        int(median_price),
                        int(iqr_spread),
                        ts,
                    ),
                )
                return True
        except Exception as e:
            logger.error("Failed to set comps cache for %s (%s): %s", internal_id, features_hash, e)
            return False

    def get_comps_cache(self, internal_id: str, features_hash: str) -> Optional[dict[str, Any]]:
        """Retrieve cached comps aggregates."""
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT * FROM comps_cache WHERE internal_id = ? AND features_hash = ?",
            (internal_id, features_hash),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # -----------------------------------------------------------------------
    # 6. Retention Params Operations (Empirical Bayes)
    # -----------------------------------------------------------------------

    def set_retention_params(
        self,
        category: str,
        sample_size: int,
        fitted_retention: float,
        updated_at: Optional[int] = None,
    ) -> bool:
        """Upsert fitted retention rate for an item category."""
        conn = self._get_connection()
        ts = updated_at if updated_at is not None else int(time.time() * 1000)
        query = """
        INSERT INTO retention_params (category, sample_size, fitted_retention, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category) DO UPDATE SET
            sample_size = excluded.sample_size,
            fitted_retention = excluded.fitted_retention,
            updated_at = excluded.updated_at
        """
        try:
            with conn:
                conn.execute(
                    query,
                    (category, int(sample_size), float(fitted_retention), ts),
                )
                return True
        except Exception as e:
            logger.error("Failed to set retention params for %s: %s", category, e)
            return False

    def get_retention_params(self, category: str) -> Optional[dict[str, Any]]:
        """Retrieve fitted retention rate for an item category."""
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT * FROM retention_params WHERE category = ?",
            (category,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_all_retention_params(self) -> list[dict[str, Any]]:
        """Retrieve all fitted retention records."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM retention_params ORDER BY updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


# Module-level default singleton instance
_default_db: Optional[DatabaseManager] = None


def get_db(db_path: str = "data/market_intelligence.db") -> DatabaseManager:
    """Retrieve or instantiate DatabaseManager singleton."""
    global _default_db
    if _default_db is None or _default_db.db_path != str(db_path):
        _default_db = DatabaseManager(db_path=db_path)
    return _default_db
