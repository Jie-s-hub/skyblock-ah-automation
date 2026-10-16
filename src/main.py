"""Main CLI orchestrator, async polling loop, and signal handling for Hypixel AH Monitor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from collections import defaultdict
from typing import Any, Optional

# Ensure Windows consoles don't crash on Minecraft symbols (e.g. ✪ \u272a)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import threading

from src.bazaar import BazaarCache
from src.client import HypixelClient, calculate_next_poll_delay
from src.config import Settings, get_settings
from src.evaluator import FlipEvaluator, FlipResult
from src.local_server import LocalWebSocketBroadcaster
from src.nbt_decoder import decode_item_bytes
from src.notifier import DiscordNotifier
from src.telemetry import (
    JsonLogFormatter,
    PipelineTelemetry,
    StageTimer,
    send_health_ping,
)
from src.velocity import SalesVelocityTracker


def configure_process_logging(enable_json: bool = False, level: int = logging.INFO) -> logging.Logger:
    """Ensure a single standardized logger configuration per process."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    if enable_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(handler)
    return logging.getLogger("HypixelAHMonitor")


logger = configure_process_logging()


def parse_cli_args() -> argparse.Namespace:
    """Parse command-line flags and parameters."""
    parser = argparse.ArgumentParser(
        description="Hypixel SkyBlock Auction House Arbitrage & Discord Alert Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Run in dry-run mode (print flips to console, no Discord dispatch)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Poll interval in seconds between auction cycles",
    )
    parser.add_argument(
        "--min-profit",
        type=int,
        default=None,
        help="Minimum net profit in coins required for an alert",
    )
    parser.add_argument(
        "--min-margin",
        type=float,
        default=None,
        help="Minimum net profit margin percentage required for an alert",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Hypixel API key (optional)",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Discord Webhook URL for alerting",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run a single polling cycle and exit cleanly",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Maximum number of polling cycles to run before exiting",
    )
    parser.add_argument(
        "--report-profile",
        action="store_true",
        default=False,
        help="Dispatch profile AH tracker report to Discord webhook and exit",
    )
    parser.add_argument(
        "--player",
        type=str,
        default=None,
        help="Player username for profile tracking report (default: TRACKED_PLAYER_IGN from settings)",
    )
    parser.add_argument(
        "--skip-player-tracking",
        "--skip-profile-viewer",
        "--no-player-tracking",
        action="store_true",
        default=False,
        help="Skip player profile tracking and Mojang identity lookup",
    )
    return parser.parse_args()


def load_configured_settings(args: argparse.Namespace) -> Settings:
    """Load settings from .env file and override with CLI arguments if provided."""
    overrides: dict[str, Any] = {}
    if args.dry_run is not None:
        overrides["DRY_RUN"] = args.dry_run
    if args.interval is not None:
        overrides["POLL_INTERVAL_SECONDS"] = args.interval
    if args.min_profit is not None:
        overrides["MIN_PROFIT_COINS"] = args.min_profit
    if args.min_margin is not None:
        overrides["MIN_PROFIT_MARGIN_PCT"] = args.min_margin
    if args.api_key is not None:
        overrides["HYPIXEL_API_KEY"] = args.api_key
    if args.webhook_url is not None:
        overrides["DISCORD_WEBHOOK_URL"] = args.webhook_url
    if getattr(args, "player", None) is not None:
        overrides["TRACKED_PLAYER_IGN"] = args.player
    if getattr(args, "skip_player_tracking", False):
        overrides["ENABLE_PLAYER_TRACKING"] = False

    try:
        return get_settings(**overrides)
    except Exception as err:
        logger.error("Configuration validation error: %s", err)
        sys.exit(1)


def decode_auction_batch(
    auctions: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    CPU-bound worker function to decode item_bytes for a list of auctions.
    Executed in a background thread to prevent blocking the async event loop.
    """
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for auction in auctions:
        decoded = decode_item_bytes(auction.get("item_bytes"))
        results.append((auction, decoded))
    return results


async def _drain_alerts_background(
    flips_to_log: list[FlipResult],
    alerts_to_send: list[FlipResult],
    db: Optional[Any],
    settings: Settings,
    notifier: DiscordNotifier,
) -> None:
    """Asynchronously persist alerts and dispatch Discord notifications without delaying cycle timer."""
    if db is not None and getattr(settings, "ENABLE_DB_LOGGING", True) and flips_to_log:
        try:
            for flip in flips_to_log:
                db.log_alert(
                    auction_uuid=flip.auction_uuid,
                    internal_id=flip.internal_id,
                    buy_price=flip.buy_price,
                    estimated_resell=flip.target_resell_price,
                    projected_profit=flip.net_profit,
                    target_resell_price=flip.target_resell_price,
                    net_profit=flip.net_profit,
                    margin_pct=flip.margin_pct,
                    confidence_label=flip.confidence_label,
                    valuation_path=flip.valuation_path,
                    derpy_active=1 if flip.derpy_active else 0,
                    fee_model_version=flip.fee_model_version,
                    trace_json=getattr(flip, "trace_json", None),
                    listing_start=getattr(flip, "listing_start", None),
                    features_json=getattr(flip, "features_json", None),
                )
        except Exception as err:
            logger.warning("Failed to log alerts to DB in background: %s", err)

    if alerts_to_send:
        try:
            await asyncio.gather(*(notifier.send_alert(f) for f in alerts_to_send), return_exceptions=True)
        except Exception as err:
            logger.warning("Failed to send Discord alerts in background: %s", err)


async def run_background_housekeeping(
    client: HypixelClient,
    bazaar_cache: BazaarCache,
    velocity_tracker: SalesVelocityTracker,
    sales_ingestor: Optional[Any],
    player_tracker: Optional[Any],
    election_tracker: Any,
    evaluator: FlipEvaluator,
    notifier: DiscordNotifier,
    settings: Settings,
) -> None:
    """
    Run ancillary market intelligence updates and persistence during the post-alert
    sleep window sequentially. Never blocks active auction house sniping or evaluation latency.
    """
    try:
        session = await client.get_session()
        derpy_active = await election_tracker.update(session)
        evaluator.set_derpy_active(derpy_active)
    except Exception as err:
        logger.debug("Background mayor update failed: %s", err)

    if settings.ENABLE_NETWORTH_VALUATION:
        try:
            await bazaar_cache.fetch_bazaar_data()
        except Exception as err:
            logger.debug("Background Bazaar update failed: %s", err)

    try:
        ended_auctions = await client.fetch_ended_auctions()
        if ended_auctions:
            new_sales = velocity_tracker.record_ended_auctions(ended_auctions)
            if sales_ingestor is not None:
                await asyncio.to_thread(sales_ingestor.ingest_ended_auctions, ended_auctions)
            if (
                getattr(settings, "ENABLE_DYNAMIC_RETENTION", False)
                and getattr(evaluator, "retention_fitter", None) is not None
            ):
                base_floors = {k: v[0] for k, v in evaluator.price_cache.items() if v}
                await asyncio.to_thread(
                    evaluator.retention_fitter.fit_from_sales_history,
                    None,
                    base_floors,
                )
            if player_tracker is not None:
                completed_trades = player_tracker.process_ended_auctions(ended_auctions)
                for trade in completed_trades:
                    await notifier.send_completed_trade(trade)
            logger.info("Recorded %d new sales from %d ended auctions.", new_sales, len(ended_auctions))
    except Exception as err:
        logger.debug("Background ended auctions processing failed: %s", err)

    if hasattr(evaluator, "db") and evaluator.db is not None:
        try:
            if evaluator.is_cold_start:
                sales_cnt = await asyncio.to_thread(evaluator.db.get_total_sales_count)
                cold_thresh = getattr(settings, "COLD_START_MIN_SALES", 50)
                evaluator.set_cold_start(sales_cnt < cold_thresh)
            snapshot = await asyncio.to_thread(evaluator.db.build_telemetry_snapshot)
            evaluator.set_telemetry_snapshot(snapshot)
        except Exception as err:
            logger.debug("Background telemetry snapshot refresh failed: %s", err)


async def run_monitor(
    settings: Settings,
    stop_event: asyncio.Event,
    max_cycles: Optional[int] = None,
) -> None:
    """Primary asynchronous continuous monitoring loop."""
    logger.info("Initializing Hypixel AH Monitor...")
    logger.info("Dry-Run: %s | Poll Interval: %ds", settings.DRY_RUN, settings.POLL_INTERVAL_SECONDS)
    logger.info(
        "Profit Threshold: >= %s coins | Margin Threshold: >= %.1f%%",
        f"{settings.MIN_PROFIT_COINS:,}",
        settings.MIN_PROFIT_MARGIN_PCT,
    )

    client = HypixelClient(settings)
    velocity_tracker = SalesVelocityTracker(settings)
    bazaar_cache = BazaarCache(settings)

    db = None
    sales_ingestor = None
    comps_evaluator = None
    player_tracker = None
    retention_fitter = None

    if (
        getattr(settings, "ENABLE_DB_LOGGING", True)
        or getattr(settings, "ENABLE_SOLD_COMPS", False)
        or getattr(settings, "ENABLE_PLAYER_TRACKING", False)
        or getattr(settings, "ENABLE_DYNAMIC_RETENTION", False)
    ):
        try:
            from src.db import DatabaseManager

            db = DatabaseManager(settings.DB_PATH)
            velocity_tracker.seed_from_db(db)
            if getattr(settings, "ENABLE_SOLD_COMPS", False):
                from src.comps_evaluator import CompsEvaluator
                from src.sales_ingestor import SalesIngestor

                sales_ingestor = SalesIngestor(db, settings)
                comps_evaluator = CompsEvaluator(db, settings, bazaar_cache=bazaar_cache)
            if getattr(settings, "ENABLE_DYNAMIC_RETENTION", False):
                from src.retention_fitter import RetentionFitter

                retention_fitter = RetentionFitter(db, settings, bazaar_cache=bazaar_cache)
        except Exception as err:
            logger.warning("Failed to initialize database or sold-comps components: %s", err)

    evaluator = FlipEvaluator(
        settings,
        velocity_tracker=velocity_tracker,
        bazaar_cache=bazaar_cache,
        comps_evaluator=comps_evaluator,
        retention_fitter=retention_fitter,
        db=db,
    )
    from src.election import MayorElectionTracker

    election_tracker = MayorElectionTracker(
        session=getattr(client, "_session", None),
        override=getattr(settings, "MAYOR_OVERRIDE", None),
        cache_ttl_seconds=getattr(settings, "ELECTION_CACHE_TTL_SECONDS", 600),
    )
    survival_auditor = None
    if db is not None and getattr(settings, "ENABLE_SURVIVAL_AUDITING", False):
        try:
            from src.telemetry import SurvivalAuditor

            survival_auditor = SurvivalAuditor(db=db, velocity_tracker=velocity_tracker, config=settings)
            evaluator.set_survival_auditor(survival_auditor)
        except Exception as err:
            logger.warning("Failed to initialize survival auditor: %s", err)

    ws_broadcaster: Optional[LocalWebSocketBroadcaster] = None
    if getattr(settings, "ENABLE_LOCAL_WS", True):
        try:
            ws_broadcaster = LocalWebSocketBroadcaster(config=settings)
            await ws_broadcaster.start()
        except Exception as err:
            logger.warning("Failed to start local WebSocket broadcaster: %s", err)
            ws_broadcaster = None

    notifier = DiscordNotifier(settings, ws_broadcaster=ws_broadcaster)

    if db is not None and getattr(settings, "ENABLE_PLAYER_TRACKING", False):
        try:
            from src.player_tracker import PlayerTracker

            player_tracker = PlayerTracker(db, config=settings, notifier=notifier)
            await player_tracker.resolve_identity()
        except Exception as err:
            logger.warning("Failed to initialize player tracking: %s", err)

    outcome_labeler = None
    if db is not None and getattr(settings, "ENABLE_OUTCOME_LABELING", False):
        try:
            from src.outcome_labeler import OutcomeLabeler

            outcome_labeler = OutcomeLabeler(db, config=settings)
        except Exception as err:
            logger.warning("Failed to initialize outcome labeler: %s", err)

    # In-memory tracking of alerted auction UUIDs to prevent duplicate alerts
    alerted_uuids: set[str] = set()
    decoded_cache: dict[str, dict[str, Any]] = {}
    active_auctions: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    consecutive_fast_retries: int = 0
    telemetry = PipelineTelemetry()
    evaluator.set_telemetry(telemetry)
    evaluator.set_mayor_tracker(election_tracker)

    housekeeping_tasks: set[asyncio.Task[Any]] = set()

    # Initialize zero-IO market telemetry snapshot for hot-path lookups
    if db is not None:
        try:
            snapshot = db.build_telemetry_snapshot()
            evaluator.set_telemetry_snapshot(snapshot)
            logger.info("Market telemetry snapshot initialized with %d comps summaries.", len(snapshot.comps_summary))
        except Exception as err:
            logger.debug("Initial telemetry snapshot build: %s", err)

    # Cold start / warm-up state evaluation & logging (Phase 3)
    sales_count = db.get_total_sales_count() if db is not None else 0
    cold_start_thresh = getattr(settings, "COLD_START_MIN_SALES", 50)
    is_cold_start = sales_count < cold_start_thresh
    evaluator.set_cold_start(is_cold_start)
    if is_cold_start:
        logger.warning(
            "System in COLD START / WARM-UP state: only %d recorded sales in history (threshold: %d). "
            "Comps-dependent lanes (pets, vanity) will emit nothing; floor lane capped at MEDIUM.",
            sales_count,
            cold_start_thresh,
        )
    else:
        logger.info("Market intelligence warmed up: %d recorded sales in history.", sales_count)

    # Pre-warm market intelligence feeds once before entering polling loop
    if settings.ENABLE_NETWORTH_VALUATION:
        try:
            await bazaar_cache.fetch_bazaar_data()
        except Exception as err:
            logger.debug("Initial Bazaar warm-up: %s", err)
    try:
        session = await client.get_session()
        derpy_active = await election_tracker.update(session)
        evaluator.set_derpy_active(derpy_active)
    except Exception as err:
        logger.debug("Initial mayor warm-up: %s", err)
    try:
        ended_auctions = await client.fetch_ended_auctions()
        if ended_auctions:
            new_sales = velocity_tracker.record_ended_auctions(ended_auctions)
            if sales_ingestor is not None:
                await asyncio.to_thread(sales_ingestor.ingest_ended_auctions, ended_auctions)
            logger.info("Initial ended auctions warmed up: %d new sales from %d auctions.", new_sales, len(ended_auctions))
    except Exception as err:
        logger.debug("Initial ended auctions warm-up: %s", err)

    try:
        while not stop_event.is_set():
            try:
                cycle_start = asyncio.get_running_loop().time()
                logger.info("Starting auction polling cycle...")

                is_first_tick = (len(active_auctions) == 0)
                active_telemetry = None if is_first_tick else telemetry

                # 1. Fetch active auctions FIRST! (Critical path for ultra-fast sniping)
                with StageTimer("fetch", active_telemetry):
                    bin_auctions = await client.fetch_all_auctions()

                flips_found: list[FlipResult] = []

                if bin_auctions is None:
                    logger.info("Cycle completed with no new data.")
                else:
                    active_uuids = {str(a.get("uuid", "")) for a in bin_auctions if a.get("uuid")}
                    stale_held = getattr(client, "stale_held_uuids", set())

                    vanished_uuids = (set(active_auctions.keys()) - active_uuids) - stale_held
                    new_uuids = active_uuids - set(active_auctions.keys())
                    uncached_auctions = [
                        a for a in bin_auctions
                        if str(a.get("uuid", "")) in new_uuids
                    ]

                    logger.info(
                        "BIN listings: %d total (%d active book, %d new delta to decode)",
                        len(bin_auctions),
                        len(active_auctions),
                        len(uncached_auctions),
                    )

                    # Step A2: Delta-Only NBT Decode & In-Memory Floor Overlay (Phase 1.2)
                    new_decoded_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
                    with StageTimer("decode", active_telemetry):
                        if uncached_auctions:
                            new_decoded_pairs = await asyncio.to_thread(decode_auction_batch, uncached_auctions)
                            for a, dec in new_decoded_pairs:
                                u_str = str(a.get("uuid", ""))
                                if u_str:
                                    decoded_cache[u_str] = dec
                                    active_auctions[u_str] = (a, dec)

                    eval_now_ms = time.time() * 1000.0

                    with StageTimer("cache", active_telemetry):
                        if vanished_uuids:
                            evaluator.prune_vanished_auctions(vanished_uuids, active_auctions)
                            for u in vanished_uuids:
                                active_auctions.pop(u, None)
                                decoded_cache.pop(u, None)

                        prev_tick_floors = {k: v[0] for k, v in evaluator.price_cache.items() if v}
                        if not evaluator.price_cache and active_auctions:
                            # Initial cold-start full cache load
                            evaluator.update_cache(list(active_auctions.values()), now_ms=eval_now_ms)
                        elif new_decoded_pairs:
                            evaluator.overlay_new_auctions(new_decoded_pairs, now_ms=eval_now_ms)

                    logger.info(
                        "Price cache updated with %d unique item IDs.",
                        len(evaluator.price_cache),
                    )

                    # Step A3: Delta Evaluation with Intra-Batch Guards (Phase 1.2)
                    batch_items_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
                    for a, dec in new_decoded_pairs:
                        iid = dec.get("internal_id")
                        if iid:
                            cid = iid
                            if dec.get("is_pet"):
                                eff_tier = str(dec.get("effective_tier") or dec.get("pet_tier", "COMMON")).strip().upper()
                                p_type = str(dec.get("pet_type", "")).strip().upper()
                                if p_type and eff_tier:
                                    cid = f"PET_{p_type}_{eff_tier}"
                            unit_cnt = max(1, int(dec.get("count", 1)))
                            if unit_cnt > 1:
                                a["_unit_count"] = unit_cnt
                            batch_items_map[cid].append(a)

                    delta_context = {
                        "prev_tick_floors": prev_tick_floors,
                        "batch_items": batch_items_map,
                    }

                    candidates_to_eval = new_decoded_pairs if prev_tick_floors else list(active_auctions.values())
                    candidates_to_eval.sort(
                        key=lambda pair: float(pair[0].get("start", 0) or 0),
                        reverse=True,
                    )

                    max_snipe_age = getattr(settings, "MAX_SNIPE_LISTING_AGE_SECONDS", 0)

                    with StageTimer("eval", active_telemetry):
                        for auction, decoded in candidates_to_eval:
                            uuid = str(auction.get("uuid", ""))
                            if uuid in alerted_uuids:
                                continue

                            # Fast break: since candidates_to_eval is sorted descending by start time,
                            # once an auction exceeds max_snipe_age, all subsequent auctions are strictly older.
                            if max_snipe_age > 0:
                                start_ms = auction.get("start")
                                if start_ms is not None and start_ms > 0:
                                    age_seconds = max(0.0, (eval_now_ms - float(start_ms)) / 1000.0)
                                    if age_seconds > float(max_snipe_age):
                                        break

                            flip = evaluator.evaluate_listing(
                                auction, decoded, now_ms=eval_now_ms, delta_context=delta_context
                            )
                            if flip:
                                flips_found.append(flip)
                                alerted_uuids.add(uuid)

                                # Zero-latency in-game broadcast: immediately push to Minecraft client mod
                                # Don't wait for remaining auctions or Discord webhooks!
                                shadow_lanes = getattr(settings, "SHADOW_MODE_LANES", [])
                                if (
                                    ws_broadcaster is not None
                                    and getattr(settings, "ENABLE_LOCAL_WS", True)
                                    and flip.lane not in shadow_lanes
                                ):
                                    try:
                                        asyncio.create_task(ws_broadcaster.broadcast_flip(flip))
                                        flip.ws_broadcasted = True
                                    except Exception as err:
                                        logger.debug("Failed zero-latency WS broadcast for %s: %s", uuid, err)

                    alerted_uuids.intersection_update(active_uuids)

                    logger.info(
                        "Evaluation complete. Found %d profitable flip(s).",
                        len(flips_found),
                    )

                    # 6. Dispatch qualified flips to DiscordNotifier without blocking cycle timer
                    with StageTimer("alert", active_telemetry):
                        alerts_to_send = []
                        flips_to_log = []
                        shadow_lanes = getattr(settings, "SHADOW_MODE_LANES", [])
                        for flip in flips_found:
                            if stop_event.is_set():
                                break
                            flips_to_log.append(flip)
                            if flip.lane in shadow_lanes:
                                logger.info(
                                    "Flip [%s] in lane [%s] is in SHADOW_MODE — persisted but alert suppressed",
                                    flip.auction_uuid,
                                    flip.lane,
                                )
                                continue
                            alerts_to_send.append(flip)

                        if flips_to_log or alerts_to_send:
                            asyncio.create_task(
                                _drain_alerts_background(
                                    flips_to_log=flips_to_log,
                                    alerts_to_send=alerts_to_send,
                                    db=db,
                                    settings=settings,
                                    notifier=notifier,
                                )
                            )

                    cycle_elapsed_ms = (asyncio.get_running_loop().time() - cycle_start) * 1000.0
                    if not is_first_tick:
                        telemetry.record_stage_latency("cycle_total", cycle_elapsed_ms)
                        telemetry.record_cycle(snipes=len(flips_found))

                    # Background market feeds and persistence: runs strictly during post-cycle sleep window
                    hk_task = asyncio.create_task(
                        run_background_housekeeping(
                            client=client,
                            bazaar_cache=bazaar_cache,
                            velocity_tracker=velocity_tracker,
                            sales_ingestor=sales_ingestor,
                            player_tracker=player_tracker,
                            election_tracker=election_tracker,
                            evaluator=evaluator,
                            notifier=notifier,
                            settings=settings,
                        )
                    )
                    housekeeping_tasks.add(hk_task)
                    hk_task.add_done_callback(housekeeping_tasks.discard)

                # Periodic operational health heartbeat
                health_interval = getattr(settings, "HEALTH_PING_INTERVAL_CYCLES", 0)
                if health_interval > 0 and telemetry.cycle_count > 0 and telemetry.cycle_count % health_interval == 0 and bin_auctions is not None:
                    try:
                        await send_health_ping(settings.DISCORD_WEBHOOK_URL, telemetry)
                    except Exception as err:
                        logger.warning("Failed to dispatch scheduled health ping: %s", err)

                if max_cycles is not None and telemetry.cycle_count >= max_cycles:
                    logger.info("Reached maximum cycles limit (%d). Exiting run.", max_cycles)
                    break

                # 7. Sleep for remainder of interval or until shutdown requested
                if bin_auctions is None:
                    if client.last_updated_ms is not None:
                        next_delay = calculate_next_poll_delay(client.last_updated_ms)
                        if next_delay <= 1.5:
                            consecutive_fast_retries += 1
                            # Fast probe with HEAD (0 bytes) before sleeping or looping
                            is_fresh, last_mod, age = await client.probe_cache_freshness()
                            if is_fresh or (age > 0 and age <= 1):
                                logger.info(
                                    "Edge cache refresh detected via probe (age=%ds). Fetching fresh auctions...", age
                                )
                                continue
                            sleep_duration = 0.4 if consecutive_fast_retries <= 10 else 1.0
                            logger.info(
                                "Awaiting edge cache update via probe (attempt %d). Polling in %.1fs...",
                                consecutive_fast_retries,
                                sleep_duration,
                            )
                        else:
                            consecutive_fast_retries = 0
                            sleep_duration = next_delay
                            logger.info("Cache unchanged. Sleeping %.1fs until expected refresh.", sleep_duration)
                    else:
                        sleep_duration = 1.0
                else:
                    consecutive_fast_retries = 0
                    if client.last_updated_ms is not None:
                        sleep_duration = calculate_next_poll_delay(client.last_updated_ms)
                        logger.info(
                            "Synchronized with Hypixel cache (lastUpdated=%d). Next poll in %.1fs.",
                            client.last_updated_ms,
                            sleep_duration,
                        )
                    else:
                        elapsed = asyncio.get_running_loop().time() - cycle_start
                        sleep_duration = max(0.0, float(settings.POLL_INTERVAL_SECONDS) - elapsed)

                # Warm keep-alive sleep loop: sleep in chunks of up to 10s calling keep_connection_warm()
                remaining_sleep = sleep_duration
                while remaining_sleep > 0 and not stop_event.is_set():
                    chunk = min(remaining_sleep, 10.0)
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=chunk)
                        break
                    except asyncio.TimeoutError:
                        remaining_sleep -= chunk
                        if remaining_sleep > 0 and client is not None:
                            try:
                                await client.keep_connection_warm()
                            except Exception as err:
                                logger.debug("Keep-alive warm probe error: %s", err)

                # Boundary precision probe: verify edge cache flipped before full 44-page download
                if not stop_event.is_set() and client.last_updated_ms is not None:
                    boundary_attempts = 0
                    while not stop_event.is_set() and boundary_attempts < 10:
                        is_fresh, last_mod, age = await client.probe_cache_freshness()
                        if is_fresh or (age > 0 and age <= 2):
                            break
                        boundary_attempts += 1
                        await asyncio.sleep(0.35)

            except Exception as cycle_err:
                if stop_event.is_set():
                    break
                logger.error("Unexpected error in auction polling cycle: %s", cycle_err, exc_info=True)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass

    finally:
        logger.info("Shutting down network clients...")
        if housekeeping_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*list(housekeeping_tasks), return_exceptions=True), timeout=3.0)
            except Exception as err:
                logger.debug("Housekeeping shutdown wait: %s", err)
        if settings.VELOCITY_CACHE_FILE:
            try:
                velocity_tracker.save_to_disk()
            except Exception as err:
                logger.warning("Failed to save velocity cache on shutdown: %s", err)
        try:
            await client.close()
        except Exception as err:
            logger.warning("Failed to close Hypixel client: %s", err)
        try:
            await notifier.close()
        except Exception as err:
            logger.warning("Failed to close notifier: %s", err)
        try:
            await bazaar_cache.close()
        except Exception as err:
            logger.warning("Failed to close bazaar cache: %s", err)
        if ws_broadcaster:
            try:
                await ws_broadcaster.stop()
            except Exception as err:
                logger.warning("Failed to stop WS broadcaster: %s", err)
        if db is not None:
            try:
                db.close()
            except Exception as err:
                logger.warning("Failed to close DB: %s", err)
        logger.info("Shutdown complete.")


def cleanup_orphan_threads(timeout: float = 2.0) -> None:
    """Join any non-daemon background threads before process exit."""
    main_th = threading.main_thread()
    for th in threading.enumerate():
        if th is not main_th and th.is_alive() and not th.daemon:
            logger.debug("Joining background thread: %s", th.name)
            th.join(timeout=timeout)


async def dispatch_profile_report(settings: Settings) -> int:
    """Send profile AH tracker report embed to Discord webhook and exit."""
    from src.db import DatabaseManager
    from src.player_tracker import PlayerTracker

    db = DatabaseManager(settings.DB_PATH)
    try:
        async with DiscordNotifier(config=settings) as notifier:
            async with PlayerTracker(db, config=settings, notifier=notifier) as tracker:
                await tracker.resolve_identity()
                metrics = tracker.get_portfolio_metrics()
                open_positions = tracker.get_open_positions(limit=5)
                recent_trades = tracker.get_recent_trades(limit=5)

                logger.info("Dispatching profile AH tracker report for %s...", metrics["player_ign"])
            success = await notifier.send_profile_tracker_report(
                metrics=metrics,
                open_positions=open_positions,
                recent_trades=recent_trades,
            )
            if success:
                logger.info("Successfully dispatched profile AH tracker report to Discord webhook.")
                return 0
            else:
                logger.error("Failed to dispatch profile AH tracker report to Discord webhook.")
                return 1
    finally:
        db.close()


def main() -> None:
    """CLI entrypoint with signal handling and event loop orchestration."""
    args = parse_cli_args()
    settings = load_configured_settings(args)

    # Standardize on single process-wide logger
    configure_process_logging(enable_json=getattr(settings, "ENABLE_JSON_LOGGING", False))

    if args.report_profile:
        exit_code = asyncio.run(dispatch_profile_report(settings))
        sys.exit(exit_code)

    stop_event = asyncio.Event()

    def handle_signal(*_: Any) -> None:
        logger.info("Received termination signal. Initiating graceful shutdown...")
        stop_event.set()

    # Register POSIX / Windows signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except (AttributeError, ValueError):
            pass

    max_cycles = 1 if args.once else args.max_cycles

    try:
        asyncio.run(run_monitor(settings, stop_event, max_cycles=max_cycles))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process interrupted. Exiting.")
    except Exception as fatal_err:
        logger.critical("Fatal unhandled error in monitor: %s", fatal_err, exc_info=True)
        sys.exit(1)
    finally:
        cleanup_orphan_threads()
        logging.shutdown()


if __name__ == "__main__":
    main()
