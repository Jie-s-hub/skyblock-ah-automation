from typing import Any, Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    HYPIXEL_API_KEY: Optional[str] = Field(
        default=None,
        description="Optional Hypixel API key for authenticated requests",
    )
    DISCORD_WEBHOOK_URL: str = Field(
        default="",
        description="Discord Webhook URL for alerting on profitable flips",
    )
    MIN_PROFIT_COINS: int = Field(
        default=1_000_000,
        description="Minimum net coin profit required to trigger an alert",
    )
    MIN_PROFIT_MARGIN_PCT: float = Field(
        default=15.0,
        description="Minimum net profit percentage margin required to trigger an alert",
    )
    POLL_INTERVAL_SECONDS: int = Field(
        default=60,
        description="Time in seconds between auction poll cycles",
    )
    AH_TAX_RATE: float = Field(
        default=0.02,
        description="Default Hypixel AH listing tax rate (2%)",
    )
    MIN_SAMPLE_SIZE: int = Field(
        default=3,
        description="Minimum active listings required to establish a trustworthy resell price",
    )
    MAX_TARGET_TO_BUY_RATIO: float = Field(
        default=3.0,
        description="Anti-bait ceiling ratio: rejects flips where target resell > buy_price * MAX_TARGET_TO_BUY_RATIO",
    )
    DRY_RUN: bool = Field(
        default=False,
        description="When True, outputs flip alerts to console instead of sending to Discord",
    )

    # Cosmetic and vanity filtering
    COSMETIC_FILTER_ENABLED: bool = Field(
        default=True,
        description="Enable or disable filtering of cosmetic and vanity items",
    )
    COSMETIC_BLACKLIST_PREFIXES: list[str] = Field(
        default_factory=lambda: [
            "PET_SKIN_",
            "DYE_",
            "MINION_SKIN_",
            "FURNITURE_",
            "BALLOON_",
            "PLUSHIE_",
            "CARPET_",
            "BANNER_",
            "PAINTING_",
            "MODEL_",
            "FIREWORK_",
            "BED_",
            "RUNE_",
        ],
        description="Prefixes of internal_ids to blacklist as cosmetic/vanity items",
    )
    COSMETIC_BLACKLIST_SUBSTRINGS: list[str] = Field(
        default_factory=lambda: [
            "_SKIN_",
            "_SKIN",
            "_RUNE",
        ],
        description="Substrings of internal_ids to blacklist as cosmetic/vanity items",
    )
    COSMETIC_BLACKLIST_EXACT_IDS: list[str] = Field(
        default_factory=lambda: [
            "RUNE",
        ],
        description="Exact internal_ids to blacklist as cosmetic/vanity items",
    )

    # Sales velocity & volume filtering
    ENABLE_VELOCITY_FILTER: bool = Field(
        default=True,
        description="Enable or disable sales velocity and volume turnover validation",
    )
    MIN_SALES_PER_DAY: int = Field(
        default=5,
        description="Minimum sales per day required to qualify a candidate flip",
    )
    MAX_HOURS_WITHOUT_SALE: Optional[float] = Field(
        default=24.0,
        description="Maximum hours without sale before item is marked stale",
    )
    VELOCITY_WINDOW_HOURS: int = Field(
        default=24,
        description="Rolling window in hours for sales velocity calculation",
    )
    VELOCITY_CACHE_FILE: Optional[str] = Field(
        default="data/sales_velocity.json",
        description="File path for persisting sales velocity data across restarts",
    )

    # Progression staples configuration
    STAPLE_MIN_PROFIT_COINS: int = Field(
        default=500_000,
        description="Relaxed minimum net coin profit required for progression staples",
    )
    STAPLE_MIN_PROFIT_MARGIN_PCT: float = Field(
        default=8.0,
        description="Relaxed minimum profit margin percentage required for progression staples",
    )

    # Dual-mode & Networth evaluation configuration
    ENABLE_NETWORTH_VALUATION: bool = Field(
        default=True,
        description="Enable dual-mode intrinsic networth evaluation for upgraded items",
    )
    RETENTION_RATE: float = Field(
        default=0.70,
        description="Resell baseline retention rate for intrinsic addon values (default: 0.70 / 70%)",
    )
    BAZAAR_CACHE_TTL_SECONDS: int = Field(
        default=60,
        description="TTL in seconds for cached Bazaar product prices",
    )

    # Local WebSocket broadcaster configuration (Fabric in-game client mod)
    ENABLE_LOCAL_WS: bool = Field(
        default=True,
        description="Enable local WebSocket broadcasting to in-game Fabric client mod",
    )
    LOCAL_WS_HOST: str = Field(
        default="127.0.0.1",
        description="Host interface for local WebSocket server",
    )
    LOCAL_WS_PORT: int = Field(
        default=8765,
        description="Port for local WebSocket server (default: 8765)",
    )

    # Craft cost ceiling & anti-craft-bait configuration
    ENABLE_CRAFT_CEILING: bool = Field(
        default=True,
        description="Enable craft cost ceiling calculation and anti-craft-bait gating",
    )
    CRAFT_BUY_THRESHOLD_RATIO: float = Field(
        default=0.95,
        description="Reject flips where buy_price >= craft_cost * CRAFT_BUY_THRESHOLD_RATIO (0.95)",
    )
    CRAFT_CEILING_RATIO: float = Field(
        default=1.05,
        description="Ceiling clamping multiplier for craftable items (1.05)",
    )
    CRAFT_MANIPULATION_THRESHOLD_RATIO: float = Field(
        default=1.30,
        description="Market spread threshold over craft cost to flag artificial manipulation (1.30)",
    )
    CRAFT_MANIPULATION_CEILING_RATIO: float = Field(
        default=1.02,
        description="Clamped resell multiplier for manipulated craftable markets (1.02)",
    )


    # Stale auction and ghost listing guardrails
    MAX_BENCHMARK_AGE_HOURS: float = Field(
        default=12.0,
        description=(
            "Age in hours after which a pet bucket reference listing is treated as a possible ghost "
            "(GHOST_REFERENCE guard). Does NOT drop live listings from the price cache; only expired "
            "auctions (end time passed) are excluded there."
        ),
    )

    # Purse budget guardrails
    MAX_PURSE_BUDGET: int = Field(
        default=100_000_000,
        description="Maximum purse budget in coins for purchasing flips",
    )
    OUT_OF_BUDGET_WEBHOOK_URL: Optional[str] = Field(
        default=None,
        description="Optional secondary Discord webhook URL for flips exceeding MAX_PURSE_BUDGET",
    )

    # Feed ingestion configuration (REST or WEBSOCKET)
    FEED_MODE: str = Field(
        default="REST",
        description="Auction feed ingestion mode: 'REST' or 'WEBSOCKET'",
    )
    COFLNET_WS_URL: str = Field(
        default="wss://sky.coflnet.com/modsocket",
        description="WebSocket feed URL for low-latency auction ingestion",
    )

    # Persistence & Intelligence Database (Phase 1)
    DB_PATH: str = Field(
        default="data/market_intelligence.db",
        description="Path to SQLite persistence database",
    )
    ENABLE_DB_LOGGING: bool = Field(
        default=True,
        description="Enable asynchronous logging of alerts, purchases, and snapshots to SQLite",
    )
    USE_PROGRESSIVE_TAX: bool = Field(
        default=False,
        description="Apply Hypixel progressive tax schedule (<10M: 1%, 10-100M: 2%, >100M: 2% + 1% excess)",
    )

    # Player Portfolio & Trade Performance Tracking (Phase 2)
    TRACKED_PLAYER_IGN: str = Field(
        default="ongvvkk",
        description="Player username to automatically track for buy/sell execution and portfolio metrics",
    )
    ENABLE_PLAYER_TRACKING: bool = Field(
        default=True,
        description="Enable automated trade recognition and portfolio tracking for TRACKED_PLAYER_IGN",
    )

    # Sold-Comps Valuation Engine (Phase 3)
    ENABLE_SOLD_COMPS: bool = Field(
        default=True,
        description="Enable Phase 3 historical comparable-sales valuation engine",
    )
    MIN_COMPS_THRESHOLD: int = Field(
        default=3,
        description="Minimum number of comparable sales required within window to qualify comps valuation",
    )
    COMPS_WINDOW_HOURS: float = Field(
        default=72.0,
        description="Sliding lookback window in hours for historical comparable sales",
    )
    COMPS_TIME_DECAY_LAMBDA: float = Field(
        default=0.02,
        description="Exponential decay rate lambda per hour for weighting older comparable sales",
    )
    COMPS_MAX_PATH_B_DIVERGENCE: float = Field(
        default=0.25,
        description="Maximum allowed divergence (25%) before comps valuation falls back to conservative Path B baseline",
    )
    QUICK_SALE_QUANTILE: float = Field(
        default=0.25,
        description="Quantile for quick-sale comps target pricing (default: 0.25)",
    )
    MIN_COMPS_FOR_HIGH: int = Field(
        default=5,
        description="Minimum same-bucket sales in the comps window required for HIGH confidence (default: 5)",
    )
    SHADOW_MODE_LANES: list[str] = Field(
        default_factory=list,
        description="Lanes that are evaluated and persisted but never alerted (comma-separated or list)",
    )
    COLD_START_MIN_SALES: int = Field(
        default=50,
        description="Minimum total recorded completed sales before exiting cold start / warm-up state",
    )

    @field_validator("SHADOW_MODE_LANES", mode="before")
    @classmethod
    def _parse_shadow_mode_lanes(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [lane.strip() for lane in v.split(",") if lane.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    # Empirical Bayes Retention Fitting (Phase 4)
    ENABLE_DYNAMIC_RETENTION: bool = Field(
        default=True,
        description="Enable Phase 4 empirical Bayes data-fitted retention rates for upgraded items",
    )
    RETENTION_SHRINKAGE_TAU: float = Field(
        default=10.0,
        description="Prior strength pseudo-count tau for Empirical Bayes shrinkage",
    )
    MIN_RETENTION_SAMPLES: int = Field(
        default=3,
        description="Minimum observed sales required before applying data-fitted retention",
    )
    RETENTION_MIN_CLAMP: float = Field(
        default=0.30,
        description="Hard lower bound clamp for fitted retention rate",
    )
    RETENTION_MAX_CLAMP: float = Field(
        default=0.95,
        description="Hard upper bound clamp for fitted retention rate",
    )

    # Capital Efficiency & Multi-Factor Confidence (Phase 5)
    ENABLE_CAPITAL_EFFICIENCY: bool = Field(
        default=True,
        description="Enable Phase 5 capital-efficiency calculation and multi-factor confidence ranking",
    )
    MIN_CAPITAL_EFFICIENCY: float = Field(
        default=0.0,
        description="Minimum capital efficiency (%/hr) required to alert on a flip",
    )

    # Anti-Domination & Bazaar Shock Guard (Phase 6)
    ENABLE_BAZAAR_SHOCK_GUARD: bool = Field(
        default=True,
        description="Enable Bazaar price shock detection (>15% deviation from rolling median)",
    )
    BAZAAR_SHOCK_THRESHOLD_PCT: float = Field(
        default=15.0,
        description="Percentage deviation from rolling median to trigger price shock guard (default: 15.0%)",
    )
    BAZAAR_SHOCK_MIN_SNAPSHOTS: int = Field(
        default=3,
        description="Minimum snapshots required before evaluating price shock guard",
    )
    BAZAAR_SHOCK_WINDOW_SECONDS: float = Field(
        default=300.0,
        description="Time window in seconds (5 minutes) for rolling median calculation",
    )
    ENABLE_SELLER_DOMINATION_GUARD: bool = Field(
        default=True,
        description="Enable seller domination guard against market manipulation",
    )
    MAX_SELLER_DOMINATION_RATIO: float = Field(
        default=0.40,
        description="Maximum percentage of active listings owned by a single seller before rejecting flip (default: 40%)",
    )
    PATH_A_MIN_LISTINGS: int = Field(
        default=3,
        description="Minimum active listings required for Path A clean flip evaluation (default: 3)",
    )

    # Complete Addon Coverage & Vanity Lane (Phase 7)
    ENABLE_VANITY_COMPS_LANE: bool = Field(
        default=False,
        description="Enable vanity sold-comps lane for pet skins and runes with >= 3 historical sales",
    )
    PET_CANDY_PENALTY_PER_CANDY: float = Field(
        default=0.015,
        description="Penalty percentage per pet candy applied (default 1.5% per candy, max 15%)",
    )
    ADDON_RULES_PATH: str = Field(
        default="config/addon_rules.json",
        description="Path to addon rules configuration JSON",
    )

    # Pet Bucket Valuation Engine & Merged-State Floor
    ENABLE_PET_BUCKET_VALUATION: bool = Field(
        default=True,
        description="Enable granular pet bucketing, merged-state floor reference, and sold-comps gating",
    )
    PET_XP_WINDOW_PCT: float = Field(
        default=25.0,
        description="Allowed XP deviation percentage for pet bucket reference matching (default: 25.0%)",
    )
    PET_MIN_SOLD_COMPS_48H: int = Field(
        default=5,
        description="Minimum confirmed sold comps in 48h required to alert on a pet flip (default: 5)",
    )
    PET_MIN_SALES_BY_RARITY: dict[str, int] = Field(
        default_factory=lambda: {
            "COMMON": 8,
            "UNCOMMON": 8,
            "RARE": 6,
            "EPIC": 4,
            "LEGENDARY": 3,
            "MYTHIC": 3,
        },
        description="Minimum same-bucket sales in the last 48h required for a pet alert by rarity",
    )
    PET_COMPS_WINDOW_HOURS: float = Field(
        default=48.0,
        description="Rolling lookback window in hours for pet sold comps (default: 48.0)",
    )
    PET_COMPS_PERCENTILE: float = Field(
        default=0.75,
        description="Percentile of sold comps to use as target resale cap (default: 0.75 / P75)",
    )
    PET_MARGIN_REVIEW_PCT: float = Field(
        default=40.0,
        description="Margin threshold above which pet confidence is capped at LOW (default: 40.0%)",
    )
    PET_HIGH_CONFIDENCE_MAX_SPREAD_PCT: float = Field(
        default=20.0,
        description="Maximum comps price spread (IQR / median) to qualify for HIGH confidence (default: 20.0%)",
    )
    PET_MINOR_HELD_ITEM_THRESHOLD_COINS: int = Field(
        default=2_000_000,
        description="Value threshold in coins below which held items are grouped into 'minor' bucket (default: 2,000,000)",
    )
    PET_DEDUPE_TTL_SECONDS: int = Field(
        default=300,
        description="Time-to-live in seconds for deduplicating pet alerts by (bucket, buy_price) (default: 300s)",
    )
    MERGED_FLOOR_TIE_TOLERANCE_PCT: float = Field(
        default=1.0,
        description="Percentage threshold for floor tie and supply dump detection (default: 1.0%)",
    )

    # High-Throughput Ingestion & Telemetry (Phase 8)
    HEALTH_PING_INTERVAL_CYCLES: int = Field(
        default=60,
        description="Number of cycles between automated Discord health pings (0 to disable)",
    )
    ENABLE_JSON_LOGGING: bool = Field(
        default=False,
        description="Enable structured JSON log formatting",
    )
    FEED_RECONNECT_INITIAL_DELAY: float = Field(
        default=3.0,
        description="Initial delay in seconds for WebSocket feed reconnection",
    )
    FEED_RECONNECT_BACKOFF_MULTIPLIER: float = Field(
        default=2.0,
        description="Backoff multiplier for WebSocket feed reconnection",
    )
    FEED_RECONNECT_MAX_DELAY: float = Field(
        default=32.0,
        description="Maximum delay in seconds for WebSocket feed reconnection",
    )

    # Economic Hardening & Tax Cliff Optimization
    ENABLE_CLIFF_SNAPPING: bool = Field(
        default=True,
        description="Enable snapping target resell price down to tax cliff boundaries (9,999,999 / 99,999,999)",
    )
    MAYOR_DERPY_ACTIVE: bool = Field(
        default=False,
        description="Mayor Derpy quadruples all Auction House listing fees and collection taxes (4.0x multiplier)",
    )
    COLLECTION_TAX_ENABLED: bool = Field(
        default=True,
        description="Enable 1.0% collection claim tax on AH sales",
    )
    COLLECTION_TAX_MIN_PRICE: int = Field(
        default=1_000_000,
        description="Minimum price threshold in coins for 1.0% collection tax (default: 1,000,000)",
    )

    # Order-Book Queue Depth & Aligned Hold Time
    ENABLE_QUEUE_DEPTH_HOLD_TIME: bool = Field(
        default=True,
        description="Enable order-book queue depth modeling for holding time estimation",
    )
    MIN_HOLD_TIME_HOURS: float = Field(
        default=0.25,
        description="Minimum hold time floor in hours (default: 0.25h / 15m)",
    )

    # Anti-Wash Trading & Comps Sanitization
    MIN_DISTINCT_BUYERS: int = Field(
        default=3,
        description="Minimum distinct buyers required in a sold comps cluster to prevent wash trading",
    )
    MIN_DISTINCT_SELLERS: int = Field(
        default=3,
        description="Minimum distinct sellers required in a sold comps cluster to prevent wash trading",
    )
    MAX_ACTOR_COMP_SHARE: float = Field(
        default=0.333,
        description="Maximum fraction of comps a single actor (buyer or seller) may represent in a cluster",
    )

    # Alert Survival Tracking & Dynamic Haircut
    ALERT_STALE_THRESHOLD_MINUTES: int = Field(
        default=30,
        description="Minutes after which an active alert in a high-velocity lane is classified as STALE",
    )
    ENABLE_LANE_CONVERSION_HAIRCUT: bool = Field(
        default=True,
        description="Apply 5% haircut when a valuation lane's 24h conversion rate falls below 40%",
    )

    # Mayor & Election Configuration (Phase 1)
    DERPY_COLLECTION_FEE_RATE: float = Field(
        default=0.04,
        description="Collection fee rate applied when Mayor Derpy is active (default: 0.04 / 4%)",
    )
    MAYOR_OVERRIDE: Optional[str] = Field(
        default=None,
        description="Manual override for active SkyBlock mayor (e.g. 'derpy' or 'paul')",
    )
    ELECTION_CACHE_TTL_SECONDS: int = Field(
        default=600,
        description="TTL in seconds for caching Hypixel mayor election API response (default: 600s / 10m)",
    )

    # Phase 4: Outcome Labels & Calibration
    ENABLE_OUTCOME_LABELING: bool = Field(
        default=True,
        description="Enable background evaluation and labeling of alert outcomes against ended auction sales",
    )
    OUTCOME_HORIZON_HOURS: float = Field(
        default=6.0,
        description="Hours after alert creation before an outcome is finalized and labeled (default: 6.0)",
    )
    SNIPE_SURVIVAL_MINUTES: float = Field(
        default=15.0,
        description="Window in minutes for a flagged listing to sell to be categorized as SOLD_FAST (default: 15.0)",
    )

    @model_validator(mode="after")
    def validate_webhook_and_defaults(self) -> "Settings":
        """Ensure valid webhook in production mode and fallback in dry run mode."""
        import logging
        cfg_logger = logging.getLogger(__name__)
        if self.AH_TAX_RATE != 0.02 or self.USE_PROGRESSIVE_TAX:
            cfg_logger.warning(
                "AH_TAX_RATE and USE_PROGRESSIVE_TAX are deprecated and ignored; "
                "src/constants/taxes.py is now the single source of truth for all fee calculations."
            )

        if not self.DISCORD_WEBHOOK_URL:
            if self.DRY_RUN:
                self.DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/dummy"
            else:
                raise ValueError(
                    "DISCORD_WEBHOOK_URL is required when DRY_RUN is False. "
                    "Set it in your environment or enable DRY_RUN."
                )
        return self


def get_settings(**kwargs) -> Settings:
    """Helper to instantiate Settings with optional overrides."""
    return Settings(**kwargs)
