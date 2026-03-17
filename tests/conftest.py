# Shared pytest fixtures for the MCP tool-handler test suite.
# Referenced by test_mcp_tools.py and test_bridge_protocol.py.

import copy
import pytest
from unittest.mock import MagicMock

from bridge_config import (
    MSG_EXECUTE_SCRIPT,
    MSG_GET_STATE,
    MSG_RESET,
    MSG_TAKE_SCREENSHOT,
)
from intent_extractor import IntentResult


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

SAMPLE_STATE = {
    "timestamp": "2026-03-17T12:00:00",
    "units": "cm",
    "body_count": 1,
    "sketch_count": 0,
    "feature_count": 1,
    "bodies": [
        {
            "index": 0,
            "name": "Body1",
            "face_count": 6,
            "edge_count": 12,
            "volume_cm3": 125.0,
            "bbox": {
                "min": {"x": 0, "y": 0, "z": 0},
                "max": {"x": 5, "y": 5, "z": 5},
                "size": {"x": 5, "y": 5, "z": 5},
                "center": {"x": 2.5, "y": 2.5, "z": 2.5},
            },
        }
    ],
    "origin_planes": [
        {
            "name": "XY Plane",
            "code": "rootComp.xYConstructionPlane",
            "label": "Top",
        }
    ],
}

EMPTY_STATE = {
    "timestamp": "2026-03-17T12:00:00",
    "units": "cm",
    "body_count": 0,
    "sketch_count": 0,
    "feature_count": 0,
    "bodies": [],
    "origin_planes": [],
}

SAMPLE_SELECTION = {"count": 0, "items": []}

SAMPLE_INTENT = IntentResult(
    operation_family="extrude",
    human_intent="Create a 5cm cube",
    params={"size_cm": 5.0},
    required_selections=[],
    raw={},
)

SAMPLE_COMPOSITE_INTENT = IntentResult(
    operation_family="composite",
    human_intent="Create a box with a hole on top",
    params={"width_cm": 5.0, "height_cm": 5.0, "hole_diameter_cm": 1.0},
    required_selections=[],
    raw={},
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_state():
    """Return a fresh copy of EMPTY_STATE."""
    return copy.deepcopy(EMPTY_STATE)


@pytest.fixture
def sample_state():
    """Return a fresh deep copy of SAMPLE_STATE."""
    return copy.deepcopy(SAMPLE_STATE)


@pytest.fixture
def mock_openai_client():
    """Return a MagicMock that behaves like an OpenAI chat completions client."""
    client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"operation_family": "extrude", "human_intent": "Create a 5cm cube",'
        ' "params": {"size_cm": 5.0}, "required_selections": []}'
    )
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_response
    return client


def _make_success_bridge():
    """Build a MockBridgeClient whose handler returns success for all message types.

    get_state returns EMPTY_STATE on the first call and SAMPLE_STATE on every
    subsequent call, simulating the state before / after script execution.
    """
    from bridge_client import MockBridgeClient

    call_count = {"n": 0}

    def handler(msg_type, payload):
        if msg_type == MSG_GET_STATE:
            call_count["n"] += 1
            state = EMPTY_STATE if call_count["n"] == 1 else SAMPLE_STATE
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(state), "selection": SAMPLE_SELECTION},
                "error": None,
            }
        if msg_type == MSG_EXECUTE_SCRIPT:
            return {
                "ok": True,
                "data": {
                    "state": copy.deepcopy(SAMPLE_STATE),
                    "selection": SAMPLE_SELECTION,
                    "ok": True,
                },
                "error": None,
            }
        if msg_type == MSG_RESET:
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(EMPTY_STATE)},
                "error": None,
            }
        if msg_type == MSG_TAKE_SCREENSHOT:
            return {
                "ok": True,
                "data": {"image_base64": "abc123", "mime_type": "image/png"},
                "error": None,
            }
        return {"ok": False, "data": {}, "error": f"Unknown message type: {msg_type}"}

    return MockBridgeClient(handler=handler)


def _make_failure_bridge():
    """Build a MockBridgeClient whose execute_script always returns ok=False."""
    from bridge_client import MockBridgeClient

    def handler(msg_type, payload):
        if msg_type == MSG_GET_STATE:
            return {
                "ok": True,
                "data": {
                    "state": copy.deepcopy(SAMPLE_STATE),
                    "selection": SAMPLE_SELECTION,
                },
                "error": None,
            }
        if msg_type == MSG_EXECUTE_SCRIPT:
            return {
                "ok": False,
                "data": {},
                "error": "Script execution failed in Fusion 360",
            }
        if msg_type == MSG_RESET:
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(EMPTY_STATE)},
                "error": None,
            }
        if msg_type == MSG_TAKE_SCREENSHOT:
            return {
                "ok": False,
                "data": {},
                "error": "Screenshot capture failed",
            }
        return {"ok": False, "data": {}, "error": f"Unknown message type: {msg_type}"}

    return MockBridgeClient(handler=handler)


@pytest.fixture
def mock_bridge_success():
    """MockBridgeClient that returns success for all message types."""
    return _make_success_bridge()


@pytest.fixture
def mock_bridge_execute_failure():
    """MockBridgeClient whose execute_script and take_screenshot always fail."""
    return _make_failure_bridge()


@pytest.fixture
def handler_ctx(mock_openai_client, mock_bridge_success):
    """A HandlerContext wired with a mock OpenAI client and a success bridge."""
    from tool_handlers import HandlerContext

    return HandlerContext(
        client=mock_openai_client,
        model="gpt-4o",
        fallback_model="gpt-4o-mini",
        bridge=mock_bridge_success,
        conversation_history={},
    )
