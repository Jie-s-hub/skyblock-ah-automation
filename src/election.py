"""Hypixel SkyBlock Mayor election resource client and Derpy active detection."""

from __future__ import annotations

import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

ELECTION_ENDPOINT = "https://api.hypixel.net/v2/resources/skyblock/election"


class MayorElectionTracker:
    """Tracks active Hypixel SkyBlock mayor via /v2/resources/skyblock/election."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        override: str | None = None,
        cache_ttl_seconds: int = 600,
    ) -> None:
        self._session = session
        self.override = override
        self.cache_ttl_seconds = cache_ttl_seconds
        self._last_checked_ms: float = 0.0
        self._last_known_derpy: bool = False
        self._last_mayor_name: str | None = None

    def _check_override(self) -> bool | None:
        """Check MAYOR_OVERRIDE from instance or os.environ."""
        val = self.override if self.override is not None else os.environ.get("MAYOR_OVERRIDE")
        if val is not None and str(val).strip():
            s = str(val).strip().lower()
            return s in ("derpy", "true", "1", "yes")
        return None

    @property
    def derpy_active(self) -> bool:
        """Synchronously return current known derpy state (honoring override)."""
        override_val = self._check_override()
        if override_val is not None:
            return override_val
        return self._last_known_derpy

    @property
    def mayor_name(self) -> str | None:
        """Return the last known active mayor name."""
        return self._last_mayor_name

    async def update(self, session: aiohttp.ClientSession | None = None) -> bool:
        """
        Poll election endpoint if cache has expired (~10 minutes).
        Returns whether Derpy is currently active mayor.
        On failure, preserves last known state and logs a warning.
        """
        override_val = self._check_override()
        if override_val is not None:
            self._last_known_derpy = override_val
            return override_val

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
                    self._last_checked_ms = now_ms
                    logger.info(
                        "Hypixel mayor election updated: active mayor='%s', derpy=%s",
                        mayor_name,
                        self._last_known_derpy,
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
