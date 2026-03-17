"""
bridge_client.py — Async WebSocket client for the Fusion 360 bridge.

Usage (async context):
    client = BridgeClient()
    await client.connect()
    result = await client.execute_script(script_text)
    await client.disconnect()

Usage (sync context, e.g. tool_handlers):
    result = run_bridge_call(client.execute_script(script_text))
"""

import asyncio
import json
import uuid

import websockets

from bridge_config import (
    BRIDGE_PORT,
    MAX_RECONNECT_ATTEMPTS,
    MSG_EXECUTE_SCRIPT,
    MSG_GET_STATE,
    MSG_RESET,
    MSG_TAKE_SCREENSHOT,
    RECONNECT_INTERVAL_S,
    REQUEST_TIMEOUT_S,
)


class BridgeClient:
    """Async WebSocket client for the Fusion 360 bridge."""

    def __init__(self, port: int = BRIDGE_PORT):
        self._uri = f"ws://localhost:{port}"
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self):
        """Connect with retry. Raises RuntimeError if all attempts fail."""
        last_exc = None
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            try:
                self._ws = await websockets.connect(self._uri)
                # Start background message reader
                self._reader_task = asyncio.ensure_future(self._reader())
                return
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(RECONNECT_INTERVAL_S)
        raise RuntimeError(
            f"BridgeClient: could not connect to {self._uri} after "
            f"{MAX_RECONNECT_ATTEMPTS} attempts. Last error: {last_exc}"
        )

    async def disconnect(self):
        """Close the connection and cancel the background reader."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Reject any still-pending futures
        self._reject_pending(ConnectionError("BridgeClient disconnected"))

    # ------------------------------------------------------------------
    # Internal: background reader resolves pending futures by request_id
    # ------------------------------------------------------------------

    async def _reader(self):
        """Read messages from the WebSocket and resolve matching pending futures."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                request_id = msg.get("request_id")
                if request_id and request_id in self._pending:
                    fut = self._pending.pop(request_id)
                    if not fut.done():
                        fut.set_result(msg)
        except Exception as exc:
            # Connection dropped — reject all pending futures
            self._reject_pending(ConnectionError(f"BridgeClient reader error: {exc}"))

    def _reject_pending(self, exc: Exception):
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ------------------------------------------------------------------
    # Internal: send a typed request, await the matching response
    # ------------------------------------------------------------------

    async def _send(self, msg_type: str, payload: dict) -> dict:
        """Send a request envelope, await the matching response by request_id.

        Returns the full response dict.
        Raises TimeoutError if no response arrives within REQUEST_TIMEOUT_S.
        Raises ConnectionError if the WebSocket is not connected.
        """
        if not self._ws:
            raise ConnectionError("BridgeClient is not connected. Call connect() first.")

        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut

        envelope = {
            "type": msg_type,
            "request_id": request_id,
            "payload": payload,
        }
        try:
            await self._ws.send(json.dumps(envelope))
        except Exception as exc:
            self._pending.pop(request_id, None)
            raise ConnectionError(f"BridgeClient send failed: {exc}") from exc

        try:
            response = await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(
                f"BridgeClient: no response for request_id={request_id} "
                f"(msg_type={msg_type}) within {REQUEST_TIMEOUT_S}s"
            )

        return response

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_script(self, script: str) -> dict:
        """Send an execute_script message.

        Returns {"ok": bool, "data": {...}, "error": str|None}.
        """
        return await self._send(MSG_EXECUTE_SCRIPT, {"script": script})

    async def get_state(self) -> dict:
        """Send a get_state message. Returns the bridge response dict."""
        return await self._send(MSG_GET_STATE, {})

    async def reset(self) -> dict:
        """Send a reset message. Returns the bridge response dict."""
        return await self._send(MSG_RESET, {})

    async def take_screenshot(self) -> dict:
        """Send a take_screenshot message. Returns the bridge response dict."""
        return await self._send(MSG_TAKE_SCREENSHOT, {})


# ---------------------------------------------------------------------------
# Sync convenience wrapper (for tool_handlers running in a sync MCP context)
# ---------------------------------------------------------------------------

def run_bridge_call(coro) -> dict:
    """Run an async bridge coroutine synchronously.

    Used by tool_handlers in a sync MCP context.
    """
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# MockBridgeClient — for unit tests
# ---------------------------------------------------------------------------

class MockBridgeClient:
    """Fake BridgeClient that routes calls to a user-supplied handler callable.

    The handler receives (msg_type, payload) and returns a canned response dict.
    Example:
        def my_handler(msg_type, payload):
            if msg_type == MSG_GET_STATE:
                return {"ok": True, "data": {"state": {}}, "error": None}
            return {"ok": True, "data": {}, "error": None}

        mock = MockBridgeClient(handler=my_handler)
    """

    def __init__(self, handler=None):
        self._handler = handler or (lambda msg_type, payload: {"ok": True, "data": {}, "error": None})

    def _call(self, msg_type: str, payload: dict) -> dict:
        return self._handler(msg_type, payload)

    # Mirror the async interface as sync coroutine-compatible awaitables
    # so that run_bridge_call() works seamlessly.

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def execute_script(self, script: str) -> dict:
        return self._call(MSG_EXECUTE_SCRIPT, {"script": script})

    async def get_state(self) -> dict:
        return self._call(MSG_GET_STATE, {})

    async def reset(self) -> dict:
        return self._call(MSG_RESET, {})

    async def take_screenshot(self) -> dict:
        return self._call(MSG_TAKE_SCREENSHOT, {})
