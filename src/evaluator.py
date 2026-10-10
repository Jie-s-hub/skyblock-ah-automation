"""Auction House arbitrage flipping evaluator and profit calculation engine."""

from __future__ import annotations

import bisect
import json
import logging
import math
import re
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)


def calculate_percentile(values: list[int], pct: float) -> float:
    """Calculate linear interpolation percentile for a list of integer values."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    k = (n - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_v[int(k)])
    d0 = sorted_v[int(f)] * (c - k)
    d1 = sorted_v[int(c)] * (k - f)
    return float(d0 + d1)


from pydantic import BaseModel, Field

from src.bazaar import BazaarCache
from src.config import Settings
from src.constants.attributes import (
    calculate_attribute_estimated_value,
    check_god_roll,
    format_attributes,
)
from src.constants.pet_levels import (
    calculate_pet_level,
    get_held_item_group,
    get_pet_bucket_key,
    get_pet_held_item_info,
    get_pet_level_bracket,
    get_pet_level_premium,
    get_pet_max_level,
    is_pet_xp_in_window,
)
from src.constants.reforge_stones import REFORGE_STONE_DISPLAY_NAMES, REFORGE_STONE_MAP
from src.constants.star_costs import get_star_essence_cost
from src.constants.recipes import get_base_item_id, get_recipe
from src.craft_evaluator import calculate_craft_cost
from src.velocity import SalesVelocityTracker


class FlipResult(BaseModel):
    """Structured data container representing a validated arbitrage opportunity."""

    auction_uuid: str
    internal_id: str
    item_name: str
    buy_price: int
    target_resell_price: int
    net_profit: int
    margin_pct: float
    dungeon_stars: int = 0
    rarity_upgrades: int = 0
    enchantments: dict[str, int] = Field(default_factory=dict)
    daily_velocity: Optional[float] = None
    is_priority: bool = False
    staple_category: Optional[str] = None

    # Dual-mode & Networth Engine attributes
    is_networth_snipe: bool = False
    base_lbin: Optional[int] = None
    modifier: Optional[str] = None
    addon_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    total_addon_cost: int = 0
    intrinsic_worth: int = 0
    master_stars: int = 0
    gemstones: list[dict[str, str]] = Field(default_factory=list)
    retention_rate: float = 0.70

    # Crimson Isle Attribute valuation attributes
    is_attribute_snipe: bool = False
    is_god_roll: bool = False
    god_roll_type: Optional[str] = None
    attributes: dict[str, int] = Field(default_factory=dict)

    # Pet valuation attributes
    is_pet: bool = False
    pet_type: Optional[str] = None
    pet_tier: Optional[str] = None
    effective_rarity: Optional[str] = None
    pet_level: Optional[int] = None
    pet_exp: Optional[float] = None
    pet_held_item: Optional[str] = None
    pet_held_item_name: Optional[str] = None
    held_item_value: int = 0
    base_lvl_1_price: Optional[int] = None
    pet_candy_used: int = 0
    pet_skin: Optional[str] = None
    baseline_source: Optional[str] = None
    reference_price: Optional[int] = None
    comp_count: Optional[int] = None
    comp_median: Optional[int] = None
    rejection_reason: Optional[str] = None
    pet_bucket_key: Optional[str] = None

    # Guardrails and High-Value Gear attributes
    is_out_of_budget: bool = False
    prestige_tier: Optional[str] = None
    gemstone_chambers: int = 0
    art_of_war_count: int = 0
    wood_singularity_count: int = 0
    transmission_tuners: int = 0

    # Craft Cost Ceiling attributes
    craft_cost: Optional[int] = None
    is_craft_capped: bool = False

    # Intelligence & Confidence attributes (Phase 1 & Phase 5)
    confidence_label: str = "MEDIUM"
    valuation_path: str = "PATH_A"
    confidence_score: Optional[float] = None
    estimated_hold_hours: Optional[float] = None
    capital_efficiency: Optional[float] = None

    # Distribution-aligned & Queue-depth attributes
    conservative_target: Optional[int] = None
    optimistic_target: Optional[int] = None
    expected_profit: Optional[int] = None
    optimistic_upside: Optional[int] = None
    queue_depth: int = 0

    # Phase 1 Fee Model and Dead Zone Cliff Optimization
    suggested_list_price: Optional[int] = None
    derpy_active: bool = False
    fee_model_version: int = 2



PROGRESSION_STAPLES: dict[str, str] = {
    # Wither Armor Sets (WITHER_ARMOR)
    "WITHER_HELMET": "WITHER_ARMOR",
    "WITHER_CHESTPLATE": "WITHER_ARMOR",
    "WITHER_LEGGINGS": "WITHER_ARMOR",
    "WITHER_BOOTS": "WITHER_ARMOR",
    "NECRON_HELMET": "WITHER_ARMOR",
    "NECRON_CHESTPLATE": "WITHER_ARMOR",
    "NECRON_LEGGINGS": "WITHER_ARMOR",
    "NECRON_BOOTS": "WITHER_ARMOR",
    "STORM_HELMET": "WITHER_ARMOR",
    "STORM_CHESTPLATE": "WITHER_ARMOR",
    "STORM_LEGGINGS": "WITHER_ARMOR",
    "STORM_BOOTS": "WITHER_ARMOR",
    "MAXOR_HELMET": "WITHER_ARMOR",
    "MAXOR_CHESTPLATE": "WITHER_ARMOR",
    "MAXOR_LEGGINGS": "WITHER_ARMOR",
    "MAXOR_BOOTS": "WITHER_ARMOR",
    "GOLDOR_HELMET": "WITHER_ARMOR",
    "GOLDOR_CHESTPLATE": "WITHER_ARMOR",
    "GOLDOR_LEGGINGS": "WITHER_ARMOR",
    "GOLDOR_BOOTS": "WITHER_ARMOR",
    # Midgame Combat Gear (MIDGAME_COMBAT)
    "LIVID_DAGGER": "MIDGAME_COMBAT",
    "JUJU_SHORTBOW": "MIDGAME_COMBAT",
    "SHADOW_FURY": "MIDGAME_COMBAT",
    "FELTHORN_REAPER": "MIDGAME_COMBAT",
    "BOUQUET_OF_LIES": "MIDGAME_COMBAT",
    "FLOWER_OF_TRUTH": "MIDGAME_COMBAT",
    "SPIRIT_SCEPTRE": "MIDGAME_COMBAT",
    "YETI_SWORD": "MIDGAME_COMBAT",
    "GIANTS_SWORD": "MIDGAME_COMBAT",
    "GIANT_SWORD": "MIDGAME_COMBAT",
    "SHADOW_ASSASSIN_HELMET": "MIDGAME_COMBAT",
    "SHADOW_ASSASSIN_CHESTPLATE": "MIDGAME_COMBAT",
    "SHADOW_ASSASSIN_LEGGINGS": "MIDGAME_COMBAT",
    "SHADOW_ASSASSIN_BOOTS": "MIDGAME_COMBAT",
    # Endgame Progression (ENDGAME_PROGRESSION)
    "HYPERION": "ENDGAME_PROGRESSION",
    "VALKYRIE": "ENDGAME_PROGRESSION",
    "SCYLLA": "ENDGAME_PROGRESSION",
    "ASTRAEA": "ENDGAME_PROGRESSION",
    "TERMINATOR": "ENDGAME_PROGRESSION",
    "DARK_CLAYMORE": "ENDGAME_PROGRESSION",
    # Slayer Craftables (SLAYER_CRAFTABLE)
    "WARDEN_HELMET": "SLAYER_CRAFTABLE",
    "REAPER_SCYTHE": "SLAYER_CRAFTABLE",
    "AXE_OF_THE_SHREDDED": "SLAYER_CRAFTABLE",
    "REAPER_MASK": "SLAYER_CRAFTABLE",
    "REAPER_FALCHION": "SLAYER_CRAFTABLE",
    "TARANTULA_HELMET": "SLAYER_CRAFTABLE",
    "SCORPION_FOIL": "SLAYER_CRAFTABLE",
    "MANA_FLUX_POWER_ORB": "SLAYER_CRAFTABLE",
    "OVERFLUX_POWER_ORB": "SLAYER_CRAFTABLE",
    "PLASMAFLUX_POWER_ORB": "SLAYER_CRAFTABLE",
    "VORPAL_KATANA": "SLAYER_CRAFTABLE",
    "ATOMSPLIT_KATANA": "SLAYER_CRAFTABLE",
    "FINAL_DESTINATION_HELMET": "SLAYER_CRAFTABLE",
    "FINAL_DESTINATION_CHESTPLATE": "SLAYER_CRAFTABLE",
    "FINAL_DESTINATION_LEGGINGS": "SLAYER_CRAFTABLE",
    "FINAL_DESTINATION_BOOTS": "SLAYER_CRAFTABLE",
}


class FlipEvaluator:
    """Evaluates active SkyBlock BIN listings against market baselines for flips."""

    def __init__(
        self,
        config: Settings,
        velocity_tracker: Optional[SalesVelocityTracker] = None,
        bazaar_cache: Optional[BazaarCache] = None,
        comps_evaluator: Optional[Any] = None,
        retention_fitter: Optional[Any] = None,
        db: Optional[Any] = None,
        survival_auditor: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.velocity_tracker = velocity_tracker
        self.bazaar_cache = bazaar_cache
        self.comps_evaluator = comps_evaluator
        self.retention_fitter = retention_fitter
        self.db = db or (getattr(comps_evaluator, "db", None) if comps_evaluator else None)
        self.survival_auditor = survival_auditor
        self._price_cache: dict[str, list[int]] = {}
        self._clean_price_cache: dict[str, list[int]] = {}
        self._seller_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._item_listing_counts: dict[str, int] = defaultdict(int)
        self._pet_buckets: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        self.last_pet_rejections: dict[str, str] = {}
        self._pet_dedupe_cache: dict[tuple, float] = {}
        self.derpy_active: bool = bool(getattr(config, "MAYOR_DERPY_ACTIVE", False))
        self.derpy_collection_fee_rate: float = float(getattr(config, "DERPY_COLLECTION_FEE_RATE", 0.04))

    def set_derpy_active(self, active: bool) -> None:
        """Set whether Mayor Derpy is currently active."""
        self.derpy_active = active

    def set_db(self, db: Any) -> None:
        """Set or update the persistence database manager."""
        self.db = db

    def set_comps_evaluator(self, comps_evaluator: Any) -> None:
        """Set or update the comparable sales evaluator."""
        self.comps_evaluator = comps_evaluator
        if self.db is None and hasattr(comps_evaluator, "db"):
            self.db = comps_evaluator.db

    def set_retention_fitter(self, retention_fitter: Any) -> None:
        """Set or update the Empirical Bayes retention fitter."""
        self.retention_fitter = retention_fitter

    def set_survival_auditor(self, survival_auditor: Any) -> None:
        """Set or update the market survival auditor."""
        self.survival_auditor = survival_auditor

    def calculate_queue_depth(
        self,
        internal_id: str,
        target_resell: int,
        buy_price: Optional[int] = None,
        pet_bucket_key: Optional[tuple] = None,
    ) -> int:
        """
        Count of active listings sitting ahead in the order book (price < target_resell).
        Excludes the candidate listing itself if present at buy_price.
        """
        if pet_bucket_key is not None and pet_bucket_key in self._pet_buckets:
            listings = self._pet_buckets[pet_bucket_key]
            count = sum(1 for item in listings if int(item.get("price", 0)) < target_resell)
            if buy_price is not None and buy_price < target_resell:
                if any(int(item.get("price", 0)) == buy_price for item in listings):
                    count = max(0, count - 1)
            return count

        prices = self._price_cache.get(internal_id, [])
        if not prices:
            return 0
        count = sum(1 for p in prices if p < target_resell)
        if buy_price is not None and buy_price < target_resell and buy_price in prices:
            count = max(0, count - 1)
        return count

    def _apply_economic_hardening(
        self,
        target_resell: int,
        valuation_path: str,
    ) -> int:
        """
        Applies lane conversion haircut (if conversion rate < 40%)
        and snaps price down to tax cliff boundaries if cliff snapping is enabled.
        """
        resell = target_resell
        if getattr(self.config, "ENABLE_LANE_CONVERSION_HAIRCUT", True) and self.survival_auditor is not None:
            haircut = self.survival_auditor.get_lane_haircut(valuation_path)
            if haircut > 0.0:
                resell = int(resell * (1.0 - haircut))

        if getattr(self.config, "ENABLE_CLIFF_SNAPPING", True):
            from src.constants.taxes import optimize_target_resell
            resell = optimize_target_resell(resell, derpy=getattr(self.config, "MAYOR_DERPY_ACTIVE", False))

        return resell

    @property
    def price_cache(self) -> dict[str, list[int]]:
        """Return the current in-memory price cache mapping internal_id to sorted prices."""
        return self._price_cache

    @property
    def pet_buckets(self) -> dict[tuple, list[dict[str, Any]]]:
        """Return the current in-memory pet buckets mapping bucket_key to active listings."""
        return self._pet_buckets

    def set_pet_bucket_cache(self, buckets: dict[tuple, list[dict[str, Any]]]) -> None:
        """Set or mock pet bucket cache (useful for testing)."""
        self._pet_buckets = defaultdict(list, buckets)

    def get_last_pet_rejection_reason(self, uuid: str) -> Optional[str]:
        """Retrieve rejection reason code for a pet listing UUID."""
        return self.last_pet_rejections.get(uuid)

    @property
    def clean_price_cache(self) -> dict[str, list[int]]:
        """Return the current in-memory price cache of clean listings (0 stars, 0 recomb)."""
        return self._clean_price_cache

    def set_price_cache(
        self,
        cache: dict[str, list[int]],
        clean_cache: Optional[dict[str, list[int]]] = None,
    ) -> None:
        """Explicitly set or mock the internal price cache (useful for testing)."""
        self._price_cache = {k: sorted(v) for k, v in cache.items()}
        if clean_cache is not None:
            self._clean_price_cache = {k: sorted(v) for k, v in clean_cache.items()}
        else:
            self._clean_price_cache = {}
        for k, v in self._price_cache.items():
            if k not in self._item_listing_counts or self._item_listing_counts[k] == 0:
                self._item_listing_counts[k] = len(v)

    def set_clean_price_cache(self, cache: dict[str, list[int]]) -> None:
        """Explicitly set or mock the clean price cache (useful for testing)."""
        self._clean_price_cache = {k: sorted(v) for k, v in cache.items()}

    def set_seller_cache(
        self,
        seller_counts: dict[str, dict[str, int]],
        listing_counts: Optional[dict[str, int]] = None,
    ) -> None:
        """Set mock seller distribution for unit testing."""
        self._seller_counts = defaultdict(
            lambda: defaultdict(int),
            {k: defaultdict(int, v) for k, v in seller_counts.items()}
        )
        if listing_counts is not None:
            self._item_listing_counts = defaultdict(int, listing_counts)
        else:
            self._item_listing_counts = defaultdict(
                int,
                {k: sum(v.values()) for k, v in seller_counts.items()}
            )

    def get_seller_domination_ratio(self, internal_id: str) -> float:
        """Return the highest listing ownership percentage by a single seller for an item."""
        total = self._item_listing_counts.get(internal_id, 0)
        if total <= 0:
            prices = self._price_cache.get(internal_id, [])
            total = len(prices)
        if total <= 0:
            return 0.0

        seller_dict = self._seller_counts.get(internal_id, {})
        if not seller_dict:
            return 0.0

        max_count = max(seller_dict.values())
        return float(max_count) / float(total)

    def is_seller_dominated(
        self,
        internal_id: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        Anti-Domination Guard: Check if a single seller controls >= 40% (or threshold)
        of the active market listings for an item.
        """
        total = self._item_listing_counts.get(internal_id, 0)
        min_listings = getattr(self.config, "PATH_A_MIN_LISTINGS", 3)
        if total < min_listings:
            prices = self._price_cache.get(internal_id, [])
            if len(prices) < min_listings:
                return False

        ratio = self.get_seller_domination_ratio(internal_id)
        dom_thresh = (
            threshold
            if threshold is not None
            else getattr(self.config, "MAX_SELLER_DOMINATION_RATIO", 0.40)
        )
        return ratio >= dom_thresh

    def get_base_lbin(self, internal_id: str) -> Optional[int]:
        """
        Lookup baseline LBIN for an item.
        1. First try active listings with 0 stars and rarity_upgrades == 0.
        2. Fallback: If 0 clean listings exist, use the absolute lowest BIN currently active for that internal_id.
        3. Fragged fallback: If this is a fragged item (e.g. STARRED_BAT_WAND), fall back to base item floor.
        4. Returns None safely if no listings exist at all.
        """
        clean_prices = self._clean_price_cache.get(internal_id, [])
        if clean_prices:
            return clean_prices[0]

        all_prices = self._price_cache.get(internal_id, [])
        if all_prices:
            return all_prices[0]

        base_id = get_base_item_id(internal_id)
        if base_id and base_id != internal_id:
            return self.get_base_lbin(base_id)

        return None

    def calculate_net_revenue(self, resell_price: int) -> int:
        """
        Calculate net revenue from target resell price accounting for creation and collection fees.
        src/constants/taxes.py is the single source of truth.
        """
        from src.constants.taxes import net_revenue
        return net_revenue(
            resell_price,
            derpy_active=self.derpy_active,
            derpy_rate=self.derpy_collection_fee_rate,
        )

    def is_blacklisted(self, internal_id: str, item_name: str = "") -> bool:
        """
        Universal Blacklist Gate: drops cosmetic, vanity, and illiquid bait items.
        Runs at the very beginning of evaluation, before Path A or Path B.

        Keywords to block:
        - _SKIN (covers _SKIN_, _SKIN suffix, PET_SKIN_, etc.)
        - PET_SKIN_
        - DYE_
        - RUNE
        - PORTAL
        - FURNITURE
        - MUSIC_DISC
        """
        if not self.config.COSMETIC_FILTER_ENABLED:
            return False

        if not internal_id or internal_id == "UNKNOWN":
            return False

        upper_id = internal_id.strip().upper()

        # Stage 0 Vanity Lane Check (Phase 7):
        if getattr(self.config, "ENABLE_VANITY_COMPS_LANE", False):
            from src.addon_rules import is_permanently_blacklisted, is_vanity_item

            if is_permanently_blacklisted(upper_id, item_name):
                return True
            if is_vanity_item(upper_id, item_name):
                if self.comps_evaluator is not None and getattr(self.config, "ENABLE_SOLD_COMPS", False):
                    db = getattr(self.comps_evaluator, "db", None)
                    if db is not None:
                        sales = db.get_sales(internal_id=upper_id, limit=getattr(self.config, "MIN_COMPS_THRESHOLD", 3))
                        if len(sales) >= getattr(self.config, "MIN_COMPS_THRESHOLD", 3):
                            return False
                return True

        # 1. Keywords to block
        if "_SKIN" in upper_id:
            return True

        if upper_id.startswith("PET_SKIN_") or "PET_SKIN" in upper_id:
            return True

        if upper_id.startswith("DYE_") or "DYE_" in upper_id:
            return True

        # RUNE (exact ID, prefix RUNE_, suffix _RUNE, or _RUNE_ infix)
        if upper_id == "RUNE" or upper_id.startswith("RUNE_") or upper_id.endswith("_RUNE") or "_RUNE_" in upper_id:
            return True

        if "PORTAL" in upper_id:
            return True

        if "FURNITURE" in upper_id:
            return True

        if "MUSIC_DISC" in upper_id:
            return True

        # 2. Configured exact IDs
        if any(upper_id == exact_id.strip().upper() for exact_id in self.config.COSMETIC_BLACKLIST_EXACT_IDS):
            return True

        # 3. Configured prefixes
        for prefix in self.config.COSMETIC_BLACKLIST_PREFIXES:
            if upper_id.startswith(prefix.strip().upper()):
                return True

        # 4. Configured substrings
        for sub in self.config.COSMETIC_BLACKLIST_SUBSTRINGS:
            if sub.strip().upper() in upper_id:
                return True

        # 5. Standalone rune display name check (e.g. "Couture Rune I", "Blood Rune III")
        if item_name:
            clean_name = item_name.strip(" \t\n\r◆")
            if re.search(r"\bRune(\s+[IVXLCDM]+)?$", clean_name, re.IGNORECASE):
                return True

        return False

    def is_blacklisted_cosmetic(self, internal_id: str, item_name: str = "") -> bool:
        """Alias for is_blacklisted to maintain full backward compatibility."""
        return self.is_blacklisted(internal_id, item_name)

    def is_valuable_enchant(self, enchant_name: str, level: int) -> bool:
        """Determine if an enchantment qualifies as a notable, valuable upgrade."""
        name_upper = enchant_name.strip().upper()
        if "ULTIMATE" in name_upper or name_upper in {
            "SOUL_EATER",
            "WISDOM",
            "LEGION",
            "CHIMERA",
            "ONE_FOR_ALL",
            "SWARM",
            "FATAL_TEMPO",
            "REND",
            "COMBO",
            "BANK",
            "ULTIMATE_JERRY",
            "LAST_STAND",
            "NO_PAIN_NO_GAIN",
            "FLASH",
            "DUPLEX",
            "INFERNO",
            "HABANERO_TACTICS",
            "ULTIMATE_WISE",
            "WISE",
        }:
            return True
        if level >= 6:
            return True
        if level >= 5 and name_upper in {"OVERLOAD", "MANA_VAMPIRE", "FIRST_STRIKE", "VICIOUS", "REJUVENATE"}:
            return True
        return False

    def is_upgraded_item(self, decoded_data: dict[str, Any]) -> bool:
        """
        Check if an item has notable addons:
        - rarity_upgrades >= 1 (Recombobulated), OR
        - dungeon_stars > 0, OR
        - master_stars > 0, OR
        - Applied gemstones exist, OR
        - Contains valuable enchants, OR
        - Has an applied reforge stone modifier, OR
        - Unlocked gemstone chambers (Divan's armor), OR
        - The Art of War applied, OR
        - Wood Singularity applied, OR
        - Transmission Tuners applied.
        """
        if int(decoded_data.get("rarity_upgrades", 0)) >= 1:
            return True
        if int(decoded_data.get("dungeon_stars", 0)) > 0:
            return True
        if int(decoded_data.get("master_stars", 0)) > 0:
            return True
        if len(decoded_data.get("gemstones", [])) > 0:
            return True
        if int(decoded_data.get("gemstone_chambers", 0)) > 0:
            return True
        if int(decoded_data.get("art_of_war_count", 0)) > 0:
            return True
        if int(decoded_data.get("wood_singularity_count", 0)) > 0:
            return True
        if int(decoded_data.get("transmission_tuners", 0)) > 0:
            return True
        if int(decoded_data.get("hot_potato_count", 0)) > 0:
            return True
        if bool(decoded_data.get("ability_scrolls")):
            return True
        if bool(decoded_data.get("talisman_enrichment")):
            return True
        if any(bool(decoded_data.get(k)) for k in ("drill_part_engine", "drill_part_fuel_tank", "drill_part_upgrade_module")):
            return True
        modifier = str(decoded_data.get("modifier", "")).strip().lower()
        if modifier and modifier in REFORGE_STONE_MAP and REFORGE_STONE_MAP[modifier]:
            return True
        enchants = decoded_data.get("enchantments", {})
        for name, lvl in enchants.items():
            if self.is_valuable_enchant(name, lvl):
                return True
        return False

    def _format_enchant_display_name(self, enchant_name: str) -> str:
        """Format an internal enchant name into clean title case with Ultimate prefix if applicable."""
        clean = enchant_name.replace("ultimate_", "").replace("_", " ").strip()
        if enchant_name.lower().startswith("ultimate_") or enchant_name.lower() in {
            "soul_eater",
            "wisdom",
            "legion",
            "chimera",
            "one_for_all",
            "swarm",
            "fatal_tempo",
            "rend",
            "combo",
            "bank",
            "ultimate_jerry",
            "last_stand",
            "no_pain_no_gain",
            "flash",
            "duplex",
            "inferno",
            "ultimate_wise",
            "wise",
        }:
            clean = "Ultimate " + clean
        return " ".join(w.capitalize() for w in clean.split())

    def _resolve_enchant_bazaar_cost(self, enchant_name: str, level: int) -> tuple[str, int]:
        """
        Lookup instant-buy price for an enchanted book with Minecraft anvil tier compounding.
        Delegates to src.networth_evaluator.calculate_enchant_bazaar_cost.
        """
        from src.enchant_evaluator import get_enchant_bazaar_cost

        return get_enchant_bazaar_cost(self.bazaar_cache, enchant_name, level)

    @staticmethod
    def _is_expired(auction: dict[str, Any], now_ms: float) -> bool:
        """Return True if the auction has a known end time that has already passed."""
        end_ms = auction.get("end")
        if not end_ms:
            return False
        try:
            return float(end_ms) <= now_ms
        except (ValueError, TypeError):
            return False

    def update_cache(
        self,
        decoded_auctions: list[tuple[dict[str, Any], dict[str, Any]]],
        now_ms: Optional[float] = None,
    ) -> None:
        """
        Rebuild the in-memory price cache from active BIN listings.
        Tracks all active listings as well as clean listings (0 stars, 0 rarity upgrades, 0 secondary addons).
        Filters out expired auctions (end time already passed). Listings are NOT dropped by start age:
        a BIN listed 20h ago that is still live is real competition and must count toward the floor.

        Args:
            decoded_auctions: Sequence of (raw_auction_dict, decoded_nbt_dict) pairs.
            now_ms: Optional reference timestamp in milliseconds for expiry calculation.
        """
        new_cache: dict[str, list[int]] = defaultdict(list)
        clean_cache: dict[str, list[int]] = defaultdict(list)
        new_seller_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        new_listing_counts: dict[str, int] = defaultdict(int)
        new_pet_buckets: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        current_now_ms = now_ms if now_ms is not None else (time.time() * 1000.0)

        for auction, decoded in decoded_auctions:
            internal_id = decoded.get("internal_id")
            if not internal_id or internal_id == "UNKNOWN":
                continue

            item_name = decoded.get("item_name") or auction.get("item_name", "")
            if self.is_blacklisted(internal_id, item_name):
                continue

            # Expired listing guardrail: drop auctions whose end time has already passed
            if self._is_expired(auction, current_now_ms):
                continue
            start_ms = auction.get("start")

            price = auction.get("starting_bid")
            if price is not None and isinstance(price, (int, float)) and price > 0:
                item_count = max(1, int(decoded.get("count") or 1))
                int_price = int(price) // item_count
                new_cache[internal_id].append(int_price)
                new_listing_counts[internal_id] += 1

                seller = str(auction.get("auctioneer") or auction.get("seller") or "")
                if seller:
                    new_seller_counts[internal_id][seller] += 1

                # Clean item check: must strictly match is_upgraded_item (no enchants, gems, reforges, etc.)
                if not self.is_upgraded_item(decoded):
                    clean_cache[internal_id].append(int_price)

                if decoded.get("is_pet"):
                    minor_thresh = getattr(self.config, "PET_MINOR_HELD_ITEM_THRESHOLD_COINS", 2_000_000)
                    b_key = get_pet_bucket_key(decoded, minor_threshold=minor_thresh, bazaar_cache=self.bazaar_cache)
                    new_pet_buckets[b_key].append({
                        "uuid": str(auction.get("uuid", "")),
                        "price": int_price,
                        "xp": float(decoded.get("pet_exp", 0.0)),
                        "level": int(decoded.get("pet_level", 1)),
                        "auctioneer": seller,
                        "start": start_ms,
                        "item_name": item_name,
                        "candy_used": int(decoded.get("pet_candy_used", 0)),
                        "skin": decoded.get("pet_skin"),
                        "held_item": decoded.get("pet_held_item", ""),
                        "effective_tier": decoded.get("effective_tier") or decoded.get("pet_tier", "COMMON"),
                        "pet_type": decoded.get("pet_type", ""),
                    })

        for internal_id in new_cache:
            new_cache[internal_id].sort()

        for internal_id in clean_cache:
            clean_cache[internal_id].sort()

        for b_key in new_pet_buckets:
            new_pet_buckets[b_key].sort(key=lambda x: x["price"])

        self._price_cache = dict(new_cache)
        self._clean_price_cache = dict(clean_cache)
        self._seller_counts = new_seller_counts
        self._item_listing_counts = new_listing_counts
        self._pet_buckets = new_pet_buckets

    def insert_listing(
        self,
        auction: dict[str, Any],
        decoded: dict[str, Any],
        now_ms: Optional[float] = None,
    ) -> None:
        """
        Insert a newly fetched single auction listing into active indexes in-place.
        Used by low-latency streaming WebSocket feeds before evaluating the candidate.
        """
        internal_id = decoded.get("internal_id")
        if not internal_id or internal_id == "UNKNOWN":
            return

        item_name = decoded.get("item_name") or auction.get("item_name", "")
        if self.is_blacklisted(internal_id, item_name):
            return

        price = auction.get("starting_bid")
        if price is None or not isinstance(price, (int, float)) or price <= 0:
            return
        item_count = max(1, int(decoded.get("count") or 1))
        int_price = int(price) // item_count

        start_ms = auction.get("start")
        current_now_ms = now_ms if now_ms is not None else (time.time() * 1000.0)
        if self._is_expired(auction, current_now_ms):
            return

        if internal_id not in self._price_cache:
            self._price_cache[internal_id] = []
        bisect.insort(self._price_cache[internal_id], int_price)
        self._item_listing_counts[internal_id] += 1

        seller = str(auction.get("auctioneer") or auction.get("seller") or "")
        if seller:
            self._seller_counts[internal_id][seller] += 1

        if not self.is_upgraded_item(decoded):
            if internal_id not in self._clean_price_cache:
                self._clean_price_cache[internal_id] = []
            bisect.insort(self._clean_price_cache[internal_id], int_price)

        if decoded.get("is_pet"):
            minor_thresh = getattr(self.config, "PET_MINOR_HELD_ITEM_THRESHOLD_COINS", 2_000_000)
            b_key = get_pet_bucket_key(decoded, minor_threshold=minor_thresh, bazaar_cache=self.bazaar_cache)
            record = {
                "uuid": str(auction.get("uuid", "")),
                "price": int_price,
                "xp": float(decoded.get("pet_exp", 0.0)),
                "level": int(decoded.get("pet_level", 1)),
                "auctioneer": seller,
                "start": start_ms,
                "item_name": item_name,
                "candy_used": int(decoded.get("pet_candy_used", 0)),
                "skin": decoded.get("pet_skin"),
                "held_item": decoded.get("pet_held_item", ""),
                "effective_tier": decoded.get("effective_tier") or decoded.get("pet_tier", "COMMON"),
                "pet_type": decoded.get("pet_type", ""),
            }
            bucket_list = self._pet_buckets[b_key]
            insert_idx = 0
            while insert_idx < len(bucket_list) and bucket_list[insert_idx]["price"] <= int_price:
                insert_idx += 1
            bucket_list.insert(insert_idx, record)

    def _evaluate_upgraded_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
    ) -> Optional[FlipResult]:
        """
        Path B: Upgraded Flip Evaluation (Networth Engine).
        Calculates intrinsic networth of attached addons from Bazaar prices
        and resells using dynamic tier-based retention rates.
        """
        if not self.bazaar_cache:
            return None

        internal_id = decoded_data["internal_id"]
        base_lbin = self.get_base_lbin(internal_id)
        if base_lbin is None:
            if sorted_prices:
                base_lbin = sorted_prices[0]
            else:
                return None
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None

        from src.networth_evaluator import calculate_item_networth

        override_retention = None
        if getattr(self.config, "ENABLE_DYNAMIC_RETENTION", False) and self.retention_fitter is not None:
            override_retention = self.retention_fitter.get_retention_rate(internal_id, base_lbin)

        addon_breakdown, total_addon_cost, intrinsic_worth, retention_rate, resell_baseline = (
            calculate_item_networth(
                base_lbin=base_lbin,
                decoded_data=decoded_data,
                bazaar_cache=self.bazaar_cache,
                is_valuable_enchant_fn=self.is_valuable_enchant,
                format_enchant_fn=self._format_enchant_display_name,
                override_retention_rate=override_retention,
            )
        )

        if total_addon_cost <= 0:
            return None

        # Bazaar Price Shock Guard (Phase 6): discard if any addon has an active price shock
        if getattr(self.config, "ENABLE_BAZAAR_SHOCK_GUARD", False) and self.bazaar_cache is not None:
            for addon in addon_breakdown:
                for prod_id in addon.get("product_ids", []):
                    if prod_id and hasattr(self.bazaar_cache, "is_price_shocked") and self.bazaar_cache.is_price_shocked(prod_id):
                        logger.debug(
                            "Rejecting upgraded flip for %s: addon %s is price shocked in Bazaar",
                            internal_id,
                            prod_id,
                        )
                        return None

        # Anti-Domination Guard (Phase 6): reject if listings dominated by one seller
        if getattr(self.config, "ENABLE_SELLER_DOMINATION_GUARD", False):
            if self.is_seller_dominated(internal_id):
                return None

        if resell_baseline <= buy_price:
            return None

        craft_cost: Optional[int] = None
        is_craft_capped: bool = False

        if getattr(self.config, "ENABLE_CRAFT_CEILING", True) and self.bazaar_cache is not None:
            craft_cost = calculate_craft_cost(internal_id, self.bazaar_cache, ah_cache=self)

        if craft_cost is not None:
            retained_addon_value = int(round(total_addon_cost * retention_rate))
            effective_craft_cost = craft_cost + retained_addon_value

            # Gate 1 (Never Buy Above Craft Cost)
            buy_threshold = effective_craft_cost * getattr(self.config, "CRAFT_BUY_THRESHOLD_RATIO", 0.95)
            if buy_price >= buy_threshold:
                return None

            # Gate 3 (Artificial Spread / Bait Guard): >30% higher than craft cost
            manipulation_threshold = effective_craft_cost * getattr(self.config, "CRAFT_MANIPULATION_THRESHOLD_RATIO", 1.30)
            if resell_baseline > manipulation_threshold:
                resell_baseline = int(effective_craft_cost * getattr(self.config, "CRAFT_MANIPULATION_CEILING_RATIO", 1.02))
                is_craft_capped = True
            else:
                # Gate 2 (Ceiling Clamping)
                craft_ceiling = int(effective_craft_cost * getattr(self.config, "CRAFT_CEILING_RATIO", 1.05))
                if resell_baseline > craft_ceiling:
                    resell_baseline = craft_ceiling
                    is_craft_capped = True

            if resell_baseline <= buy_price:
                return None

        # Anti-bait ceiling: reject if resell_baseline excessively exceeds buy_price
        if resell_baseline > (buy_price * self.config.MAX_TARGET_TO_BUY_RATIO):
            return None

        resell_baseline = self._apply_economic_hardening(resell_baseline, "PATH_B")
        net_revenue = self.calculate_net_revenue(resell_baseline)
        net_profit = int(round(net_revenue - buy_price))
        margin_pct = (net_profit / buy_price) * 100.0

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        clean_id = internal_id.strip().upper()
        staple_category = PROGRESSION_STAPLES.get(clean_id)
        is_staple = staple_category is not None

        if is_staple:
            required_profit = self.config.STAPLE_MIN_PROFIT_COINS
            required_margin = self.config.STAPLE_MIN_PROFIT_MARGIN_PCT
        else:
            required_profit = self.config.MIN_PROFIT_COINS
            required_margin = self.config.MIN_PROFIT_MARGIN_PCT

        if net_profit >= required_profit and margin_pct >= required_margin:
            modifier = str(decoded_data.get("modifier", "")).strip().lower()
            queue_depth = self.calculate_queue_depth(internal_id, resell_baseline, buy_price=buy_price)
            return FlipResult(
                auction_uuid=str(auction.get("uuid", "")),
                internal_id=internal_id,
                item_name=decoded_data.get("item_name") or auction.get("item_name", "Unknown Item"),
                buy_price=buy_price,
                target_resell_price=resell_baseline,
                net_profit=net_profit,
                margin_pct=round(margin_pct, 2),
                dungeon_stars=int(decoded_data.get("dungeon_stars", 0)),
                rarity_upgrades=int(decoded_data.get("rarity_upgrades", 0)),
                enchantments=decoded_data.get("enchantments", {}),
                daily_velocity=daily_velocity,
                is_priority=is_staple,
                staple_category=staple_category,
                is_networth_snipe=True,
                base_lbin=base_lbin,
                modifier=modifier or None,
                addon_breakdown=addon_breakdown,
                total_addon_cost=total_addon_cost,
                intrinsic_worth=intrinsic_worth,
                master_stars=int(decoded_data.get("master_stars", 0)),
                gemstones=decoded_data.get("gemstones", []),
                retention_rate=retention_rate,
                is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
                prestige_tier=decoded_data.get("prestige_tier"),
                gemstone_chambers=int(decoded_data.get("gemstone_chambers", 0)),
                art_of_war_count=int(decoded_data.get("art_of_war_count", 0)),
                wood_singularity_count=int(decoded_data.get("wood_singularity_count", 0)),
                transmission_tuners=int(decoded_data.get("transmission_tuners", 0)),
                craft_cost=craft_cost,
                is_craft_capped=is_craft_capped,
                valuation_path="PATH_B",
                conservative_target=resell_baseline,
                optimistic_target=resell_baseline,
                expected_profit=net_profit,
                optimistic_upside=net_profit,
                queue_depth=queue_depth,
            )

        return None


    def _evaluate_clean_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
    ) -> Optional[FlipResult]:
        """
        Path A: Non-Addon / Clean Flip (Preserved Logic).
        Compares candidate buy price against the 2nd lowest BIN price.
        Clean (non-upgraded) candidates are benchmarked only against clean listings, so starred /
        recombobulated copies of the same item cannot inflate the reference price.
        """
        internal_id = decoded_data["internal_id"]
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None

        item_count = max(1, int(decoded_data.get("count") or 1))
        unit_buy_price = buy_price // item_count

        # Candidate excludes itself
        other_prices = list(sorted_prices)
        if unit_buy_price in other_prices:
            other_prices.remove(unit_buy_price)

        ref_pool = sorted_prices
        if not self.is_upgraded_item(decoded_data):
            clean_pool = self._clean_price_cache.get(internal_id)
            if clean_pool:
                ref_pool = clean_pool
                clean_others = list(clean_pool)
                if unit_buy_price in clean_others:
                    clean_others.remove(unit_buy_price)
                if not clean_others:
                    return None
                # Upgraded listings priced above the clean floor are not comparable; cheaper ones
                # still dominate a clean copy, so they remain and can lower the reference.
                clean_floor = clean_others[0]
                other_prices = [p for p in other_prices if p <= clean_floor]

        if not other_prices:
            return None

        # Path A Sample Size Gate: require at least PATH_A_MIN_LISTINGS (or MIN_SAMPLE_SIZE)
        path_a_min = min(getattr(self.config, "PATH_A_MIN_LISTINGS", 3), self.config.MIN_SAMPLE_SIZE)
        if len(ref_pool) < path_a_min:
            return None

        # Reference price is lowest price among other active listings
        ref_price = other_prices[0]

        # Floor tie: If any other listing is at or below candidate's price (within 1%), no flip
        tie_tol = getattr(self.config, "MERGED_FLOOR_TIE_TOLERANCE_PCT", 1.0) / 100.0
        if ref_price <= unit_buy_price * (1.0 + tie_tol):
            return None

        # Supply dump: skip when 2+ other listings share the lowest price (within 1%)
        dump_count = sum(1 for p in other_prices if p <= ref_price * (1.0 + tie_tol))
        if dump_count >= 2:
            return None

        # Anti-Domination Guard (Phase 6): reject if >= 40% of listings belong to one seller
        if getattr(self.config, "ENABLE_SELLER_DOMINATION_GUARD", False):
            if self.is_seller_dominated(internal_id):
                return None

        # Fragged / Base-linked item ceiling (e.g. STARRED_BAT_WAND -> BAT_WAND + 8x THORN_FRAGMENT)
        base_id = get_base_item_id(internal_id)
        if base_id and base_id != internal_id:
            base_floor = self.get_base_lbin(base_id)
            if base_floor is not None and base_floor > 0:
                frag_upgrade_cost = 0
                frag_recipe = get_recipe(internal_id)
                if frag_recipe and self.bazaar_cache is not None:
                    for ing, qty in frag_recipe.items():
                        if ing != base_id and hasattr(self.bazaar_cache, "get_buy_price"):
                            p = self.bazaar_cache.get_buy_price(ing)
                            if p and p > 0:
                                frag_upgrade_cost += int(round(p * qty))
                if frag_upgrade_cost == 0:
                    frag_upgrade_cost = 150_000

                max_fragged_worth = base_floor + frag_upgrade_cost
                # If candidate is priced at or above base floor + upgrade cost, it's not a flip!
                if unit_buy_price >= max_fragged_worth:
                    logger.debug(
                        "Rejecting fragged item %s: buy price %d >= base floor %d + frag cost %d",
                        internal_id,
                        unit_buy_price,
                        base_floor,
                        frag_upgrade_cost,
                    )
                    return None

                frag_ceiling = int(round(max_fragged_worth * 1.05))
                if ref_price > frag_ceiling:
                    ref_price = frag_ceiling

        target_resell_price = ref_price * item_count

        craft_cost: Optional[int] = None
        is_craft_capped: bool = False

        if getattr(self.config, "ENABLE_CRAFT_CEILING", True) and self.bazaar_cache is not None:
            raw_craft = calculate_craft_cost(internal_id, self.bazaar_cache, ah_cache=self)
            if raw_craft is not None:
                craft_cost = raw_craft * item_count

        if craft_cost is not None:
            # Gate 1 (Never Buy Above Craft Cost)
            buy_threshold = craft_cost * getattr(self.config, "CRAFT_BUY_THRESHOLD_RATIO", 0.95)
            if buy_price >= buy_threshold:
                return None

            # Gate 3 (Artificial Spread / Bait Guard): >30% higher than craft cost
            manipulation_threshold = craft_cost * getattr(self.config, "CRAFT_MANIPULATION_THRESHOLD_RATIO", 1.30)
            if target_resell_price > manipulation_threshold:
                target_resell_price = int(craft_cost * getattr(self.config, "CRAFT_MANIPULATION_CEILING_RATIO", 1.02))
                is_craft_capped = True
            else:
                # Gate 2 (Ceiling Clamping)
                craft_ceiling = int(craft_cost * getattr(self.config, "CRAFT_CEILING_RATIO", 1.05))
                if target_resell_price > craft_ceiling:
                    target_resell_price = craft_ceiling
                    is_craft_capped = True

            if target_resell_price <= buy_price:
                return None

        # Anti-bait ceiling: reject if target resell is excessively higher than buy price
        if target_resell_price > (buy_price * self.config.MAX_TARGET_TO_BUY_RATIO):
            return None

        target_resell_price = self._apply_economic_hardening(target_resell_price, "PATH_A")

        # Net revenue accounting for Hypixel AH listing tax
        net_revenue = self.calculate_net_revenue(target_resell_price)
        net_profit = int(net_revenue - buy_price)
        margin_pct = (net_profit / buy_price) * 100.0

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        clean_id = internal_id.strip().upper()
        staple_category = PROGRESSION_STAPLES.get(clean_id)
        is_staple = staple_category is not None

        if is_staple:
            required_profit = self.config.STAPLE_MIN_PROFIT_COINS
            required_margin = self.config.STAPLE_MIN_PROFIT_MARGIN_PCT
        else:
            required_profit = self.config.MIN_PROFIT_COINS
            required_margin = self.config.MIN_PROFIT_MARGIN_PCT

        if (
            net_profit >= required_profit
            and margin_pct >= required_margin
        ):
            queue_depth = self.calculate_queue_depth(internal_id, target_resell_price, buy_price=buy_price)
            return FlipResult(
                auction_uuid=str(auction.get("uuid", "")),
                internal_id=internal_id,
                item_name=decoded_data.get("item_name") or auction.get("item_name", "Unknown Item"),
                buy_price=buy_price,
                target_resell_price=target_resell_price,
                net_profit=net_profit,
                margin_pct=round(margin_pct, 2),
                dungeon_stars=int(decoded_data.get("dungeon_stars", 0)),
                rarity_upgrades=int(decoded_data.get("rarity_upgrades", 0)),
                enchantments=decoded_data.get("enchantments", {}),
                daily_velocity=daily_velocity,
                is_priority=is_staple,
                staple_category=staple_category,
                is_networth_snipe=False,
                base_lbin=ref_pool[0] * item_count,
                modifier=str(decoded_data.get("modifier", "")).strip().lower() or None,
                is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
                prestige_tier=decoded_data.get("prestige_tier"),
                gemstone_chambers=int(decoded_data.get("gemstone_chambers", 0)),
                art_of_war_count=int(decoded_data.get("art_of_war_count", 0)),
                wood_singularity_count=int(decoded_data.get("wood_singularity_count", 0)),
                transmission_tuners=int(decoded_data.get("transmission_tuners", 0)),
                craft_cost=craft_cost,
                is_craft_capped=is_craft_capped,
                valuation_path="PATH_A",
                conservative_target=target_resell_price,
                optimistic_target=target_resell_price,
                expected_profit=net_profit,
                optimistic_upside=net_profit,
                queue_depth=queue_depth,
            )

        return None

    def _evaluate_attribute_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
    ) -> Optional[FlipResult]:
        """
        Evaluate Crimson Isle and Nether gear attributes for God Rolls.
        If the item possesses a recognized God Roll combination (e.g. Mana Pool + Mana Regen),
        flags it as a God Roll and compares buy price against estimated market value floor.
        """
        attributes = decoded_data.get("attributes", {})
        if not attributes:
            return None

        internal_id = decoded_data["internal_id"]
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None

        base_lbin = self.get_base_lbin(internal_id) or (sorted_prices[0] if sorted_prices else buy_price)

        is_gr, gr_type, estimated_market_value = calculate_attribute_estimated_value(attributes, base_lbin)
        if not is_gr or estimated_market_value <= 0:
            return None

        target_resell_price = estimated_market_value
        if target_resell_price <= buy_price:
            return None

        # Anti-bait ceiling: reject if estimated value is absurdly higher than buy price (e.g. > 15x)
        if target_resell_price > (buy_price * max(self.config.MAX_TARGET_TO_BUY_RATIO, 15.0)):
            return None

        net_revenue = self.calculate_net_revenue(target_resell_price)
        net_profit = int(net_revenue - buy_price)
        margin_pct = (net_profit / buy_price) * 100.0

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        clean_id = internal_id.strip().upper()
        staple_category = PROGRESSION_STAPLES.get(clean_id)
        is_staple = staple_category is not None

        if is_staple:
            required_profit = self.config.STAPLE_MIN_PROFIT_COINS
            required_margin = self.config.STAPLE_MIN_PROFIT_MARGIN_PCT
        else:
            required_profit = self.config.MIN_PROFIT_COINS
            required_margin = self.config.MIN_PROFIT_MARGIN_PCT

        if net_profit >= required_profit and margin_pct >= required_margin:
            return FlipResult(
                auction_uuid=str(auction.get("uuid", "")),
                internal_id=internal_id,
                item_name=decoded_data.get("item_name") or auction.get("item_name", "Unknown Item"),
                buy_price=buy_price,
                target_resell_price=target_resell_price,
                net_profit=net_profit,
                margin_pct=round(margin_pct, 2),
                dungeon_stars=int(decoded_data.get("dungeon_stars", 0)),
                rarity_upgrades=int(decoded_data.get("rarity_upgrades", 0)),
                enchantments=decoded_data.get("enchantments", {}),
                daily_velocity=daily_velocity,
                is_priority=True,  # God rolls are always high-priority alerts
                staple_category=f"GOD_ROLL_{gr_type}",
                base_lbin=base_lbin,
                is_attribute_snipe=True,
                is_god_roll=True,
                god_roll_type=gr_type,
                attributes=attributes,
                is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
                prestige_tier=decoded_data.get("prestige_tier"),
                gemstone_chambers=int(decoded_data.get("gemstone_chambers", 0)),
                art_of_war_count=int(decoded_data.get("art_of_war_count", 0)),
                wood_singularity_count=int(decoded_data.get("wood_singularity_count", 0)),
                transmission_tuners=int(decoded_data.get("transmission_tuners", 0)),
                valuation_path="ATTRIBUTE",
                conservative_target=target_resell_price,
                optimistic_target=target_resell_price,
                expected_profit=net_profit,
                optimistic_upside=net_profit,
                queue_depth=self.calculate_queue_depth(internal_id, target_resell_price, buy_price=buy_price),
            )

        return None

    def _evaluate_pet_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
        now_ms: Optional[float] = None,
    ) -> Optional[FlipResult]:
        """
        Route pet evaluation to either the new Pet Bucket Valuation Engine
        (ENABLE_PET_BUCKET_VALUATION=True) or legacy heuristic.
        """
        if getattr(self.config, "ENABLE_PET_BUCKET_VALUATION", True):
            return self._evaluate_pet_bucket_listing(
                auction, decoded_data, sorted_prices, now_ms=now_ms
            )
        return self._evaluate_legacy_pet_listing(auction, decoded_data, sorted_prices)

    def _evaluate_legacy_pet_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
    ) -> Optional[FlipResult]:
        """
        Legacy pet heuristic evaluation (Preserved for backwards compatibility when disabled):
        - Calculates held item value from Bazaar or catalog
        - Evaluates max-level premium (Level 100 or Golden Dragon Level 200)
        - Computes estimated worth = Base Level 1 LBIN + Level Premium + Held Item Value
        """
        internal_id = decoded_data["internal_id"]
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None

        pet_type = decoded_data.get("pet_type", "")
        pet_tier = decoded_data.get("pet_tier", "COMMON")
        pet_level = int(decoded_data.get("pet_level", 1))
        pet_held_item = decoded_data.get("pet_held_item", "")

        held_item_name, held_item_value = get_pet_held_item_info(pet_held_item, self.bazaar_cache)
        level_premium = get_pet_level_premium(pet_type, pet_tier, pet_level)

        base_lvl_1_price = self.get_base_lbin(internal_id) or (sorted_prices[0] if sorted_prices else buy_price)

        if level_premium == 0 and held_item_value == 0:
            return None

        estimated_worth = base_lvl_1_price + level_premium + held_item_value

        pet_candy_used = int(decoded_data.get("pet_candy_used", 0))
        if pet_candy_used > 0:
            from src.addon_rules import calculate_pet_candy_penalty

            penalty_pct = calculate_pet_candy_penalty(
                pet_candy_used,
                getattr(self.config, "PET_CANDY_PENALTY_PER_CANDY", 0.015),
            )
            estimated_worth = int(round(estimated_worth * (1.0 - penalty_pct)))

        target_resell_price = estimated_worth

        if target_resell_price <= buy_price:
            return None

        if target_resell_price > (buy_price * max(self.config.MAX_TARGET_TO_BUY_RATIO, 5.0)):
            return None

        net_revenue = self.calculate_net_revenue(target_resell_price)
        net_profit = int(net_revenue - buy_price)
        margin_pct = (net_profit / buy_price) * 100.0

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        required_profit = self.config.MIN_PROFIT_COINS
        required_margin = self.config.MIN_PROFIT_MARGIN_PCT

        if net_profit >= required_profit and margin_pct >= required_margin:
            return FlipResult(
                auction_uuid=str(auction.get("uuid", "")),
                internal_id=internal_id,
                item_name=decoded_data.get("item_name") or auction.get("item_name", "Unknown Pet"),
                buy_price=buy_price,
                target_resell_price=target_resell_price,
                net_profit=net_profit,
                margin_pct=round(margin_pct, 2),
                daily_velocity=daily_velocity,
                is_priority=(level_premium > 0 or held_item_value >= 20_000_000),
                staple_category="PET",
                base_lbin=base_lvl_1_price,
                is_pet=True,
                pet_type=pet_type,
                pet_tier=pet_tier,
                effective_rarity=decoded_data.get("effective_tier") or pet_tier,
                pet_level=pet_level,
                pet_exp=float(decoded_data.get("pet_exp", 0.0)),
                pet_held_item=pet_held_item or None,
                pet_held_item_name=held_item_name,
                held_item_value=held_item_value,
                base_lvl_1_price=base_lvl_1_price,
                pet_candy_used=pet_candy_used,
                pet_skin=decoded_data.get("pet_skin"),
                is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
                valuation_path="PET",
                conservative_target=target_resell_price,
                optimistic_target=target_resell_price,
                expected_profit=net_profit,
                optimistic_upside=net_profit,
                queue_depth=self.calculate_queue_depth(internal_id, target_resell_price, buy_price=buy_price),
            )

        return None

    def _evaluate_pet_bucket_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
        now_ms: Optional[float] = None,
    ) -> Optional[FlipResult]:
        """
        Granular Pet Bucket Valuation Engine with Merged-State Floor, Sold-Comps Gating,
        and Anti-Domination / Supply-Dump Protection.
        """
        uuid = str(auction.get("uuid", ""))
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None

        current_now_ms = now_ms if now_ms is not None else (time.time() * 1000.0)
        pet_type = str(decoded_data.get("pet_type", "")).strip().upper()
        pet_tier = str(decoded_data.get("pet_tier", "COMMON")).strip().upper()
        effective_tier = str(decoded_data.get("effective_tier") or pet_tier).strip().upper()
        pet_level = int(decoded_data.get("pet_level", 1))
        pet_exp = float(decoded_data.get("pet_exp", 0.0))
        pet_held_item = str(decoded_data.get("pet_held_item", "")).strip().upper()
        held_item_name, held_item_value = get_pet_held_item_info(pet_held_item, self.bazaar_cache)
        pet_candy_used = int(decoded_data.get("pet_candy_used", 0))
        pet_skin = decoded_data.get("pet_skin")

        minor_thresh = getattr(self.config, "PET_MINOR_HELD_ITEM_THRESHOLD_COINS", 2_000_000)
        bucket_key = get_pet_bucket_key(decoded_data, minor_threshold=minor_thresh, bazaar_cache=self.bazaar_cache)
        internal_id = bucket_key[0]  # Exact effective rarity internal_id (e.g. PET_GOBLIN_LEGENDARY)

        # 300s Deduplication check by (bucket_key, buy_price)
        dedupe_key = (bucket_key, buy_price)
        now_sec = current_now_ms / 1000.0
        dedupe_ttl = getattr(self.config, "PET_DEDUPE_TTL_SECONDS", 300)
        last_alert = self._pet_dedupe_cache.get(dedupe_key)
        if last_alert is not None and (now_sec - last_alert) < dedupe_ttl:
            logger.debug(
                "Pet flip [%s] suppressed by dedupe cache (bucket=%s, price=%d)",
                uuid,
                bucket_key,
                buy_price,
            )
            return None

        # Fetch active listings in candidate's bucket
        bucket_listings = list(self._pet_buckets.get(bucket_key, []))

        # Candidate excludes itself strictly by uuid, never by price
        other_listings = [l for l in bucket_listings if l.get("uuid") != uuid]

        # Filter other listings by XP window (+/- 25%)
        xp_window_pct = getattr(self.config, "PET_XP_WINDOW_PCT", 25.0)
        comparable_listings = [
            l
            for l in other_listings
            if is_pet_xp_in_window(pet_exp, float(l.get("xp", 0.0)), window_pct=xp_window_pct)
        ]

        ref_listing: Optional[dict[str, Any]] = None
        ref_price: Optional[int] = None
        tie_tol = getattr(self.config, "MERGED_FLOOR_TIE_TOLERANCE_PCT", 1.0) / 100.0

        if comparable_listings:
            comparable_listings.sort(key=lambda x: x["price"])
            ref_listing = comparable_listings[0]
            ref_price = int(ref_listing["price"])

            # 1. Floor tie check: lowest other listing at or below candidate price within 1%
            if ref_price <= buy_price * (1.0 + tie_tol):
                self.last_pet_rejections[uuid] = "TIE_AT_FLOOR"
                logger.info(
                    "Rejected pet flip [%s] (buy: %d, ref: %d): reason=TIE_AT_FLOOR",
                    uuid,
                    buy_price,
                    ref_price,
                )
                return None

            # 2. Supply dump check: 2+ other listings share the lowest price within 1%
            floor_matches = [
                l for l in comparable_listings if l["price"] <= ref_price * (1.0 + tie_tol)
            ]
            if len(floor_matches) >= 2:
                self.last_pet_rejections[uuid] = "SUPPLY_DUMP"
                logger.info(
                    "Rejected pet flip [%s]: reason=SUPPLY_DUMP (%d floor listings at %d)",
                    uuid,
                    len(floor_matches),
                    ref_price,
                )
                return None

            # 3. Seller domination check in bucket floor / listings
            min_dom_listings = getattr(self.config, "PATH_A_MIN_LISTINGS", 3)
            if len(comparable_listings) >= min_dom_listings:
                dom_threshold = getattr(self.config, "MAX_SELLER_DOMINATION_RATIO", 0.40)
                seller_counts: dict[str, int] = defaultdict(int)
                for l in comparable_listings:
                    s_name = l.get("auctioneer") or ""
                    if s_name:
                        seller_counts[s_name] += 1
                if seller_counts:
                    max_owned = max(seller_counts.values())
                    if (max_owned / len(comparable_listings)) >= dom_threshold:
                        self.last_pet_rejections[uuid] = "SELLER_DOMINATION"
                        logger.info(
                            "Rejected pet flip [%s]: reason=SELLER_DOMINATION (seller owns %d/%d)",
                            uuid,
                            max_owned,
                            len(comparable_listings),
                        )
                        return None

        # Fetch persisted sold comps in 48h lookback window
        comps_window_hours = getattr(self.config, "PET_COMPS_WINDOW_HOURS", 48.0)
        start_window_ms = int(current_now_ms - comps_window_hours * 3600.0 * 1000.0)

        db_instance = self.db or (
            getattr(self.comps_evaluator, "db", None) if self.comps_evaluator else None
        )
        raw_sales: list[dict[str, Any]] = []
        if db_instance is not None:
            raw_sales = db_instance.get_sales(
                internal_id=internal_id, start_time=start_window_ms
            )

        # Filter sales matching exact bucket
        matching_sales: list[dict[str, Any]] = []
        for s in raw_sales:
            s_feat = s.get("features_json")
            if isinstance(s_feat, str):
                try:
                    s_decoded = json.loads(s_feat)
                except Exception:
                    s_decoded = {}
            elif isinstance(s_feat, dict):
                s_decoded = s_feat
            else:
                s_decoded = {}

            s_bucket = get_pet_bucket_key(
                s_decoded, minor_threshold=minor_thresh, bazaar_cache=self.bazaar_cache
            )
            if s_bucket == bucket_key:
                matching_sales.append(s)

        # Phase 2c & 2d: Per-rarity sales gate & comps capping
        if not matching_sales:
            self.last_pet_rejections[uuid] = "pet_no_comps"
            logger.info("Rejected pet flip [%s]: reason=pet_no_comps (no same-bucket comps in 48h)", uuid)
            return None

        rarity_gate_map = getattr(self.config, "PET_MIN_SALES_BY_RARITY", None) or {
            "COMMON": 8,
            "UNCOMMON": 8,
            "RARE": 6,
            "EPIC": 4,
            "LEGENDARY": 3,
            "MYTHIC": 3,
        }
        min_comps = rarity_gate_map.get(effective_tier) or rarity_gate_map.get(pet_tier) or getattr(self.config, "PET_MIN_SOLD_COMPS_48H", 3)

        if len(matching_sales) < min_comps:
            self.last_pet_rejections[uuid] = "pet_sales_gate"
            logger.info(
                "Rejected pet flip [%s]: reason=pet_sales_gate (found %d, required %d for %s)",
                uuid,
                len(matching_sales),
                min_comps,
                effective_tier,
            )
            return None

        # Calculate comps distribution
        sale_prices = sorted([int(s["price"]) for s in matching_sales])
        comp_count = len(sale_prices)
        comp_median = int(round(calculate_percentile(sale_prices, 0.50)))
        comps_pctile = getattr(self.config, "PET_COMPS_PERCENTILE", 0.75)
        comp_p75 = int(round(calculate_percentile(sale_prices, comps_pctile)))
        comp_p25 = int(round(calculate_percentile(sale_prices, 0.25)))

        # 12h Stale reference listing guard
        if ref_listing is not None:
            ref_start = ref_listing.get("start")
            max_age_hours = getattr(self.config, "MAX_BENCHMARK_AGE_HOURS", 12.0)
            if ref_start is not None and max_age_hours > 0:
                try:
                    ref_age_hours = (current_now_ms - float(ref_start)) / (1000.0 * 3600.0)
                    if ref_age_hours > max_age_hours:
                        if ref_price > comp_median * 1.05:
                            self.last_pet_rejections[uuid] = "GHOST_REFERENCE"
                            logger.info(
                                "Rejected pet flip [%s]: reason=GHOST_REFERENCE (stale ref %d > comp_median %d)",
                                uuid,
                                ref_price,
                                comp_median,
                            )
                            return None
                except (ValueError, TypeError):
                    pass

        # Target resell price calculation:
        # Phase 2d: min(model target, same-bucket sold-comps value)
        # Model target comes from lowest valid other active listing (ref_price), else comp_median
        if ref_price is not None:
            model_target = ref_price
            baseline_source = "BUCKET_REF_AND_COMPS_MEDIAN"
        else:
            model_target = comp_median
            baseline_source = "COMPS_MEDIAN"

        # Pet candy penalty if applicable
        if pet_candy_used > 0:
            from src.addon_rules import calculate_pet_candy_penalty

            penalty_pct = calculate_pet_candy_penalty(
                pet_candy_used,
                getattr(self.config, "PET_CANDY_PENALTY_PER_CANDY", 0.015),
            )
            model_target = int(round(model_target * (1.0 - penalty_pct)))

        # Craft cost ceiling reuse if present
        craft_cost = None
        is_craft_capped = False
        if getattr(self.config, "ENABLE_CRAFT_CEILING", True) and self.bazaar_cache is not None:
            craft_cost = calculate_craft_cost(internal_id, self.bazaar_cache, ah_cache=self)
            if craft_cost is not None:
                buy_thresh = craft_cost * getattr(
                    self.config, "CRAFT_BUY_THRESHOLD_RATIO", 0.95
                )
                if buy_price >= buy_thresh:
                    return None
                ceiling = int(craft_cost * getattr(self.config, "CRAFT_CEILING_RATIO", 1.05))
                if model_target > ceiling:
                    model_target = ceiling
                    is_craft_capped = True

        # Phase 2d: Resale target = min(model target, same-bucket sold-comps value)
        # Model may only lower the target, never raise it above what recent sales support.
        target_resell = min(model_target, comp_median)

        if target_resell <= buy_price:
            return None

        # Anti-bait ceiling
        if target_resell > (buy_price * max(self.config.MAX_TARGET_TO_BUY_RATIO, 5.0)):
            return None

        target_resell = self._apply_economic_hardening(target_resell, "PET_BUCKET")

        net_revenue = self.calculate_net_revenue(target_resell)
        net_profit = int(net_revenue - buy_price)
        margin_pct = (net_profit / buy_price) * 100.0

        if (
            net_profit < self.config.MIN_PROFIT_COINS
            or margin_pct < self.config.MIN_PROFIT_MARGIN_PCT
        ):
            return None

        # Multi-factor confidence & Margin review cap
        iqr = comp_p75 - comp_p25
        spread_pct = (iqr / comp_median * 100.0) if comp_median > 0 else 0.0
        max_spread = getattr(self.config, "PET_HIGH_CONFIDENCE_MAX_SPREAD_PCT", 20.0)
        margin_review_cap = getattr(self.config, "PET_MARGIN_REVIEW_PCT", 40.0)

        if margin_pct > margin_review_cap:
            confidence_label = "LOW"
            self.last_pet_rejections[uuid] = "MARGIN_REVIEW"
            logger.info(
                "Pet flip [%s] margin %.1f%% > %.1f%% review cap -> confidence capped at LOW",
                uuid,
                margin_pct,
                margin_review_cap,
            )
        elif comp_count >= min_comps and spread_pct <= max_spread:
            confidence_label = "HIGH"
        else:
            confidence_label = "MEDIUM"

        # Downgrade confidence if lane conversion haircut is active
        if getattr(self.config, "ENABLE_LANE_CONVERSION_HAIRCUT", True) and self.survival_auditor is not None:
            if self.survival_auditor.get_lane_haircut("PET_BUCKET") > 0.0:
                confidence_label = "LOW" if confidence_label == "MEDIUM" else "MEDIUM" if confidence_label == "HIGH" else "LOW"

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        # Mark dedupe timestamp upon successful alert qualification
        self._pet_dedupe_cache[dedupe_key] = now_sec

        queue_depth = self.calculate_queue_depth(internal_id, target_resell, buy_price=buy_price, pet_bucket_key=bucket_key)

        return FlipResult(
            auction_uuid=uuid,
            internal_id=internal_id,
            item_name=decoded_data.get("item_name")
            or auction.get("item_name", "Unknown Pet"),
            buy_price=buy_price,
            target_resell_price=target_resell,
            net_profit=net_profit,
            margin_pct=round(margin_pct, 2),
            daily_velocity=daily_velocity,
            is_priority=(held_item_value >= 20_000_000 or confidence_label == "HIGH"),
            staple_category="PET",
            base_lbin=ref_price or comp_median,
            is_pet=True,
            pet_type=pet_type,
            pet_tier=pet_tier,
            effective_rarity=effective_tier,
            pet_level=pet_level,
            pet_exp=pet_exp,
            pet_held_item=pet_held_item or None,
            pet_held_item_name=held_item_name,
            held_item_value=held_item_value,
            base_lvl_1_price=ref_price or comp_median,
            pet_candy_used=pet_candy_used,
            pet_skin=pet_skin,
            baseline_source=baseline_source,
            reference_price=ref_price,
            comp_count=comp_count,
            comp_median=comp_median,
            rejection_reason=self.last_pet_rejections.get(uuid),
            pet_bucket_key=str(bucket_key),
            is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
            confidence_label=confidence_label,
            valuation_path="PET_BUCKET",
            craft_cost=craft_cost,
            is_craft_capped=is_craft_capped,
            conservative_target=target_resell,
            optimistic_target=target_resell,
            expected_profit=net_profit,
            optimistic_upside=net_profit,
            queue_depth=queue_depth,
        )

    def _evaluate_comps_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        sorted_prices: list[int],
        now_ms: Optional[float] = None,
    ) -> tuple[Optional[FlipResult], bool]:
        """
        Evaluate an auction using the historical Comparable-Sales (Sold-Comps) engine.
        Returns:
            (FlipResult | None, comps_found: bool)
            comps_found is True if Tier 1 or Tier 2 had enough sales (>= MIN_COMPS_THRESHOLD).
            If comps_found is False, indicates Tier 3 fallback (insufficient sales data).
        """
        if self.comps_evaluator is None:
            return None, False

        internal_id = decoded_data["internal_id"]
        buy_price = int(auction.get("starting_bid", 0))
        if buy_price <= 0:
            return None, False

        is_upgraded = self.is_upgraded_item(decoded_data)
        base_lbin = self.get_base_lbin(internal_id) or (sorted_prices[0] if sorted_prices else buy_price)

        addon_breakdown: list[dict[str, Any]] = []
        total_addon_cost = 0
        intrinsic_worth = 0
        retention_rate = getattr(self.config, "RETENTION_RATE", 0.70)
        path_b_resell: Optional[int] = None

        if is_upgraded and self.bazaar_cache is not None:
            from src.networth_evaluator import calculate_item_networth
            override_retention = None
            if getattr(self.config, "ENABLE_DYNAMIC_RETENTION", False) and self.retention_fitter is not None:
                override_retention = self.retention_fitter.get_retention_rate(internal_id, base_lbin)

            addon_breakdown, total_addon_cost, intrinsic_worth, retention_rate, path_b_resell = (
                calculate_item_networth(
                    base_lbin=base_lbin,
                    decoded_data=decoded_data,
                    bazaar_cache=self.bazaar_cache,
                    is_valuable_enchant_fn=self.is_valuable_enchant,
                    format_enchant_fn=self._format_enchant_display_name,
                    override_retention_rate=override_retention,
                )
            )

        comps_res = self.comps_evaluator.estimate_comps_value(
            item_features=decoded_data,
            now_ms=now_ms,
            path_b_resell=path_b_resell,
        )

        if comps_res is None:
            # Tier 3 fallback: not enough comps
            return None, False

        # Comps data was found (Tier 1 or Tier 2)
        lane = f"COMPS_{comps_res.tier}"
        p25 = comps_res.p25_price
        p75 = comps_res.p75_price
        median = comps_res.median_price

        # Conservative baseline (P25 or Median, capped by Path B if divergent)
        conservative_target = comps_res.conservative_resell or (p25 if p25 > 0 else median)
        optimistic_target = p75 if p75 > 0 else median

        target_resell = conservative_target
        if target_resell <= buy_price:
            return None, True

        craft_cost: Optional[int] = None
        is_craft_capped: bool = False

        if getattr(self.config, "ENABLE_CRAFT_CEILING", True) and self.bazaar_cache is not None:
            craft_cost = calculate_craft_cost(internal_id, self.bazaar_cache, ah_cache=self)

        if craft_cost is not None:
            effective_craft_cost = craft_cost + int(round(total_addon_cost * retention_rate)) if total_addon_cost > 0 else craft_cost

            # Gate 1: Never Buy Above Craft Cost
            buy_threshold = effective_craft_cost * getattr(self.config, "CRAFT_BUY_THRESHOLD_RATIO", 0.95)
            if buy_price >= buy_threshold:
                return None, True

            # Gate 3: Artificial Spread / Bait Guard (>30% higher than craft cost)
            manipulation_threshold = effective_craft_cost * getattr(self.config, "CRAFT_MANIPULATION_THRESHOLD_RATIO", 1.30)
            if target_resell > manipulation_threshold:
                target_resell = int(effective_craft_cost * getattr(self.config, "CRAFT_MANIPULATION_CEILING_RATIO", 1.02))
                is_craft_capped = True
            else:
                # Gate 2: Ceiling Clamping
                craft_ceiling = int(effective_craft_cost * getattr(self.config, "CRAFT_CEILING_RATIO", 1.05))
                if target_resell > craft_ceiling:
                    target_resell = craft_ceiling
                    is_craft_capped = True

            if target_resell <= buy_price:
                return None, True

        # Anti-bait ceiling: reject if resell excessively exceeds buy price
        if target_resell > (buy_price * self.config.MAX_TARGET_TO_BUY_RATIO):
            return None, True

        # Apply economic hardening (lane conversion haircut + tax cliff snapping)
        target_resell = self._apply_economic_hardening(target_resell, lane)
        optimistic_target = self._apply_economic_hardening(optimistic_target, lane)

        net_revenue = self.calculate_net_revenue(target_resell)
        net_profit = int(round(net_revenue - buy_price))
        margin_pct = (net_profit / buy_price) * 100.0

        daily_velocity: Optional[float] = None
        if self.velocity_tracker is not None:
            daily_velocity = self.velocity_tracker.get_daily_velocity(internal_id)

        clean_id = internal_id.strip().upper()
        staple_category = PROGRESSION_STAPLES.get(clean_id)
        is_staple = staple_category is not None

        if is_staple:
            required_profit = self.config.STAPLE_MIN_PROFIT_COINS
            required_margin = self.config.STAPLE_MIN_PROFIT_MARGIN_PCT
        else:
            required_profit = self.config.MIN_PROFIT_COINS
            required_margin = self.config.MIN_PROFIT_MARGIN_PCT

        # Strict qualification on conservative resale price
        if net_profit >= required_profit and margin_pct >= required_margin:
            conf_score = comps_res.confidence_score
            if conf_score >= 0.70:
                conf_label = "HIGH"
            elif conf_score >= 0.40:
                conf_label = "MEDIUM"
            else:
                conf_label = "LOW"

            # Downgrade confidence if lane conversion haircut is active
            if getattr(self.config, "ENABLE_LANE_CONVERSION_HAIRCUT", True) and self.survival_auditor is not None:
                if self.survival_auditor.get_lane_haircut(lane) > 0.0:
                    conf_label = "LOW" if conf_label == "MEDIUM" else "MEDIUM" if conf_label == "HIGH" else "LOW"

            opt_net_revenue = self.calculate_net_revenue(optimistic_target)
            optimistic_upside = max(0, int(round(opt_net_revenue - buy_price)))

            queue_depth = self.calculate_queue_depth(internal_id, target_resell, buy_price=buy_price)

            modifier = str(decoded_data.get("modifier", "")).strip().lower()

            flip = FlipResult(
                auction_uuid=str(auction.get("uuid", "")),
                internal_id=internal_id,
                item_name=decoded_data.get("item_name") or auction.get("item_name", "Unknown Item"),
                buy_price=buy_price,
                target_resell_price=target_resell,
                net_profit=net_profit,
                margin_pct=round(margin_pct, 2),
                dungeon_stars=int(decoded_data.get("dungeon_stars", 0)),
                rarity_upgrades=int(decoded_data.get("rarity_upgrades", 0)),
                enchantments=decoded_data.get("enchantments", {}),
                daily_velocity=daily_velocity,
                is_priority=is_staple,
                staple_category=staple_category,
                is_networth_snipe=is_upgraded,
                base_lbin=base_lbin,
                modifier=modifier or None,
                addon_breakdown=addon_breakdown,
                total_addon_cost=total_addon_cost,
                intrinsic_worth=intrinsic_worth,
                master_stars=int(decoded_data.get("master_stars", 0)),
                gemstones=decoded_data.get("gemstones", []),
                retention_rate=retention_rate,
                is_out_of_budget=(buy_price > self.config.MAX_PURSE_BUDGET),
                prestige_tier=decoded_data.get("prestige_tier"),
                gemstone_chambers=int(decoded_data.get("gemstone_chambers", 0)),
                art_of_war_count=int(decoded_data.get("art_of_war_count", 0)),
                wood_singularity_count=int(decoded_data.get("wood_singularity_count", 0)),
                transmission_tuners=int(decoded_data.get("transmission_tuners", 0)),
                craft_cost=craft_cost,
                is_craft_capped=is_craft_capped,
                confidence_label=conf_label,
                valuation_path=lane,
                conservative_target=target_resell,
                optimistic_target=optimistic_target,
                expected_profit=net_profit,
                optimistic_upside=optimistic_upside,
                queue_depth=queue_depth,
            )
            return flip, True

        return None, True

    def _finalize_flip(self, flip: Optional[FlipResult]) -> Optional[FlipResult]:
        """
        Finalize a candidate flip through capital-efficiency ranking, multi-factor confidence scoring,
        and minimum capital efficiency thresholds (Phase 5).
        """
        if flip is None:
            return None

        from src.constants.taxes import best_resale_net
        best_res = best_resale_net(
            flip.target_resell_price,
            derpy_active=self.derpy_active,
            derpy_rate=self.derpy_collection_fee_rate,
        )
        if best_res.list_price != flip.target_resell_price:
            flip.suggested_list_price = best_res.list_price
        flip.derpy_active = self.derpy_active
        flip.fee_model_version = 2

        if getattr(self.config, "ENABLE_CAPITAL_EFFICIENCY", True):
            from src.ranking import score_and_rank_flip

            flip = score_and_rank_flip(flip, self.config)
            min_ces = getattr(self.config, "MIN_CAPITAL_EFFICIENCY", 0.0)
            if min_ces > 0.0 and (flip.capital_efficiency or 0.0) < min_ces:
                return None
        return flip

    def evaluate_listing(
        self,
        auction: dict[str, Any],
        decoded_data: dict[str, Any],
        now_ms: Optional[float] = None,
    ) -> Optional[FlipResult]:
        """
        Evaluate a single BIN auction listing against market price baselines.
        Routes to:
        - Pet Valuation Engine for Pets
        - Attribute Valuation Engine for Crimson Isle God Rolls
        - Historical Sold-Comps Engine when enabled and sufficient data exists
        - Path B (Networth Engine) for upgraded items
        - Path A (2nd Lowest BIN) for clean items
        """
        internal_id = decoded_data.get("internal_id")
        if not internal_id or internal_id == "UNKNOWN":
            return None

        # Stage 0: Universal Blacklist Gate (Cosmetic, Dye, Vanity, Illiquid Bait)
        item_name = decoded_data.get("item_name") or auction.get("item_name", "")
        if self.is_blacklisted(internal_id, item_name):
            return None

        # Stage 1: Sales Velocity & Volume Validation check (R2)
        if self.config.ENABLE_VELOCITY_FILTER and self.velocity_tracker is not None:
            if not self.velocity_tracker.is_velocity_sufficient(internal_id):
                return None

        # Stage 1.5: Early Craft Cost Ceiling Gate (Clean craftable bait rejection)
        buy_price = int(auction.get("starting_bid", 0))
        is_upgraded = self.is_upgraded_item(decoded_data)
        if (
            not is_upgraded
            and not decoded_data.get("is_pet")
            and not decoded_data.get("attributes")
            and getattr(self.config, "ENABLE_CRAFT_CEILING", True)
            and self.bazaar_cache is not None
        ):
            craft_cost = calculate_craft_cost(internal_id, self.bazaar_cache, ah_cache=self)
            if craft_cost is not None and buy_price >= (craft_cost * getattr(self.config, "CRAFT_BUY_THRESHOLD_RATIO", 0.95)):
                return None

        sorted_prices = self._price_cache.get(internal_id, [])

        # Stage 2: Specialized Evaluation Paths (Pets and Attribute God Rolls)
        if decoded_data.get("is_pet"):
            pet_flip = self._evaluate_pet_listing(auction, decoded_data, sorted_prices, now_ms=now_ms)
            if pet_flip is not None:
                return self._finalize_flip(pet_flip)
            if getattr(self.config, "ENABLE_PET_BUCKET_VALUATION", True):
                return None

        if decoded_data.get("attributes"):
            attr_flip = self._evaluate_attribute_listing(auction, decoded_data, sorted_prices)
            if attr_flip is not None:
                return self._finalize_flip(attr_flip)

        # Stage 2.5: Historical Comparable-Sales Engine (Phase 3)
        if getattr(self.config, "ENABLE_SOLD_COMPS", False) and self.comps_evaluator is not None:
            comps_flip, comps_found = self._evaluate_comps_listing(
                auction, decoded_data, sorted_prices, now_ms=now_ms
            )
            if comps_found:
                return self._finalize_flip(comps_flip)

        # Sample size gate for standard market listings
        if len(sorted_prices) < self.config.MIN_SAMPLE_SIZE:
            return None

        is_upgraded = self.is_upgraded_item(decoded_data)

        # Path B: Upgraded Flip (Networth Engine)
        if is_upgraded and self.config.ENABLE_NETWORTH_VALUATION and self.bazaar_cache is not None:
            networth_flip = self._evaluate_upgraded_listing(auction, decoded_data, sorted_prices)
            if networth_flip is not None:
                return self._finalize_flip(networth_flip)

        # Path A: Clean / Non-Addon Flip (Current Preserved Logic)
        if not is_upgraded or not self.config.ENABLE_NETWORTH_VALUATION or self.bazaar_cache is None:
            return self._finalize_flip(self._evaluate_clean_listing(auction, decoded_data, sorted_prices))

        return None
