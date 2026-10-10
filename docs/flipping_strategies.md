# Hypixel SkyBlock Advanced Arbitrage Strategies: Mathematical Formalization & Algorithmic Feasibility

> [!NOTE]
> This document provides the formal mathematical and economic foundation for advanced automated arbitrage across the Hypixel SkyBlock economy. It establishes quantitative profit formulations, fee models, liquidity risk frameworks, and architectural blueprints for four high-yield flipping archetypes beyond simple Buy-It-Now (BIN) floor delta evaluation.

---

## 1. Executive Summary & Market Microstructure

Hypixel SkyBlock operates a dual-exchange virtual economy with distinct clearing mechanisms, order dynamics, and participant behavior:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       HYPIXEL SKYBLOCK DUAL ECONOMY                         │
├──────────────────────────────────────┬──────────────────────────────────────┤
│       BAZAAR EXCHANGE (BZ)           │       AUCTION HOUSE (AH)             │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ - Continuous Double Auction          │ - Asynchronous Single/Batch Listings │
│ - Standardized Commodities           │ - Highly Heterogeneous Assets (NBT)  │
│ - Instant Buy / Instant Sell (Market)│ - Buy-It-Now (BIN) & Timed Auctions  │
│ - Buy Order / Sell Offer (Limit)     │ - No Native Order Book (Floor Search)│
│ - Ultra-High Turnover Velocity       │ - Dispersed Turnover Velocity        │
│ - Low Gross Margins (1% – 5%)        │ - High Gross Margins (5% – 300%+)    │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

Standard automated monitors restrict their search space to single-asset floor price misalignments on the Auction House (e.g., purchasing an item listed at $P_{\text{lowest}} < P_{\text{second\_lowest}}$). While computationally simple ($O(N \log N)$ sorting), this strategy is saturated by high-frequency scrapers and vulnerable to price manipulation ("bait listings").

Substantial economic alpha resides at the intersection of:
1. **Cross-Exchange Arbitrage (BZ $\leftrightarrow$ AH)**: Exploiting convenience premia on assembled craftables.
2. **State-Transformation Arbitrage (NPC / Upgrade Pipelines)**: Capitalizing on time-discounted or item-transformed assets (e.g., Kat Pet training).
3. **Multi-Attribute Valuation**: Disentangling high-dimensional combinatorial traits (e.g., Kuudra armor attributes) from base-item floor valuations.
4. **Bundle Dispersion & Extraction**: Decomposing heavily upgraded, hypermaxed items into salvageable or unbundled sub-components.

---

## 2. Foundational Economic & Tax Framework

All net profit functions derive from the exact fee structure governing Hypixel SkyBlock exchanges.

### 2.1 Auction House Tax Function ($\tau_{\text{AH}}$)

The Auction House collects a non-refundable listing fee upon creation and a sales tax upon completion. For standard BIN listings, the total deduction $\tau_{\text{AH}}$ is modeled as a function of the gross resale price $P_{\text{sell}}$:

$$\tau_{\text{AH}}(P_{\text{sell}}) = \begin{cases} 
0.01 \cdot P_{\text{sell}} & \text{if } P_{\text{sell}} < 10{,}000{,}000 \\ 
0.02 \cdot P_{\text{sell}} & \text{if } P_{\text{sell}} \ge 10{,}000{,}000 
\end{cases}$$

For high-value assets ($P_{\text{sell}} \ge 10{,}000{,}000\text{ coins}$), which represent the primary domain of advanced arbitrage, $\tau_{\text{AH}} \equiv 0.02$ ($2.0\%$).

### 2.2 Bazaar Tax Function ($\tau_{\text{BZ}}$)

The Bazaar levies a percentage tax on the filled value of both Buy Orders and Sell Offers. The base rate is $1.25\%$, reducible via the Community Center "Bazaar Taxes" account perk upgrade:

$$\tau_{\text{BZ}} = 0.0125 \cdot (1 - 0.04 \cdot k) \quad \text{for } k \in \{0, 1, 2, 3, 4, 5\}$$

Where $k$ represents the player's Community Center upgrade tier. At tier $k = 5$ (maximum perk), the effective tax rate is:

$$\tau_{\text{BZ}} = 0.0125 \cdot (1 - 0.20) = 0.0100 \quad (1.00\%)$$

When sourcing ingredients via Bazaar **Buy Orders** (limit orders), the unit acquisition cost is:

$$P_{\text{acquire}} = P_{\text{order}} \cdot (1 + \tau_{\text{BZ}})$$

When sourcing via **Instant Buy** (market orders taking the lowest Sell Offer), the unit cost is the direct instantaneous offer price:

$$P_{\text{acquire}} = P_{\text{instant\_offer}}$$

### 2.3 Auxiliary Conversion & Frictional Costs ($C_{\text{aux}}$)

Arbitrage pipelines incur non-exchange overheads:
- **Anvil Coin & Experience Costs ($C_{\text{anvil}}$)**: Combining items consumes player experience levels. The coin equivalent is quantified using Grand Experience Bottles ($3{,}000\text{ XP}$) or Titanic Experience Bottles ($42{,}000\text{ XP}$) sourced from the Bazaar:
  $$C_{\text{XP}}(L) = \left\lceil \frac{\text{XP\_Required}(L)}{\text{XP\_Bottle}} \right\rceil \cdot P_{\text{BZ}}(\text{Bottle}) \cdot (1 + \tau_{\text{BZ}})$$
- **Gemstone Grinder Removal Fee ($C_{\text{grind}}$)**: Extracting a gemstone from a socket requires a nominal coin fee ranging from $25{,}000$ to $100{,}000\text{ coins}$ per slot depending on rarity.
- **Kat Flower Speedup Cost ($C_{\text{flower}}$)**: Bypassing a 24-hour pet training block requires 1 Kat Flower:
  $$C_{\text{flower}} = P_{\text{BZ}}(\text{KAT\_FLOWER}) \cdot (1 + \tau_{\text{BZ}})$$

---

## 3. Archetype 1: Bazaar-to-AH Craft Arbitrage

```
┌────────────────────────┐      ┌────────────────────────┐
│  Bazaar Raw Materials  │      │  Auction House (Base)  │
│  (e.g., Null Ovoids,   │      │  (e.g., Judgement Core,│
│   Quartz, Iron Blocks) │      │   Warden Heart)        │
└───────────┬────────────┘      └───────────┬────────────┘
            │ Buy Orders                    │ Lowest BIN
            └───────────────┬───────────────┘
                            ▼
               ┌────────────────────────┐
               │    Crafting Assembly   │
               │   (Slayer / Collection │
               │    Level Requirement)  │
               └────────────┬───────────┘
                            ▼
               ┌────────────────────────┐
               │   Auction House BIN    │
               │ (e.g., Terminator,     │
               │  Warden Helmet, Juju)  │
               └────────────────────────┘
```

### 3.1 Concept & Mechanism
Hypixel SkyBlock players routinely pay a significant convenience markup for fully assembled weapons, armor, and accessories to avoid:
1. Sourcing dozens of disparate materials across inventory limits.
2. Unlocking high-tier recipe requirements (e.g., Enderman Slayer 7 for Terminator, Zombie Slayer 8 for Warden Helmet, Wolf Slayer 7 for Overflux).
3. Undergoing manual multi-step crafting trees.

An automated arbitrage engine monitors the spread between the sum of component costs on the Bazaar and the lowest active BIN listing of the finished product on the Auction House.

**Canonical Exemplars:**
- **Terminator**: 1 Judgement Core (AH) + 3 Braided Griffin Feathers (AH/BZ) + 1 Tessellated Ender Pearl (BZ) + 32 Null Blades (BZ) + 80 Soul Strings (BZ).
- **Juju Shortbow**: 32 Enchanted Quartz Blocks (BZ) + 32 Null Ovoids (BZ) + 32 Enchanted Iron Blocks (BZ).
- **Warden Helmet**: 1 Warden Heart (AH) + 1 Revive Book (BZ) + 128 Rev Catalyst (BZ) + 256 Golden Powder (BZ).
- **Overflux Power Orb**: 1 Overflux Capacitor (AH) + 1 Mana Flux Power Orb (AH/Craft) + 128 Enchanted Gold Blocks (BZ) + 256 Enchanted Redstone Blocks (BZ) + 256 Enchanted Iron Blocks (BZ).

### 3.2 Formal Mathematical Profit Formula

Let:
- $P_{\text{AH, sell}}$: Target resale price of the finished item on the Auction House (typically $P_{\text{BIN, 2nd}} - 1$).
- $\tau_{\text{AH}}$: Auction House tax ($0.02$).
- $P_{\text{base}}$: Acquisition cost of the non-Bazaar core component on AH (if applicable, else $0$).
- $R = \{(c_i, q_i)\}_{i=1}^m$: Recipe multiset where $c_i$ is the $i$-th Bazaar commodity identifier and $q_i$ is its required quantity.
- $P_{\text{BZ, buy}}^{(i)}$: Best Buy Order price for commodity $i$.
- $\tau_{\text{BZ}}$: Player's Bazaar tax rate ($0.010$ to $0.0125$).
- $C_{\text{craft}}$: Incidental recipe crafting fee or conversion cost.

The net profit function $\Pi_{\text{craft}}$ is:

$$\Pi_{\text{craft}} = P_{\text{AH, sell}} \cdot (1 - \tau_{\text{AH}}) - \left[ P_{\text{base}} + \sum_{i=1}^{m} q_i \cdot P_{\text{BZ, buy}}^{(i)} \cdot (1 + \tau_{\text{BZ}}) + C_{\text{craft}} \right]$$

The net percentage margin $M_{\text{craft}}$ is:

$$M_{\text{craft}} = \frac{\Pi_{\text{craft}}}{P_{\text{base}} + \sum_{i=1}^{m} q_i \cdot P_{\text{BZ, buy}}^{(i)} \cdot (1 + \tau_{\text{BZ}}) + C_{\text{craft}}} \times 100\%$$

When factoring in inventory opportunity cost and capital lockup duration $T_{\text{craft}}$ (time to fill Bazaar buy orders plus AH sale time):

$$\text{Capital Yield Rate } \rho_{\text{craft}} = \frac{\Pi_{\text{craft}}}{\text{Total Investment} \times T_{\text{craft}}} \quad \left[\frac{\text{coins}}{\text{coin} \cdot \text{hour}}\right]$$

### 3.3 Exact Fee Structure Breakdown

| Cost Component | Applicable Rate | Trigger Mechanism | Mathematical Representation |
|---|---|---|---|
| AH Listing / Sales Tax | $2.0\%$ | Resale of assembled item on AH | $0.02 \cdot P_{\text{AH, sell}}$ |
| Bazaar Buy Order Tax | $1.00\% - 1.25\%$ | Execution of commodity Buy Orders | $\tau_{\text{BZ}} \cdot \sum q_i P_{\text{BZ, buy}}^{(i)}$ |
| Bazaar Instant Buy Premium | Variable spread ($3\% - 15\%$) | Fallback if filling instantly via Sell Offers | $\sum q_i (P_{\text{instant}}^{(i)} - P_{\text{order}}^{(i)})$ |
| Anvil Combination XP | Variable ($10\text{k} - 150\text{k coins}$) | Multi-step weapon/armor assembly | $C_{\text{XP}}$ |

### 3.4 Velocity Metrics & Liquidity Indicators

To avoid capital traps in illiquid craftables, the engine evaluates turnover metrics across both platforms:

1. **Finished Asset Liquidity ($V_{\text{AH}}$)**:
   - Polled from `/v2/skyblock/auctions_ended`.
   - Metric: 24-hour finished sales volume $V_{\text{AH, 24h}} = \sum \mathbf{1}_{\{\text{internal\_id} = \text{target}, \text{timestamp} > t - 86400\}}$.
   - **Turnover Threshold**: $V_{\text{AH, 24h}} \ge 10\text{ units/day}$ for mid-tier items (e.g., Juju Shortbow); $V_{\text{AH, 24h}} \ge 3\text{ units/day}$ for pinnacle endgame craftables (e.g., Terminator).

2. **Component Sourcing Liquidity ($V_{\text{BZ}}$)**:
   - Polled from `/v2/skyblock/bazaar` (`quick_status`).
   - Sourcing Velocity Ratio:
     $$SVR_i = \frac{\text{weeklyMovingAverageVolume}_i}{7 \times 24} \quad \left[\frac{\text{units}}{\text{hour}}\right]$$
   - Requirement: $SVR_i \ge 10 \cdot q_i$ to ensure complete order fulfillment within 60 minutes without moving market order books.

### 3.5 Liquidity Risks & Quantitative Mitigation

- **Undercut Cascade Risk**: Competitors craft identical items, initiating a 1-coin undercut race.
  - *Mitigation*: Require an **Excess Volatility Buffer** $\sigma_{\text{price}}$:
    $$\Pi_{\text{craft}} \ge 2.5 \cdot \sigma_{\text{AH, 7d}} + \text{MIN\_PROFIT\_COINS}$$
    Where $\sigma_{\text{AH, 7d}}$ is the 7-day standard deviation of the lowest BIN price.
- **Bazaar Price Slippage During Accumulation**: Filling large quantities of high-tier materials (e.g., 32 Null Ovoids) consumes order book depth.
  - *Mitigation*: Inspect order book depth arrays (`buy_summary` / `sell_summary`) to calculate effective volume-weighted average price (VWAP) rather than relying on top-of-book prices:
    $$P_{\text{VWAP}} = \frac{\sum_{k=1}^K p_k \cdot \min(q_k, q_{\text{remaining}})}{\sum_{k=1}^K \min(q_k, q_{\text{remaining}})}$$

### 3.6 Automated Alerting Feasibility Assessment

- **Hypixel API Data Availability**: **Full Availability**. `/v2/skyblock/bazaar` returns complete order books for all commodities every 10–15 seconds; `/v2/skyblock/auctions` provides live BIN listings.
- **Computational Latency**: $< 3\text{ ms}$ per evaluation cycle. Recipe trees are static directed acyclic graphs (DAGs) evaluated via cached top-of-book values.
- **Implementation Complexity**: **3 / 10**. Requires a static recipe catalog and a unified pricing dictionary combining BZ and AH caches.

---

## 4. Archetype 2: Kat Pet Upgrades

```
┌────────────────────────┐      ┌────────────────────────┐
│  Auction House (Base)  │      │  Bazaar Upgrade Items  │
│  (e.g., Epic E-Dragon, │      │  (e.g., Super Eggs,    │
│   Epic Baby Yeti)      │      │   Packed Ice, Beef)    │
└───────────┬────────────┘      └───────────┬────────────┘
            │ Buy Lowest BIN                │ Buy Orders
            └───────────────┬───────────────┘
                            ▼
               ┌────────────────────────┐
               │    Kat Pet Care NPC    │
               │  - Direct Coin Fee     │
               │  - Waiting Period (D)  │
               └────────────┬───────────┘
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
    [Mode A: Instant Flip]          [Mode B: Carry Trade]
    Kat Flowers (1 flower/24h)      Wait full duration D
            │                               │
            └───────────────┬───────────────┘
                            ▼
               ┌────────────────────────┐
               │  Auction House Resale  │
               │  (Legendary Pet BIN)   │
               └────────────────────────┘
```

### 4.1 Concept & Mechanism
Kat is an NPC who elevates pet rarities (e.g., Rare $\to$ Epic, Epic $\to$ Legendary). Upgrading confers superior base stats and unlocks critical perks (e.g., Ender Dragon's *Superior Blood*, Baby Yeti's *Ice Shields*). 

Kat requires:
1. Base pet of rarity $R$.
2. Fixed coin fee $C_{\text{Kat, coin}}$.
3. Specific crafting commodities $M_{\text{Kat}}$.
4. Real-time duration $D$ (ranging from 1 to 14 real-world days).

Arbitrageurs operate in one of two distinct modalities:
- **Instant Acceleration Mode**: Consume Kat Flowers ($1\text{ flower} = 24\text{ hours bypassed}$) to liquidate the upgraded pet instantly.
- **Passive Carry Trade Mode**: Commit capital to the training queue, absorbing time risk in exchange for zero Kat Flower expenditure.

### 4.2 Formal Mathematical Profit Formulation

#### Mode A: Instant Flip Formulation
Let:
- $P_{\text{pet, target}}$: Resale price of the upgraded rarity pet on AH.
- $P_{\text{pet, base}}$: Purchase price of the base pet on AH.
- $C_{\text{Kat, coin}}$: Kat's hardcoded coin requirement.
- $C_{\text{Kat, mat}} = \sum_{j} q_j P_{\text{BZ}}^{(j)} \cdot (1 + \tau_{\text{BZ}})$: Sourced ingredient cost.
- $D_{\text{days}} = \left\lceil \frac{T_{\text{hours}}}{24} \right\rceil$: Total Kat Flowers required.
- $P_{\text{flower}}$: Bazaar acquisition price of a Kat Flower ($+ \tau_{\text{BZ}}$).

$$\Pi_{\text{Kat, instant}} = P_{\text{pet, target}} \cdot (1 - \tau_{\text{AH}}) - \left[ P_{\text{pet, base}} + C_{\text{Kat, coin}} + C_{\text{Kat, mat}} + D_{\text{days}} \cdot P_{\text{flower}} \right]$$

$$M_{\text{Kat, instant}} = \frac{\Pi_{\text{Kat, instant}}}{P_{\text{pet, base}} + C_{\text{Kat, coin}} + C_{\text{Kat, mat}} + D_{\text{days}} \cdot P_{\text{flower}}} \times 100\%$$

#### Mode B: Passive Carry Trade Formulation
In passive mode, flower costs drop to zero, but expected profit must account for price drift over duration $D$:

$$\Pi_{\text{Kat, passive}} = \mathbb{E}[P_{\text{pet, target}}(t + D)] \cdot (1 - \tau_{\text{AH}}) - \left[ P_{\text{pet, base}} + C_{\text{Kat, coin}} + C_{\text{Kat, mat}} \right]$$

$$\text{Annualized Return on Investment (AROI)} = \left( 1 + \frac{\Pi_{\text{Kat, passive}}}{P_{\text{pet, base}} + C_{\text{Kat, coin}} + C_{\text{Kat, mat}}} \right)^{\frac{365}{D_{\text{days}}}} - 1$$

#### Pet Experience Adjustment Factor
Kat preserves cumulative pet experience. A Level 100 base pet produces a Level 100 upgraded pet. Because Level 100 pets command a substantial liquidity premium over Level 1 pets ($\Delta_{\text{lvl100}} \approx 20\text{M} - 45\text{M coins}$), the valuation model incorporates an experience pricing function:

$$P_{\text{pet, target}}(XP) = P_{\text{target, floor}} + \psi(XP) \cdot \left( P_{\text{target, lvl100}} - P_{\text{target, floor}} \right)$$

Where $\psi(XP) = \left(\frac{XP}{25{,}353{,}230}\right)^{\gamma}$ models non-linear market valuation of pet leveling progress ($\gamma \approx 1.4$).

### 4.3 Exact Fee Structure Breakdown

| Category | Cost Type | Exact Amount / Rate | Economic Function |
|---|---|---|---|
| AH Tax | Exchange Fee | $2.0\%$ | Resale tax on upgraded pet |
| Kat Coin Charge | Hard Sink | Fixed per recipe (e.g., $50{,}000{,}000\text{ coins}$ for Ender Dragon; $250{,}000\text{ coins}$ for Baby Yeti) | Direct coin deflation sink |
| Upgrade Materials | Commodity | Sourced from BZ $+ 1.0\% - 1.25\%$ tax | Recipe input requirement |
| Kat Flowers | Acceleration | $D_{\text{days}} \times P_{\text{flower}} \cdot (1 + \tau_{\text{BZ}})$ | Time-to-market elimination fee |

### 4.4 Velocity Metrics & Liquidity Indicators

1. **Rarity Segment Turnover Divergence**:
   - High-tier pets exhibit velocity variance between Epic and Legendary tiers.
   - Example: Epic Ender Dragon $V_{\text{AH}} \approx 2 - 4\text{ sales/day}$; Legendary Ender Dragon $V_{\text{AH}} \approx 5 - 10\text{ sales/day}$.
   - Upgrading shifts the asset into a higher-velocity market regime.
2. **Kat Flower Market Depth**:
   - Instant mode requires purchasing 10–14 Kat Flowers simultaneously.
   - Sourcing liquidity must verify that the Kat Flower order book can absorb $D_{\text{days}}$ units without exceeding the instant-flip margin.

### 4.5 Liquidity Risks & Quantitative Mitigation

- **Pet Candy Degradation Discount**:
  - SkyBlock collectors penalize pets fed with Pet Candy ($1/10$ through $10/10$).
  - An upgraded pet bearing non-zero candies suffers an empirical market discount of $5\% - 15\%$.
  - *Mitigation*: The NBT parser inspects `ExtraAttributes.petInfo` (JSON):
    $$\text{candies\_used} = \text{json.loads}(\text{petInfo})[\text{"candyUsed"}]$$
    Enforce a strict filter: $\text{candies\_used} == 0$.
- **Passive Duration Volatility & Mayor Shocks**:
  - Over a 14-day training period, mayor rotations (e.g., Diana, Jerry) or balance patches can alter pet values.
  - *Mitigation*: Apply a time-discounted volatility penalty factor $\exp(-\lambda \cdot D_{\text{days}})$ to passive margin projections. If $\Pi_{\text{Kat, instant}} \ge 0.5 \cdot \Pi_{\text{Kat, passive}}$, default to Instant Flip.

### 4.6 Automated Alerting Feasibility Assessment

- **Hypixel API Data Availability**: **Full Availability**. Complete pet statistics, tier, experience, and candy count reside in `ExtraAttributes.petInfo`.
- **Computational Latency**: $< 5\text{ ms}$. Requires JSON deserialization of the embedded `petInfo` string and constant-time table lookups.
- **Implementation Complexity**: **5 / 10**. Requires a structured Kat recipe database mapping pet IDs, coin fees, item requirements, and upgrade durations.

---

## 5. Archetype 3: Kuudra Attribute Combinations

```
┌────────────────────────────────────────────────────────────────────────┐
│                        KUUDRA ARMOR / EQUIPMENT                        │
│            (Crimson, Aurora, Terror, Fervor, Hollow, Molten)           │
├───────────────────────────────────┬────────────────────────────────────┤
│           ATTRIBUTE 1             │            ATTRIBUTE 2             │
│   (e.g., Mana Pool, Veteran)      │   (e.g., Mana Regen, Vitality)     │
└───────────────────────────────────┴────────────────────────────────────┘
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │             NBT Extraction & Synergy Parser             │
       │  Reads `ExtraAttributes.attributes`                     │
       └────────────────────────────┬────────────────────────────┘
                                    │
             ┌──────────────────────┴──────────────────────┐
             ▼                                             ▼
  [Floor / Junk Roll]                           [Synergy "God Roll"]
  Attributes: (mending, speed)                  Attributes: (mana_pool, mana_regen)
  Market Value: ~2M - 4M coins                  Market Value: ~80M - 250M coins
  (Sold at Base Armor Floor)                    (Mislisted at Base Floor -> SNIPE)
```

### 5.1 Concept & Mechanism
Crimson Isle armor (Crimson, Aurora, Terror, Fervor, Hollow) and equipment (Molten, Magma, Vanquished) generate with two random attributes between Tier 1 and Tier 10. Attribute mechanics create extreme value asymmetry:
- **Junk Rolls**: Undesirable combinations (e.g., *Mending + Health Regeneration*) trade at the raw base-item floor price ($\sim 1.5\text{M} - 3\text{M coins}$).
- **Synergy / "God Rolls"**: Mathematically optimal pairings for endgame combat trade at $50\times - 100\times$ the base-item floor:
  - **Aurora Armor**: Mana Pool (`mana_pool`, MP) + Mana Regeneration (`mana_regeneration`, MR) $\to$ Bis Mage gear ($\approx 80\text{M} - 250\text{M coins}$).
  - **Crimson Armor**: Veteran (`veteran`, Vet) + Vitality (`vitality`, Vit) or Magic Find (`magic_find`, MF) + Veteran $\to$ Slayer / EHP meta ($\approx 60\text{M} - 160\text{M coins}$).
  - **Terror Armor**: Lifeline (`lifeline`, LL) + Mana Pool (`mana_pool`) $\to$ Kuudra boss fight DPS meta ($\approx 50\text{M} - 120\text{M coins}$).

Inexperienced sellers frequently list God Rolls at the generic base-item floor price on the Auction House, creating high-margin arbitrage opportunities.

### 5.2 Formal Mathematical Profit Formulation

Let an item listing have base internal ID $I$, attribute pair $\mathcal{A} = \{(a_1, t_1), (a_2, t_2)\}$, and buy price $P_{\text{buy}}$.

#### Combinatorial Valuation Function
The true market value $V_{\text{market}}(I, \mathcal{A})$ is modeled as:

$$V_{\text{market}}(I, \mathcal{A}) = \max \left( P_{\text{floor}}(I),\; \Omega(I, a_1, a_2) \cdot 2^{\min(t_1, t_2) - 1},\; P_{\text{shard}}(a_1, t_1) + P_{\text{shard}}(a_2, t_2) \right)$$

Where:
- $P_{\text{floor}}(I)$: Lowest BIN of base item $I$ regardless of attributes.
- $\Omega(I, a_1, a_2)$: Empirical baseline market value of the Tier 1/Tier 1 synergy pair $(a_1, a_2)$ on item $I$.
- $2^{\min(t_1, t_2) - 1}$: Geometric tier scaling factor based on the exponential fusion cost curve ($2^{T-1}$ base units required to forge Tier $T$).
- $P_{\text{shard}}(a, t)$: Salvage or extraction value of the individual attribute components.

#### Net Arbitrage Profit
$$\Pi_{\text{attr}} = V_{\text{target}}(I, \mathcal{A}) \cdot (1 - \tau_{\text{AH}}) - P_{\text{buy}}$$

Where $V_{\text{target}}(I, \mathcal{A})$ is set conservatively to $0.90 \cdot V_{\text{market}}(I, \mathcal{A})$ (a $10\%$ undercut discount below fair value to guarantee fast liquidation).

The net margin is:

$$M_{\text{attr}} = \frac{\Pi_{\text{attr}}}{P_{\text{buy}}} \times 100\%$$

#### Attribute Fusion Arbitrage
Arbitrageurs can also purchase low-tier synergy pairs and fuse them at an Anvil using Crimson Essence and Kuudra Teeth:

$$\Pi_{\text{fuse}} = V(I, a, t+1) \cdot (1 - \tau_{\text{AH}}) - \left[ 2 \cdot V(I, a, t) + C_{\text{essence}} + C_{\text{teeth}} + C_{\text{anvil}} \right]$$

### 5.3 Exact Fee Structure Breakdown

| Action | Cost Type | Exact Formula / Value |
|---|---|---|
| AH Resale Tax | Exchange Tax | $0.02 \cdot V_{\text{target}}$ |
| Anvil Fusion Fee | Direct Coin Fee | $100{,}000 - 500{,}000\text{ coins}$ per fusion step |
| Crimson Essence Cost | Bazaar Input | $Q_{\text{essence}} \cdot P_{\text{BZ}}(\text{CRIMSON\_ESSENCE}) \cdot (1 + \tau_{\text{BZ}})$ |
| Kuudra Teeth Cost | Soulbound Token | Zero direct coin cost; quantified via opportunity cost |

### 5.4 Velocity Metrics & Thin-Market Liquidity Indicators

Unlike standard high-velocity weapons, specific God Roll Kuudra pieces trade in thin, high-value market regimes:
- **Turnover Velocity**: $1 - 4\text{ sales/week}$ per exact piece and tier.
- **Liquidity Metric via Aggregate Matching**:
  Because `/v2/skyblock/auctions_ended` may contain zero sales for a specific piece over 24 hours, velocity must be aggregated across all equipment sharing the synergy pair:
  $$V_{\text{synergy}}(a_1, a_2) = \sum_{I \in \text{KuudraSet}} \text{EndedSales}_{24\text{h}}(I, a_1, a_2)$$
- **Holding Period Expectation**: Liquidation typically spans $24 - 72\text{ hours}$. To compensate for capital lockup, the required margin threshold is strictly elevated:
  $$\text{Thresholds: } M_{\text{attr}} \ge 35.0\% \quad \text{and} \quad \Pi_{\text{attr}} \ge 15{,}000{,}000\text{ coins}$$

### 5.5 Liquidity Risks & Quantitative Mitigation

- **Admin Attribute Balance / Rework Risk**: Game balance overhauls can alter attribute utility.
  - *Mitigation*: Whitelist only structural combat staples:
    $$\mathcal{W}_{\text{pairs}} = \{(\text{MP}, \text{MR}), (\text{Vet}, \text{Vit}), (\text{Vet}, \text{MF}), (\text{LL}, \text{MP})\}$$
    Blacklist speculative combinations.
- **False Positive Floor Distortion**: An item listed with high-tier junk attributes (e.g., Tier 7 *Arachno Resistance*) must not be evaluated using geometric scaling $2^{T-1}$.
  - *Mitigation*: Strict synergy gating—if $(a_1, a_2) \notin \mathcal{W}_{\text{pairs}}$, set $\Omega(I, a_1, a_2) \equiv 0$, valuing the item solely at $P_{\text{floor}}(I)$.

### 5.6 Automated Alerting Feasibility Assessment

- **Hypixel API Data Availability**: **Full Availability**. The `ExtraAttributes.attributes` compound tag contains precise key-value pairs (e.g., `{"mana_pool": 1, "mana_regeneration": 1}`).
- **Computational Latency**: $< 2\text{ ms}$. Constant-time key lookup and dictionary parsing.
- **Implementation Complexity**: **7 / 10**. Requires managing a synergy lookup matrix, tier-scaling exponents, and distinct whitelist valuations.

---

## 6. Archetype 4: Enchantment Unbundling & Upgrade Arbitrage

```
┌────────────────────────────────────────────────────────────────────────┐
│                      HYPERMAXED / UPGRADED LISTING                     │
│                (Priced near base floor by unaware seller)              │
├───────────────────────────────────┬────────────────────────────────────┤
│ Base Item Floor: $P_{\text{base}}$│ Upgrades: Recomb, T7 Enchants,     │
│                                   │           Master Stars, Gems       │
└───────────────────────────────────┴────────────────────────────────────┘
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │             NBT Upgrade Disassembler Engine             │
       │  Parses: gems, enchantments, rarity_upgrades, stars     │
       └────────────────────────────┬────────────────────────────┘
                                    │
             ┌──────────────────────┴──────────────────────┐
             ▼                                             ▼
  [Path A: Physical Extraction]               [Path B: Hypermaxed Resale]
  Remove Perfect Gemstones via Grinder        Resell intact at bundled
  Sell Gemstones on Bazaar                    fair market value ($\gamma$ discount)
  Resell stripped item on AH
```

### 6.1 Concept & Mechanism
Endgame equipment features expensive, layered upgrades:
- **Tier 7 & Pinnacle Enchantments**: *Giant Killer 7, Critical 7, Sharpness 7, Power 7, Dragon Hunter 5, Chimera 1–5*.
- **Rarity Modification**: *Recombobulator 3000* ($6\text{M} - 9\text{M coins}$).
- **Master Stars**: Stars 6 through 10 (First through Fifth Master Stars, $10\text{M} - 60\text{M coins}$ each).
- **Socketed Gemstones**: Flawless and Perfect Gemstones ($4\text{M} - 22\text{M coins}$ each).

Sellers frequently list heavily upgraded assets at or near the clean base-item floor price. Arbitrageurs capture value via:
1. **Physical Gemstone Unbundling**: Removing socketed gemstones at the Gemstone Grinder NPC and liquidating them on the Bazaar.
2. **Bundled Upgrade Resale Arbitrage**: Relisting the fully upgraded asset at its bundled fair market value.

### 6.2 Formal Mathematical Profit Formulation

Let an item listing have purchase price $P_{\text{buy}}$, clean base floor price $P_{\text{base}}$, and upgrade payload:
- Gemstones: $\mathcal{G} = \{(g_j, \text{tier}_j)\}_{j=1}^k$
- Enchantments: $\mathcal{E} = \{(e_i, l_i)\}_{i=1}^m$
- Recombobulator: $r \in \{0, 1\}$
- Master Stars: $s \in \{0, 1, 2, 3, 4, 5\}$

#### Path A: Physical Gemstone Extraction
Gemstones can be extracted at the Gemstone Grinder and sold to the Bazaar. The stripped item is relisted on the Auction House:

$$\Pi_{\text{extract}} = \left[ \sum_{j=1}^k P_{\text{BZ, sell}}(g_j, \text{tier}_j) \cdot (1 - \tau_{\text{BZ}}) \right] + P_{\text{base}} \cdot (1 - \tau_{\text{AH}}) - \left[ P_{\text{buy}} + \sum_{j=1}^k C_{\text{removal}}(g_j) \right]$$

This path provides **deterministic, instantaneous liquidation** for the extracted gemstones through the Bazaar order book.

#### Path B: Bundled Hypermaxed Resale
When enchantments and stars cannot be physically extracted, the asset is resold as a package. 

Total Intrinsic Replacement Cost $V_{\text{intrinsic}}$ is:

$$V_{\text{intrinsic}} = P_{\text{base}} + r \cdot P_{\text{recomb}} + \sum_{s=1}^s P_{\text{star}}(s) + \sum_{j=1}^k P_{\text{gem}}(g_j) + \sum_{i=1}^m P_{\text{ench}}(e_i, l_i)$$

Because the secondary market applies a bundled convenience discount, resale value follows a bundled recovery function:

$$P_{\text{target, bundle}} = P_{\text{base}} + \sum_{U \in \text{Upgrades}} \gamma(U) \cdot \text{Cost}(U)$$

Where $\gamma(U) \in (0, 1)$ is the empirical capital recovery coefficient:
- $\gamma_{\text{recomb}} \approx 0.85$ (Recombobulators retain high liquid recovery).
- $\gamma_{\text{stars}} \approx 0.70 - 0.80$ (Master stars retain moderate recovery).
- $\gamma_{\text{gems}} \approx 0.75 - 0.85$ (Perfect gemstones retain high recovery).
- $\gamma_{\text{T7}} \approx 0.50 - 0.70$ (Tier 7 enchants retain variable recovery).

The net profit from bundled resale is:

$$\Pi_{\text{bundle}} = P_{\text{target, bundle}} \cdot (1 - \tau_{\text{AH}}) - P_{\text{buy}}$$

$$M_{\text{bundle}} = \frac{\Pi_{\text{bundle}}}{P_{\text{buy}}} \times 100\%$$

### 6.3 Exact Fee Structure Breakdown

| Operation | Cost / Fee | Applicable Rate | Channel |
|---|---|---|---|
| Gemstone Removal | NPC Service Fee | $25{,}000 - 100{,}000\text{ coins}$ / slot | In-game coin sink |
| Bazaar Gemstone Liquidation | Bazaar Tax | $1.00\% - 1.25\%$ | BZ Sell Offer or Instant Sell |
| AH Stripped Item Resale | AH Tax | $2.0\%$ | Reselling clean item on AH |
| AH Bundled Resale | AH Tax | $2.0\%$ | Reselling hypermaxed item on AH |

### 6.4 Velocity Metrics & Tier-Divergence Indicators

- **Base Item vs Hypermaxed Turnover**:
  Clean items exhibit high turnover ($V_{\text{AH}} \ge 20\text{ sales/day}$), whereas hypermaxed items exhibit low turnover ($V_{\text{AH}} \approx 1 - 3\text{ sales/day}$).
- **Liquidity Verification Protocol**:
  1. For **Extraction Arbitrage**, verify Bazaar weekly moving average volume for the extracted gemstone tiers:
     $$V_{\text{BZ, week}}(\text{Gem}) \ge 500\text{ units}$$
  2. For **Bundled Resale**, require a conservative safety margin:
     $$P_{\text{buy}} \le P_{\text{base}} + 0.40 \cdot (V_{\text{intrinsic}} - P_{\text{base}})$$
     This guarantees profitability even if the bundled recovery rate drops to $40\%$.

### 6.5 Liquidity Risks & Quantitative Mitigation

- **Niche / Devalued Enchantment Traps**:
  Enchantments such as *Fire Aspect 3*, *Knockback 2*, or *Bane of Arthropods 7* add negligible market resale value despite high crafting costs.
  - *Mitigation*: Restrict valuation strictly to universally demanded upgrades:
    $$\mathcal{E}_{\text{liquid}} = \{\text{Chimera}, \text{Sharpness 7}, \text{Smite 7}, \text{Power 7}, \text{Critical 7}, \text{Giant Killer 7}, \text{Dragon Hunter 5}\}$$
    Assign zero marginal valuation ($\gamma = 0$) to all other enchantments.
- **Gemstone Slot Unlocking Costs**:
  Some weapons require expensive gemstone chamber or gemstone unlock items to re-socket gems.
  - *Mitigation*: Extraction path evaluates only pre-socketed gems and accounts for removal fees.

### 6.6 Automated Alerting Feasibility Assessment

- **Hypixel API Data Availability**: **Full Availability**. Complete upgrade telemetry is present in NBT:
  - `ExtraAttributes.gems` (socketed gemstone tiers).
  - `ExtraAttributes.enchantments` (enchantment levels).
  - `ExtraAttributes.rarity_upgrades` (recombobulator flag).
  - `ExtraAttributes.dungeon_item_level` / stars.
- **Computational Latency**: $15 - 25\text{ ms}$. Requires cross-referencing multiple component price caches (Bazaar gemstones + AH enchant books).
- **Implementation Complexity**: **8 / 10**. Requires an NBT upgrade extractor, component pricing tables, and empirical recovery coefficients ($\gamma$).

---

## 7. Comprehensive Comparative Summary Matrix

The following matrix compares all four advanced arbitrage archetypes across operational and economic dimensions:

| Dimension | Archetype 1: BZ-to-AH Craft Arbitrage | Archetype 2: Kat Pet Upgrades | Archetype 3: Kuudra Attribute Combos | Archetype 4: Upgrade & Gem Unbundling |
|---|---|---|---|---|
| **Typical Target Items** | Terminator, Juju Shortbow, Warden Helmet, Overflux | Ender Dragon, Baby Yeti, Tiger, Griffin | Aurora (MP/MR), Crimson (Vet/Vit), Molten Gear | Hypermaxed Wither Blades, Perfect Gem Armor |
| **Capital Requirement** | Medium to High ($15\text{M} - 650\text{M}$) | High ($25\text{M} - 550\text{M}$) | Low to Medium ($2\text{M} - 120\text{M}$) | High ($40\text{M} - 1{,}200\text{M}$) |
| **Net Margin %** | $8\% - 22\%$ | $10\% - 30\%$ (Instant)<br>$25\% - 65\%$ (Passive) | $40\% - 250\%$ | $15\% - 45\%$ |
| **Net Profit / Trade** | $3\text{M} - 55\text{M}$ | $5\text{M} - 80\text{M}$ | $15\text{M} - 150\text{M}$ | $8\text{M} - 120\text{M}$ |
| **Turnover Velocity** | Fast ($2 - 8\text{ hours}$) | Fast ($2 - 6\text{ h}$, Instant)<br>Slow ($7 - 14\text{ d}$, Passive) | Moderate to Slow ($12 - 72\text{ hours}$) | Instant (Gems on BZ)<br>Slow ($24 - 72\text{ h}$, Bundle) |
| **Capital Efficiency ($\rho$)** | **Very High** | High (Instant)<br>Low (Passive) | **Extreme** (on snipes) | High (Extraction)<br>Moderate (Bundle) |
| **Hypixel API Endpoints** | `/skyblock/bazaar`<br>`/skyblock/auctions`<br>`/skyblock/auctions_ended` | `/skyblock/auctions`<br>`/skyblock/bazaar`<br>`/skyblock/auctions_ended` | `/skyblock/auctions`<br>`/skyblock/auctions_ended` | `/skyblock/auctions`<br>`/skyblock/bazaar`<br>`/skyblock/auctions_ended` |
| **Computation Latency** | $< 3\text{ ms}$ | $< 5\text{ ms}$ | $< 2\text{ ms}$ | $15 - 25\text{ ms}$ |
| **Primary Risk Vectors** | Undercutting race, BZ order book slippage | Candy taint ($>0$), price drift during wait | Thin trading volume, balance/meta patch shifts | Illiquid bundled demand, devalued enchantments |
| **Automation Feasibility** | **High** (Alert + Guided Sourcing) | **High** (Alert + Recipe Engine) | **Very High** (Direct Listing Snipe) | **High** (Direct Listing Snipe) |
| **Complexity Score (1-10)**| **3 / 10** | **5 / 10** | **7 / 10** | **8 / 10** |

---

## 8. Algorithmic Integration & Architecture Blueprint

To operationalize these strategies without compromising latency or system stability, the current `FlipEvaluator` pipeline should be structured into an extensible, multi-stage evaluation engine:

```
                          ┌────────────────────────┐
                          │ Active Auction Ingest  │
                          │ (/skyblock/auctions)   │
                          └───────────┬────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │   NBT Extraction &     │
                          │   Attribute Sanitizer  │
                          └───────────┬────────────┘
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼                                             ▼
┌─────────────────────────────┐               ┌─────────────────────────────┐
│  Stage 1: Floor BIN Filter  │               │ Stage 2: Advanced Evaluator │
│  - Simple AH Delta          │               │ - Modular Strategy Registry │
│  - Cosmetic Blacklist       │               └──────────────┬──────────────┘
│  - Velocity Validation      │                              │
└─────────────────────────────┘                              ▼
                                              ┌─────────────────────────────┐
                                              │ - Craft Arbitrage Plugin    │
                                              │ - Kat Upgrade Plugin        │
                                              │ - Kuudra Synergy Plugin     │
                                              │ - Unbundling Plugin         │
                                              └──────────────┬──────────────┘
                                                             │
                                                             ▼
                                              ┌─────────────────────────────┐
                                              │ Unified Risk & Margin Gate  │
                                              │ - Fee / Tax Deductions      │
                                              │ - Minimum Profit & Margin   │
                                              └──────────────┬──────────────┘
                                                             │
                                                             ▼
                                              ┌─────────────────────────────┐
                                              │  Discord Priority Dispatch  │
                                              └─────────────────────────────┘
```

### 8.1 Data Model Extensions

The underlying data structures should represent multi-archetype opportunities:

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class StrategyArchetype(str, Enum):
    STANDARD_BIN = "STANDARD_BIN"
    BZ_TO_AH_CRAFT = "BZ_TO_AH_CRAFT"
    KAT_PET_UPGRADE = "KAT_PET_UPGRADE"
    KUUDRA_ATTRIBUTE = "KUUDRA_ATTRIBUTE"
    UPGRADE_UNBUNDLING = "UPGRADE_UNBUNDLING"

class AdvancedFlipOpportunity(BaseModel):
    archetype: StrategyArchetype
    item_name: str
    internal_id: str
    auction_uuid: str
    buy_price: int
    gross_target_resell: int
    net_profit: int
    margin_pct: float
    turnover_velocity_24h: float
    liquidity_risk_score: float  # 0.0 (liquid) to 1.0 (illiquid)
    
    # Archetype-Specific Telemetry
    recipe_components: Optional[dict[str, int]] = None
    pet_level: Optional[int] = None
    candies_used: Optional[int] = None
    attribute_pair: Optional[tuple[str, str]] = None
    salvageable_gemstones: Optional[list[str]] = None
    bundled_intrinsic_value: Optional[int] = None
```

### 8.2 Capital Allocation & Risk Gating

To protect capital against portfolio-level drawdowns across concurrent flips:

1. **Maximum Allocation Cap**:
   $$\text{Capital Allocated per Flip} \le 0.30 \times \text{Total Liquid Capital}$$
2. **Velocity-Weighted Margin Hurdle**:
   The minimum acceptable net margin $M_{\text{min}}$ scales dynamically with turnover velocity:
   $$M_{\text{min}}(V_{\text{24h}}) = \max \left( 8.0\%,\; 8.0\% + \frac{15.0\%}{\max(1.0, V_{\text{24h}})} \right)$$
   High-velocity staples ($V_{\text{24h}} \ge 25$) clear at an $8.6\%$ margin hurdle, whereas low-velocity attribute pieces ($V_{\text{24h}} \le 2$) require a minimum margin hurdle of $15.5\% - 23.0\%$.

---

## 9. References & Empirical Parameter Table

### 9.1 Hypixel REST API Reference
- `/v2/skyblock/auctions`: Paginated active auctions (query frequency: $60\text{ s}$).
- `/v2/skyblock/auctions_ended`: Finished sales with purchase price and timestamp (query frequency: $60\text{ s}$).
- `/v2/skyblock/bazaar`: Complete commodity order book snapshots (query frequency: $15\text{ s}$).

### 9.2 Empirical Parameter Calibration

| Parameter | Symbol | Calibrated Value | Source / Methodology |
|---|---|---|---|
| AH Resale Tax Rate | $\tau_{\text{AH}}$ | $0.02$ ($2.0\%$) | Hypixel Auction House Rules ($\ge 10\text{M coins}$) |
| Bazaar Min Tax Rate | $\tau_{\text{BZ}}$ | $0.01$ ($1.0\%$) | Community Center Account Upgrade Tier 5 |
| Max Kat Candy Allowance | $\text{Candy}_{\text{max}}$ | $0$ | Preserves collector resale liquidity |
| Kat Flower Hours Bypassed | $H_{\text{flower}}$ | $24\text{ hours}$ | In-game item mechanic |
| Min Sample Size (AH) | $N_{\text{sample}}$ | $3\text{ active listings}$ | Mitigates single-listing price manipulation |
| Anti-Bait Resell Ratio | $R_{\text{max}}$ | $3.0\times$ | Filters extreme outlier price targets |
| Gem Recovery Factor | $\gamma_{\text{gems}}$ | $0.80$ | Empirical historical secondary market discount |
| Recomb Recovery Factor | $\gamma_{\text{recomb}}$ | $0.85$ | Empirical historical secondary market discount |
| T7 Enchants Recovery Factor | $\gamma_{\text{T7}}$ | $0.60$ | Empirical historical secondary market discount |
