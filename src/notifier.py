"""Discord webhook dispatcher for real-time arbitrage flip alerts."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from src.config import Settings
from src.constants.attributes import format_attributes
from src.evaluator import FlipResult

logger = logging.getLogger(__name__)


def format_upgrades(flip: FlipResult) -> str:
    """Format stars, recombobulator status, and enchantments into human-readable text."""
    parts = []
    stars_str = f"⭐ {flip.dungeon_stars} Stars" if flip.dungeon_stars > 0 else "⭐ None"
    parts.append(f"**Stars:** {stars_str}")

    recomb_str = "✅ Yes" if flip.rarity_upgrades > 0 else "❌ No"
    parts.append(f"**Recombobulated:** {recomb_str}")

    if getattr(flip, "gemstone_chambers", 0) > 0:
        parts.append(f"**Chambers:** 💎 {flip.gemstone_chambers} Unlocked")

    if getattr(flip, "art_of_war_count", 0) > 0:
        parts.append("**Art of War:** ⚔️ Applied")

    if getattr(flip, "wood_singularity_count", 0) > 0:
        parts.append("**Wood Singularity:** 🪵 Applied")

    if getattr(flip, "transmission_tuners", 0) > 0:
        parts.append(f"**Transmission Tuners:** 🌀 {flip.transmission_tuners} Applied")

    if flip.enchantments:
        ench_list = [f"{name.capitalize()} {lvl}" for name, lvl in sorted(flip.enchantments.items())]
        if len(ench_list) > 6:
            ench_text = ", ".join(ench_list[:6]) + f" *(+{len(ench_list) - 6} more)*"
        else:
            ench_text = ", ".join(ench_list)
        parts.append(f"**Enchants:** {ench_text}")
    else:
        parts.append("**Enchants:** None")

    return "\n".join(parts)


def _append_confidence_and_efficiency_fields(fields: list[dict[str, Any]], flip: FlipResult) -> None:
    """Helper to append Confidence badge, Capital Efficiency, and Optimistic Upside fields to Discord embeds."""
    if getattr(flip, "confidence_label", None):
        conf_label = flip.confidence_label
        badge = "🟢 HIGH" if conf_label == "HIGH" else ("🟡 MEDIUM" if conf_label == "MEDIUM" else "🔴 LOW")
        conf_str = f"**{badge}**"
        if getattr(flip, "confidence_score", None) is not None:
            conf_str += f" ({flip.confidence_score:.0f}/100)"
        fields.append({
            "name": "Confidence",
            "value": conf_str,
            "inline": True,
        })

    if getattr(flip, "capital_efficiency", None) is not None and getattr(flip, "estimated_hold_hours", None) is not None:
        queue_note = f" (Queue: #{flip.queue_depth})" if getattr(flip, "queue_depth", 0) > 0 else ""
        fields.append({
            "name": "⏱️ Capital Efficiency",
            "value": f"~{flip.estimated_hold_hours:.1f}h hold{queue_note} • **{flip.capital_efficiency:.1f}%/hr**",
            "inline": True,
        })

    if getattr(flip, "optimistic_upside", None) is not None and getattr(flip, "optimistic_target", None) is not None:
        if flip.optimistic_upside > flip.net_profit:
            fields.append({
                "name": "📈 Optimistic Upside (P75)",
                "value": f"+{flip.optimistic_upside:,} coins (Target: {flip.optimistic_target:,})",
                "inline": True,
            })


def format_resell_price_str(flip: FlipResult, suffix: str = "") -> str:
    val = f"{flip.target_resell_price:,}{suffix}"
    if getattr(flip, "suggested_list_price", None) and flip.suggested_list_price != flip.target_resell_price:
        val += f"\n💡 Suggested List: **{flip.suggested_list_price:,}** coins"
    return val


class DiscordNotifier:
    """Dispatches formatted arbitrage alerts to a Discord channel via webhook and local WebSocket."""

    def __init__(
        self,
        config: Settings,
        session: Optional[aiohttp.ClientSession] = None,
        ws_broadcaster: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._session = session
        self._owns_session = session is None
        self.ws_broadcaster = ws_broadcaster

    async def __aenter__(self) -> "DiscordNotifier":
        await self.get_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def get_session(self) -> aiohttp.ClientSession:
        """Retrieve active ClientSession or instantiate one."""
        if self._session is None or getattr(self._session, "closed", False) is True:
            timeout = aiohttp.ClientTimeout(total=15.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close active ClientSession if owned by this notifier."""
        if self._owns_session and self._session and not getattr(self._session, "closed", False):
            await self._session.close()

    def build_embed_payload(self, flip: FlipResult) -> dict[str, Any]:
        """
        Construct Discord webhook embed payload according to conditional formatting:
        - Format A: Pet Snipe Alert
        - Format A (Attributes): Crimson Isle Attribute / God Roll Snipe Alert
        - Format B: Networth Addon Alert (Expanded SkyHelper-style Tree Breakdown)
        - Format A (Standard): Standard Clean Item Alert (2nd Lowest BIN)
        """
        if flip.is_pet:
            return self._build_pet_embed(flip)
        if flip.is_attribute_snipe:
            return self._build_attribute_embed(flip)
        if flip.is_networth_snipe:
            return self._build_networth_embed(flip)
        return self._build_clean_embed(flip)

    def _build_clean_embed(self, flip: FlipResult) -> dict[str, Any]:
        """Format A: Standard Clean Item Alert (Preserved Format)."""
        upgrades_display = format_upgrades(flip)

        if flip.is_priority:
            title = f"🔥 [PRIORITY STAPLE] {flip.item_name}"
            color = 0xFF9900
        else:
            title = f"[FLIP FOUND] {flip.item_name}"
            color = 0x00FF00

        fields = [
            {
                "name": "Buy Price",
                "value": f"{flip.buy_price:,} coins",
                "inline": True,
            },
            {
                "name": "Target Resell",
                "value": format_resell_price_str(flip, " coins"),
                "inline": True,
            },
            {
                "name": "Net Profit",
                "value": f"+{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)",
                "inline": False,
            },
        ]

        if flip.is_priority:
            fields.append({
                "name": "⚡ Priority Classification",
                "value": f"**{flip.staple_category}** *(High Velocity Demand)*",
                "inline": False,
            })

        if flip.craft_cost is not None:
            craft_val = f"{flip.craft_cost:,} coins (Bazaar Instant-Buy)"
            if flip.is_craft_capped:
                craft_val += f"\n⚠️ Resell Capped at: {flip.target_resell_price:,} coins"
            fields.append({
                "name": "Craft Cost Ceiling",
                "value": craft_val,
                "inline": False,
            })

        _append_confidence_and_efficiency_fields(fields, flip)

        fields.extend([
            {
                "name": "Upgrades",
                "value": upgrades_display,
                "inline": False,
            },
            {
                "name": "Quick Command",
                "value": f"```/viewauction {flip.auction_uuid}```",
                "inline": False,
            },
        ])

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Hypixel AH Arbitrage • Internal ID: {flip.internal_id} • Method: {flip.valuation_path}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"embeds": [embed]}

    def _build_networth_embed(self, flip: FlipResult) -> dict[str, Any]:
        """Format B: Networth Addon Alert (Itemized SkyHelper-style Tree Breakdown)."""
        if flip.is_priority:
            title = f"🔥 [PRIORITY NETWORTH SNIPE] {flip.item_name}"
            color = 0xFF9900
        else:
            title = f"[NETWORTH SNIPE] {flip.item_name}"
            color = 0x9B59B6  # Royal purple for networth snipes

        buy_m = flip.buy_price / 1_000_000
        base_val = flip.base_lbin if flip.base_lbin is not None else flip.buy_price
        base_m = base_val / 1_000_000
        retention_pct = round(flip.retention_rate * 100)

        # Build calculations tree:
        # → Recombobulator 3000 ({recomb_cost})
        # → {Enchant Name} {Level} ({enchant_cost})
        # → {Count}x {Essence Type} ({essence_cost})
        # → {Reforge Stone} ({stone_cost})
        # → Master Stars: {count}x Applied ({ms_cost})
        # → Gemstones: {summary} ({gem_cost})
        calc_lines = []
        for addon in flip.addon_breakdown:
            calc_lines.append(f"→ {addon['name']} ({addon['cost']:,})")
        if not calc_lines:
            calc_lines = ["→ None"]

        calc_text = "\n".join(calc_lines)

        fields = [
            {
                "name": "Price",
                "value": f"{flip.buy_price:,} ({buy_m:.2f}M)",
                "inline": True,
            },
            {
                "name": "Base Price",
                "value": f"{base_val:,} ({base_m:.2f}M)",
                "inline": True,
            },
            {
                "name": "Retention Rate Applied",
                "value": f"{retention_pct}%",
                "inline": True,
            },
            {
                "name": "Calculations",
                "value": calc_text,
                "inline": False,
            },
            {
                "name": "Estimated Resell Value",
                "value": format_resell_price_str(flip, " coins"),
                "inline": True,
            },
            {
                "name": "Net Profit",
                "value": f"+{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)",
                "inline": True,
            },
        ]

        if flip.is_priority:
            fields.append({
                "name": "⚡ Priority Classification",
                "value": f"**{flip.staple_category}** *(High Velocity Demand)*",
                "inline": False,
            })

        if flip.craft_cost is not None:
            craft_val = f"{flip.craft_cost:,} coins (Bazaar Instant-Buy)"
            if flip.is_craft_capped:
                craft_val += f"\n⚠️ Resell Capped at: {flip.target_resell_price:,} coins"
            fields.append({
                "name": "Craft Cost Ceiling",
                "value": craft_val,
                "inline": False,
            })

        _append_confidence_and_efficiency_fields(fields, flip)

        fields.append({
            "name": "Quick Command",
            "value": f"```/viewauction {flip.auction_uuid}```",
            "inline": False,
        })

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Hypixel AH Arbitrage • Internal ID: {flip.internal_id}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"embeds": [embed]}

    def _build_attribute_embed(self, flip: FlipResult) -> dict[str, Any]:
        """Format A (Attributes): Crimson Isle Attribute / God Roll Snipe Embed."""
        stars_str = f" ★ {flip.dungeon_stars}" if flip.dungeon_stars > 0 else ""
        title = f"[ATTRIBUTE SNIPE] {flip.item_name}{stars_str}"
        color = 0xFF5500 if flip.is_god_roll else 0x9B59B6

        buy_m = flip.buy_price / 1_000_000
        resell_m = flip.target_resell_price / 1_000_000
        attr_display = format_attributes(flip.attributes)

        fields = [
            {
                "name": "Attributes",
                "value": attr_display,
                "inline": False,
            },
            {
                "name": "Price",
                "value": f"{flip.buy_price:,} ({buy_m:.2f}M)",
                "inline": True,
            },
            {
                "name": "Estimated Market Value",
                "value": format_resell_price_str(flip, f" ({resell_m:.2f}M)"),
                "inline": True,
            },
            {
                "name": "Net Profit",
                "value": f"+{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)",
                "inline": False,
            },
        ]

        _append_confidence_and_efficiency_fields(fields, flip)

        fields.append({
            "name": "Quick Command",
            "value": f"```/viewauction {flip.auction_uuid}```",
            "inline": False,
        })

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Hypixel AH Arbitrage • Internal ID: {flip.internal_id}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"embeds": [embed]}

    def _build_pet_embed(self, flip: FlipResult) -> dict[str, Any]:
        """Format B (Pets): Pet Snipe Alert Embed."""
        tier_display = (flip.pet_tier or "").title()
        pet_name_display = (flip.pet_type or "").replace("_", " ").title()
        level_display = flip.pet_level if flip.pet_level is not None else 1

        title = f"[PET SNIPE] [Lvl {level_display}] {tier_display} {pet_name_display}"
        color = 0x3498DB  # Sky blue for pets

        buy_m = flip.buy_price / 1_000_000
        resell_m = flip.target_resell_price / 1_000_000
        base_val = flip.base_lvl_1_price if flip.base_lvl_1_price is not None else (flip.base_lbin or flip.buy_price)
        base_m = base_val / 1_000_000

        held_name = flip.pet_held_item_name or "None"
        held_val_str = f"{flip.held_item_value:,}" if flip.held_item_value > 0 else "0"
        held_item_display = f"✦ {held_name} ({held_val_str})" if held_name != "None" else "None"

        xp_display = f"{flip.pet_exp:,.0f} XP" if flip.pet_exp is not None else "0 XP"
        candy_display = f"Yes ({flip.pet_candy_used}/10)" if flip.pet_candy_used > 0 else "None (0/10)"
        eff_rarity = (flip.effective_rarity or flip.pet_tier or "COMMON").title()
        skin_display = flip.pet_skin or "None"

        fields = [
            {
                "name": "Level / XP",
                "value": f"Lvl {level_display} ({xp_display})",
                "inline": True,
            },
            {
                "name": "Effective Rarity",
                "value": eff_rarity,
                "inline": True,
            },
            {
                "name": "Candy / Skin",
                "value": f"Candy: {candy_display} • Skin: {skin_display}",
                "inline": True,
            },
            {
                "name": "Held Item",
                "value": held_item_display,
                "inline": False,
            },
            {
                "name": "Price",
                "value": f"{flip.buy_price:,} ({buy_m:.2f}M)",
                "inline": True,
            },
        ]

        if flip.reference_price is not None:
            ref_m = flip.reference_price / 1_000_000
            fields.append({
                "name": "Reference Price",
                "value": f"{flip.reference_price:,} ({ref_m:.2f}M)",
                "inline": True,
            })
        else:
            fields.append({
                "name": "Base Lvl 1 Price",
                "value": f"{base_val:,} ({base_m:.2f}M)",
                "inline": True,
            })

        fields.extend([
            {
                "name": "Estimated Worth",
                "value": format_resell_price_str(flip, f" ({resell_m:.2f}M)"),
                "inline": True,
            },
            {
                "name": "Net Profit",
                "value": f"+{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)",
                "inline": False,
            },
        ])

        if flip.baseline_source:
            fields.append({
                "name": "Baseline Source",
                "value": flip.baseline_source,
                "inline": True,
            })

        if flip.comp_count is not None and flip.comp_count > 0:
            median_str = f"{flip.comp_median:,}" if flip.comp_median else "N/A"
            fields.append({
                "name": "Comparable Sales",
                "value": f"{flip.comp_count} sales (Median: {median_str})",
                "inline": True,
            })

        _append_confidence_and_efficiency_fields(fields, flip)

        fields.append({
            "name": "Quick Command",
            "value": f"```/viewauction {flip.auction_uuid}```",
            "inline": False,
        })

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"Hypixel AH Arbitrage • Internal ID: {flip.internal_id}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"embeds": [embed]}

    async def send_alert(self, flip: FlipResult, max_retries: int = 3) -> bool:
        """
        Deliver a flip alert. Broadcasts to in-game Fabric clients via local WebSocket,
        and logs to stdout in DRY_RUN mode or sends to Discord webhook.

        Includes rate-limit throttling and Discord 429 backoff handling.
        """
        # Broadcast to connected in-game Fabric client mods
        if self.ws_broadcaster and getattr(self.config, "ENABLE_LOCAL_WS", True):
            try:
                await self.ws_broadcaster.broadcast_flip(flip)
            except Exception as err:
                logger.warning("Failed to broadcast flip to local WebSocket: %s", err)

        if self.config.DRY_RUN:
            if flip.is_pet:
                tier_disp = (flip.pet_tier or "").title()
                pet_name = (flip.pet_type or "").replace("_", " ").title()
                lvl = flip.pet_level or 1
                held_name = flip.pet_held_item_name or "None"
                held_val_str = f"{flip.held_item_value:,}" if flip.held_item_value > 0 else "0"
                buy_m = flip.buy_price / 1_000_000
                resell_m = flip.target_resell_price / 1_000_000
                base_val = flip.base_lvl_1_price or flip.buy_price
                base_m = base_val / 1_000_000
                msg = (
                    f"\n[DRY RUN PET SNIPE] [Lvl {lvl}] {tier_disp} {pet_name} ({flip.internal_id})\n"
                    f"  Held Item:        ✦ {held_name} ({held_val_str})\n"
                    f"  Price:            {flip.buy_price:,} coins ({buy_m:.2f}M)\n"
                    f"  Base Lvl 1 Price: {base_val:,} coins ({base_m:.2f}M)\n"
                    f"  Estimated Worth:  {flip.target_resell_price:,} coins ({resell_m:.2f}M)\n"
                    f"  Net Profit:       +{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)\n"
                    f"  Command:          /viewauction {flip.auction_uuid}\n"
                )
            elif flip.is_attribute_snipe:
                attr_str = format_attributes(flip.attributes)
                buy_m = flip.buy_price / 1_000_000
                resell_m = flip.target_resell_price / 1_000_000
                msg = (
                    f"\n[DRY RUN ATTRIBUTE SNIPE] {flip.item_name} ({flip.internal_id})\n"
                    f"  Attributes:             {attr_str}\n"
                    f"  Price:                  {flip.buy_price:,} coins ({buy_m:.2f}M)\n"
                    f"  Estimated Market Value: {flip.target_resell_price:,} coins ({resell_m:.2f}M)\n"
                    f"  Net Profit:             +{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)\n"
                    f"  Command:                /viewauction {flip.auction_uuid}\n"
                )
            elif flip.is_networth_snipe:
                badge = f"🔥 [PRIORITY: {flip.staple_category}] " if flip.is_priority else ""
                buy_m = flip.buy_price / 1_000_000
                base_val = flip.base_lbin if flip.base_lbin is not None else flip.buy_price
                base_m = base_val / 1_000_000
                calc_str = "\n".join(f"    → {a['name']} ({a['cost']:,})" for a in flip.addon_breakdown)
                retention_pct = round(flip.retention_rate * 100)
                craft_line = ""
                if flip.craft_cost is not None:
                    cap_info = f" (⚠️ Resell Capped at {flip.target_resell_price:,})" if flip.is_craft_capped else ""
                    craft_line = f"  Craft Cost:             {flip.craft_cost:,} coins{cap_info}\n"
                msg = (
                    f"\n[DRY RUN NETWORTH SNIPE] {badge}{flip.item_name} ({flip.internal_id})\n"
                    f"  Price:                  {flip.buy_price:,} coins ({buy_m:.2f}M)\n"
                    f"  Base Price:             {base_val:,} coins ({base_m:.2f}M)\n"
                    f"{craft_line}"
                    f"  Calculations:\n{calc_str}\n"
                    f"  Retention Rate Applied: {retention_pct}%\n"
                    f"  Estimated Resell Value: {flip.target_resell_price:,} coins\n"
                    f"  Net Profit:             +{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)\n"
                    f"  Command:                /viewauction {flip.auction_uuid}\n"
                )
            else:
                badge = f"🔥 [PRIORITY: {flip.staple_category}] " if flip.is_priority else ""
                craft_line = ""
                if flip.craft_cost is not None:
                    cap_info = f" (⚠️ Resell Capped at {flip.target_resell_price:,})" if flip.is_craft_capped else ""
                    craft_line = f"  Craft Cost:    {flip.craft_cost:,} coins{cap_info}\n"
                msg = (
                    f"\n[DRY RUN FLIP] {badge}{flip.item_name} ({flip.internal_id})\n"
                    f"  Buy Price:     {flip.buy_price:,} coins\n"
                    f"{craft_line}"
                    f"  Target Resell: {flip.target_resell_price:,} coins\n"
                    f"  Net Profit:    +{flip.net_profit:,} coins ({flip.margin_pct:.1f}%)\n"
                    f"  Command:       /viewauction {flip.auction_uuid}\n"
                )
            try:
                print(msg)
            except UnicodeEncodeError:
                encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
                print(msg.encode(encoding, errors="replace").decode(encoding))
            logger.info("Dispatched dry-run alert for flip %s (%s)", flip.item_name, flip.auction_uuid)
            return True

        target_webhook = self.config.DISCORD_WEBHOOK_URL
        if getattr(flip, "is_out_of_budget", False):
            out_of_budget_webhook = getattr(self.config, "OUT_OF_BUDGET_WEBHOOK_URL", None)
            if out_of_budget_webhook and "dummy" not in out_of_budget_webhook:
                target_webhook = out_of_budget_webhook
            else:
                logger.info(
                    "Flip %s (%s) exceeds purse budget (%d > %d) with no secondary webhook configured. Skipping alert.",
                    flip.item_name,
                    flip.auction_uuid,
                    flip.buy_price,
                    self.config.MAX_PURSE_BUDGET,
                )
                return False

        if not target_webhook or "dummy" in target_webhook:
            logger.warning("Discord webhook URL is empty or dummy. Skipping alert for %s", flip.auction_uuid)
            return False

        session = await self.get_session()
        payload = self.build_embed_payload(flip)

        for attempt in range(max_retries):
            try:
                async with session.post(target_webhook, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info("Successfully dispatched Discord alert for %s", flip.auction_uuid)
                        # Throttle to prevent Discord webhook bursts
                        await asyncio.sleep(0.5)
                        return True

                    if resp.status == 429:
                        try:
                            data = await resp.json()
                            retry_after = float(data.get("retry_after", 1.0))
                        except Exception:
                            retry_after = 2.0

                        logger.warning(
                            "Discord webhook rate limited (429). Retrying after %.2fs...",
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    text = await resp.text()
                    logger.error("Discord webhook failed with HTTP %d: %s", resp.status, text[:200])
                    return False
            except Exception as err:
                logger.error("Error dispatching Discord webhook for %s: %s", flip.auction_uuid, err)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                    continue
                return False

        return False

    def build_completed_trade_embed(self, trade: Any, player_ign: str = "ongvvkk") -> dict[str, Any]:
        """Build celebration Discord webhook embed for a realized player flip."""
        pnl_sign = "+" if trade.net_realized_pnl >= 0 else ""
        title = f"🎉 [TRADE COMPLETED] {player_ign} flipped {trade.item_name}!"
        color = 0x2ECC71 if trade.net_realized_pnl >= 0 else 0xE74C3C

        hold_str = (
            trade.format_duration()
            if hasattr(trade, "format_duration")
            else f"{int(trade.hold_duration_seconds // 3600)}h {int((trade.hold_duration_seconds % 3600) // 60)}m"
        )

        fields = [
            {
                "name": "Bought",
                "value": f"{trade.buy_price:,} coins",
                "inline": True,
            },
            {
                "name": "Sold",
                "value": f"{trade.sale_price:,} coins",
                "inline": True,
            },
            {
                "name": "Progressive AH Tax",
                "value": f"{trade.progressive_tax:,} coins",
                "inline": True,
            },
            {
                "name": "Net Realized PnL",
                "value": f"**{pnl_sign}{trade.net_realized_pnl:,} coins** ({trade.roi_pct:+.2f}%)",
                "inline": False,
            },
            {
                "name": "Hold Duration",
                "value": hold_str,
                "inline": True,
            },
            {
                "name": "PnL / Hour",
                "value": f"{pnl_sign}{trade.pnl_per_hour:,.0f} coins/hr",
                "inline": True,
            },
            {
                "name": "Quick Command",
                "value": f"```/viewauction {trade.auction_uuid}```",
                "inline": False,
            },
        ]

        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"SkyBlock Portfolio Ledger • Trader: {player_ign}",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"embeds": [embed]}

    async def send_completed_trade_alert(
        self,
        trade: Any,
        player_ign: str = "ongvvkk",
        max_retries: int = 3,
    ) -> bool:
        """Dispatch real-time completed trade alert to Discord or console if dry-run."""
        if self.config.DRY_RUN:
            pnl_sign = "+" if trade.net_realized_pnl >= 0 else ""
            hold_str = (
                trade.format_duration()
                if hasattr(trade, "format_duration")
                else f"{int(trade.hold_duration_seconds // 3600)}h {int((trade.hold_duration_seconds % 3600) // 60)}m"
            )
            msg = (
                f"\n🎉 [DRY RUN TRADE COMPLETED] {player_ign} flipped {trade.item_name}!\n"
                f"  Bought:           {trade.buy_price:,} coins\n"
                f"  Sold:             {trade.sale_price:,} coins\n"
                f"  Progressive Tax:  {trade.progressive_tax:,} coins\n"
                f"  Net Realized PnL: {pnl_sign}{trade.net_realized_pnl:,} coins ({trade.roi_pct:+.2f}%)\n"
                f"  Hold Duration:    {hold_str}\n"
                f"  PnL / Hour:       {pnl_sign}{trade.pnl_per_hour:,.0f} coins/hr\n"
                f"  Command:          /viewauction {trade.auction_uuid}\n"
            )
            try:
                print(msg)
            except UnicodeEncodeError:
                encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
                print(msg.encode(encoding, errors="replace").decode(encoding))
            logger.info("Dispatched dry-run trade completed alert for %s (%s)", trade.item_name, player_ign)
            return True

        target_webhook = self.config.DISCORD_WEBHOOK_URL
        if not target_webhook or "dummy" in target_webhook:
            logger.warning("Discord webhook URL is empty or dummy. Skipping trade alert for %s", trade.auction_uuid)
            return False

        session = await self.get_session()
        payload = self.build_completed_trade_embed(trade, player_ign=player_ign)

        for attempt in range(max_retries):
            try:
                async with session.post(target_webhook, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info("Successfully dispatched Discord trade alert for %s (%s)", trade.auction_uuid, player_ign)
                        await asyncio.sleep(0.5)
                        return True
                    if resp.status == 429:
                        try:
                            data = await resp.json()
                            retry_after = float(data.get("retry_after", 1.0))
                        except Exception:
                            retry_after = 2.0
                        await asyncio.sleep(retry_after)
                        continue
                    text = await resp.text()
                    logger.error("Discord trade webhook failed with HTTP %d: %s", resp.status, text[:200])
                    return False
            except Exception as err:
                logger.error("Error dispatching Discord trade webhook for %s: %s", trade.auction_uuid, err)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0)
                    continue
                return False

        return False
