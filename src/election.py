"""Hypixel SkyBlock Mayor election resource client and Derpy active detection."""

from __future__ import annotations

import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

ELECTION_ENDPOINT = "https://api.hypixel.net/v2/resources/skyblock/election"


DUNGEON_CHEST_ITEMS = {
    "NECRON_HANDLE",
    "WITHER_SHIELD_SCROLL",
    "IMPLOSION_SCROLL",
    "SHADOW_WARP_SCROLL",
    "FIRST_MASTER_STAR",
    "SECOND_MASTER_STAR",
    "THIRD_MASTER_STAR",
    "FOURTH_MASTER_STAR",
    "FIFTH_MASTER_STAR",
    "RECOMBOBULATOR_3000",
}

EVENT_MAYORS = {"diana", "cole", "marina", "scorpius"}
EVENT_ITEM_POOLS = {
    "diana": {"DAEDALUS_AXE", "CHIMERA", "GRIFFIN_FEATHER", "CROWN_OF_GREED"},
    "cole": {"DIVAN_ALLOY", "GLOSSY_GEMSTONE", "REFINED_MINERAL"},
    "marina": {"MEGALODON", "FLYING_FISH", "FISHING_EXPERIENCE"},
    "scorpius": {"MIDAS_STAFF", "PLASMA_CORE", "HECATOMB"},
}


class MayorElectionTracker:
    """Tracks active Hypixel SkyBlock mayor via /v2/resources/skyblock/election."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        override: str | None = None,
        cache_ttl_seconds: int = 600,
        paul_haircut_rate: float = 0.12,
    ) -> None:
        self._session = session
        self.override = override
        self.cache_ttl_seconds = cache_ttl_seconds
        self.paul_haircut_rate = paul_haircut_rate
        self._last_checked_ms: float = 0.0
        self._last_known_derpy: bool = False
        self._last_known_paul: bool = False
        self._last_mayor_name: str | None = None

    def _check_override(self) -> str | None:
        """Check MAYOR_OVERRIDE from instance or os.environ."""
        val = self.override if self.override is not None else os.environ.get("MAYOR_OVERRIDE")
        if val is not None and str(val).strip():
            return str(val).strip().lower()
        return None

    @property
    def derpy_active(self) -> bool:
        """Synchronously return current known derpy state (honoring override)."""
        override_val = self._check_override()
        if override_val is not None:
            return override_val in ("derpy", "true", "1", "yes")
        return self._last_known_derpy

    @property
    def paul_active(self) -> bool:
        """Return whether Mayor Paul is active (or overridden)."""
        override_val = self._check_override()
        if override_val is not None:
            return override_val == "paul"
        return self._last_known_paul

    @property
    def mayor_name(self) -> str | None:
        """Return the last known active mayor name."""
        override_val = self._check_override()
        if override_val is not None:
            return override_val.title()
        return self._last_mayor_name

    def get_dungeon_haircut(self, internal_id: str) -> float:
        """Return 12% (configurable) haircut for dungeon chest drops if Paul is active."""
        if not self.paul_active:
            return 0.0
        clean_id = (internal_id or "").strip().upper()
        if clean_id in DUNGEON_CHEST_ITEMS or clean_id.endswith("_MASTER_STAR"):
            return self.paul_haircut_rate
        return 0.0

    def is_event_pool_item(self, internal_id: str) -> bool:
        """Check if an item belongs to the currently active event mayor's pool."""
        m_name = (self.mayor_name or "").lower()
        if m_name in EVENT_ITEM_POOLS:
            clean_id = (internal_id or "").strip().upper()
            return clean_id in EVENT_ITEM_POOLS[m_name]
        return False

    async def update(self, session: aiohttp.ClientSession | None = None) -> bool:
        """
        Poll election endpoint if cache has expired (~10 minutes).
        Returns whether Derpy is currently active mayor.
        On failure, preserves last known state and logs a warning.
        """
        override_val = self._check_override()
        if override_val is not None:
            self._last_known_derpy = override_val in ("derpy", "true", "1", "yes")
            self._last_known_paul = override_val == "paul"
            self._last_mayor_name = override_val.title()
            return self._last_known_derpy

        now_ms = time.time() * 1000.0
        if (now_ms - self._last_checked_ms) < (self.cache_ttl_seconds * 1000.0) and self._last_mayor_name is not None:
            return self._last_known_derpy

        sess = session or self._session
        should_close = False
        if sess is None or getattr(sess, "closed", False):
            sess = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
            should_close = True

        try:
            async with sess.get(ELECTION_ENDPOINT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mayor_dict = data.get("mayor", {}) if isinstance(data, dict) else {}
                    mayor_name = str(mayor_dict.get("name", "")).strip()
                    self._last_mayor_name = mayor_name
                    self._last_known_derpy = mayor_name.lower() == "derpy"
                    self._last_known_paul = mayor_name.lower() == "paul"
                    self._last_checked_ms = now_ms
                    logger.info(
                        "Hypixel mayor election updated: active mayor='%s', derpy=%s, paul=%s",
                        mayor_name,
                        self._last_known_derpy,
                        self._last_known_paul,
                    )
                else:
                    logger.warning(
                        "Hypixel election API returned HTTP %s; keeping last known derpy=%s",
                        resp.status,
                        self._last_known_derpy,
                    )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "Failed to fetch Hypixel election resource: %s; keeping last known derpy=%s",
                err,
                self._last_known_derpy,
            )
        finally:
            if should_close:
                await sess.close()

        return self._last_known_derpy


def get_dungeon_haircut(internal_id: str, mayor_tracker: MayorElectionTracker | None = None) -> float:
    """Module-level helper to get the dungeon chest haircut for an item."""
    if mayor_tracker is not None:
        return mayor_tracker.get_dungeon_haircut(internal_id)
    return 0.0
