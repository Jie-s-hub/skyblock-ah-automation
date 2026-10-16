"""Empirical Bayes retention rate fitting engine for upgraded SkyBlock gear."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from src.bazaar import BazaarCache
from src.config import Settings
from src.db import DatabaseManager
from src.networth_evaluator import calculate_item_addons, get_dynamic_retention_rate

logger = logging.getLogger(__name__)


def compute_empirical_bayes_retention(
    sample_mean: float,
    n_samples: int,
    prior_retention: float,
    tau: float = 10.0,
    min_clamp: float = 0.30,
    max_clamp: float = 0.95,
) -> float:
    """
    Calculate Empirical Bayes shrunk retention rate:
        R_hat = (n / (n + tau)) * R_bar + (tau / (n + tau)) * R_prior

    Clamped to [min_clamp, max_clamp].
    """
    if n_samples <= 0:
        return max(min_clamp, min(max_clamp, float(prior_retention)))

    weight_data = float(n_samples) / (float(n_samples) + float(tau))
    weight_prior = float(tau) / (float(n_samples) + float(tau))

    raw_fitted = (weight_data * float(sample_mean)) + (weight_prior * float(prior_retention))
    clamped = max(float(min_clamp), min(float(max_clamp), raw_fitted))
    return round(clamped, 4)


def calculate_observed_retention(
    sale_price: int,
    base_lbin: int,
    addon_worth: int,
) -> Optional[float]:
    """
    Calculate empirical observed retention rate on a single upgraded item sale:
        R_obs = (Sale Price - Base LBIN) / Addon Worth

    Returns None if Addon Worth <= 0 or if the observed value is an extreme outlier (R_obs < -0.5 or > 2.0).
    """
    if addon_worth <= 0 or sale_price <= 0 or base_lbin <= 0:
        return None

    r_obs = (sale_price - base_lbin) / float(addon_worth)
    # Sanitize wild anomalies (e.g. catastrophic panic sales or billions wash trading)
    if r_obs < -0.5 or r_obs > 2.0:
        return None

    return r_obs


class RetentionFitter:
    """
    Fits and manages Empirical Bayes data-driven retention rates per item category or internal_id.
    Persists parameters to SQLite table `retention_params`.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: Optional[Settings] = None,
        bazaar_cache: Optional[BazaarCache] = None,
    ) -> None:
        self.db = db
        self.config = config or Settings(DRY_RUN=True)
        self.bazaar_cache = bazaar_cache
        self._cache: dict[str, float] = {}
        self._negative_cache: set[str] = set()
        self._snapshot_loaded: bool = False

    def set_cached_rates(self, rates: Optional[dict[str, float]]) -> None:
        """Populate retention rates from a pre-aggregated MarketTelemetrySnapshot (zero hot-path DB I/O)."""
        if rates is not None:
            for k, v in rates.items():
                clean_k = str(k).strip().upper()
                self._cache[clean_k] = float(v)
                self._negative_cache.discard(clean_k)
        self._snapshot_loaded = True

    def preload_from_db(self) -> int:
        """Preload all fitted retention parameters from SQLite into in-memory cache."""
        loaded = 0
        try:
            conn = self.db.get_connection()
            cur = conn.execute(
                """
                SELECT category, sample_size, fitted_retention
                FROM retention_params;
                """
            )
            for row in cur.fetchall():
                cat = str(row[0]).strip().upper()
                if int(row[1]) >= self.min_samples:
                    self._cache[cat] = float(row[2])
                    self._negative_cache.discard(cat)
                    loaded += 1
            self._snapshot_loaded = True
        except Exception as err:
            logger.debug("Failed preloading retention_params from DB: %s", err)
        return loaded

    @property
    def tau(self) -> float:
        return getattr(self.config, "RETENTION_SHRINKAGE_TAU", 10.0)

    @property
    def min_samples(self) -> int:
        return getattr(self.config, "MIN_RETENTION_SAMPLES", 3)

    @property
    def min_clamp(self) -> float:
        return getattr(self.config, "RETENTION_MIN_CLAMP", 0.30)

    @property
    def max_clamp(self) -> float:
        return getattr(self.config, "RETENTION_MAX_CLAMP", 0.95)

    def get_prior_retention(self, base_lbin: int) -> float:
        """Lookup default prior retention rate based on item price tier."""
        return get_dynamic_retention_rate(base_lbin)

    def fit_item_retention(
        self,
        internal_id: str,
        base_lbin: int,
        observed_retentions: list[float],
        now_ms: Optional[int] = None,
    ) -> Optional[float]:
        """
        Fit retention rate from a list of observed retention values using Empirical Bayes shrinkage.
        Persists fitted parameters to SQLite table `retention_params`.
        """
        clean_id = internal_id.strip().upper()
        valid_obs = [r for r in observed_retentions if r is not None and -0.5 <= r <= 2.0]
        n = len(valid_obs)

        if n < self.min_samples:
            return None

        sample_mean = sum(valid_obs) / float(n)
        prior = self.get_prior_retention(base_lbin)
        fitted = compute_empirical_bayes_retention(
            sample_mean=sample_mean,
            n_samples=n,
            prior_retention=prior,
            tau=self.tau,
            min_clamp=self.min_clamp,
            max_clamp=self.max_clamp,
        )

        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        self.db.set_retention_params(
            category=clean_id,
            sample_size=n,
            fitted_retention=fitted,
            updated_at=ts,
        )
        self._cache[clean_id] = fitted
        self._negative_cache.discard(clean_id)
        logger.info(
            "Fitted Empirical Bayes retention for %s (n=%d, mean=%.3f, prior=%.2f -> fitted=%.3f)",
            clean_id,
            n,
            sample_mean,
            prior,
            fitted,
        )
        return fitted

    def get_retention_rate(self, internal_id: str, base_lbin: int) -> float:
        """
        Retrieve retention rate for an item:
        - If ENABLE_DYNAMIC_RETENTION is False: returns standard tier prior.
        - If fitted rate exists in cache or DB: returns fitted rate.
        - Otherwise falls back to prior retention rate.
        """
        prior = self.get_prior_retention(base_lbin)
        if not getattr(self.config, "ENABLE_DYNAMIC_RETENTION", False):
            return prior

        clean_id = internal_id.strip().upper()
        if clean_id in self._cache:
            return self._cache[clean_id]

        if self._snapshot_loaded or clean_id in self._negative_cache:
            return prior

        row = self.db.get_retention_params(clean_id)
        if row and int(row.get("sample_size", 0)) >= self.min_samples:
            rate = float(row["fitted_retention"])
            self._cache[clean_id] = rate
            return rate

        self._negative_cache.add(clean_id)
        return prior

    def fit_from_sales_history(
        self,
        internal_id: Optional[str] = None,
        base_lbin_lookup: Optional[dict[str, int]] = None,
    ) -> dict[str, float]:
        """
        Scan sales_history in SQLite, extract observed retentions on upgraded items,
        and fit Empirical Bayes retention rates.
        """
        if not self.bazaar_cache:
            return {}

        sales = self.db.get_sales(internal_id=internal_id, limit=2000)
        if not sales:
            return {}

        grouped_obs: dict[str, list[float]] = {}
        grouped_bases: dict[str, int] = {}
        base_lookup = base_lbin_lookup or {}

        for sale in sales:
            item_id = str(sale.get("internal_id", "")).strip().upper()
            if not item_id or item_id == "UNKNOWN":
                continue

            price = int(sale.get("price", 0))
            if price <= 0:
                continue

            raw_feat = sale.get("features_json")
            decoded: dict[str, Any] = {}
            if isinstance(raw_feat, str):
                try:
                    decoded = json.loads(raw_feat)
                except Exception:
                    continue
            elif isinstance(raw_feat, dict):
                decoded = raw_feat

            # Calculate addon cost
            _, total_addon_cost = calculate_item_addons(
                decoded_data=decoded,
                bazaar_cache=self.bazaar_cache,
            )
            if total_addon_cost <= 0:
                continue

            base_lbin = base_lookup.get(item_id)
            if base_lbin is None or base_lbin <= 0:
                # Approximate base price from lowest clean price or normalized floor
                base_lbin = int(price * 0.6)  # fallback approximation
            grouped_bases[item_id] = base_lbin

            r_obs = calculate_observed_retention(price, base_lbin, total_addon_cost)
            if r_obs is not None:
                if item_id not in grouped_obs:
                    grouped_obs[item_id] = []
                grouped_obs[item_id].append(r_obs)

        results: dict[str, float] = {}
        for item_id, obs_list in grouped_obs.items():
            base_lbin = grouped_bases.get(item_id, 50_000_000)
            fitted = self.fit_item_retention(item_id, base_lbin, obs_list)
            if fitted is not None:
                results[item_id] = fitted

        return results
