"""
Canonical Lane Identifiers and Bucket-Key Scheme.
Canonical lanes:
- 'floor': Clean baseline floor; Key = ITEM:{internal_id}
- 'upgraded-networth': Replacement networth via Bazaar addons + dynamic retention; Key = ITEM:{internal_id}
- 'sold-comps': Time-decayed historical comps; Key = ITEM:{internal_id}:{features_hash}
- 'pets': Pet bracket isolation; Key = PET:{pet_type}:{rarity}:{bracket}:{held_item_class}
- 'god-roll': High-value Crimson/Kuudra attributes; Key = ATTR:{internal_id}:{attr1}_{lvl}:{attr2}_{lvl}
"""

from __future__ import annotations

LANE_FLOOR = "floor"
LANE_UPGRADED_NETWORTH = "upgraded-networth"
LANE_SOLD_COMPS = "sold-comps"
LANE_PETS = "pets"
LANE_GOD_ROLL = "god-roll"

CANONICAL_LANES = (
    LANE_FLOOR,
    LANE_UPGRADED_NETWORTH,
    LANE_SOLD_COMPS,
    LANE_PETS,
    LANE_GOD_ROLL,
)


def resolve_canonical_lane(
    valuation_path: str,
    *,
    is_pet: bool = False,
    is_god_roll: bool = False,
    is_attribute: bool = False,
    is_upgraded: bool = False,
) -> str:
    """Resolve a valuation path and item characteristics into a canonical lane identifier."""
    path_upper = str(valuation_path).strip().upper()
    if is_pet or "PET" in path_upper:
        return LANE_PETS
    if is_god_roll or is_attribute or "ATTR" in path_upper:
        return LANE_GOD_ROLL
    if "COMPS" in path_upper or "VANITY" in path_upper:
        return LANE_SOLD_COMPS
    if "PATH_B" in path_upper or "NETWORTH" in path_upper or is_upgraded:
        return LANE_UPGRADED_NETWORTH
    return LANE_FLOOR
