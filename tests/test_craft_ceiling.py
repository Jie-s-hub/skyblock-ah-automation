"""Unit tests for Craft Cost Ceiling and Anti-Craft-Bait Gate."""

from __future__ import annotations

from typing import Any
import pytest

from src.config import Settings
from src.constants.recipes import RECIPES, get_recipe, register_recipe
from src.craft_evaluator import calculate_craft_cost
from src.evaluator import FlipEvaluator, FlipResult
from src.local_server import build_flip_payload
from src.notifier import DiscordNotifier


class MockBazaarCache:
    """Mock BazaarCache returning pre-configured prices."""

    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = {k.upper(): float(v) for k, v in prices.items()}

    def get_buy_price(self, product_id: str) -> float | None:
        return self.prices.get(product_id.upper())


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        DRY_RUN=True,
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test",
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=5.0,
        MIN_SAMPLE_SIZE=2,
        AH_TAX_RATE=0.01,
        ENABLE_NETWORTH_VALUATION=True,
        ENABLE_CRAFT_CEILING=True,
        CRAFT_BUY_THRESHOLD_RATIO=0.95,
        CRAFT_CEILING_RATIO=1.05,
        CRAFT_MANIPULATION_THRESHOLD_RATIO=1.30,
        CRAFT_MANIPULATION_CEILING_RATIO=1.02,
        ENABLE_VELOCITY_FILTER=False,
    )


# ---------------------------------------------------------------------------
# 1. calculate_craft_cost tests
# ---------------------------------------------------------------------------

def test_calculate_craft_cost_valid_recipe():
    # SUPER_COMPACTOR_3000: 448 ENCHANTED_COBBLESTONE + 1 ENCHANTED_REDSTONE_BLOCK
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,
    })
    cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bz)
    assert cost == 448 * 1000 + 52000  # 500,000


def test_calculate_craft_cost_dict_bazaar():
    bz_dict = {
        "ENCHANTED_COBBLESTONE": {"buyPrice": 1200},
        "ENCHANTED_REDSTONE_BLOCK": {"buyPrice": 45000},
    }
    cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bz_dict)
    assert cost == 448 * 1200 + 45000


def test_calculate_craft_cost_missing_ingredient():
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        # Missing ENCHANTED_REDSTONE_BLOCK
    })
    cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bz)
    assert cost is None


def test_calculate_craft_cost_zero_price_ingredient():
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 0,
    })
    cost = calculate_craft_cost("SUPER_COMPACTOR_3000", bz)
    assert cost is None


def test_calculate_craft_cost_uncraftable_item():
    bz = MockBazaarCache({"SOME_DROP": 100000})
    cost = calculate_craft_cost("LIVID_DAGGER", bz)
    assert cost is None


def test_calculate_craft_cost_none_inputs():
    assert calculate_craft_cost("", MockBazaarCache({})) is None
    assert calculate_craft_cost("SUPER_COMPACTOR_3000", None) is None


# ---------------------------------------------------------------------------
# 2. Gate 1: Never Buy Above Craft Cost (Clean item rejection)
# ---------------------------------------------------------------------------

def test_craft_bait_rejection_clean_listing(test_config):
    """
    Craft cost is 500k.
    Seller lists for 1,800,000 (craft bait spread against artificial 2nd LBIN of 4,000,000).
    Buy price (1.8M) >= craft_cost * 0.95 (475k). Must be rejected immediately!
    """
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"SUPER_COMPACTOR_3000": [1_800_000, 4_000_000]})

    auction = {"uuid": "bait-1", "starting_bid": 1_800_000}
    decoded = {"internal_id": "SUPER_COMPACTOR_3000", "item_name": "Super Compactor 3000"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None, "Should reject listing priced above 95% of craft cost"


def test_craft_bait_rejection_at_exact_threshold(test_config):
    """Buy price is exactly at 95% of craft cost -> rejected."""
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,  # craft cost = 500,000
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"SUPER_COMPACTOR_3000": [475_000, 1_000_000]})

    auction = {"uuid": "bait-exact", "starting_bid": 475_000}  # exactly 500k * 0.95
    decoded = {"internal_id": "SUPER_COMPACTOR_3000", "item_name": "Super Compactor 3000"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


# ---------------------------------------------------------------------------
# 3. Gate 2 & Gate 3: Ceiling Clamping & Artificial Spread Guard
# ---------------------------------------------------------------------------

def test_craft_ceiling_clamping_moderate_spread(test_config):
    """
    Craft cost = 500,000.
    Buy price = 250,000 (< 475,000).
    2nd LBIN = 600,000 (ratio = 600k / 500k = 1.20 <= 1.30).
    Expected resell clamped to: min(600,000, 500,000 * 1.05) = 525,000.
    Net profit calculated on 525,000 after 1% tax:
    525,000 * 0.99 - 250,000 = 519,750 - 250,000 = 269,750.
    """
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"SUPER_COMPACTOR_3000": [250_000, 600_000]})

    auction = {"uuid": "valid-1", "starting_bid": 250_000}
    decoded = {"internal_id": "SUPER_COMPACTOR_3000", "item_name": "Super Compactor 3000"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.craft_cost == 500_000
    assert result.is_craft_capped is True
    assert result.target_resell_price == 525_000
    assert result.net_profit == int(525_000 * 0.99 - 250_000)


def test_craft_ceiling_manipulation_spread_guard(test_config):
    """
    Craft cost = 500,000.
    Buy price = 200,000.
    2nd LBIN = 1,500,000 (ratio = 3.0 > 1.30 -> artificial spread).
    Clamped strictly to 1.02x craft cost: 500,000 * 1.02 = 510,000.
    Net profit calculated on 510,000 after 1% tax:
    510,000 * 0.99 - 200,000 = 504,900 - 200,000 = 304,900.
    """
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"SUPER_COMPACTOR_3000": [200_000, 1_500_000]})

    auction = {"uuid": "manipulated-1", "starting_bid": 200_000}
    decoded = {"internal_id": "SUPER_COMPACTOR_3000", "item_name": "Super Compactor 3000"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.craft_cost == 500_000
    assert result.is_craft_capped is True
    assert result.target_resell_price == 510_000
    assert result.net_profit == int(510_000 * 0.99 - 200_000)


def test_craft_ceiling_not_capped_when_below_ceiling(test_config):
    """
    Craft cost = 500,000.
    Buy price = 250,000.
    2nd LBIN = 450,000 (below 500k * 1.05 = 525k).
    target_resell_price stays 450,000, is_craft_capped is False.
    """
    bz = MockBazaarCache({
        "ENCHANTED_COBBLESTONE": 1000,
        "ENCHANTED_REDSTONE_BLOCK": 52000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"SUPER_COMPACTOR_3000": [250_000, 450_000]})

    auction = {"uuid": "valid-uncapped", "starting_bid": 250_000}
    decoded = {"internal_id": "SUPER_COMPACTOR_3000", "item_name": "Super Compactor 3000"}

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.craft_cost == 500_000
    assert result.is_craft_capped is False
    assert result.target_resell_price == 450_000


# ---------------------------------------------------------------------------
# 4. Upgraded / Networth Flip Craft Ceiling
# ---------------------------------------------------------------------------

def test_upgraded_item_craft_ceiling(test_config):
    """
    Upgraded craftable item (e.g. JUJU_SHORTBOW with Recombobulator).
    JUJU_SHORTBOW craft cost:
      192 ENCHANTED_STRING @ 2,000 = 384,000
      32 ENCHANTED_IRON @ 1,000 = 32,000
      32 ENCHANTED_OBSIDIAN @ 1,000 = 32,000
      32 NULL_OVOID @ 10,000 = 320,000
      Total base craft cost = 768,000.
    Addon: Recombobulator 3000 @ 6,000,000, retention 70% -> +4,200,000.
    Effective craft cost = 768,000 + 4,200,000 = 4,968,000.
    If buy price is 2,000,000, and resell baseline is 8,000,000 (> 4,968,000 * 1.30 = 6,458,400):
    Resell is clamped to 1.02 * 4,968,000 = 5,067,360.
    """
    bz = MockBazaarCache({
        "ENCHANTED_STRING": 2000,
        "ENCHANTED_IRON": 1000,
        "ENCHANTED_OBSIDIAN": 1000,
        "NULL_OVOID": 10000,
        "RECOMBOBULATOR_3000": 6_000_000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"JUJU_SHORTBOW": [2_000_000, 3_000_000]})
    evaluator.set_clean_price_cache({"JUJU_SHORTBOW": [2_500_000]})

    auction = {"uuid": "juju-upgraded", "starting_bid": 2_000_000}
    decoded = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "rarity_upgrades": 1,
        "dungeon_stars": 0,
    }

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is not None
    assert result.craft_cost == 768_000
    assert result.is_craft_capped is True
    assert result.is_networth_snipe is True
    # For budget items (base_lbin 2.5M < 50M), dynamic retention rate is 50%:
    # retained_addon_value = 6,000,000 * 0.50 = 3,000,000
    # effective_craft_cost = 768,000 + 3,000,000 = 3,768,000
    # Unclamped resell_baseline (5.5M) > effective_craft_cost * 1.30 (4,898,400)
    # Clamped to 1.02 * 3,768,000 = 3,843,360
    assert result.target_resell_price == int(3_768_000 * 1.02)


def test_upgraded_item_buy_above_effective_craft_cost_rejected(test_config):
    """
    If seller lists an upgraded item at 10M when effective craft cost is 4.968M,
    buy_price (10M) >= effective_craft_cost * 0.95 -> rejected!
    """
    bz = MockBazaarCache({
        "ENCHANTED_STRING": 2000,
        "ENCHANTED_IRON": 1000,
        "ENCHANTED_OBSIDIAN": 1000,
        "NULL_OVOID": 10000,
        "RECOMBOBULATOR_3000": 6_000_000,
    })
    evaluator = FlipEvaluator(config=test_config, bazaar_cache=bz)
    evaluator.set_price_cache({"JUJU_SHORTBOW": [5_500_000, 8_000_000]})
    evaluator.set_clean_price_cache({"JUJU_SHORTBOW": [4_000_000]})

    auction = {"uuid": "juju-expensive", "starting_bid": 5_500_000}  # > 4.968M * 0.95 = 4.7196M
    decoded = {
        "internal_id": "JUJU_SHORTBOW",
        "item_name": "Juju Shortbow",
        "rarity_upgrades": 1,
    }

    result = evaluator.evaluate_listing(auction, decoded)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Embed Formatting and WebSocket Payload Integration
# ---------------------------------------------------------------------------

def test_embed_contains_craft_cost_field(test_config):
    notifier = DiscordNotifier(config=test_config)
    flip = FlipResult(
        auction_uuid="uuid-craft",
        internal_id="SUPER_COMPACTOR_3000",
        item_name="Super Compactor 3000",
        buy_price=250_000,
        target_resell_price=525_000,
        net_profit=269_750,
        margin_pct=107.9,
        craft_cost=500_000,
        is_craft_capped=True,
    )
    payload = notifier.build_embed_payload(flip)
    embed = payload["embeds"][0]
    field_names = [f["name"] for f in embed["fields"]]
    assert "Craft Cost Ceiling" in field_names

    craft_field = next(f for f in embed["fields"] if f["name"] == "Craft Cost Ceiling")
    assert "500,000 coins" in craft_field["value"]
    assert "Resell Capped at: 525,000" in craft_field["value"]


def test_websocket_payload_contains_craft_cost(test_config):
    flip = FlipResult(
        auction_uuid="uuid-ws-craft",
        internal_id="SUPER_COMPACTOR_3000",
        item_name="Super Compactor 3000",
        buy_price=250_000,
        target_resell_price=525_000,
        net_profit=269_750,
        margin_pct=107.9,
        craft_cost=500_000,
        is_craft_capped=True,
    )
    ws_data = build_flip_payload(flip)
    assert ws_data["craft_cost"] == 500_000
    assert ws_data["is_craft_capped"] is True
    assert any("Craft Cost: 500,000 (Capped)" in calc for calc in ws_data["calculations"])
