# AGENTS.md — Hypixel SkyBlock AH Arbitrage Engine & Sniper

## 1. Non-Negotiable Compliance Guardrails
- **Strictly Observational:** NEVER implement packet injection, automated auction purchasing, slot clicking, container macroing, or inventory manipulation.
- **In-Game Mod Scope:** `client-mod/` is strictly restricted to rendering informational GUI overlays (`FlipScreen`) and executing standard `/viewauction <uuid>` commands upon user toggle (`AutoInspectManager`).
- **Local Loopback Security:** `src/local_server.py` must bind exclusively to `127.0.0.1:8765` and enforce origin checks and HMAC token validation when configured.

---

## 2. Repository Map & Module Boundaries
- `src/client.py` — Cloudflare edge cache clock sync (`lastUpdated` HEAD probing) and parallel page scraping (`aiohttp`).
- `src/nbt_decoder.py` — Offloaded gzip/zlib NBT decoding (`item_bytes`) and priority sorting by listing `start` timestamp.
- `src/evaluator.py` — Core orchestrator for the 5 valuation lanes (`floor`, `upgraded_networth`, `sold_comps`, `pets`, `god_roll`) and manipulation guards.
- `src/networth_evaluator.py` & `src/craft_evaluator.py` — Addon retention valuation and instant-buy craft-cost ceilings.
- `src/comps_evaluator.py` — Historical `sales_history` feature-hash matching, `1.5 * IQR` outlier filtering, and `p25` pricing.
- `src/constants/` — Fee/tax rules (`taxes.py`), canonical lanes (`lanes.py`), pet curves, recipes, and attribute tables.
- `src/ranking.py` — Multi-factor confidence scoring (0–100) and capital velocity prioritization.
- `src/db.py` — SQLite WAL persistence manager (`data/market_intelligence.db`).
- `src/local_server.py` & `src/notifier.py` — Local WebSocket broadcaster and Discord webhook dispatcher.
- `client-mod/` — Java 25 / Minecraft Fabric 1.21.x client mod (`AhSniperClient`, `FlipScreen`, `AutoInspectManager`).
- `scripts/` — Operational CLI suite (`backtest.py`, `calibration_report.py`, `explain.py`, `ledger_cli.py`).
- `tests/` — Offline unit and integration test suite (`pytest`, 357+ tests).

---

## 3. Core Architectural Invariants (Never Violate)
1. **Auction UUID vs. NBT Item UUID Separation:**
   - Top-level `auction["uuid"]` is the Hypixel Auction ID required by `/viewauction <uuid>`.
   - NBT `tag.ExtraAttributes.uuid` is the SkyBlock item instance ID.
   - NEVER allow decoded NBT dictionaries to overwrite the top-level Auction `uuid` in `FlipResult`, database tables, or WebSocket payloads.
2. **Hot-Path Latency & Zero-DB Discipline (< 5ms Target):**
   - **Zero SQLite on Critical Path:** `src/evaluator.py` and `src/ranking.py` must NEVER execute SQLite queries or disk I/O during tick evaluation. All historical comps, $\mathbb{E}[T_{\text{sell}}]$ velocity tables, and open portfolio holdings (`purchases`) must be pre-aggregated into in-memory dictionaries during background housekeeping and swapped atomically in RAM.
   - **3-Step Delta Order of Operations:**
     1. Prune disappeared/sold UUIDs from the in-memory active book and floor cache.
     2. Decode NBT (`item_bytes`) ONLY for newly appeared UUIDs and overlay their prices into the in-memory floor cache.
     3. Evaluate only the new UUIDs against the updated in-memory floor cache while enforcing `TIE_AT_FLOOR` and `INTRA_BATCH_SUPPLY_DUMP` guards before broadcasting to the WebSocket.

3. **Tax, Derpy & Craft Ceiling Enforcement:**
   - All net profit and margin calculations must route through `src/constants/taxes.py` (`best_resale_net()`, accounting for 1%/2%/2.5% creation tiers, 1% collection fee, and 4% Mayor Derpy tax).
   - No `upgraded_networth` or `god_roll` valuation may exceed the craft-cost ceiling from `src/craft_evaluator.py` unless backed by verified collector comps.
4. **Structured Decision Trace Compatibility:**
   - Any new guard, multiplier, or scoring factor added to `src/evaluator.py` or `src/ranking.py` must record its inputs and rejection/adjustment reason in the structured decision trace so `scripts/explain.py` remains 100% compatible.
5. **WebSocket Protocol Sync:**
   - Any schema change to messages emitted by `src/local_server.py` must be updated in lockstep with `client-mod/src/main/java/` JSON deserializers.

---

## 4. Mandatory Agent Workflow & Verification Loop
Follow this strict sequence for every task:

1. **Explore First:** Read the target modules in `src/` and corresponding tests in `tests/` before proposing edits.
2. **Plan Artifact:** For multi-file changes, output a concise Implementation Plan specifying touched files, data-model diffs, and latency impact before writing code.
3. **Execute with Type Safety:** Maintain strict Python type annotations across `src/` and clean Java 25 syntax in `client-mod/`.
4. **Verification Commands (Run After Every Phase):**
   - **Python Engine & CLI Suite:**
     ```bash
     pytest -q
     ```
     *Requirement:* All 357+ existing tests must pass, and every new valuation rule, parser optimization, or guard must include unit tests in `tests/`.
   - **Fabric Client Mod (when `client-mod/` or WebSocket schema is modified):**
     ```bash
     cd client-mod && ./gradlew build
     ```
     *(Use `.\gradlew.bat build` on Windows PowerShell).*
