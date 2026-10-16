"""
Background outcome labeling and calibration engine for SkyBlock arbitrage alerts.
Evaluates matured alerts against ended auction sales and player ledger purchases.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.config import Settings
from src.constants.lanes import resolve_canonical_lane
from src.constants.taxes import calculate_net_revenue
from src.db import DatabaseManager
from src.sales_ingestor import generate_relaxed_features_hash

logger = logging.getLogger(__name__)


class OutcomeLabeler:
    """
    Evaluates alerts older than OUTCOME_HORIZON_HOURS from the completed sales data.
    Assigns outcome statuses:
    - SOLD_FAST: flagged listing sold within SNIPE_SURVIVAL_MINUTES (default 15)
    - SOLD_SLOW: flagged listing sold after SNIPE_SURVIVAL_MINUTES
    - UNSOLD: flagged listing was not recorded as sold in sales history
    Checks target_hit:
    - 1 if a relaxed-hash comparable sold within the horizon at >= 95% of target price, else 0
    Checks realized_net_margin:
    - Ledger purchases override with realized PnL, else calculated from actual sale price if sold
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: Settings | None = None,
    ) -> None:
        self.db = db
        self.config = config or Settings(DRY_RUN=True)

    def evaluate_pending_outcomes(self, now_ms: int | None = None) -> int:
        """
        Evaluate and label alerts older than OUTCOME_HORIZON_HOURS.

        Returns:
            int: Number of alert outcomes evaluated and recorded.
        """
        current_now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        horizon_hours = getattr(self.config, "OUTCOME_HORIZON_HOURS", 6.0)
        horizon_ms = int(horizon_hours * 3600.0 * 1000.0)

        pending_alerts = self.db.get_pending_alerts_for_outcomes(
            horizon_ms=horizon_ms,
            now_ms=current_now_ms,
        )
        if not pending_alerts:
            return 0

        labeled_count = 0
        snipe_survival_seconds = float(getattr(self.config, "SNIPE_SURVIVAL_MINUTES", 15.0) * 60.0)

        for alert in pending_alerts:
            alert_id = alert.get("id")
            auction_uuid = str(alert.get("auction_uuid", ""))
            internal_id = str(alert.get("internal_id", ""))
            buy_price = int(alert.get("buy_price", 0))
            target_price = int(alert.get("estimated_resell", 0))
            label = str(alert.get("confidence_label", "MEDIUM"))
            alerted_at = int(alert.get("timestamp", 0))
            listing_start = alert.get("listing_start")
            derpy_active = bool(alert.get("derpy_active", 0))

            # Decode features and trace
            feat_raw = alert.get("features_json")
            features: dict[str, Any] = {}
            if isinstance(feat_raw, str):
                try:
                    features = json.loads(feat_raw)
                except (json.JSONDecodeError, TypeError):
                    features = {}
            elif isinstance(feat_raw, dict):
                features = feat_raw

            trace_raw = alert.get("trace_json")
            trace_data: dict[str, Any] = {}
            if isinstance(trace_raw, str):
                try:
                    trace_data = json.loads(trace_raw)
                except (json.JSONDecodeError, TypeError):
                    trace_data = {}
            elif isinstance(trace_raw, dict):
                trace_data = trace_raw

            path = str(trace_data.get("path") or alert.get("valuation_path") or "UNKNOWN")
            lane = str(
                trace_data.get("lane")
                or alert.get("lane")
                or resolve_canonical_lane(
                    path,
                    is_pet=features.get("is_pet"),
                    is_god_roll=bool(features.get("attributes")),
                    is_upgraded=bool(features.get("dungeon_stars") or features.get("rarity_upgrades")),
                )
            )

            intermediates = trace_data.get("intermediates", {}) if isinstance(trace_data, dict) else {}
            conf_score = intermediates.get("confidence_score")
            bucket_key = intermediates.get("pet_bucket_key") or internal_id

            # 1. Determine Status: Did the flagged listing itself sell?
            flagged_sale = self.db.get_sale(auction_uuid)
            listing_sold_after_s: float | None = None
            resolved_st = str(alert.get("resolved_status") or "")
            survival_s = float(alert.get("survival_duration_seconds") or alert.get("survival_seconds") or 0.0)

            if flagged_sale is not None:
                sale_ts = int(flagged_sale.get("timestamp", alerted_at))
                start_ref = int(listing_start) if listing_start and int(listing_start) > 0 else alerted_at
                listing_sold_after_s = max(0.0, (sale_ts - start_ref) / 1000.0)

                # Vanished within 1-2 ticks or <= 60s and claimed by buyer
                has_buyer = bool(flagged_sale.get("buyer_uuid"))
                if (listing_sold_after_s <= 60.0 and has_buyer) or (
                    abs(sale_ts - alerted_at) <= 30_000 and has_buyer
                ) or resolved_st == "SOLD_FAST_CLAIMED":
                    status = "SOLD_FAST_CLAIMED"
                elif listing_sold_after_s <= snipe_survival_seconds:
                    status = "SOLD_FAST"
                else:
                    status = "SOLD_SLOW"
            else:
                listing_sold_after_s = None
                # Vanished early before end without sale record -> seller cancelled
                if resolved_st == "SELLER_CANCELLED":
                    status = "SELLER_CANCELLED"
                elif resolved_st in ("SURVIVED_MULTI_TICK", "STALE") or survival_s >= 180.0:
                    status = "SURVIVED_MULTI_TICK"
                else:
                    status = "UNSOLD"

            # 2. Determine target_hit: A relaxed-hash comparable sold within the horizon at >= 95% of target
            target_hit = 0
            target_hit_threshold = int(target_price * 0.95)
            horizon_end_ms = alerted_at + horizon_ms

            cand_relaxed_hash = generate_relaxed_features_hash(features)
            recent_sales = self.db.get_sales(
                internal_id=internal_id,
                start_time=alerted_at,
                end_time=horizon_end_ms,
                limit=200,
            )

            first_comp_sale_price: int | None = None
            for s in recent_sales:
                # Exclude the flagged listing buy event itself if recorded with buy_price
                if str(s.get("uuid")) == auction_uuid and int(s.get("price", 0)) == buy_price:
                    continue

                s_feat = s.get("features_json")
                s_decoded: dict[str, Any] = {}
                if isinstance(s_feat, str):
                    try:
                        s_decoded = json.loads(s_feat)
                    except (json.JSONDecodeError, TypeError):
                        s_decoded = {}
                elif isinstance(s_feat, dict):
                    s_decoded = s_feat

                # Matching logic: for pets match pet bucket; for gear match relaxed features hash
                is_match = False
                if features.get("is_pet"):
                    from src.constants.pet_levels import get_pet_bucket_key

                    is_match = get_pet_bucket_key(s_decoded) == get_pet_bucket_key(features)
                else:
                    s_hash = generate_relaxed_features_hash(s_decoded)
                    is_match = s_hash == cand_relaxed_hash

                if is_match:
                    s_price = int(s.get("price", 0))
                    if first_comp_sale_price is None and s_price > 0:
                        first_comp_sale_price = s_price
                    if s_price >= target_hit_threshold:
                        target_hit = 1
                        break

            # 3. Determine realized_net_margin: Ledger rows override with realized PnL
            realized_net_margin: float | None = None
            purchase_record = self.db.get_purchase(auction_uuid)

            if purchase_record is not None:
                p_pnl = purchase_record.get("net_realized_pnl")
                p_sell = purchase_record.get("realized_sell_price")
                p_buy = purchase_record.get("buy_price") or buy_price
                p_derpy = bool(purchase_record.get("derpy_active", derpy_active))

                if p_pnl is not None and p_buy > 0:
                    realized_net_margin = round((float(p_pnl) / float(p_buy)) * 100.0, 2)
                elif p_sell is not None and p_buy > 0:
                    net_rev = calculate_net_revenue(int(p_sell), derpy_active=p_derpy)
                    realized_net_margin = round(((net_rev - p_buy) / float(p_buy)) * 100.0, 2)
            elif first_comp_sale_price is not None and buy_price > 0:
                net_rev = calculate_net_revenue(first_comp_sale_price, derpy_active=derpy_active)
                realized_net_margin = round(((net_rev - buy_price) / float(buy_price)) * 100.0, 2)

            # Record outcome
            success = self.db.record_alert_outcome(
                alert_id=alert_id,
                auction_uuid=auction_uuid,
                lane=lane,
                path=path,
                bucket_key=bucket_key,
                buy_price=buy_price,
                target_price=target_price,
                confidence_score=float(conf_score) if conf_score is not None else None,
                label=label,
                alerted_at=alerted_at,
                status=status,
                listing_sold_after_s=listing_sold_after_s,
                target_hit=target_hit,
                realized_net_margin=realized_net_margin,
                evaluated_at=current_now_ms,
            )
            if success:
                labeled_count += 1

        return labeled_count
