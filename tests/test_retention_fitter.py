"""Unit and integration tests for Phase 4: Data-Fitted Retention Rates (Empirical Bayes)."""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from src.bazaar import BazaarCache
from src.config import Settings
from src.db import DatabaseManager
from src.evaluator import FlipEvaluator
from src.networth_evaluator import calculate_item_networth, get_dynamic_retention_rate
from src.retention_fitter import (
    RetentionFitter,
    calculate_observed_retention,
    compute_empirical_bayes_retention,
)


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database manager for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = DatabaseManager(db_path)
    yield db
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def dummy_bazaar_cache():
    """Mock bazaar cache returning predictable prices."""
    cache = BazaarCache(Settings(DRY_RUN=True))
    cache._buy_prices = {
        "RECOMBOBULATOR_3000": 8_000_000.0,
        "WITHER_ESSENCE": 4_000.0,
        "HOT_POTATO_BOOK": 150_000.0,
        "FUMING_POTATO_BOOK": 1_200_000.0,
        "ENCHANTMENT_ULTIMATE_WISE_1": 500_000.0,
    }
    cache._last_updated = time.time()
    return cache


# ---------------------------------------------------------------------------
# 1. Pure Mathematical Formula Tests
# ---------------------------------------------------------------------------

def test_compute_empirical_bayes_retention_formula():
    # n = 0 -> returns prior
    assert compute_empirical_bayes_retention(0.80, 0, 0.70) == 0.70

    # n = 10, tau = 10 -> 50% data, 50% prior
    # (10/20) * 0.90 + (10/20) * 0.70 = 0.45 + 0.35 = 0.80
    fitted = compute_empirical_bayes_retention(
        sample_mean=0.90,
        n_samples=10,
        prior_retention=0.70,
        tau=10.0,
    )
    assert pytest.approx(fitted, 0.0001) == 0.80

    # n = 30, tau = 10 -> 75% data, 25% prior
    # (30/40) * 0.60 + (10/40) * 0.80 = 0.45 + 0.20 = 0.65
    fitted_large = compute_empirical_bayes_retention(
        sample_mean=0.60,
        n_samples=30,
        prior_retention=0.80,
        tau=10.0,
    )
    assert pytest.approx(fitted_large, 0.0001) == 0.65


def test_compute_empirical_bayes_clamping():
    # Extreme upper bound clamping to max_clamp (0.95)
    high_fitted = compute_empirical_bayes_retention(
        sample_mean=1.50,
        n_samples=50,
        prior_retention=0.85,
        max_clamp=0.95,
    )
    assert high_fitted == 0.95

    # Extreme lower bound clamping to min_clamp (0.30)
    low_fitted = compute_empirical_bayes_retention(
        sample_mean=0.10,
        n_samples=50,
        prior_retention=0.50,
        min_clamp=0.30,
    )
    assert low_fitted == 0.30


def test_calculate_observed_retention():
    # Base: 20M, Addons: 20M, Sale Price: 35M -> (35 - 20) / 20 = 0.75
    r_obs = calculate_observed_retention(
        sale_price=35_000_000,
        base_lbin=20_000_000,
        addon_worth=20_000_000,
    )
    assert r_obs is not None
    assert pytest.approx(r_obs, 0.001) == 0.75

    # Zero addon worth -> returns None
    assert calculate_observed_retention(30_000_000, 20_000_000, 0) is None

    # Extreme outlier (> 2.0) -> returns None
    assert calculate_observed_retention(100_000_000, 10_000_000, 10_000_000) is None

    # Catastrophic panic sale (< -0.5) -> returns None
    assert calculate_observed_retention(5_000_000, 20_000_000, 10_000_000) is None


# ---------------------------------------------------------------------------
# 2. Retention Fitter Prior & Tier Lookup Tests
# ---------------------------------------------------------------------------

def test_retention_fitter_priors(temp_db):
    settings = Settings(DRY_RUN=True)
    fitter = RetentionFitter(temp_db, settings)

    assert fitter.get_prior_retention(600_000_000) == 0.85
    assert fitter.get_prior_retention(100_000_000) == 0.70
    assert fitter.get_prior_retention(20_000_000) == 0.50


# ---------------------------------------------------------------------------
# 3. Fitting, Persistence & Database Operations Tests
# ---------------------------------------------------------------------------

def test_fit_item_retention_sample_size_threshold(temp_db):
    settings = Settings(DRY_RUN=True, MIN_RETENTION_SAMPLES=3)
    fitter = RetentionFitter(temp_db, settings)

    # 2 observations (< 3 threshold) -> None
    assert fitter.fit_item_retention("JUJU_SHORTBOW", 20_000_000, [0.60, 0.65]) is None

    # 4 observations (>= 3 threshold) -> fitted rate
    fitted = fitter.fit_item_retention("JUJU_SHORTBOW", 20_000_000, [0.60, 0.65, 0.70, 0.65])
    assert fitted is not None
    assert 0.50 <= fitted <= 0.70

    # Verify persistence to SQLite table retention_params
    db_record = temp_db.get_retention_params("JUJU_SHORTBOW")
    assert db_record is not None
    assert db_record["sample_size"] == 4
    assert pytest.approx(db_record["fitted_retention"], 0.0001) == fitted


def test_retention_fitter_persistence_across_instances(temp_db):
    settings = Settings(DRY_RUN=True, ENABLE_DYNAMIC_RETENTION=True, MIN_RETENTION_SAMPLES=3)
    fitter_1 = RetentionFitter(temp_db, settings)

    fitter_1.fit_item_retention("NECRON_CHESTPLATE", 100_000_000, [0.85, 0.80, 0.82])

    # Reinstantiate fitter pointing to same database
    fitter_2 = RetentionFitter(temp_db, settings)
    rate = fitter_2.get_retention_rate("NECRON_CHESTPLATE", 100_000_000)
    assert rate > 0.70  # Higher than default prior 0.70 due to ~0.82 observed sales


def test_retention_fitter_toggle_behavior(temp_db):
    # Case A: Disabled -> returns prior
    settings_off = Settings(DRY_RUN=True, ENABLE_DYNAMIC_RETENTION=False)
    fitter_off = RetentionFitter(temp_db, settings_off)
    temp_db.set_retention_params("HYPERION", 10, 0.92)

    assert fitter_off.get_retention_rate("HYPERION", 600_000_000) == 0.85  # Prior rate!

    # Case B: Enabled -> returns fitted rate
    settings_on = Settings(DRY_RUN=True, ENABLE_DYNAMIC_RETENTION=True)
    fitter_on = RetentionFitter(temp_db, settings_on)
    assert fitter_on.get_retention_rate("HYPERION", 600_000_000) == 0.92


# ---------------------------------------------------------------------------
# 4. Fitting from Sales History
# ---------------------------------------------------------------------------

def test_fit_from_sales_history(temp_db, dummy_bazaar_cache):
    settings = Settings(DRY_RUN=True, ENABLE_DYNAMIC_RETENTION=True, MIN_RETENTION_SAMPLES=3)
    fitter = RetentionFitter(temp_db, settings, bazaar_cache=dummy_bazaar_cache)

    now_ms = int(time.time() * 1000)
    # Add 4 completed sales of an upgraded item with Recomb (8M worth)
    # Base LBIN = 20M, Addon = 8M -> Total intrinsic = 28M
    # Sells for 26M -> Observed retention = (26M - 20M) / 8M = 0.75
    for i in range(4):
        temp_db.record_sale(
            uuid=f"hist-sale-{i}",
            internal_id="JUJU_SHORTBOW",
            price=26_000_000,
            buyer_uuid=f"b-{i}",
            seller_uuid=f"s-{i}",
            timestamp=now_ms,
            features_json={"internal_id": "JUJU_SHORTBOW", "rarity_upgrades": 1},
            features_hash="hash-1",
        )

    base_lookup = {"JUJU_SHORTBOW": 20_000_000}
    fitted_map = fitter.fit_from_sales_history(base_lbin_lookup=base_lookup)

    assert "JUJU_SHORTBOW" in fitted_map
    assert 0.50 <= fitted_map["JUJU_SHORTBOW"] <= 0.75


# ---------------------------------------------------------------------------
# 5. FlipEvaluator Path B Dynamic Retention Integration Tests
# ---------------------------------------------------------------------------

def test_flip_evaluator_uses_dynamic_retention_in_path_b(temp_db, dummy_bazaar_cache):
    # Enable dynamic retention
    settings = Settings(
        DRY_RUN=True,
        ENABLE_NETWORTH_VALUATION=True,
        ENABLE_DYNAMIC_RETENTION=True,
        MIN_RETENTION_SAMPLES=3,
        MIN_SAMPLE_SIZE=1,
    )
    fitter = RetentionFitter(temp_db, settings, bazaar_cache=dummy_bazaar_cache)
    evaluator = FlipEvaluator(settings, bazaar_cache=dummy_bazaar_cache, retention_fitter=fitter)

    # Set up baseline price cache
    evaluator.set_clean_price_cache({"JUJU_SHORTBOW": [20_000_000]})
    evaluator.set_price_cache({"JUJU_SHORTBOW": [20_000_000]})

    # Fit a high retention rate: 0.85 (instead of budget prior 0.50)
    temp_db.set_retention_params(
        category="JUJU_SHORTBOW",
        sample_size=10,
        fitted_retention=0.85,
    )

    decoded_item = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 0,
        "rarity_upgrades": 1,  # Recomb worth 8M
        "modifier": "",
        "gemstones": [],
        "enchantments": {},
    }

    # Candidate auction at 21M
    auction = {
        "uuid": "dyn-auc-1",
        "starting_bid": 21_000_000,
        "item_name": "Juju Shortbow",
    }

    result = evaluator.evaluate_listing(auction, decoded_item)
    assert result is not None
    assert result.is_networth_snipe is True
    # Verify retention rate used was the fitted 0.85 rather than the static 0.50!
    assert pytest.approx(result.retention_rate, 0.001) == 0.85
    # Resell = 20M + round(8M * 0.85) = 26.8M
    assert result.target_resell_price == 26_800_000


def test_flip_evaluator_preserves_static_retention_when_disabled(temp_db, dummy_bazaar_cache):
    # Default: ENABLE_DYNAMIC_RETENTION is False
    settings = Settings(
        DRY_RUN=True,
        ENABLE_NETWORTH_VALUATION=True,
        ENABLE_DYNAMIC_RETENTION=False,
        MIN_SAMPLE_SIZE=1,
    )
    fitter = RetentionFitter(temp_db, settings, bazaar_cache=dummy_bazaar_cache)
    evaluator = FlipEvaluator(settings, bazaar_cache=dummy_bazaar_cache, retention_fitter=fitter)

    evaluator.set_clean_price_cache({"JUJU_SHORTBOW": [20_000_000]})
    evaluator.set_price_cache({"JUJU_SHORTBOW": [20_000_000]})

    # DB has 0.85, but flag is False
    temp_db.set_retention_params("JUJU_SHORTBOW", 10, 0.85)

    decoded_item = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "dungeon_stars": 0,
        "rarity_upgrades": 1,  # 8M
        "modifier": "",
        "gemstones": [],
        "enchantments": {},
    }
    auction = {
        "uuid": "static-auc-1",
        "starting_bid": 21_000_000,
        "item_name": "Juju Shortbow",
    }

    result = evaluator.evaluate_listing(auction, decoded_item)
    assert result is not None
    # Must preserve standard static tier retention (0.50 for < 50M)
    assert pytest.approx(result.retention_rate, 0.001) == 0.50
    # Resell = 20M + round(8M * 0.50) = 24.0M
    assert result.target_resell_price == 24_000_000
