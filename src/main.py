"""Main CLI orchestrator, async polling loop, and signal handling for Hypixel AH Monitor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("HypixelAHMonitor")


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
    if db is not None:
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
        except Exception as err:
            logger.warning("Failed to initialize player tracking: %s", err)

    # In-memory tracking of alerted auction UUIDs to prevent duplicate alerts
    alerted_uuids: set[str] = set()
    telemetry = PipelineTelemetry()

    try:
        while not stop_event.is_set():
            cycle_start = asyncio.get_running_loop().time()
            logger.info("Starting auction polling cycle...")

            # 0. Check active mayor (Derpy detection)
            try:
                session = await client.get_session()
                derpy_active = await election_tracker.update(session)
                evaluator.set_derpy_active(derpy_active)
            except Exception as err:
                logger.warning("Failed to update active mayor status: %s", err)

            # 0b. Fetch / refresh Bazaar market cache if networth valuation enabled
            if settings.ENABLE_NETWORTH_VALUATION:
                try:
                    await bazaar_cache.fetch_bazaar_data()
                except Exception as err:
                    telemetry.record_error()
                    logger.error("Failed to update Bazaar market data: %s", err)

            # 1. Fetch ended auctions to track sales velocity, sold comps, and player trades
            try:
                ended_auctions = await client.fetch_ended_auctions()
                if ended_auctions:
                    new_sales = velocity_tracker.record_ended_auctions(ended_auctions)
                    if sales_ingestor is not None:
                        sales_ingestor.ingest_ended_auctions(ended_auctions)
                    if player_tracker is not None:
                        completed_trades = player_tracker.process_ended_auctions(ended_auctions)
                        for trade in completed_trades:
                            await notifier.send_completed_trade(trade)
                    logger.info(
                        "Recorded %d new sales from %d ended auctions.",
                        new_sales,
                        len(ended_auctions),
                    )
            except Exception as err:
                telemetry.record_error()
                logger.error("Failed to fetch or process ended auctions: %s", err)

            # 2. Fetch auctions via client
            with StageTimer("fetch", telemetry):
                bin_auctions = await client.fetch_all_auctions()

            flips_found: list[FlipResult] = []

            if bin_auctions is None:
                logger.info(
                    "Cycle completed with no new data. Sleeping for %ds...",
                    settings.POLL_INTERVAL_SECONDS,
                )
            else:
                logger.info("Decoding %d BIN listings in background thread pool...", len(bin_auctions))
                # 2. Decode NBT offloaded to background thread pool
                with StageTimer("decode", telemetry):
                    decoded_pairs = await asyncio.to_thread(decode_auction_batch, bin_auctions)

                # 3. Update rolling price cache
                evaluator.update_cache(decoded_pairs)
                logger.info(
                    "Price cache updated with %d unique item IDs.",
                    len(evaluator.price_cache),
                )

                # 4. Evaluate each listing
                with StageTimer("eval", telemetry):
                    for auction, decoded in decoded_pairs:
                        uuid = str(auction.get("uuid", ""))
                        if uuid in alerted_uuids:
                            continue

                        flip = evaluator.evaluate_listing(auction, decoded)
                        if flip:
                            flips_found.append(flip)
                            alerted_uuids.add(uuid)

                # 5. Garbage-collect expired/sold auction UUIDs from alerted set & audit survival
                active_uuids = {str(a.get("uuid", "")) for a in bin_auctions}
                alerted_uuids.intersection_update(active_uuids)
                if survival_auditor is not None:
                    try:
                        survival_auditor.audit_cycle(active_uuids)
                    except Exception as err:
                        logger.debug("Failed to run survival audit cycle: %s", err)

                logger.info(
                    "Evaluation complete. Found %d profitable flip(s).",
                    len(flips_found),
                )

                # 6. Dispatch qualified flips to DiscordNotifier
                with StageTimer("alert", telemetry):
                    for flip in flips_found:
                        if stop_event.is_set():
                            break
                        if db is not None and getattr(settings, "ENABLE_DB_LOGGING", True):
                            try:
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
                                )
                            except Exception as err:
                                logger.warning("Failed to log alert to DB: %s", err)
                        await notifier.send_alert(flip)

            cycle_elapsed_ms = (asyncio.get_running_loop().time() - cycle_start) * 1000.0
            telemetry.record_stage_latency("cycle_total", cycle_elapsed_ms)
            telemetry.record_cycle(snipes=len(flips_found))

            # Periodic operational health heartbeat
            health_interval = getattr(settings, "HEALTH_PING_INTERVAL_CYCLES", 0)
            if health_interval > 0 and telemetry.cycle_count % health_interval == 0:
                try:
                    await send_health_ping(settings.DISCORD_WEBHOOK_URL, telemetry)
                except Exception as err:
                    logger.warning("Failed to dispatch scheduled health ping: %s", err)

            if max_cycles is not None and telemetry.cycle_count >= max_cycles:
                logger.info("Reached maximum cycles limit (%d). Exiting run.", max_cycles)
                break

            # 7. Sleep for remainder of interval or until shutdown requested
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

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_duration)
            except asyncio.TimeoutError:
                # Normal interval timeout elapsed, proceed to next cycle
                pass

    finally:
        logger.info("Shutting down network clients...")
        if settings.VELOCITY_CACHE_FILE:
            try:
                velocity_tracker.save_to_disk()
            except Exception as err:
                logger.warning("Failed to save velocity cache on shutdown: %s", err)
        await client.close()
        await notifier.close()
        await bazaar_cache.close()
        if ws_broadcaster:
            await ws_broadcaster.stop()
        if db is not None:
            db.close()
        logger.info("Shutdown complete.")


def main() -> None:
    """CLI entrypoint with signal handling and event loop orchestration."""
    args = parse_cli_args()
    settings = load_configured_settings(args)

    if getattr(settings, "ENABLE_JSON_LOGGING", False):
        root_logger = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        root_logger.handlers = [handler]

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


if __name__ == "__main__":
    main()
