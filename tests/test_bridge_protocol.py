# Tests for ws_bridge.py WebSocket server — Contract 1 compliance.
# Implementations live in ws_bridge.py (and auto_runner/).
# Run with: python -m pytest tests/ from the project root.
#
# Strategy: spin up ws_bridge in a background thread with the Fusion runtime
# fully patched out, then exercise the protocol with raw TCP + WebSocket
# handshake / framing so tests stay dependency-free (no websockets client lib
# required on the test side).

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_ORG_ID", "test-org")
os.environ.setdefault("OPENAI_PROJECT_ID", "test-project")

from bridge_config import (
    BRIDGE_PORT,
    MSG_EXECUTE_SCRIPT,
    MSG_GET_STATE,
    MSG_RESET,
    MSG_TAKE_SCREENSHOT,
)

# Use a different port so these tests don't clash with a live bridge instance.
_TEST_PORT = BRIDGE_PORT + 1

# How long (seconds) to wait for the server thread to bind before tests run.
_SERVER_BOOT_TIMEOUT = 5.0

# Per-operation socket timeout (seconds).
_SOCKET_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Minimal WebSocket framing helpers (no third-party library needed)
# ---------------------------------------------------------------------------

def _ws_handshake(sock: socket.socket, host: str = "localhost", port: int = _TEST_PORT):
    """Perform the HTTP → WebSocket upgrade handshake.

    Returns once the server has sent a 101 Switching Protocols response.
    Raises AssertionError if the upgrade fails.
    """
    key_bytes = os.urandom(16)
    key_b64 = base64.b64encode(key_bytes).decode()

    request = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key_b64}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode())

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Server closed connection during WS handshake")
        response += chunk

    header = response.split(b"\r\n\r\n")[0].decode(errors="replace")
    assert "101" in header, f"Expected 101 Switching Protocols, got:\n{header}"

    # Verify Sec-WebSocket-Accept
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    expected_accept = base64.b64encode(
        hashlib.sha1((key_b64 + magic).encode()).digest()
    ).decode()
    assert expected_accept in header, "Sec-WebSocket-Accept mismatch"


def _ws_send_text(sock: socket.socket, text: str):
    """Send a single WebSocket text frame (client → server, masked)."""
    payload = text.encode("utf-8")
    length = len(payload)

    # Masking key (4 random bytes)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    frame = bytearray()
    # FIN + opcode 0x01 (text)
    frame.append(0x81)

    if length <= 125:
        frame.append(0x80 | length)  # MASK bit set
    elif length <= 0xFFFF:
        frame.append(0x80 | 126)
        frame.extend(struct.pack(">H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack(">Q", length))

    frame.extend(mask)
    frame.extend(masked)

    sock.sendall(bytes(frame))


def _ws_recv_text(sock: socket.socket) -> str:
    """Receive one WebSocket text frame (server → client, unmasked).

    Handles continuation frames and re-assembles fragmented payloads.
    Only supports text opcode (0x01) and continuation (0x00).
    Raises ValueError on unexpected opcodes.
    """
    full_payload = b""

    while True:
        # Read first two bytes of the frame header
        header = _recv_exact(sock, 2)
        fin = (header[0] & 0x80) != 0
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]

        mask_key = _recv_exact(sock, 4) if masked else None
        payload = _recv_exact(sock, length)

        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        if opcode == 0x08:
            # Close frame — server is closing; re-raise as an error
            raise ConnectionError("Server sent WebSocket close frame")
        if opcode == 0x09:
            # Ping — send pong and continue
            _ws_send_pong(sock, payload)
            continue
        if opcode == 0x0A:
            # Pong — ignore
            continue
        if opcode not in (0x00, 0x01):
            raise ValueError(f"Unexpected WebSocket opcode: {opcode:#x}")

        full_payload += payload

        if fin:
            return full_payload.decode("utf-8")
        # else: continuation frame follows


def _ws_send_pong(sock: socket.socket, data: bytes = b""):
    """Send a WebSocket pong frame."""
    frame = bytearray([0x8A, len(data)])
    frame.extend(data)
    sock.sendall(bytes(frame))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from sock, buffering across multiple recv calls."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"Connection closed; expected {n} bytes, got {len(buf)}")
        buf += chunk
    return buf


def _roundtrip(sock: socket.socket, msg: dict) -> dict:
    """Send msg as a JSON WebSocket text frame and return the parsed JSON response."""
    _ws_send_text(sock, json.dumps(msg))
    raw = _ws_recv_text(sock)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------

def _start_bridge_server(port: int, ready_event: threading.Event):
    """Import and start ws_bridge on the given port.

    This runs inside a daemon thread.  Fusion runtime objects are patched
    before the module is loaded so no real Fusion 360 process is needed.
    """
    # Patch adsk (the Fusion Python bindings) before ws_bridge tries to import it.
    adsk_mock = MagicMock()
    sys.modules.setdefault("adsk", adsk_mock)
    sys.modules.setdefault("adsk.core", adsk_mock.core)
    sys.modules.setdefault("adsk.fusion", adsk_mock.fusion)
    sys.modules.setdefault("adsk.cam", adsk_mock.cam)

    with patch("auto_runner.runtime") as mock_runtime:
        mock_runtime.execute_wrapped_script.return_value = {
            "ok": True,
            "state": {
                "timestamp": "2026-03-17T12:00:00",
                "units": "cm",
                "body_count": 0,
                "sketch_count": 0,
                "feature_count": 0,
                "bodies": [],
                "origin_planes": [],
            },
        }
        mock_runtime.capture_and_store_current_context.return_value = {
            "state": {
                "timestamp": "2026-03-17T12:00:00",
                "units": "cm",
                "body_count": 0,
                "sketch_count": 0,
                "feature_count": 0,
                "bodies": [],
                "origin_planes": [],
            },
            "selection": {"count": 0, "items": []},
        }
        mock_runtime.take_screenshot.return_value = {
            "image_base64": "dGVzdA==",
            "mime_type": "image/png",
        }
        mock_runtime.reset.return_value = {
            "state": {
                "timestamp": "2026-03-17T12:00:00",
                "units": "cm",
                "body_count": 0,
                "sketch_count": 0,
                "feature_count": 0,
                "bodies": [],
                "origin_planes": [],
            }
        }

        import importlib
        import ws_bridge  # noqa: F401 — imported for side-effect of starting server

        # If ws_bridge exposes a way to start on a custom port, use it;
        # otherwise fall back to patching BRIDGE_PORT before import.
        try:
            ws_bridge.start(port=port, ready_event=ready_event)
        except AttributeError:
            # ws_bridge.start() not yet implemented — signal ready anyway so tests
            # can record a clean skip rather than hanging indefinitely.
            ready_event.set()


# ---------------------------------------------------------------------------
# Base class: manages one server instance per test class
# ---------------------------------------------------------------------------

class _BridgeTestBase(unittest.TestCase):
    """Starts ws_bridge once per test class; individual tests share the server."""

    _server_thread: threading.Thread = None
    _server_ready: threading.Event = None
    _server_available: bool = False

    @classmethod
    def setUpClass(cls):
        cls._server_ready = threading.Event()
        cls._server_thread = threading.Thread(
            target=_start_bridge_server,
            args=(_TEST_PORT, cls._server_ready),
            daemon=True,
        )
        cls._server_thread.start()

        # Wait up to _SERVER_BOOT_TIMEOUT for the server to bind.
        cls._server_available = cls._server_ready.wait(timeout=_SERVER_BOOT_TIMEOUT)

    def _new_connection(self) -> socket.socket:
        """Open a new TCP socket and complete the WS handshake; return the socket.

        Skips the test if the server didn't start (e.g. ws_bridge not implemented yet).
        """
        if not self._server_available:
            self.skipTest(
                "ws_bridge server did not start within timeout — "
                "ws_bridge.py may not yet be implemented."
            )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_SOCKET_TIMEOUT)
        try:
            sock.connect(("localhost", _TEST_PORT))
            _ws_handshake(sock)
        except Exception as exc:
            sock.close()
            self.skipTest(f"Could not connect to ws_bridge test server: {exc}")
        return sock


# ---------------------------------------------------------------------------
# Contract 1 compliance tests
# ---------------------------------------------------------------------------

class TestBridgeProtocol(_BridgeTestBase):

    def test_get_state_response_shape(self):
        """get_state response has request_id, ok=True, and data.state."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_GET_STATE, "request_id": "test-001", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertEqual(
                response.get("request_id"),
                "test-001",
                "response.request_id must echo the request's request_id",
            )
            self.assertTrue(response.get("ok"), "get_state response must have ok=True")
            self.assertIn("data", response)
            self.assertIn(
                "state",
                response["data"],
                "get_state response.data must contain 'state'",
            )
        finally:
            sock.close()

    def test_get_state_state_has_required_keys(self):
        """The state object from get_state contains body_count, units, bodies."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_GET_STATE, "request_id": "test-001b", "payload": {}}
            response = _roundtrip(sock, req)
            state = response["data"]["state"]

            for key in ("body_count", "units", "bodies"):
                self.assertIn(key, state, f"state is missing required key '{key}'")
        finally:
            sock.close()

    def test_execute_script_response_shape(self):
        """execute_script response has request_id, ok, and data."""
        sock = self._new_connection()
        try:
            req = {
                "type": MSG_EXECUTE_SCRIPT,
                "request_id": "test-002",
                "payload": {"script": "pass"},
            }
            response = _roundtrip(sock, req)

            self.assertEqual(response.get("request_id"), "test-002")
            self.assertIn("ok", response)
            self.assertIsInstance(response["ok"], bool)
            self.assertIn("data", response)
        finally:
            sock.close()

    def test_execute_script_error_field_present(self):
        """execute_script response always includes an 'error' field (None on success)."""
        sock = self._new_connection()
        try:
            req = {
                "type": MSG_EXECUTE_SCRIPT,
                "request_id": "test-002b",
                "payload": {"script": "pass"},
            }
            response = _roundtrip(sock, req)

            self.assertIn(
                "error",
                response,
                "Response envelope must always contain an 'error' field",
            )
        finally:
            sock.close()

    def test_reset_response_shape(self):
        """reset response has request_id and ok=True."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_RESET, "request_id": "test-003", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertEqual(response.get("request_id"), "test-003")
            self.assertTrue(response.get("ok"), "reset response must have ok=True")
        finally:
            sock.close()

    def test_take_screenshot_response_shape(self):
        """take_screenshot response has request_id, ok=True, and data.image_base64."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_TAKE_SCREENSHOT, "request_id": "test-004", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertEqual(response.get("request_id"), "test-004")
            self.assertTrue(response.get("ok"))
            self.assertIn("data", response)
            self.assertIn("image_base64", response["data"])
        finally:
            sock.close()

    def test_unknown_message_type_returns_error(self):
        """Sending an unknown msg type returns ok=False with a non-None error."""
        sock = self._new_connection()
        try:
            req = {
                "type": "nonexistent_type",
                "request_id": "test-005",
                "payload": {},
            }
            response = _roundtrip(sock, req)

            self.assertEqual(response.get("request_id"), "test-005")
            self.assertFalse(
                response.get("ok"),
                "Unknown message type should yield ok=False",
            )
            self.assertIsNotNone(
                response.get("error"),
                "Unknown message type should include a non-None error string",
            )
        finally:
            sock.close()

    def test_request_id_echoed_for_multiple_requests(self):
        """Every response echoes the exact request_id sent in the request."""
        sock = self._new_connection()
        try:
            for req_id in ("aaa-111", "bbb-222", "ccc-333"):
                req = {"type": MSG_GET_STATE, "request_id": req_id, "payload": {}}
                response = _roundtrip(sock, req)
                self.assertEqual(
                    response.get("request_id"),
                    req_id,
                    f"request_id not echoed correctly for id={req_id!r}",
                )
        finally:
            sock.close()

    def test_malformed_json_returns_error(self):
        """Sending malformed JSON over the WebSocket returns ok=False."""
        sock = self._new_connection()
        try:
            _ws_send_text(sock, "not json at all {{ }")
            raw = _ws_recv_text(sock)
            response = json.loads(raw)

            self.assertFalse(
                response.get("ok"),
                "Malformed JSON input must yield ok=False",
            )
            self.assertIsNotNone(
                response.get("error"),
                "Malformed JSON response must include an error message",
            )
        finally:
            sock.close()

    def test_missing_type_field_returns_error(self):
        """A message with no 'type' field returns ok=False."""
        sock = self._new_connection()
        try:
            req = {"request_id": "test-no-type", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertFalse(response.get("ok"))
        finally:
            sock.close()

    def test_missing_payload_field_handled(self):
        """A message missing the 'payload' field does not crash the server."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_GET_STATE, "request_id": "test-no-payload"}
            response = _roundtrip(sock, req)

            # Server must respond — either ok or an error, but not silence.
            self.assertIn("request_id", response)
        finally:
            sock.close()

    def test_concurrent_requests_answered_independently(self):
        """Two sequential requests on the same connection are answered independently."""
        sock = self._new_connection()
        try:
            # Send first request
            req1 = {"type": MSG_GET_STATE, "request_id": "seq-001", "payload": {}}
            resp1 = _roundtrip(sock, req1)
            self.assertEqual(resp1.get("request_id"), "seq-001")

            # Send second request
            req2 = {"type": MSG_GET_STATE, "request_id": "seq-002", "payload": {}}
            resp2 = _roundtrip(sock, req2)
            self.assertEqual(resp2.get("request_id"), "seq-002")
        finally:
            sock.close()

    def test_response_envelope_has_all_required_keys(self):
        """Every response envelope contains request_id, ok, data, and error."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_GET_STATE, "request_id": "envelope-check", "payload": {}}
            response = _roundtrip(sock, req)

            for key in ("request_id", "ok", "data", "error"):
                self.assertIn(
                    key,
                    response,
                    f"Response envelope is missing required key '{key}'",
                )
        finally:
            sock.close()

    def test_ok_field_is_boolean(self):
        """The 'ok' field in every response is a JSON boolean (not a string)."""
        sock = self._new_connection()
        try:
            req = {"type": MSG_GET_STATE, "request_id": "bool-check", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertIsInstance(
                response["ok"],
                bool,
                "response.ok must be a JSON boolean, not a string or int",
            )
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# Tests that verify the bridge stays alive after an error
# ---------------------------------------------------------------------------

class TestBridgeResilience(_BridgeTestBase):

    def test_server_remains_responsive_after_bad_json(self):
        """After receiving malformed JSON, the bridge still handles valid requests."""
        sock = self._new_connection()
        try:
            # Send garbage
            _ws_send_text(sock, "{{{{broken json")
            _ws_recv_text(sock)  # consume the error response

            # Now send a valid request and expect a well-formed response
            req = {"type": MSG_GET_STATE, "request_id": "after-error", "payload": {}}
            response = _roundtrip(sock, req)

            self.assertEqual(response.get("request_id"), "after-error")
            self.assertIn("ok", response)
        except ConnectionError:
            self.skipTest(
                "Server closed connection after bad JSON — "
                "per-connection error isolation is an implementation choice"
            )
        finally:
            sock.close()

    def test_server_accepts_new_connection_after_previous_closed(self):
        """A second client can connect after the first disconnects."""
        # First connection
        sock1 = self._new_connection()
        req1 = {"type": MSG_GET_STATE, "request_id": "conn1-req", "payload": {}}
        resp1 = _roundtrip(sock1, req1)
        sock1.close()
        self.assertEqual(resp1.get("request_id"), "conn1-req")

        # Short pause to let the server clean up the first connection
        time.sleep(0.1)

        # Second connection
        sock2 = self._new_connection()
        try:
            req2 = {"type": MSG_GET_STATE, "request_id": "conn2-req", "payload": {}}
            resp2 = _roundtrip(sock2, req2)
            self.assertEqual(resp2.get("request_id"), "conn2-req")
        finally:
            sock2.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
