"""
WebSocket bridge server for Fusion 360 MCP integration.

Runs a stdlib WebSocket server on a background thread and marshals all
Fusion API calls onto the UI thread via Fusion's custom event system.

Thread safety model:
  Background thread  →  puts work on _work_queue, fires custom event
  UI thread          →  BridgeEventHandler.notify() dequeues and calls runtime
  Background thread  ←  reads result from _result_queue, sends WS response
"""

import base64
import hashlib
import json
import os
import queue
import socket
import struct
import sys
import threading
import traceback

import adsk.core

# ── Path setup ────────────────────────────────────────────────────────────────
# auto_runner/ is at ADDIN_DIR; project root is one level up.
ADDIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(ADDIN_DIR, os.pardir))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if ADDIN_DIR not in sys.path:
    sys.path.insert(0, ADDIN_DIR)

# ── Import shared constants ────────────────────────────────────────────────────
try:
    import bridge_config as _cfg
    BRIDGE_PORT = _cfg.BRIDGE_PORT
    REQUEST_TIMEOUT_S = _cfg.REQUEST_TIMEOUT_S
    MSG_EXECUTE_SCRIPT = _cfg.MSG_EXECUTE_SCRIPT
    MSG_GET_STATE = _cfg.MSG_GET_STATE
    MSG_RESET = _cfg.MSG_RESET
    MSG_TAKE_SCREENSHOT = _cfg.MSG_TAKE_SCREENSHOT
except Exception:
    # Fallback defaults so the module at least loads even if bridge_config is missing
    BRIDGE_PORT = 8765
    REQUEST_TIMEOUT_S = 120
    MSG_EXECUTE_SCRIPT = "execute_script"
    MSG_GET_STATE = "get_state"
    MSG_RESET = "reset"
    MSG_TAKE_SCREENSHOT = "take_screenshot"

# ── Import runtime (do NOT call from background thread) ───────────────────────
# auto_runner.py uses bare `import runtime as _runtime` because ADDIN_DIR is on
# sys.path before the add-in runs.  We match that pattern.
try:
    import runtime as _runtime
except Exception:
    _runtime = None

# ── Module-level state ────────────────────────────────────────────────────────
BRIDGE_EVENT_ID = "FusionMCPBridgeEvent_v1"

_server_thread: threading.Thread = None
_stop_event: threading.Event = None
_work_queue: queue.Queue = None    # (request_id, msg_type, payload) tuples
_result_queue: queue.Queue = None  # result dicts keyed by request_id
_event_handler = None              # adsk.core.CustomEventHandler instance
_custom_event = None               # adsk.core.CustomEvent object
_app = None                        # adsk.core.Application reference


# ── Logging helper ─────────────────────────────────────────────────────────────
def _log(msg: str):
    try:
        if _runtime is not None:
            _runtime.log(f"[bridge] {msg}")
        else:
            import time
            log_path = os.path.join(ADDIN_DIR, "log.txt")
            with open(log_path, "a") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] [bridge] {msg}\n")
    except Exception:
        pass


# ── WebSocket framing constants ───────────────────────────────────────────────
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


# ── WebSocket helpers ─────────────────────────────────────────────────────────

def _ws_handshake(conn: socket.socket) -> bool:
    """
    Read the HTTP upgrade request from the client and send a 101 Switching
    Protocols response.  Returns True on success, False on failure.
    """
    try:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return False
            data += chunk

        # Parse headers line by line
        headers_raw = data.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
        headers = {}
        for line in headers_raw.split("\r\n")[1:]:  # skip request line
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        ws_key = headers.get("sec-websocket-key", "")
        if not ws_key:
            return False

        # Compute Sec-WebSocket-Accept
        accept = base64.b64encode(
            hashlib.sha1((ws_key + _WS_MAGIC).encode("utf-8")).digest()
        ).decode("utf-8")

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        conn.sendall(response.encode("utf-8"))
        return True
    except Exception:
        _log(f"Handshake error: {traceback.format_exc()}")
        return False


def _recv_exactly(conn: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from the socket, or raise ConnectionError."""
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed mid-read")
        buf += chunk
    return buf


def _ws_recv_frame(conn: socket.socket):
    """
    Read one WebSocket frame per RFC 6455.

    Returns:
      (opcode, payload_text) for text frames
      (opcode, None)         for ping/pong/close
    Raises ConnectionError on socket close or protocol error.
    """
    header = _recv_exactly(conn, 2)
    fin_and_op = header[0]
    mask_and_len = header[1]

    opcode = fin_and_op & 0x0F
    masked = bool(mask_and_len & 0x80)
    payload_len = mask_and_len & 0x7F

    if payload_len == 126:
        payload_len = struct.unpack("!H", _recv_exactly(conn, 2))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", _recv_exactly(conn, 8))[0]

    masking_key = b""
    if masked:
        masking_key = _recv_exactly(conn, 4)

    payload = _recv_exactly(conn, payload_len)

    if masked:
        payload = bytes(b ^ masking_key[i % 4] for i, b in enumerate(payload))

    if opcode == _OP_TEXT:
        return opcode, payload.decode("utf-8", errors="replace")
    return opcode, None


def _ws_send_frame(conn: socket.socket, text: str):
    """
    Send an unmasked text WebSocket frame.  Server→client frames are never
    masked per RFC 6455 §5.1.
    """
    payload = text.encode("utf-8")
    length = len(payload)

    # FIN=1, RSV=0, opcode=0x1 (text)
    header = bytearray([0x81])

    if length <= 125:
        header.append(length)
    elif length <= 65535:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)

    conn.sendall(bytes(header) + payload)


def _ws_send_close(conn: socket.socket):
    """Send a WebSocket close frame."""
    try:
        conn.sendall(bytes([0x88, 0x00]))  # FIN + close opcode, no payload
    except Exception:
        pass


# ── Connection handler ────────────────────────────────────────────────────────

def _handle_connection(conn: socket.socket):
    """
    Handle one full WebSocket client lifecycle: handshake → message loop → close.
    Runs on the server background thread.
    """
    try:
        if not _ws_handshake(conn):
            _log("WebSocket handshake failed")
            return
        _log("Client connected")

        while not _stop_event.is_set():
            try:
                conn.settimeout(2.0)
                opcode, text = _ws_recv_frame(conn)
            except socket.timeout:
                continue
            except ConnectionError:
                break
            except Exception:
                _log(f"Frame read error: {traceback.format_exc()}")
                break

            if opcode == _OP_CLOSE:
                _ws_send_close(conn)
                break

            if opcode == _OP_PING:
                # Respond with pong, same payload (but we dropped the payload above — send empty)
                try:
                    conn.sendall(bytes([0x8A, 0x00]))
                except Exception:
                    pass
                continue

            if opcode != _OP_TEXT or text is None:
                continue

            # Parse envelope
            try:
                envelope = json.loads(text)
                msg_type = envelope.get("type", "")
                req_id = envelope.get("request_id", "")
                payload = envelope.get("payload", {})
            except Exception as e:
                _log(f"Bad JSON envelope: {e}")
                continue

            if not req_id:
                _log("Message missing request_id, skipping")
                continue

            # Hand off to UI thread via Fusion custom event
            _work_queue.put((req_id, msg_type, payload))
            try:
                _app.fireCustomEvent(BRIDGE_EVENT_ID, json.dumps({"request_id": req_id}))
            except Exception as e:
                _log(f"fireCustomEvent failed: {e}")
                _result_queue.put({
                    "request_id": req_id,
                    "ok": False,
                    "data": {},
                    "error": f"Failed to dispatch to UI thread: {e}",
                })

            # Wait for the UI thread to finish processing
            result = None
            deadline = REQUEST_TIMEOUT_S
            import time
            t_start = time.monotonic()
            while time.monotonic() - t_start < deadline:
                try:
                    candidate = _result_queue.get(timeout=1.0)
                    if candidate.get("request_id") == req_id:
                        result = candidate
                        break
                    else:
                        # Not our result — put it back and keep waiting
                        _result_queue.put(candidate)
                except queue.Empty:
                    continue

            if result is None:
                result = {
                    "request_id": req_id,
                    "ok": False,
                    "data": {},
                    "error": "Timeout waiting for Fusion UI thread response.",
                }

            try:
                response_text = json.dumps({
                    "request_id": result.get("request_id", req_id),
                    "ok": result.get("ok", False),
                    "data": result.get("data", {}),
                    "error": result.get("error"),
                })
                _ws_send_frame(conn, response_text)
            except Exception as e:
                _log(f"Failed to send response: {e}")

    except Exception:
        _log(f"Connection handler error: {traceback.format_exc()}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        _log("Client disconnected")


# ── Server loop ───────────────────────────────────────────────────────────────

def _run_server():
    """
    Background thread: TCP server loop.
    Accepts one connection at a time (MCP server is the only client).
    """
    srv = None
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", BRIDGE_PORT))
        srv.listen(1)
        srv.settimeout(1.0)
        _log(f"Listening on port {BRIDGE_PORT}")

        while not _stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except Exception:
                if not _stop_event.is_set():
                    _log(f"Accept error: {traceback.format_exc()}")
                break

            _handle_connection(conn)

    except Exception:
        _log(f"Server error: {traceback.format_exc()}")
    finally:
        if srv:
            try:
                srv.close()
            except Exception:
                pass
        _log("Server thread stopped")


# ── Cleanup script builder ────────────────────────────────────────────────────

def _build_cleanup_script() -> str:
    """
    Return a Python script string that deletes all features, sketches, and
    bodies from the active Fusion design.  Ported verbatim from server.py's
    /reset route handler.
    """
    return """import adsk.core, adsk.fusion, traceback

def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        design = app.activeProduct
        root = design.rootComponent

        # Collect items to delete (safer than deleting in a loop)
        features_to_delete = []
        for i in range(root.features.count):
            features_to_delete.append(root.features.item(i))

        # Delete features (this will remove associated bodies)
        for feat in features_to_delete:
            try:
                feat.deleteMe()
            except:
                pass  # Skip if already deleted or can't delete

        # Delete remaining sketches
        sketches_to_delete = []
        for i in range(root.sketches.count):
            sketches_to_delete.append(root.sketches.item(i))

        for sketch in sketches_to_delete:
            try:
                sketch.deleteMe()
            except:
                pass

        # Delete any remaining bodies
        bodies_to_delete = []
        for i in range(root.bRepBodies.count):
            bodies_to_delete.append(root.bRepBodies.item(i))

        for body in bodies_to_delete:
            try:
                body.deleteMe()
            except:
                pass

    except Exception as e:
        if ui:
            ui.messageBox('Reset Error: {}'.format(str(e)))
"""


# ── Screenshot helper ─────────────────────────────────────────────────────────

def _capture_screenshot() -> str:
    """Capture the active Fusion viewport and return a base64-encoded PNG."""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "fusion_mcp_screenshot.png")
    viewport = _app.activeViewport
    viewport.saveAsImageFile(tmp, 0, 0)  # 0, 0 = use current viewport dimensions
    with open(tmp, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ── Fusion custom event handler (UI thread) ───────────────────────────────────

class BridgeEventHandler(adsk.core.CustomEventHandler):
    """
    Runs on the Fusion UI thread.  Dequeues work items, calls runtime
    functions, and posts results back to the background thread via
    _result_queue.
    """

    def notify(self, args):
        req_id = None
        try:
            # The work item was placed on _work_queue just before fireCustomEvent,
            # so it is always available nowait.
            req_id, msg_type, payload = _work_queue.get_nowait()

            if msg_type == MSG_EXECUTE_SCRIPT:
                if _runtime is None:
                    raise RuntimeError("runtime module is unavailable")
                result = _runtime.execute_wrapped_script(payload.get("script", ""))
                _result_queue.put({
                    "request_id": req_id,
                    "ok": result.get("ok", False),
                    "data": result,
                    "error": result.get("error"),
                })

            elif msg_type == MSG_GET_STATE:
                if _runtime is None:
                    raise RuntimeError("runtime module is unavailable")
                state, selection = _runtime.capture_and_store_current_context()
                _result_queue.put({
                    "request_id": req_id,
                    "ok": True,
                    "data": {
                        "state": state,
                        "selection": selection,
                    },
                    "error": None,
                })

            elif msg_type == MSG_RESET:
                if _runtime is None:
                    raise RuntimeError("runtime module is unavailable")
                cleanup_script = _build_cleanup_script()
                result = _runtime.execute_wrapped_script(cleanup_script)
                _result_queue.put({
                    "request_id": req_id,
                    "ok": result.get("ok", False),
                    "data": {"state": result.get("state", {})},
                    "error": result.get("error"),
                })

            elif msg_type == MSG_TAKE_SCREENSHOT:
                image_b64 = _capture_screenshot()
                _result_queue.put({
                    "request_id": req_id,
                    "ok": True,
                    "data": {
                        "image_base64": image_b64,
                        "mime_type": "image/png",
                    },
                    "error": None,
                })

            else:
                _result_queue.put({
                    "request_id": req_id,
                    "ok": False,
                    "data": {},
                    "error": f"Unknown message type: {msg_type!r}",
                })

        except queue.Empty:
            # Should not happen — work was placed before event was fired.
            _log("BridgeEventHandler: work_queue was empty (unexpected)")
            if req_id:
                _result_queue.put({
                    "request_id": req_id,
                    "ok": False,
                    "data": {},
                    "error": "Internal error: work queue was empty",
                })
        except Exception as exc:
            _log(f"BridgeEventHandler error: {traceback.format_exc()}")
            if req_id:
                _result_queue.put({
                    "request_id": req_id,
                    "ok": False,
                    "data": {},
                    "error": str(exc),
                })


# ── Public API ────────────────────────────────────────────────────────────────

def start_bridge(app, ui):
    """
    Start the WebSocket bridge.  Called from auto_runner.py run().
    Must be called on the UI thread.
    """
    global _app, _custom_event, _event_handler, _work_queue, _result_queue
    global _stop_event, _server_thread

    _app = app

    # Register the custom event that lets the background thread trigger UI-thread work
    try:
        _custom_event = app.registerCustomEvent(BRIDGE_EVENT_ID)
    except Exception as e:
        _log(f"registerCustomEvent failed: {e}")
        raise

    # Create and attach the event handler
    _event_handler = BridgeEventHandler()
    _custom_event.add(_event_handler)

    # Create the inter-thread queues
    _work_queue = queue.Queue()
    _result_queue = queue.Queue()

    # Start the background TCP/WebSocket server thread
    _stop_event = threading.Event()
    _server_thread = threading.Thread(target=_run_server, daemon=True, name="MCPBridgeServer")
    _server_thread.start()

    _log(f"Bridge started on port {BRIDGE_PORT}")


def stop_bridge():
    """
    Stop the WebSocket bridge.  Called from auto_runner.py stop().
    Must be called on the UI thread.
    """
    global _app, _custom_event, _event_handler, _server_thread, _stop_event

    # Signal the server loop to exit
    if _stop_event is not None:
        _stop_event.set()

    # Unregister the custom event (must happen on UI thread)
    if _app is not None and _custom_event is not None:
        try:
            _app.unregisterCustomEvent(BRIDGE_EVENT_ID)
        except Exception as e:
            _log(f"unregisterCustomEvent failed (ignored): {e}")
        _custom_event = None
        _event_handler = None

    # Wait for the server thread to finish
    if _server_thread is not None and _server_thread.is_alive():
        _server_thread.join(timeout=5.0)
        _server_thread = None

    _log("Bridge stopped")
