"""Unit and integration tests for telemetry, profiling, JSON logging, and health pings."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.stream_client import WebSocketFeedClient
from src.telemetry import (
    JsonLogFormatter,
    PipelineTelemetry,
    StageTimer,
    build_health_embed,
    format_uptime,
    send_health_ping,
)


def test_json_log_formatter_basic():
    """Verify JsonLogFormatter produces valid JSON with standard fields."""
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="TestLogger",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="Test log message with %s",
        args=("parameter",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["logger"] == "TestLogger"
    assert data["level"] == "INFO"
    assert data["message"] == "Test log message with parameter"
    assert "timestamp" in data


def test_json_log_formatter_custom_stage_and_latency():
    """Verify JsonLogFormatter captures stage and latency_ms attributes."""
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="Pipeline",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=10,
        msg="Stage complete",
        args=(),
        exc_info=None,
    )
    record.stage = "eval"
    record.latency_ms = 12.34
    record.extra_id = "test-uuid-1"

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["stage"] == "eval"
    assert data["latency_ms"] == 12.34
    assert data["extra_id"] == "test-uuid-1"


def test_json_log_formatter_with_exception():
    """Verify JsonLogFormatter includes formatted exception traceback."""
    formatter = JsonLogFormatter()
    try:
        raise ValueError("Simulated pipeline error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="ErrorHandler",
        level=logging.ERROR,
        pathname=__file__,
        lineno=50,
        msg="An error occurred",
        args=(),
        exc_info=exc_info,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["level"] == "ERROR"
    assert "exception" in data
    assert "ValueError: Simulated pipeline error" in data["exception"]


def test_pipeline_telemetry_metrics_accumulation():
    """Verify PipelineTelemetry records cycles, snipes, errors, and stage latencies."""
    telemetry = PipelineTelemetry()

    # Record stages
    telemetry.record_stage_latency("fetch", 10.0)
    telemetry.record_stage_latency("fetch", 20.0)
    telemetry.record_stage_latency("decode", 5.0)

    # Record cycles
    telemetry.record_cycle(snipes=2, errors=0)
    telemetry.record_cycle(snipes=1, errors=1)
    telemetry.record_snipe(3)
    telemetry.record_error(2)

    metrics = telemetry.get_metrics()
    assert metrics["cycle_count"] == 2
    assert metrics["snipes_detected"] == 6  # 2 + 1 + 3
    assert metrics["error_count"] == 3  # 1 + 2
    assert metrics["average_latencies_ms"]["fetch"] == 15.0
    assert metrics["average_latencies_ms"]["decode"] == 5.0
    assert metrics["stage_runs"]["fetch"] == 2
    assert metrics["stage_runs"]["decode"] == 1
    assert metrics["uptime_seconds"] >= 0.0

    # Reset
    telemetry.reset()
    assert telemetry.cycle_count == 0
    assert telemetry.snipes_detected == 0
    assert telemetry.get_average_latency("fetch") == 0.0


def test_stage_timer_sync():
    """Verify StageTimer context manager profiles elapsed synchronous time."""
    telemetry = PipelineTelemetry()
    with StageTimer("sync_stage", telemetry=telemetry) as timer:
        time.sleep(0.01)

    assert timer.elapsed_ms > 0.0
    assert telemetry.get_average_latency("sync_stage") > 0.0


@pytest.mark.asyncio
async def test_stage_timer_async():
    """Verify StageTimer supports asynchronous context management."""
    telemetry = PipelineTelemetry()
    async with StageTimer("async_stage", telemetry=telemetry) as timer:
        await asyncio.sleep(0.01)

    assert timer.elapsed_ms > 0.0
    assert telemetry.get_average_latency("async_stage") > 0.0


def test_format_uptime_string():
    """Verify format_uptime properly formats varied second spans."""
    assert format_uptime(45) == "45s"
    assert format_uptime(125) == "2m 5s"
    assert format_uptime(3665) == "1h 1m 5s"
    assert format_uptime(90065) == "1d 1h 1m 5s"


def test_build_health_embed():
    """Verify build_health_embed constructs a valid Discord embed structure."""
    telemetry = PipelineTelemetry()
    telemetry.record_stage_latency("fetch", 14.5)
    telemetry.record_stage_latency("eval", 3.2)
    telemetry.record_cycle(snipes=5, errors=0)

    embed = build_health_embed(telemetry)

    assert "🟢" in embed["title"]
    assert embed["color"] == 0x2ECC71  # Green (0 errors)
    field_names = [f["name"] for f in embed["fields"]]
    assert any("Uptime" in name for name in field_names)
    assert any("Cycles" in name for name in field_names)
    assert any("Snipes" in name for name in field_names)
    assert any("Errors" in name for name in field_names)
    assert any("Stage Latencies" in name for name in field_names)

    # If errors present, color changes to warning
    telemetry.record_error(1)
    embed_with_error = build_health_embed(telemetry)
    assert embed_with_error["color"] == 0xE67E22  # Orange warning


@pytest.mark.asyncio
async def test_send_health_ping_dummy_webhook_skips_http():
    """Verify send_health_ping cleanly skips dummy/empty webhooks without making HTTP calls."""
    telemetry = PipelineTelemetry()
    res = await send_health_ping("https://discord.com/api/webhooks/dummy", telemetry)
    assert res is True

    res_empty = await send_health_ping("", telemetry)
    assert res_empty is True


@pytest.mark.asyncio
async def test_send_health_ping_valid_webhook_success():
    """Verify send_health_ping posts embed payload to Discord webhook."""
    telemetry = PipelineTelemetry()
    mock_resp = AsyncMock()
    mock_resp.status = 204

    mock_session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_session.post.return_value = cm
    mock_session.closed = False

    res = await send_health_ping(
        webhook_url="https://discord.com/api/webhooks/12345/abcdef",
        telemetry=telemetry,
        session=mock_session,
    )

    assert res is True
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args
    assert "embeds" in call_args[1]["json"]


@pytest.mark.asyncio
async def test_websocket_feed_client_exponential_backoff_and_jitter():
    """Verify WebSocketFeedClient applies exponential backoff with jitter on reconnects."""
    config = Settings(DRY_RUN=True)
    client = WebSocketFeedClient(
        config=config,
        ws_url="wss://mock.stream",
        reconnect_delay=2.0,
        backoff_multiplier=2.0,
        max_reconnect_delay=10.0,
    )

    sleep_calls = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        if len(sleep_calls) >= 3:
            client._running = False

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.ws_connect.side_effect = Exception("Connection refused")
    client._session = mock_session

    with patch("asyncio.sleep", side_effect=fake_sleep):
        async for _ in client.stream_auctions():
            pass

    # Verify backoff increased across reconnect attempts
    assert len(sleep_calls) >= 3
    # First delay should be ~2.0 + jitter (approx 2.1 to 2.5)
    assert 2.0 <= sleep_calls[0] <= 3.0
    # Second delay should be ~4.0 + jitter (approx 4.1 to 4.5)
    assert 4.0 <= sleep_calls[1] <= 5.0
    # Third delay should be ~8.0 + jitter (approx 8.1 to 8.5)
    assert 8.0 <= sleep_calls[2] <= 9.0
