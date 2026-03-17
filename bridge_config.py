# Shared constants for the MCP server ↔ Fusion 360 WebSocket bridge.
# Both sides import from here — change once, applies everywhere.

# --- Transport ---
BRIDGE_PORT             = 8765
RECONNECT_INTERVAL_S    = 2
MAX_RECONNECT_ATTEMPTS  = 10
REQUEST_TIMEOUT_S       = 120

# --- Message types (Contract 1) ---
# Request envelope:
#   { "type": MSG_*, "request_id": "<uuid4>", "payload": { ... } }
# Response envelope:
#   { "request_id": "<uuid4>", "ok": true|false, "data": { ... }, "error": "str|null" }

MSG_EXECUTE_SCRIPT  = "execute_script"   # payload: {"script": str}
MSG_GET_STATE       = "get_state"        # payload: {}
MSG_RESET           = "reset"            # payload: {}
MSG_TAKE_SCREENSHOT = "take_screenshot"  # payload: {}

# --- MCP tool names (Contract 2) ---
TOOL_CREATE_GEOMETRY  = "create_geometry"
TOOL_MODIFY_GEOMETRY  = "modify_geometry"
TOOL_GET_DESIGN_STATE = "get_design_state"
TOOL_VERIFY_GEOMETRY  = "verify_geometry"
TOOL_RESET_DESIGN     = "reset_design"
TOOL_TAKE_SCREENSHOT  = "take_screenshot"
