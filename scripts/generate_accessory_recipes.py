import json

with open("data/accessory_recipes_dump.json", "r", encoding="utf-8") as f:
    data = json.load(f)

lines = [
    '"""Comprehensive registry of all craftable Hypixel SkyBlock accessories.',
    '',
    f"Contains {len(data)} craftable accessory recipes covering talismans, rings, artifacts, and relics.",
    '"""',
    "",
    "from __future__ import annotations",
    "",
    "ACCESSORY_RECIPES: dict[str, dict[str, int]] = {",
]

for item_id, ing_dict in sorted(data.items()):
    clean_ings = {k.strip().upper(): int(v) for k, v in ing_dict.items() if v > 0}
    lines.append(f"    {json.dumps(item_id)}: {json.dumps(clean_ings)},")

lines.append("}")
lines.append("")

with open("src/constants/accessory_recipes.py", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Generated src/constants/accessory_recipes.py with {len(data)} recipes!")
