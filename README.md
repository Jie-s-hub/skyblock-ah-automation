# Hypixel SkyBlock AH Arbitrage Engine & Sniper — Production Intelligence System

A high-performance, asynchronous algorithmic market ingestion and valuation engine with sub-second API clock synchronization, real-time Bazaar addon pricing, historical sold-comps evaluation, empirical Bayes dynamic retention, capital-efficiency ranking, and automated portfolio PnL tracking for Hypixel SkyBlock.

> [!NOTE]
> This system is strictly an **observational data analysis and client-assist tool**. It never injects packets, automates purchasing, or manipulates in-game inventories. In-game interactions are client-side informational displays with native `/viewauction <uuid>` command execution.

---

## System Architecture

```
                                  ┌────────────────────────────────┐
                                  │   Hypixel Public REST API /    │
                                  │   Coflnet WebSocket Firehose   │
                                  └──────────────┬─────────────────┘
                                                 │
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ High-Throughput Ingestion Layer (src/client.py & src/stream_client.py)                 │
│  - Clock-synced sub-second REST polling (/v2/skyblock/auctions)                        │
│  - WebSocket feed with exponential backoff & randomized jitter (Coflnet modsocket)     │
│  - Watermark cursor tracking & ended auctions ingestion (/skyblock/auctions_ended)    │
│  - Concurrently fetches pages via asyncio.Semaphore(15)                                │
└────────────────────────────────────────┬───────────────────────────────────────────────┘
                                         │
                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ NBT Parsing & Addon Extraction (src/nbt_decoder.py)                                    │
│  - Base64 -> Gzip -> NBT parsing via nbtlib executed in background ThreadPool          │
│  - Extracts: Reforge, Stars (Dungeon + Master), Gemstones, Enchants, Pet Level & XP,   │
│    Crimson Prestige, HPB/FPB, Ability Scrolls, Enrichments, Drill Parts, Pet Candy     │
└────────────────────────────────────────┬───────────────────────────────────────────────┘
                                         │
                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ Valuation & Risk Intelligence Engine (src/evaluator.py)                                │
│  - Path A (Clean Listings): 2nd LBIN with progressive tax math                         │
│  - Path B (Upgraded Networth): Bazaar addon pricing with dynamic retention             │
│  - Path C (Historical Sold-Comps): 1.5x IQR scrubbing & exponential time-decay median │
│  - Craft Cost Ceiling & Anti-Bait Gates: Live craft material floor comparison          │
│  - Anti-Domination Guard: Rejects markets where a single seller owns >= 40% listings   │
│  - Bazaar Shock Guard: Rejects flips when addon ingredients deviate > 15% from median  │
│  - Sales Velocity Gate: Validates turnover volume (>= 5 sales/24h)                     │
│  - Capital-Efficiency Ranking: CES (%/hr) and Multi-Factor Confidence Score (0-100)    │
└────────────────────────────────────────┬───────────────────────────────────────────────┘
                                         │
                     ┌───────────────────┴───────────────────┐
                     ▼                                       ▼
┌────────────────────────────────────────┐ ┌───────────────────────────────────────────┐
│ Local WebSocket Bridge                 │ │ Persistence & Alerting                    │
│ (src/local_server.py)                  │ │ (src/notifier.py & src/db.py)             │
│  - ws://127.0.0.1:8765                 │ │  - SQLite WAL market intelligence database │
│  - Streams JSON to Fabric 26.1.2 mod   │ │  - Rich Discord embeds with 1-click inspect│
│  - In-game GUI with [Inspect] button   │ │  - Player trade ledger & realized PnL     │
└────────────────────────────────────────┘ └───────────────────────────────────────────┘
```

---

## Core Intelligence Capabilities

### 1. Progressive Auction House Tax Schedule (`src/constants/taxes.py`)
Hypixel SkyBlock applies a progressive bracketed tax schedule:
- **Resell $< 10\text{M}$:** $1.0\%$ listing fee.
- **Resell $10\text{M} - 100\text{M}$:** $2.0\%$ listing fee.
- **Resell $> 100\text{M}$:** $2.0\%$ base listing fee $+ 1.0\%$ collection fee on amount exceeding $100\text{M}$ (effective tax $\approx 2.5\% - 3.0\%$).

### 2. Dual-Mode & Sold-Comps Valuation Engine
- **Path A (Clean Items):** Evaluates clean items against the 2nd lowest BIN (`LBIN_2`), requiring a minimum listing sample size to prevent artificial floor manipulation.
- **Path B (Upgraded Items):** Calculates replacement networth by pricing raw base LBIN plus Bazaar addon replacement costs scaled by dynamic retention rates.
- **Sold-Comps Valuation (`src/comps_evaluator.py`):**
  - **Feature Hashing:** Computes deterministic SHA-256 signatures (`strict_hash` matching all addons, `relaxed_hash` matching stars within $\pm 1$).
  - **$1.5 \times \text{IQR}$ Outlier Scrubbing:** Discards manipulated and outlier historical sales.
  - **Exponential Time Decay:** Weights recent sales higher ($w_i = e^{-\lambda \Delta t}$, half-life 48 hours).
  - **Clamping:** Caps divergence from Path B networth within $\pm 25\%$.

### 3. Empirical Bayes Data-Fitted Retention (`src/retention_fitter.py`)
Replaces static retention tiers with an empirical Bayes shrinkage estimator:
$$\hat{R} = \frac{n}{n + \tau}\bar{R} + \frac{\tau}{n + \tau}R_{\text{prior}}$$
- Shrinkage pseudo-count: $\tau = 10.0$
- Static tier priors: $85\%$ for $\ge 500\text{M}$, $70\%$ for $50\text{M} - 500\text{M}$, $50\%$ for $< 50\text{M}$.
- Fitted retention clamped strictly within $[0.30, 0.95]$.

### 4. Capital Efficiency & Multi-Factor Confidence (`src/ranking.py`)
- **Hold Duration Estimation:**
  $$T_{\text{hold}} = \max\left(0.5, \min\left(48.0, \frac{1.0}{V}\right)\right)\quad (\text{hours})$$
- **Capital Efficiency Score (CES):**
  $$\text{CES} = \frac{\text{Net Profit}}{T_{\text{hold}} \times \text{Buy Price}} \times 100.0\quad (\%/\text{hr})$$
- **Confidence Scoring ($0 - 100$):** Multi-factor composite weighting liquidity, margin %, valuation path certainty, and turnover velocity into categorical labels: `HIGH` ($\ge 75$), `MEDIUM` ($50 - 74$), and `LOW` ($< 50$).

### 5. Market Manipulation & Risk Guards
- **Craft-Cost Ceiling (`src/craft_evaluator.py`):** Calculates real-time Bazaar Instant-Buy crafting costs. Automatically rejects listings priced $\ge 95\%$ of craft cost and clamps resale expectations.
- **Anti-Domination Guard:** Rejects flips when a single seller controls $\ge 40\%$ of active listings for that item ID.
- **Bazaar Price Shock Guard:** Protects networth valuation by monitoring a rolling 10-snapshot window; rejects flips when any addon component has spiked or dumped $> 15\%$ from its rolling median.
- **Vanity Lane & Cosmetic Filter (`src/addon_rules.py`):** Permanently blocks vanity noise (dyes, furniture, portals), while routing pet skins and runes through a strict sold-comps verification lane requiring $\ge 3$ recorded sales.

### 6. Automated Player Trade Ledger & Realized PnL (`src/player_tracker.py`)
- Tracks user purchases (`HELD`) and automatically reconciles sales via Hypixel's `/skyblock/auctions_ended` feed.
- Resolves Minecraft usernames via Mojang API (e.g. `ongvvkk`).
- Uses exact `item_uuid` matching with FIFO fallback.
- Dispatches Discord completion embeds reporting holding duration, gross revenue, progressive AH taxes paid, and net realized PnL.

### 7. Telemetry & Health Monitoring (`src/telemetry.py`)
- **JSON Log Formatting:** `JsonLogFormatter` outputs single-line structured JSON logs with timestamps, stage labels, and execution latencies.
- **Stage Profiling:** Context manager `StageTimer` measures latencies across `fetch`, `decode`, `eval`, and `alert` stages.
- **Automated Health Heartbeat:** Dispatches periodic Discord embed updates with uptime, cycle counts, snipes detected, error counts, and average stage latencies.

---

## File Structure

```
skyblock-ah-automation/
├── config/
│   └── addon_rules.json          # Rules for HPB/FPB, scrolls, enrichments, drill parts, vanity
├── data/
│   └── market_intelligence.db    # SQLite WAL database for alerts, sales, comps, and retention
├── scripts/
│   ├── ledger_cli.py             # CLI portfolio manager (open, pnl, summary, add-buy, close)
│   └── backtest.py               # Historical replay engine evaluating algorithm performance
├── src/
│   ├── __init__.py
│   ├── addon_rules.py            # Complete addon pricing helpers & vanity classification
│   ├── bazaar.py                 # Bazaar cache, instant-buy pricing & shock detection
│   ├── client.py                 # Hypixel API client with pagination & clock sync
│   ├── comps_evaluator.py        # Sold-comps valuation with IQR filtering & time decay
│   ├── config.py                 # Pydantic Settings and feature flag configuration
│   ├── craft_evaluator.py        # Bazaar recipe craft cost calculator & ceiling gate
│   ├── db.py                     # SQLite persistence layer (WAL mode, 6 tables)
│   ├── evaluator.py              # Central arbitrage valuation & multi-gate filtering engine
│   ├── local_server.py           # WebSocket server bridge for Fabric client mod (ws://127.0.0.1:8765)
│   ├── main.py                   # Async polling orchestrator and lifecycle coordinator
│   ├── nbt_decoder.py            # High-throughput binary NBT extraction & upgrade parsing
│   ├── networth_evaluator.py     # Path B item networth calculator
│   ├── notifier.py               # Discord embed generator & webhook dispatcher
│   ├── player_tracker.py         # Automated portfolio reconciliation & realized PnL tracking
│   ├── ranking.py                # Capital-efficiency scoring & multi-factor confidence ranking
│   ├── retention_fitter.py       # Empirical Bayes data-fitted retention shrinkage model
│   ├── sales_ingestor.py         # Ended auctions ingestion & feature hash extraction
│   ├── stream_client.py          # WebSocket feed ingestion client with backoff & jitter
│   ├── telemetry.py              # Structured JSON logging, stage timers & Discord health pings
│   ├── velocity.py               # Sales velocity & liquidity turnover tracker
│   └── constants/
│       ├── item_categories.py    # Progression staple definitions & category tagging
│       ├── pet_levels.py         # Pet XP curves (1-100 & 1-200) and tier baselines
│       ├── prestige.py           # Crimson Isle armor tiers & Divan chamber mappings
│       ├── recipes.py            # Crafting recipes registry for craft ceiling gates
│       └── taxes.py              # Progressive AH tax bracket calculator
└── tests/                        # Automated unit and integration test suite (266 tests)
```

---

## Configuration & Feature Flags

Configure settings via `.env` or environment variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DISCORD_WEBHOOK_URL` | *Required* | Discord webhook URL for alerts and trade reports |
| `MIN_PROFIT_COINS` | `1,000,000` | Minimum net coin profit required for an alert |
| `MIN_PROFIT_MARGIN_PCT` | `15.0` | Minimum net profit margin percentage |
| `POLL_INTERVAL_SECONDS` | `60` | Fallback poll interval between auction cycles |
| `DRY_RUN` | `false` | Run without dispatching Discord webhooks |
| `ENABLE_SOLD_COMPS` | `false` | Enable historical sold-comps valuation lane |
| `ENABLE_DYNAMIC_RETENTION` | `false` | Enable Empirical Bayes data-fitted retention |
| `ENABLE_BAZAAR_SHOCK_GUARD`| `false` | Enable Bazaar price shock detection (>15% deviation) |
| `ENABLE_SELLER_DOMINATION_GUARD` | `false` | Enable seller dominance guard (>= 40% listing share) |
| `ENABLE_VANITY_COMPS_LANE` | `false` | Enable vanity comps lane for pet skins and runes |
| `ENABLE_PLAYER_TRACKING` | `false` | Enable automated player portfolio tracking |
| `TARGET_PLAYER_NAME` | `ongvvkk` | Target player username for portfolio reconciliation |
| `HEALTH_PING_INTERVAL_CYCLES` | `60` | Cycles between automated Discord health pings (0 to disable) |
| `ENABLE_JSON_LOGGING` | `false` | Enable structured single-line JSON log output |

---

## CLI Tools & Management

### 1. Player Trade Ledger CLI (`scripts/ledger_cli.py`)
Manage your portfolio and inspect trade history from the terminal:
```bash
# View portfolio metrics, win rate, and realized PnL summary for the tracked player
python scripts/ledger_cli.py player-stats

# View full report of all closed trades with hold durations and tax math
python scripts/ledger_cli.py report

# Manually register a purchased auction (records state as HELD)
python scripts/ledger_cli.py mark-bought --uuid "auc-1234" --id "HYPERION" --price 1200000000

# Manually close a position with realized sell price (computes taxes & net PnL)
python scripts/ledger_cli.py mark-sold --uuid "auc-1234" --sell-price 1450000000
```

### 2. Historical Replay Backtester (`scripts/backtest.py`)
Evaluate your arbitrage strategy against historical recorded market data:
```bash
python scripts/backtest.py --window-hours 48 --capital 100000000 --sample-size 3
```

### 3. Running the Engine
```bash
# Single-cycle dry-run inspection (fetches live market, evaluates, profiles, and exits)
python -m src.main --dry-run --once

# Continuous dry-run mode (console output only)
python -m src.main --dry-run

# Production live alerting mode (dispatches Discord embeds & WebSocket stream)
python -m src.main
```

---

## Verification & Testing

The system is rigorously verified via an automated test suite containing **266 unit and integration tests** across 30 test files with 100% pass rate:

```bash
python -m pytest -v
```

### Test Coverage Highlights
- **Taxes & Math:** `test_taxes.py`, `test_evaluator.py`, `test_star_costs.py`, `test_craft_ceiling.py`
- **Database & Persistence:** `test_db.py`, `test_ledger_cli.py`, `test_backtest.py`
- **Valuation Intelligence:** `test_comps_evaluator.py`, `test_retention_fitter.py`, `test_bazaar_shock.py`, `test_domination_guard.py`
- **Addon Rules & Networth:** `test_addon_rules.py`, `test_networth_evaluator.py`, `test_networth_advanced.py`
- **Player Tracking:** `test_player_tracker.py`
- **High-Throughput & Telemetry:** `test_telemetry.py`, `test_client.py`, `test_clock_sync.py`, `test_v2_features.py`
