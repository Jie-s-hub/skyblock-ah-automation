"""Local WebSocket broadcaster for real-time in-game Fabric client integration."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import time
import urllib.parse
from http import HTTPStatus
from typing import Any, Optional, Set

import websockets

from src.config import Settings
from src.evaluator import FlipResult

logger = logging.getLogger(__name__)


def build_flip_payload(flip: FlipResult) -> dict[str, Any]:
    """
    Format a FlipResult into a standardized JSON schema for the in-game Fabric mod:
    - uuid: Auction UUID for '/viewauction' execution
    - name: Item display name
    - buy_price: Current purchase cost in coins
    - target_resell: Estimated resell price in coins
    - profit: Net profit after AH taxes
    - margin_pct: Profit margin percentage
    - category: Item category / staple classification
    - calculations: Itemized list of addon/value computations
    - timestamp: Current unix epoch millisecond timestamp
    """
    calculations: list[str] = []

    if getattr(flip, "addon_breakdown", None):
        for addon in flip.addon_breakdown:
            name = addon.get("name") or addon.get("type", "Addon")
            cost = addon.get("cost", 0)
            calculations.append(f"{name} ({cost:,})")
    elif getattr(flip, "is_pet", False):
        if getattr(flip, "pet_level", None):
            xp_str = f" ({flip.pet_exp:,.0f} XP)" if getattr(flip, "pet_exp", None) is not None else ""
            calculations.append(f"Level: {flip.pet_level}{xp_str}")
        if getattr(flip, "effective_rarity", None):
            calculations.append(f"Effective Rarity: {flip.effective_rarity}")
        calculations.append(f"Candy: {getattr(flip, 'pet_candy_used', 0)}/10")
        held_name = getattr(flip, "pet_held_item_name", None) or "None"
        held_val = getattr(flip, "held_item_value", 0)
        calculations.append(f"Held Item: {held_name}" + (f" ({held_val:,})" if held_val > 0 else ""))
        if getattr(flip, "pet_skin", None):
            calculations.append(f"Skin: {flip.pet_skin}")
        if getattr(flip, "baseline_source", None):
            calculations.append(f"Baseline: {flip.baseline_source}")
        if getattr(flip, "reference_price", None):
            calculations.append(f"Reference Price: {flip.reference_price:,}")
        if getattr(flip, "comp_count", None):
            med_str = f" (Median: {flip.comp_median:,})" if getattr(flip, "comp_median", None) else ""
            calculations.append(f"Comps: {flip.comp_count} sales{med_str}")
    elif getattr(flip, "is_attribute_snipe", False) and getattr(flip, "attributes", None):
        for attr, tier in sorted(flip.attributes.items()):
            calculations.append(f"{attr.replace('_', ' ').title()} {tier}")

    category = flip.staple_category or (
        "NETWORTH"
        if getattr(flip, "is_networth_snipe", False)
        else (
            "PET"
            if getattr(flip, "is_pet", False)
            else ("ATTRIBUTE" if getattr(flip, "is_attribute_snipe", False) else "CLEAN")
        )
    )

    if getattr(flip, "craft_cost", None) is not None:
        calc_msg = f"Craft Cost: {flip.craft_cost:,}"
        if getattr(flip, "is_craft_capped", False):
            calc_msg += " (Capped)"
        calculations.append(calc_msg)

    return {
        "uuid": str(flip.auction_uuid),
        "name": str(flip.item_name),
        "buy_price": int(flip.buy_price),
        "target_resell": int(flip.target_resell_price),
        "profit": int(flip.net_profit),
        "margin_pct": float(round(flip.margin_pct, 2)),
        "category": str(category),
        "calculations": calculations,
        "craft_cost": getattr(flip, "craft_cost", None),
        "is_craft_capped": bool(getattr(flip, "is_craft_capped", False)),
        "is_out_of_budget": bool(getattr(flip, "is_out_of_budget", False)),
        "confidence_label": str(getattr(flip, "confidence_label", "MEDIUM")),
        "confidence_score": getattr(flip, "confidence_score", None),
        "estimated_hold_hours": getattr(flip, "estimated_hold_hours", None),
        "capital_efficiency": getattr(flip, "capital_efficiency", None),
        "pet_level": getattr(flip, "pet_level", None),
        "pet_exp": getattr(flip, "pet_exp", None),
        "effective_rarity": getattr(flip, "effective_rarity", None),
        "candy_used": getattr(flip, "pet_candy_used", 0),
        "pet_held_item": getattr(flip, "pet_held_item", None),
        "pet_skin": getattr(flip, "pet_skin", None),
        "baseline_source": getattr(flip, "baseline_source", None),
        "reference_price": getattr(flip, "reference_price", None),
        "comp_count": getattr(flip, "comp_count", None),
        "comp_median": getattr(flip, "comp_median", None),
        "timestamp": int(time.time() * 1000),
    }


class LocalWebSocketBroadcaster:
    """
    Manages an asynchronous WebSocket server on ws://127.0.0.1:8765 to broadcast
    real-time flip opportunities directly to local in-game Fabric client mods.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        config: Optional[Settings] = None,
        allowed_origins: Optional[list[str] | set[str]] = None,
        token: Optional[str] = None,
    ) -> None:
        if config:
            self.host = getattr(config, "LOCAL_WS_HOST", host)
            self.port = getattr(config, "LOCAL_WS_PORT", port)
            self.allowed_origins = set(getattr(config, "LOCAL_WS_ALLOWED_ORIGINS", []))
            self.token = getattr(config, "LOCAL_WS_TOKEN", "")
        else:
            self.host = host
            self.port = port
            self.allowed_origins = set(allowed_origins or [])
            self.token = token or ""

        # Phase 6 Hardening: Refuse non-loopback hosts (bind 127.0.0.1 only)
        if not self._is_loopback_host(self.host):
            raise ValueError(f"Local WebSocket server only binds to loopback hosts (refused '{self.host}').")

        self.config = config
        self.clients: Set[Any] = set()
        self._server: Optional[Any] = None
        self._running: bool = False
        self._actual_port: int = self.port

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        """Verify whether the requested host interface is a loopback address."""
        if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _process_request(self, *args: Any, **kwargs: Any) -> Any:
        """
        Validate incoming WebSocket handshake:
        - Reject any handshake carrying an Origin header not listed in LOCAL_WS_ALLOWED_ORIGINS.
        - Check optional LOCAL_WS_TOKEN as a ?token= query parameter checked via hmac.compare_digest.
        """
        if len(args) == 2:
            first, second = args
            if isinstance(first, str):
                path = first
                headers = second
                respond_fn = None
            else:
                connection = first
                request = second
                path = getattr(request, "path", "/")
                headers = getattr(request, "headers", {})
                respond_fn = getattr(connection, "respond", None)
        else:
            path = "/"
            headers = {}
            respond_fn = None

        # 1. Origin header check: reject any handshake carrying an Origin header not listed in allowed_origins
        origin = headers.get("Origin")
        if origin is not None and origin not in self.allowed_origins:
            logger.warning("Rejected WebSocket handshake carrying disallowed Origin: %s", origin)
            if respond_fn:
                return respond_fn(HTTPStatus.FORBIDDEN, "Forbidden: Disallowed Origin\n")
            return (HTTPStatus.FORBIDDEN, [], b"Forbidden: Disallowed Origin\n")

        # 2. Token check: optional LOCAL_WS_TOKEN as a ?token= query parameter checked with hmac.compare_digest
        if self.token:
            query = urllib.parse.urlparse(path).query
            params = urllib.parse.parse_qs(query)
            token_param = params.get("token", [""])[0]
            if not hmac.compare_digest(token_param, self.token):
                logger.warning("Rejected WebSocket handshake with missing or invalid token.")
                if respond_fn:
                    return respond_fn(HTTPStatus.UNAUTHORIZED, "Unauthorized: Invalid or missing token\n")
                return (HTTPStatus.UNAUTHORIZED, [], b"Unauthorized: Invalid or missing token\n")

        return None

    @property
    def actual_port(self) -> int:
        """Return the actual bound port (useful when bound to port 0 for tests)."""
        return self._actual_port

    async def _handler(self, websocket: Any) -> None:
        """Client connection handler: registers client and maintains connection."""
        self.clients.add(websocket)
        client_addr = getattr(websocket, "remote_address", "client")
        logger.info("Fabric client connected: %s (Total clients: %d)", client_addr, len(self.clients))
        try:
            # Keep connection open until client disconnects
            if hasattr(websocket, "wait_closed"):
                await websocket.wait_closed()
            else:
                async for _ in websocket:
                    pass
        except Exception as err:
            logger.debug("Fabric client connection closed with notice: %s", err)
        finally:
            self.clients.discard(websocket)
            logger.info("Fabric client disconnected: %s (Remaining clients: %d)", client_addr, len(self.clients))

    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            return

        if not self.token:
            logger.warning(
                "LOCAL_WS_TOKEN is not configured; local WebSocket server is accessible without authentication."
            )

        logger.info("Starting local WebSocket server on ws://%s:%d", self.host, self.port)
        self._server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            process_request=self._process_request,
        )
        self._running = True

        # Extract actual bound port if ephemeral
        sockets = getattr(self._server, "sockets", None)
        if sockets and len(sockets) > 0:
            sockname = sockets[0].getsockname()
            if isinstance(sockname, tuple) and len(sockname) > 1:
                self._actual_port = sockname[1]

        logger.info("Local WebSocket server successfully bound to ws://%s:%d", self.host, self._actual_port)

    async def stop(self) -> None:
        """Gracefully stop the server and disconnect active clients."""
        if not self._running:
            return

        self._running = False
        if self._server:
            self._server.close()
            if hasattr(self._server, "wait_closed"):
                await self._server.wait_closed()
            self._server = None

        # Disconnect any remaining clients
        for client in list(self.clients):
            try:
                await client.close()
            except Exception:
                pass
        self.clients.clear()
        logger.info("Local WebSocket server stopped.")

    async def broadcast_flip(self, flip: FlipResult) -> int:
        """
        Broadcast a qualifying flip payload to all connected Fabric mod clients.

        Returns:
            int: Number of clients that received the broadcast message.
        """
        if not self.clients:
            return 0

        payload = build_flip_payload(flip)
        message = json.dumps(payload)

        # Broadcast message to all active clients
        sent_count = 0
        disconnected = set()

        for client in list(self.clients):
            try:
                await client.send(message)
                sent_count += 1
            except Exception as err:
                logger.warning("Failed to send flip to client: %s", err)
                disconnected.add(client)

        if disconnected:
            self.clients.difference_update(disconnected)

        return sent_count

    async def __aenter__(self) -> "LocalWebSocketBroadcaster":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()
