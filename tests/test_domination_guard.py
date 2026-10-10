"""Tests for Seller Domination Guard and Path A minimum listings requirement (Phase 6)."""

import pytest
from src.bazaar import BazaarCache
from src.config import Settings
from src.evaluator import FlipEvaluator, FlipResult


def test_seller_domination_ratio_calculation():
    evaluator = FlipEvaluator(config=Settings(DRY_RUN=True))

    # 10 listings total: seller-1 has 4 (40%), seller-2 has 3 (30%), seller-3 has 3 (30%)
    evaluator.set_seller_cache({
        "HYPERION": {
            "seller-1": 4,
            "seller-2": 3,
            "seller-3": 3,
        }
    })

    ratio = evaluator.get_seller_domination_ratio("HYPERION")
    assert ratio == 0.40
    assert evaluator.is_seller_dominated("HYPERION") is True


def test_seller_domination_not_dominated():
    evaluator = FlipEvaluator(config=Settings(DRY_RUN=True))

    # 5 listings: 5 different sellers (20% each)
    evaluator.set_seller_cache({
        "HYPERION": {
            f"seller-{i}": 1 for i in range(5)
        }
    })

    ratio = evaluator.get_seller_domination_ratio("HYPERION")
    assert ratio == 0.20
    assert evaluator.is_seller_dominated("HYPERION") is False


def test_path_a_minimum_listings_gate():
    config = Settings(
        DRY_RUN=True,
        PATH_A_MIN_LISTINGS=3,
        MIN_SAMPLE_SIZE=3,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    evaluator = FlipEvaluator(config=config)

    # Case 1: Only 2 listings on market (< 3 required)
    evaluator.update_cache([
        ({"starting_bid": 10_000_000, "auctioneer": "seller-1"}, {"internal_id": "NECRON_CHESTPLATE"}),
        ({"starting_bid": 15_000_000, "auctioneer": "seller-2"}, {"internal_id": "NECRON_CHESTPLATE"}),
    ])

    auction = {"uuid": "auc-1", "starting_bid": 10_000_000}
    decoded = {"internal_id": "NECRON_CHESTPLATE", "item_name": "Necron's Chestplate"}
    assert evaluator.evaluate_listing(auction, decoded) is None

    # Case 2: 3 listings on market (meets requirement)
    evaluator.update_cache([
        ({"starting_bid": 10_000_000, "auctioneer": "seller-1"}, {"internal_id": "NECRON_CHESTPLATE"}),
        ({"starting_bid": 15_000_000, "auctioneer": "seller-2"}, {"internal_id": "NECRON_CHESTPLATE"}),
        ({"starting_bid": 16_000_000, "auctioneer": "seller-3"}, {"internal_id": "NECRON_CHESTPLATE"}),
    ])

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.buy_price == 10_000_000
    assert flip.target_resell_price == 15_000_000


def test_clean_flip_rejected_when_seller_dominates():
    config = Settings(
        DRY_RUN=True,
        ENABLE_SELLER_DOMINATION_GUARD=True,
        MAX_SELLER_DOMINATION_RATIO=0.40,
        PATH_A_MIN_LISTINGS=3,
        MIN_SAMPLE_SIZE=3,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    evaluator = FlipEvaluator(config=config)

    # 5 listings: manipulator owns 3 out of 5 (60% >= 40%)
    evaluator.update_cache([
        ({"starting_bid": 10_000_000, "auctioneer": "bad-actor"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 15_000_000, "auctioneer": "bad-actor"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 16_000_000, "auctioneer": "bad-actor"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 17_000_000, "auctioneer": "innocent-1"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 18_000_000, "auctioneer": "innocent-2"}, {"internal_id": "TEST_SWORD"}),
    ])

    auction = {"uuid": "auc-dom-1", "starting_bid": 10_000_000}
    decoded = {"internal_id": "TEST_SWORD", "item_name": "Test Sword"}

    # Should be rejected due to seller domination guard
    assert evaluator.evaluate_listing(auction, decoded) is None

    # Now simulate a decentralized market (5 listings, each 20%)
    evaluator.update_cache([
        ({"starting_bid": 10_000_000, "auctioneer": "seller-1"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 15_000_000, "auctioneer": "seller-2"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 16_000_000, "auctioneer": "seller-3"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 17_000_000, "auctioneer": "seller-4"}, {"internal_id": "TEST_SWORD"}),
        ({"starting_bid": 18_000_000, "auctioneer": "seller-5"}, {"internal_id": "TEST_SWORD"}),
    ])

    flip = evaluator.evaluate_listing(auction, decoded)
    assert flip is not None
    assert flip.buy_price == 10_000_000


def test_upgraded_flip_rejected_when_seller_dominates():
    config = Settings(
        DRY_RUN=True,
        ENABLE_SELLER_DOMINATION_GUARD=True,
        MAX_SELLER_DOMINATION_RATIO=0.40,
        ENABLE_NETWORTH_VALUATION=True,
        MIN_SAMPLE_SIZE=3,
        PATH_A_MIN_LISTINGS=3,
        MIN_PROFIT_COINS=100_000,
        MIN_PROFIT_MARGIN_PCT=10.0,
    )
    bazaar = BazaarCache(config=config)
    bazaar.set_mock_prices({"RECOMBOBULATOR_3000": 8_000_000})

    evaluator = FlipEvaluator(config=config, bazaar_cache=bazaar)

    # 4 listings: bad-actor owns 2 (50% >= 40%)
    evaluator.update_cache([
        ({"starting_bid": 20_000_000, "auctioneer": "bad-actor"}, {"internal_id": "SHADOW_ASSASSIN_CHESTPLATE"}),
        ({"starting_bid": 22_000_000, "auctioneer": "bad-actor"}, {"internal_id": "SHADOW_ASSASSIN_CHESTPLATE"}),
        ({"starting_bid": 24_000_000, "auctioneer": "legit-1"}, {"internal_id": "SHADOW_ASSASSIN_CHESTPLATE"}),
        ({"starting_bid": 25_000_000, "auctioneer": "legit-2"}, {"internal_id": "SHADOW_ASSASSIN_CHESTPLATE"}),
    ])

    auction = {"uuid": "auc-upg-dom", "starting_bid": 18_000_000}
    decoded = {
        "internal_id": "SHADOW_ASSASSIN_CHESTPLATE",
        "item_name": "Shadow Assassin Chestplate",
        "rarity_upgrades": 1,
    }

    # Should be rejected because item market is dominated by bad-actor
    assert evaluator.evaluate_listing(auction, decoded) is None
