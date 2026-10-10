"""Tests for configuration parsing, defaults, and validation."""

from __future__ import annotations

import pytest

from src.config import Settings, get_settings


def test_config_defaults_dry_run():
    """Verify default values in dry-run mode."""
    config = get_settings(_env_file=None, DRY_RUN=True)
    assert config.DRY_RUN is True
    assert config.MIN_PROFIT_COINS == 1_000_000
    assert config.MIN_PROFIT_MARGIN_PCT == 15.0
    assert config.POLL_INTERVAL_SECONDS == 60
    assert config.AH_TAX_RATE == 0.02
    assert config.MIN_SAMPLE_SIZE == 3
    assert config.MAX_TARGET_TO_BUY_RATIO == 3.0
    assert "dummy" in config.DISCORD_WEBHOOK_URL


def test_config_production_requires_webhook():
    """Verify production mode (DRY_RUN=False) raises error if webhook is empty."""
    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL is required"):
        get_settings(_env_file=None, DRY_RUN=False, DISCORD_WEBHOOK_URL="")


def test_config_production_with_valid_webhook():
    """Verify production mode succeeds with provided webhook URL."""
    config = get_settings(
        _env_file=None,
        DRY_RUN=False,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc",
    )
    assert config.DRY_RUN is False
    assert config.DISCORD_WEBHOOK_URL == "https://discord.com/api/webhooks/123/abc"
