"""Ingestion and feature hashing engine for completed SkyBlock auction sales."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from src.config import Settings
from src.db import DatabaseManager
from src.nbt_decoder import decode_item_bytes

logger = logging.getLogger(__name__)


def extract_high_tier_enchants(enchantments: dict[str, int]) -> list[tuple[str, int]]:
    """Filter enchantments down to valuable/high-tier enchants (level >= 6 or Ultimates)."""
    valuable: list[tuple[str, int]] = []
    for name, lvl in sorted(enchantments.items()):
        name_clean = name.strip().lower()
        if (
            "ultimate" in name_clean
            or lvl >= 6
            or name_clean
            in {
                "soul_eater",
                "wisdom",
                "legion",
                "chimera",
                "one_for_all",
                "swarm",
                "fatal_tempo",
                "rend",
                "combo",
                "overload",
                "rejuvenate",
            }
        ):
            valuable.append((name_clean, int(lvl)))
    return valuable


def generate_features_hash(decoded: dict[str, Any]) -> str:
    """
    Generate a deterministic feature fingerprint for Tier 1 Strict Comps Matching.
    Captures:
    - internal_id
    - dungeon_stars & master_stars
    - rarity_upgrades (recombobulator)
    - reforge modifier
    - sorted gemstones (type and quality)
    - high-tier / ultimate enchantments
    - attributes (for Crimson Isle gear)
    - prestige tier
    - gemstone chambers
    """
    internal_id = str(decoded.get("internal_id", "UNKNOWN")).strip().upper()
    stars = int(decoded.get("dungeon_stars", 0))
    m_stars = int(decoded.get("master_stars", 0))
    recomb = int(decoded.get("rarity_upgrades", 0))
    reforge = str(decoded.get("modifier", "")).strip().lower()
    chambers = int(decoded.get("gemstone_chambers", 0))
    prestige = str(decoded.get("prestige_tier", "") or "").strip().upper()

    # Gemstones: sorted list of "TYPE:QUALITY"
    raw_gems = decoded.get("gemstones", [])
    gems_list: list[str] = []
    if isinstance(raw_gems, list):
        for g in raw_gems:
            if isinstance(g, dict):
                t = str(g.get("type", "")).upper()
                q = str(g.get("quality", "")).upper()
                if t and q:
                    gems_list.append(f"{t}:{q}")
    gems_str = ",".join(sorted(gems_list))

    # Enchants: sorted list of "NAME:LVL"
    raw_enchants = decoded.get("enchantments", {})
    ench_items = extract_high_tier_enchants(raw_enchants) if isinstance(raw_enchants, dict) else []
    ench_str = ",".join(f"{k}:{v}" for k, v in ench_items)

    # Attributes: sorted list of "ATTR:LVL"
    raw_attrs = decoded.get("attributes", {})
    attrs_str = ""
    if isinstance(raw_attrs, dict) and raw_attrs:
        attrs_str = ",".join(f"{k.upper()}:{v}" for k, v in sorted(raw_attrs.items()))

    # Build canonical fingerprint string
    canonical = (
        f"{internal_id}|{stars}s|{m_stars}ms|{recomb}r|{reforge}|"
        f"GEMS[{gems_str}]|ENCH[{ench_str}]|ATTR[{attrs_str}]|P[{prestige}]|CH[{chambers}]"
    )
    # Hash canonical to fixed 16-byte hex
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def generate_relaxed_features_hash(decoded: dict[str, Any]) -> str:
    """
    Generate a relaxed fingerprint for Tier 2 Relaxed Comps Matching.
    Matches internal_id, recomb status, star tier group, and prestige.
    """
    internal_id = str(decoded.get("internal_id", "UNKNOWN")).strip().upper()
    recomb = int(decoded.get("rarity_upgrades", 0))
    stars = int(decoded.get("dungeon_stars", 0))
    prestige = str(decoded.get("prestige_tier", "") or "").strip().upper()

    canonical = f"{internal_id}|{stars}s|{recomb}r|P[{prestige}]"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class SalesIngestor:
    """Ingests completed sales from Hypixel ended auctions into SQLite sales_history."""

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[Settings] = None,
    ) -> None:
        self.db = db
        self.config = config or Settings(DRY_RUN=True)

    def ingest_ended_auctions(self, raw_auctions: list[dict[str, Any]]) -> int:
        """
        Process and record completed BIN sales from /v2/skyblock/auctions_ended into SQLite.
        Decodes NBT bytes, extracts feature hashes, and persists to sales_history.

        Returns:
            int: Number of sales successfully recorded.
        """
        ingested_count = 0
        now_ms = int(time.time() * 1000)

        for auction in raw_auctions:
            if not isinstance(auction, dict):
                continue

            # Must be a completed BIN sale
            if auction.get("buyer") is None or auction.get("bin") is not True:
                continue

            auction_uuid = str(auction.get("auction_id") or auction.get("uuid") or "").strip()
            if not auction_uuid:
                continue

            price = int(auction.get("price") or auction.get("starting_bid") or 0)
            if price <= 0:
                continue

            raw_ts = auction.get("timestamp")
            if raw_ts is not None:
                try:
                    ts_float = float(raw_ts)
                    timestamp = int(ts_float if ts_float > 1e11 else (ts_float * 1000.0))
                except (ValueError, TypeError):
                    timestamp = now_ms
            else:
                timestamp = now_ms

            # Decode item
            item_bytes = auction.get("item_bytes")
            decoded: dict[str, Any] = {}
            if item_bytes:
                decoded = decode_item_bytes(item_bytes)
            elif "decoded" in auction:
                decoded = auction["decoded"]

            internal_id = decoded.get("internal_id") or auction.get("internal_id") or "UNKNOWN"
            if not internal_id or internal_id == "UNKNOWN":
                continue

            features_hash = generate_features_hash(decoded)
            normalized_tier = int(decoded.get("dungeon_stars", 0)) + int(decoded.get("master_stars", 0))

            time_to_sell_s: Optional[float] = None
            raw_start = auction.get("start")
            if raw_start is not None:
                try:
                    s_val = float(raw_start)
                    start_ms = s_val if s_val > 1e11 else (s_val * 1000.0)
                    time_to_sell_s = max(0.0, (timestamp - start_ms) / 1000.0)
                except (ValueError, TypeError):
                    pass

            success = self.db.record_sale(
                uuid=auction_uuid,
                internal_id=internal_id,
                price=price,
                buyer_uuid=auction.get("buyer"),
                seller_uuid=auction.get("seller"),
                timestamp=timestamp,
                features_json=decoded,
                features_hash=features_hash,
                normalized_tier=normalized_tier,
                time_to_sell_s=time_to_sell_s,
            )
            if success:
                ingested_count += 1

        return ingested_count
