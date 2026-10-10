# SkyBlock AH Automation — Operations Runbook & Quickstart

## 1. Quickstart (One-Page Setup)

### Prerequisites
- Python 3.10+ (tested on Python 3.14)
- SQLite 3.35+
- (Optional) Hypixel API Key for higher rate limits
- (Optional) Fabric Client Mod running on Minecraft 1.21.x with WebSocket bridge

### Installation
```bash
# Clone and enter workspace
cd "skyblock ah automation"

# Install pinned dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your DISCORD_WEBHOOK_URL and optional HYPIXEL_API_KEY
```

### Running the Monitor
```bash
# Dry-run mode (outputs to stdout, zero Discord webhook spam)
python -m src.main --dry-run

# Single inspection cycle (for tests/diagnostics)
python -m src.main --dry-run --once

# Live production mode
python -m src.main
```

---

## 2. In-Game Bridge (Fabric Mod Connection)

The local WebSocket server broadcasts snipes in real time directly to the Fabric client mod:
- **Default Address**: `ws://127.0.0.1:8765`
- **Security Guardrails**:
  - Only binds to loopback interfaces (`127.0.0.1`, `localhost`, `::1`). External interface binds are refused.
  - Browser/CORS `Origin` header checking: Only origins listed in `LOCAL_WS_ALLOWED_ORIGINS` are accepted. Mod clients omitting `Origin` connect automatically.
  - Optional HMAC token authentication: If `LOCAL_WS_TOKEN` is configured in `.env`, connect with `ws://127.0.0.1:8765?token=<YOUR_TOKEN>`.

---

## 3. Architecture & Lanes Overview

The engine evaluates live BIN auctions through 5 canonical lanes:
1. **`floor`**: Clean items evaluated against 2nd lowest BIN and clean market floors.
2. **`upgraded_networth`**: Items with enchants, stars, recombobulators, gemstones, and craft-ceiling caps.
3. **`sold_comps`**: Items priced against recent empirical sales comps using weighted quantiles (`p25`).
4. **`pets`**: Pets bucketed strictly by `(type, rarity, level_bracket, held_item)` with sales gating.
5. **`god_roll`**: High-tier Crimson Isle / Kuudra attribute rolls.

All fees are calculated using the single source of truth in `src/constants/taxes.py`:
- **Creation Fee**: 1.0% (<10M), 2.0% (10M-100M), 2.5% (>=100M).
- **Collection Fee**: 1.0% (>=1M), 0% (<1M). Rises to `DERPY_COLLECTION_FEE_RATE` (default 4.0%) when Mayor Derpy is elected.

---

## 4. Common Failure Modes & Recovery

### A. Hypixel API Rate Limiting (HTTP 429)
- **Symptom**: `Hypixel rate limit (429) fetching auction page... Sleeping ...` in logs.
- **Root Cause**: Polling faster than Hypixel API token allowance or multiple instances sharing a key.
- **Remedy**:
  - The client automatically performs exponential backoff with jitter and respects `Retry-After`.
  - Ensure `HYPIXEL_API_KEY` is configured in `.env`.
  - Verify `MAX_CONCURRENT_PAGES` is between 10 and 15.

### B. Cold Start / Missing Alerts for Pets or Comps
- **Symptom**: Log message `System in COLD START / WARM-UP state: only N recorded sales in history`.
- **Root Cause**: Database has fewer than `COLD_START_MIN_SALES` (default 50) recorded sales in `sales_history`.
- **Remedy**: Let the monitor run for 5–15 minutes. The engine automatically ingests `/v2/skyblock/auctions_ended` every cycle to warm up sales velocity and empirical comps.

### C. WebSocket Connection Refused (HTTP 401 or 403)
- **Symptom**: Mod client fails to connect or receives HTTP 401 / 403.
- **Root Cause**:
  - **401**: `LOCAL_WS_TOKEN` is set in `.env` but client did not provide matching `?token=...`.
  - **403**: Connection came from a browser with an unapproved `Origin` header not in `LOCAL_WS_ALLOWED_ORIGINS`.
- **Remedy**: Provide the matching token in client URL or add the host to `LOCAL_WS_ALLOWED_ORIGINS`.

### D. SQLite Database Busy / Locked
- **Symptom**: `sqlite3.OperationalError: database is locked`.
- **Remedy**: Database initializes with `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. Ensure external inspection tools (e.g. SQLite browser) do not hold exclusive locks while the monitor is running.

---

## 5. Alert Diagnostics & Tooling

### Explaining an Alert Decision
To inspect why an alert was generated or rejected, inspect its trace:
```bash
# By Alert ID
python scripts/explain.py 12

# By Hypixel Auction UUID
python scripts/explain.py 381fbc464161476db841f3e7fc71cfa0
```

### Calibration & Outcome Reporting
Evaluate model accuracy and realized survival rates:
```bash
# Output console report and CSV to reports/calibration_report.csv
python scripts/calibration_report.py --csv reports/calibration.csv
```

### Historical Backtesting
Replay historical sales to test strategies:
```bash
python scripts/backtest.py --lane floor --min-profit 1000000
```
