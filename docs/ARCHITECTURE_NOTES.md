# SkyBlock AH Arbitrage Engine — Architecture Notes

## Canonical Lane Identifiers & Bucket-Key Scheme
- `floor`: Clean baseline floor; Key = `ITEM:{internal_id}`
- `upgraded-networth`: Replacement networth via Bazaar addons + dynamic retention; Key = `ITEM:{internal_id}`
- `sold-comps`: Time-decayed historical comps; Key = `ITEM:{internal_id}:{features_hash}`
- `pets`: Pet bracket isolation; Key = `PET:{pet_type}:{rarity}:{bracket}:{held_item_class}`
- `god-roll`: High-value Crimson/Kuudra attributes; Key = `ATTR:{internal_id}:{attr1}_{lvl}:{attr2}_{lvl}`

## Evaluation Flow Per Lane
1. `pets`: Pet NBT decoded -> (type, rarity, bracket [1, 2-99, 100, 101-199, 200], held item).
   Filters: supply dump, tie at floor, seller dominance, sales volume gate -> target = min(model, comps).
2. `god-roll`: Decodes attributes -> evaluates known god-roll pairs / tier floor -> clamps resale target.
3. `floor`: Clean listings (<1 star, clean NBT) compared against 2nd LBIN in bucket excluding self UUID.
   Gates: craft-cost ceiling, seller dominance, minimum sample size.
4. `sold-comps`: Feature hash lookup with 1.5x IQR outlier scrubbing and exponential time decay.
5. `upgraded-networth`: Base floor + sum(addon Bazaar/rule prices * empirical Bayes retention).
   Gates: Bazaar shock guard (>15% swing), craft ceiling, max target/buy ratio.

## Fees, Confidence & Persistence Architecture
- **Fee Model**: `src/constants/taxes.py` is the single source of truth:
  `net_revenue = price - creation_fee(price) - collection_fee(price, derpy_active)`
  where creation_fee has cliffs at 10M/100M and collection_fee (1% or 4% Derpy) starts at >=1M.
  Cliffs optimized via `best_resale_net(target)`.
- **Confidence Scoring**: `src/ranking.py` (`compute_confidence_score`):
  Weighted composite of valuation path (40%), liquidity velocity (30%), margin buffer (30%),
  and risk deductions (-20%). Categorical labels: HIGH (>=75 with comps), MEDIUM, LOW.
- **SQLite Database Tables (`data/market_intelligence.db`, WAL Mode)**:
  1. `alerts`: Recorded arbitrage opportunities and pricing traces.
  2. `purchases`: Tracked player portfolio positions, buy/sell attribution, and realized PnL.
  3. `sales_history`: Completed auction transactions ingested from `/skyblock/auctions_ended`.
  4. `bazaar_snapshots`: Rolling price points for shock detection and addon pricing.
  5. `comps_cache`: Cached median and IQR spread for item feature hashes.
  6. `retention_params`: Empirical Bayes shrinkage statistics per item category.
