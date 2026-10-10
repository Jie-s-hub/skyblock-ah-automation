"""Telemetry, stage profiling, structured JSON logging, and Discord health pings."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

import aiohttp

from src.config import Settings
from src.db import DatabaseManager

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class JsonLogFormatter(logging.Formatter):
    """
    Format standard logging.LogRecord instances as single-line structured JSON.
    Includes timestamp, level, logger name, message, stage, latency_ms, and exception info.
    """

    STANDARD_ATTRS = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Generate message string
        record.message = record.getMessage()

        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        # Include custom telemetry attributes if set on record
        if hasattr(record, "stage"):
            entry["stage"] = getattr(record, "stage")
        if hasattr(record, "latency_ms"):
            entry["latency_ms"] = getattr(record, "latency_ms")

        # Capture any extra attributes passed in kwargs
        for key, val in record.__dict__.items():
            if key not in self.STANDARD_ATTRS and key not in entry:
                try:
                    # Test JSON serializability
                    json.dumps(val)
                    entry[key] = val
                except (TypeError, OverflowError):
                    entry[key] = str(val)

        # Format exception traceback if present
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry)


class PipelineTelemetry:
    """
    In-memory operational telemetry tracker recording cycle counts,
    error rates, snipes detected, and rolling stage latencies.
    """

    def __init__(self, max_history_per_stage: int = 100) -> None:
        self.max_history_per_stage = max_history_per_stage
        self.start_time: float = time.time()
        self.cycle_count: int = 0
        self.snipes_detected: int = 0
        self.error_count: int = 0
        self._stage_latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.max_history_per_stage))
        self._stage_run_counts: dict[str, int] = defaultdict(int)
        self._rejection_counts: dict[str, int] = defaultdict(int)

    def record_stage_latency(self, stage: str, latency_ms: float) -> None:
        """Record execution latency in milliseconds for a specific pipeline stage."""
        self._stage_latencies[stage].append(latency_ms)
        self._stage_run_counts[stage] += 1

    def record_cycle(self, snipes: int = 0, errors: int = 0) -> None:
        """Increment completed cycle count and optionally record snipes/errors."""
        self.cycle_count += 1
        self.snipes_detected += snipes
        self.error_count += errors

    def record_snipe(self, count: int = 1) -> None:
        """Record newly detected profitable flip snipes."""
        self.snipes_detected += count

    def record_error(self, count: int = 1) -> None:
        """Record runtime errors encountered in the pipeline."""
        self.error_count += count

    def record_rejection(self, reason: str, count: int = 1) -> None:
        """Record an evaluation rejection reason count."""
        if reason:
            self._rejection_counts[reason] += count

    def get_top_rejections(self, limit: int = 5) -> list[tuple[str, int]]:
        """Return the top rejection reasons sorted by occurrence count descending."""
        return sorted(self._rejection_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    def get_average_latency(self, stage: str) -> float:
        """Compute the average latency (in ms) from recent history for a stage."""
        latencies = self._stage_latencies.get(stage)
        if not latencies:
            return 0.0
        return sum(latencies) / len(latencies)

    def get_uptime_seconds(self) -> float:
        """Get total elapsed running time in seconds."""
        return max(0.0, time.time() - self.start_time)

    def get_metrics(self) -> dict[str, Any]:
        """Return a dictionary snapshot of pipeline health metrics."""
        avg_latencies = {stage: round(self.get_average_latency(stage), 2) for stage in self._stage_latencies}
        return {
            "uptime_seconds": round(self.get_uptime_seconds(), 2),
            "cycle_count": self.cycle_count,
            "snipes_detected": self.snipes_detected,
            "error_count": self.error_count,
            "average_latencies_ms": avg_latencies,
            "stage_runs": dict(self._stage_run_counts),
            "top_rejections": self.get_top_rejections(limit=5),
        }

    def reset(self) -> None:
        """Reset all counters and histories."""
        self.start_time = time.time()
        self.cycle_count = 0
        self.snipes_detected = 0
        self.error_count = 0
        self._stage_latencies.clear()
        self._stage_run_counts.clear()
        self._rejection_counts.clear()


class StageTimer:
    """
    Context manager measuring execution time of a code block in milliseconds.
    Supports both synchronous and asynchronous contexts.
    """

    def __init__(
        self,
        stage_name: str,
        telemetry: Optional[PipelineTelemetry] = None,
    ) -> None:
        self.stage_name = stage_name
        self.telemetry = telemetry
        self.start_time: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> StageTimer:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000.0
        if self.telemetry is not None:
            self.telemetry.record_stage_latency(self.stage_name, self.elapsed_ms)

    async def __aenter__(self) -> StageTimer:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)


_GLOBAL_TELEMETRY = PipelineTelemetry()


def get_global_telemetry() -> PipelineTelemetry:
    """Retrieve global default PipelineTelemetry instance."""
    return _GLOBAL_TELEMETRY


def format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime string (e.g. 1d 2h 3m 4s)."""
    s = int(seconds)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, s = divmod(s, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def build_health_embed(telemetry: Optional[PipelineTelemetry] = None) -> dict[str, Any]:
    """Construct a clean Discord embed payload visualizing system health and stage latencies."""
    tel = telemetry or get_global_telemetry()
    metrics = tel.get_metrics()
    uptime_str = format_uptime(metrics["uptime_seconds"])

    # Green if healthy, orange/red if errors observed
    embed_color = 0x2ECC71 if metrics["error_count"] == 0 else 0xE67E22

    # Latencies breakdown string
    latencies = metrics["average_latencies_ms"]
    common_stages = ["fetch", "decode", "eval", "alert", "cycle_total"]
    latency_lines = []
    for stage in common_stages:
        if stage in latencies:
            latency_lines.append(f"• **{stage.capitalize()}**: `{latencies[stage]:.1f} ms`")
    # Add any extra recorded stages
    for stage, val in latencies.items():
        if stage not in common_stages:
            latency_lines.append(f"• **{stage.capitalize()}**: `{val:.1f} ms`")

    latency_display = "\n".join(latency_lines) if latency_lines else "*No stage timings recorded yet*"

    fields = [
        {"name": "⏱️ Uptime", "value": f"`{uptime_str}`", "inline": True},
        {"name": "🔄 Cycles", "value": f"`{metrics['cycle_count']:,}`", "inline": True},
        {"name": "🎯 Snipes Alerted", "value": f"`{metrics['snipes_detected']:,}`", "inline": True},
        {"name": "⚠️ Errors", "value": f"`{metrics['error_count']:,}`", "inline": True},
        {"name": "⚡ Stage Latencies (Avg)", "value": latency_display, "inline": False},
    ]

    top_rejections = metrics.get("top_rejections") or []
    if top_rejections:
        rejections_display = "\n".join([f"• `{reason}`: **{count:,}**" for reason, count in top_rejections])
        fields.append({"name": "🚫 Top Rejections", "value": rejections_display, "inline": False})

    return {
        "title": "🟢 Hypixel AH Monitor — Health Heartbeat",
        "description": "System operational and synchronizing with market feeds.",
        "color": embed_color,
        "fields": fields,
        "footer": {"text": f"Reported at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"},
    }


async def send_health_ping(
    webhook_url: str,
    telemetry: Optional[PipelineTelemetry] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """
    Send an automated operational health ping embed to Discord.
    Skips HTTP request gracefully if dummy webhook or dry run is detected.
    """
    if not webhook_url or "dummy" in webhook_url.lower():
        logger.info("Health ping generated (dry-run / dummy webhook skipped).")
        return True

    embed = build_health_embed(telemetry)
    payload = {"embeds": [embed]}

    owns_session = False
    current_session = session
    if current_session is None or current_session.closed:
        current_session = aiohttp.ClientSession()
        owns_session = True

    try:
        async with current_session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
            if resp.status in (200, 204):
                logger.info("Discord health ping sent successfully.")
                return True
            else:
                body = await resp.text()
                logger.warning("Discord health ping returned status %d: %s", resp.status, body)
                return False
    except Exception as err:
        logger.warning("Failed to dispatch Discord health ping: %s", err)
        return False
    finally:
        if owns_session and current_session and not current_session.closed:
            await current_session.close()


class SurvivalAuditor:
    """
    Market-Validated Alert Survival Tracking:
    - Tracks open market duration of alerted listings.
    - Resolves status:
      * Disappearance within <= 5m (300s): SNIPED_BY_MARKET (converted flip).
      * Persistence > 30m with hourly velocity >= 2.0/hr: STALE (market-rejected flip).
      * Disappearance after > 5m: DISAPPEARED.
    - Aggregates lane conversion rates.
    - Provides dynamic haircuts (default: 5% haircut when lane conversion rate < 40%).
    """

    def __init__(
        self,
        db: DatabaseManager,
        velocity_tracker: Optional[Any] = None,
        config: Optional[Settings] = None,
    ) -> None:
        self.db = db
        self.velocity_tracker = velocity_tracker
        self.config = config or Settings(DRY_RUN=True)

    def audit_cycle(
        self,
        active_uuids: set[str],
        current_now_ms: Optional[float] = None,
    ) -> dict[str, int]:
        """
        Audit pending alerts against the active listings order book.
        Resolves disappeared and stale alerts.
        """
        now_ms = current_now_ms if current_now_ms is not None else (time.time() * 1000.0)
        pending = self.db.get_pending_alerts()
        results: dict[str, int] = defaultdict(int)
        stale_threshold_mins = getattr(self.config, "ALERT_STALE_THRESHOLD_MINUTES", 30)

        for alert in pending:
            uuid = str(alert.get("auction_uuid", ""))
            if not uuid:
                continue

            alert_ts = float(alert.get("timestamp", 0))
            elapsed_seconds = max(0.0, (now_ms - alert_ts) / 1000.0)
            elapsed_minutes = elapsed_seconds / 60.0

            if uuid not in active_uuids:
                # Listing is no longer active on market
                if elapsed_seconds <= 300.0:  # <= 5 minutes
                    status = "SNIPED_BY_MARKET"
                else:
                    status = "DISAPPEARED"

                self.db.update_alert_survival(uuid, status, elapsed_seconds, int(now_ms))
                results[status] += 1
            else:
                # Listing is still sitting active on AH
                internal_id = str(alert.get("internal_id", ""))
                hourly_velocity = 0.0
                if self.velocity_tracker is not None and internal_id:
                    hourly_velocity = self.velocity_tracker.get_daily_velocity(internal_id) / 24.0

                if elapsed_minutes >= stale_threshold_mins and hourly_velocity >= 1.5:
                    status = "STALE"
                    self.db.update_alert_survival(uuid, status, elapsed_seconds, int(now_ms))
                    results[status] += 1

        return dict(results)

    def get_lane_conversion_rate(
        self,
        lane: str,
        window_hours: float = 24.0,
        now_ms: Optional[float] = None,
    ) -> float:
        """Calculate recent conversion rate (sniped / (sniped + stale)) for a valuation lane."""
        stats = self.db.get_lane_conversion_stats(window_hours=window_hours, now_ms=now_ms)
        lane_data = stats.get(lane)
        if not lane_data:
            return 1.0
        return float(lane_data.get("conversion_rate", 1.0))

    def get_lane_haircut(
        self,
        lane: str,
        window_hours: float = 24.0,
        now_ms: Optional[float] = None,
    ) -> float:
        """
        Return dynamic haircut percentage (e.g. 0.05 for 5%) if lane conversion rate < 40%.
        """
        if not getattr(self.config, "ENABLE_LANE_CONVERSION_HAIRCUT", True):
            return 0.0
        conversion_rate = self.get_lane_conversion_rate(lane, window_hours=window_hours, now_ms=now_ms)
        if conversion_rate < 0.40:
            return 0.05
        return 0.0
