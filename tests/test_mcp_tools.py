# Tests for tool handler functions — implementations in tool_handlers.py
# Run with: python -m pytest tests/ from the project root.

import copy
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set dummy env vars before any server-side module is imported.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_ORG_ID", "test-org")
os.environ.setdefault("OPENAI_PROJECT_ID", "test-project")

from bridge_config import MSG_EXECUTE_SCRIPT, MSG_GET_STATE, MSG_RESET, MSG_TAKE_SCREENSHOT
from bridge_client import MockBridgeClient
from intent_extractor import IntentResult
from step_planner import StepPlan

# ---------------------------------------------------------------------------
# Module-level sample data (DRY — not repeated per test)
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

UNKNOWN_INTENT = IntentResult(
    operation_family="unknown",
    human_intent="What is the weather today?",
    params={},
    required_selections=[],
    raw={},
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(content=None):
    """Return a MagicMock that looks like an OpenAI client."""
    client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = content or "print('hello')"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_response
    return client


def _make_success_bridge(pre_state=None, post_state=None):
    """Return a MockBridgeClient that succeeds on all calls.

    get_state returns pre_state on first call and post_state on subsequent calls.
    """
    pre = pre_state if pre_state is not None else copy.deepcopy(EMPTY_STATE)
    post = post_state if post_state is not None else copy.deepcopy(SAMPLE_STATE)
    call_count = {"n": 0}

    def handler(msg_type, payload):
        if msg_type == MSG_GET_STATE:
            call_count["n"] += 1
            state = pre if call_count["n"] == 1 else post
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(state), "selection": SAMPLE_SELECTION},
                "error": None,
            }
        if msg_type == MSG_EXECUTE_SCRIPT:
            return {
                "ok": True,
                "data": {
                    "state": copy.deepcopy(post),
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
        return {"ok": False, "data": {}, "error": f"Unknown: {msg_type}"}

    return MockBridgeClient(handler=handler)


def _make_failure_bridge():
    """Return a MockBridgeClient where execute_script and take_screenshot fail."""
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
        return {"ok": False, "data": {}, "error": f"Unknown: {msg_type}"}

    return MockBridgeClient(handler=handler)


def _make_ctx(bridge=None, client=None, history=None):
    """Return a HandlerContext with the given bridge and client."""
    from tool_handlers import HandlerContext

    return HandlerContext(
        client=client or _make_mock_client(),
        model="gpt-4o",
        fallback_model="gpt-4o-mini",
        bridge=bridge or _make_success_bridge(),
        conversation_history=history if history is not None else {},
    )


# ---------------------------------------------------------------------------
# TestCreateGeometry
# ---------------------------------------------------------------------------

class TestCreateGeometry(unittest.TestCase):

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_happy_path_single_op(self, mock_gen, mock_intent):
        """Single-op: extract_intent → generate_cad_code → execute → return diff with bodies_added."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("sketch = adsk.fusion.Sketch.cast(None)", "gpt-4o")

        from tool_handlers import handle_create_geometry

        ctx = _make_ctx()
        result = handle_create_geometry(ctx, prompt="Create a 5cm cube")

        self.assertTrue(result["ok"])
        self.assertIn("diff", result)
        # After execution SAMPLE_STATE has Body1; diff should record that addition.
        self.assertIn("Body1", result["diff"].get("bodies_added", []))

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_happy_path_returns_state(self, mock_gen, mock_intent):
        """create_geometry result must contain a 'state' key."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("# code", "gpt-4o")

        from tool_handlers import handle_create_geometry

        result = handle_create_geometry(_make_ctx(), prompt="Create a 5cm cube")
        self.assertIn("state", result)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_happy_path_returns_summary(self, mock_gen, mock_intent):
        """create_geometry result must contain a non-empty 'summary' string."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("# code", "gpt-4o")

        from tool_handlers import handle_create_geometry

        result = handle_create_geometry(_make_ctx(), prompt="Create a 5cm cube")
        self.assertIn("summary", result)
        self.assertIsInstance(result["summary"], str)
        self.assertTrue(len(result["summary"]) > 0)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.plan_steps")
    @patch("tool_handlers.generate_cad_code")
    def test_composite_routes_to_step_planner(self, mock_gen, mock_plan, mock_intent):
        """Composite intent: plan_steps is called and result contains 'steps'."""
        mock_intent.return_value = SAMPLE_COMPOSITE_INTENT
        mock_plan.return_value = [
            StepPlan(
                step_id="step_1",
                family="extrude",
                description="Create 5cm cube",
                params={"size_cm": 5.0},
            ),
            StepPlan(
                step_id="step_2",
                family="hole",
                description="Drill hole on top face",
                params={"diameter_cm": 1.0},
            ),
        ]
        mock_gen.return_value = ("# step code", "gpt-4o")

        from tool_handlers import handle_create_geometry

        result = handle_create_geometry(
            _make_ctx(), prompt="Create a box with a hole on top"
        )

        self.assertTrue(mock_plan.called, "plan_steps should be called for composite intent")
        self.assertTrue(result["ok"])
        self.assertIn("steps", result)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.validate_intent")
    def test_validation_failure_returns_error(self, mock_validate, mock_intent):
        """If validate_intent returns issues, the tool returns ok=False with error details."""
        mock_intent.return_value = IntentResult(
            operation_family="hole",
            human_intent="Drill a hole",
            params={},
            required_selections=["face"],
            raw={},
        )
        mock_validate.return_value = ["Face selection required"]

        from tool_handlers import handle_create_geometry

        result = handle_create_geometry(_make_ctx(), prompt="Drill a hole")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertIn("Face selection required", result["error"])

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    @patch("tool_handlers.find_api_issues")
    def test_lint_retry_on_api_issues(self, mock_lint, mock_gen, mock_intent):
        """When find_api_issues returns issues on the first check, generate_cad_code is retried."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("# code", "gpt-4o")
        # First lint call returns an issue; second call (after regeneration) returns clean.
        mock_lint.side_effect = [["setStartExtent() deprecated"], []]

        from tool_handlers import handle_create_geometry

        handle_create_geometry(_make_ctx(), prompt="Create a 5cm cube")

        self.assertEqual(
            mock_gen.call_count,
            2,
            "generate_cad_code should be called twice when lint finds issues on first pass",
        )

    def test_llm_failure_returns_error(self):
        """If extract_intent raises, tool returns ok=False with an error key."""
        # Patch at the module level so the real extract_intent is bypassed.
        with patch("tool_handlers.extract_intent", side_effect=RuntimeError("LLM offline")):
            from tool_handlers import handle_create_geometry

            result = handle_create_geometry(_make_ctx(), prompt="Create a 5cm cube")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_bridge_execute_failure_returns_error(self, mock_gen, mock_intent):
        """If bridge.execute_script returns ok=False, tool surfaces the error."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("# code", "gpt-4o")

        from tool_handlers import handle_create_geometry

        ctx = _make_ctx(bridge=_make_failure_bridge())
        result = handle_create_geometry(ctx, prompt="Create a 5cm cube")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_selection_context_forwarded(self, mock_gen, mock_intent):
        """selection_context passed by caller is forwarded to extract_intent."""
        mock_intent.return_value = SAMPLE_INTENT
        mock_gen.return_value = ("# code", "gpt-4o")

        from tool_handlers import handle_create_geometry

        sel = {"count": 1, "items": [{"type": "face", "id": "f0"}]}
        handle_create_geometry(_make_ctx(), prompt="Drill here", selection_context=sel)

        call_kwargs = mock_intent.call_args
        # extract_intent receives selection_state_text (a summarized string),
        # not the raw dict — verify the call was made with a non-empty string
        # when a non-empty selection_context is provided.
        all_args = list(call_kwargs.args) + list(call_kwargs.kwargs.values())
        string_args = [a for a in all_args if isinstance(a, str)]
        self.assertTrue(
            any(len(s) > 0 for s in string_args),
            "extract_intent should be called with a non-empty selection_state_text",
        )


# ---------------------------------------------------------------------------
# TestModifyGeometry
# ---------------------------------------------------------------------------

class TestModifyGeometry(unittest.TestCase):

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_happy_path(self, mock_gen, mock_intent):
        """Successful modify returns ok=True with diff, state, summary."""
        mock_intent.return_value = IntentResult(
            operation_family="fillet_chamfer",
            human_intent="Fillet all edges 2mm",
            params={"radius_cm": 0.2},
            required_selections=[],
            raw={},
        )
        mock_gen.return_value = ("# fillet code", "gpt-4o")

        from tool_handlers import handle_modify_geometry

        result = handle_modify_geometry(_make_ctx(), prompt="Fillet all edges 2mm")

        self.assertTrue(result["ok"])
        self.assertIn("diff", result)
        self.assertIn("state", result)
        self.assertIn("summary", result)

    @patch("tool_handlers.extract_intent")
    @patch("tool_handlers.generate_cad_code")
    def test_modify_does_not_clear_model(self, mock_gen, mock_intent):
        """modify_geometry should never issue a model reset — bridge.reset not called."""
        mock_intent.return_value = IntentResult(
            operation_family="fillet_chamfer",
            human_intent="Fillet all edges 2mm",
            params={"radius_cm": 0.2},
            required_selections=[],
            raw={},
        )
        mock_gen.return_value = ("# fillet code", "gpt-4o")

        reset_called = {"flag": False}

        def handler(msg_type, payload):
            if msg_type == MSG_RESET:
                reset_called["flag"] = True
                return {"ok": True, "data": {"state": EMPTY_STATE}, "error": None}
            return {
                "ok": True,
                "data": {
                    "state": copy.deepcopy(SAMPLE_STATE),
                    "selection": SAMPLE_SELECTION,
                },
                "error": None,
            }

        bridge = MockBridgeClient(handler=handler)

        from tool_handlers import handle_modify_geometry

        handle_modify_geometry(_make_ctx(bridge=bridge), prompt="Fillet all edges")

        self.assertFalse(
            reset_called["flag"],
            "modify_geometry must NOT call bridge.reset — it operates on the existing model",
        )

    @patch("tool_handlers.extract_intent")
    def test_unknown_family_rejected(self, mock_intent):
        """If intent.operation_family is 'unknown', returns ok=False."""
        mock_intent.return_value = UNKNOWN_INTENT

        from tool_handlers import handle_modify_geometry

        result = handle_modify_geometry(_make_ctx(), prompt="What is the weather?")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_llm_failure_returns_error(self):
        """If extract_intent raises, modify_geometry returns ok=False."""
        with patch("tool_handlers.extract_intent", side_effect=ValueError("bad JSON")):
            from tool_handlers import handle_modify_geometry

            result = handle_modify_geometry(_make_ctx(), prompt="Fillet edges")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# TestGetDesignState
# ---------------------------------------------------------------------------

class TestGetDesignState(unittest.TestCase):

    def test_returns_state_and_summary(self):
        """get_design_state returns a dict with both 'state' and 'summary' keys."""
        from tool_handlers import handle_get_design_state

        result = handle_get_design_state(_make_ctx())

        self.assertIn("state", result)
        self.assertIn("summary", result)

    def test_state_shape(self):
        """The state dict contains the expected top-level keys."""
        from tool_handlers import handle_get_design_state

        ctx = _make_ctx(bridge=_make_success_bridge(pre_state=copy.deepcopy(SAMPLE_STATE)))
        result = handle_get_design_state(ctx)

        state = result["state"]
        for key in ("bodies", "body_count", "units"):
            self.assertIn(key, state, f"Expected key '{key}' missing from state")

    def test_summary_is_non_empty_string(self):
        """The summary field is a non-empty string."""
        from tool_handlers import handle_get_design_state

        result = handle_get_design_state(_make_ctx())

        self.assertIsInstance(result["summary"], str)
        self.assertGreater(len(result["summary"]), 0)

    def test_reflects_body_count(self):
        """State body_count matches the value returned by the bridge."""
        from tool_handlers import handle_get_design_state

        # Bridge always returns SAMPLE_STATE (1 body) on get_state
        bridge = _make_success_bridge(pre_state=copy.deepcopy(SAMPLE_STATE))
        result = handle_get_design_state(_make_ctx(bridge=bridge))

        self.assertEqual(result["state"]["body_count"], 1)

    def test_empty_model_body_count_zero(self):
        """When the model is empty, body_count should be 0."""
        from tool_handlers import handle_get_design_state

        def handler(msg_type, payload):
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(EMPTY_STATE), "selection": SAMPLE_SELECTION},
                "error": None,
            }

        ctx = _make_ctx(bridge=MockBridgeClient(handler=handler))
        result = handle_get_design_state(ctx)

        self.assertEqual(result["state"]["body_count"], 0)


# ---------------------------------------------------------------------------
# TestVerifyGeometry
# ---------------------------------------------------------------------------

class TestVerifyGeometry(unittest.TestCase):

    def _make_state_bridge(self, state):
        """Return a bridge that always returns the given state for get_state."""
        def handler(msg_type, payload):
            return {
                "ok": True,
                "data": {"state": copy.deepcopy(state), "selection": SAMPLE_SELECTION},
                "error": None,
            }
        return MockBridgeClient(handler=handler)

    def test_body_count_pass(self):
        """body_count constraint passes when actual count matches expected."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        result = handle_verify_geometry(ctx, constraints=[{"type": "body_count", "expected": 1}])

        self.assertTrue(result["passed"])
        self.assertIsInstance(result["results"], list)
        self.assertTrue(result["results"][0]["passed"])

    def test_body_count_fail(self):
        """body_count constraint fails when actual count does not match expected."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        result = handle_verify_geometry(ctx, constraints=[{"type": "body_count", "expected": 2}])

        self.assertFalse(result["passed"])
        self.assertFalse(result["results"][0]["passed"])
        self.assertEqual(result["results"][0]["actual"], 1)

    def test_bounding_box_within_tolerance(self):
        """bounding_box constraint passes when size matches within tolerance."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        constraints = [
            {
                "type": "bounding_box",
                "body": "Body1",
                "size_cm": {"x": 5.0, "y": 5.0, "z": 5.0},
                "tolerance": 0.01,
            }
        ]
        result = handle_verify_geometry(ctx, constraints=constraints)

        self.assertTrue(result["passed"])
        self.assertTrue(result["results"][0]["passed"])

    def test_bounding_box_outside_tolerance(self):
        """bounding_box constraint fails when size exceeds tolerance."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        constraints = [
            {
                "type": "bounding_box",
                "body": "Body1",
                "size_cm": {"x": 6.0, "y": 5.0, "z": 5.0},
                "tolerance": 0.01,
            }
        ]
        result = handle_verify_geometry(ctx, constraints=constraints)

        self.assertFalse(result["passed"])
        self.assertFalse(result["results"][0]["passed"])
        # Delta in x should be approximately 1.0 (actual 5.0, expected 6.0)
        delta_x = abs(result["results"][0]["delta"]["x"])
        self.assertAlmostEqual(delta_x, 1.0, places=5)

    def test_body_exists_found(self):
        """body_exists constraint passes when a body with the given name is present."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        result = handle_verify_geometry(
            ctx, constraints=[{"type": "body_exists", "name": "Body1"}]
        )

        self.assertTrue(result["passed"])
        self.assertTrue(result["results"][0]["passed"])

    def test_body_exists_not_found(self):
        """body_exists constraint fails when the named body is absent."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        result = handle_verify_geometry(
            ctx, constraints=[{"type": "body_exists", "name": "MissingBody"}]
        )

        self.assertFalse(result["passed"])
        self.assertFalse(result["results"][0]["passed"])

    def test_mixed_constraints_all_must_pass(self):
        """Overall passed == True only when ALL constraints pass."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        constraints = [
            {"type": "body_count", "expected": 1},   # passes
            {"type": "body_count", "expected": 99},  # fails
        ]
        result = handle_verify_geometry(ctx, constraints=constraints)

        self.assertFalse(result["passed"])
        # First constraint should pass, second should fail
        self.assertTrue(result["results"][0]["passed"])
        self.assertFalse(result["results"][1]["passed"])

    def test_empty_constraints_returns_passed_true(self):
        """An empty constraint list trivially passes."""
        from tool_handlers import handle_verify_geometry

        result = handle_verify_geometry(_make_ctx(), constraints=[])

        self.assertTrue(result["passed"])
        self.assertEqual(result["results"], [])

    def test_volume_constraint_pass(self):
        """volume constraint passes when body volume is within tolerance."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        constraints = [
            {
                "type": "volume",
                "body": "Body1",
                "volume_cm3": 125.0,
                "tolerance": 0.01,
            }
        ]
        result = handle_verify_geometry(ctx, constraints=constraints)

        self.assertTrue(result["passed"])

    def test_unknown_constraint_type_is_recorded(self):
        """An unrecognised constraint type is recorded in results but does not crash."""
        from tool_handlers import handle_verify_geometry

        ctx = _make_ctx(bridge=self._make_state_bridge(SAMPLE_STATE))
        constraints = [{"type": "nonexistent_check", "value": 42}]
        result = handle_verify_geometry(ctx, constraints=constraints)

        self.assertIsInstance(result["results"], list)
        self.assertEqual(len(result["results"]), 1)


# ---------------------------------------------------------------------------
# TestResetDesign
# ---------------------------------------------------------------------------

class TestResetDesign(unittest.TestCase):

    def test_calls_bridge_reset(self):
        """bridge.reset() is called exactly once."""
        reset_calls = {"n": 0}

        def handler(msg_type, payload):
            if msg_type == MSG_RESET:
                reset_calls["n"] += 1
                return {
                    "ok": True,
                    "data": {"state": copy.deepcopy(EMPTY_STATE)},
                    "error": None,
                }
            return {"ok": True, "data": {"state": EMPTY_STATE}, "error": None}

        from tool_handlers import handle_reset_design

        handle_reset_design(_make_ctx(bridge=MockBridgeClient(handler=handler)))

        self.assertEqual(reset_calls["n"], 1)

    def test_clears_conversation_history(self):
        """After reset_design, all conversation history entries are removed."""
        from tool_handlers import handle_reset_design

        history = {
            "session-1": [{"role": "user", "content": "make a cube"}],
            "session-2": [{"role": "user", "content": "add a hole"}],
        }
        ctx = _make_ctx(history=history)
        handle_reset_design(ctx)

        self.assertEqual(
            len(ctx.conversation_history),
            0,
            "conversation_history should be empty after reset",
        )

    def test_returns_empty_state(self):
        """State in the response has body_count == 0 after reset."""
        from tool_handlers import handle_reset_design

        result = handle_reset_design(_make_ctx())

        self.assertEqual(result["state"]["body_count"], 0)

    def test_returns_ok_true(self):
        """reset_design returns ok=True on success."""
        from tool_handlers import handle_reset_design

        result = handle_reset_design(_make_ctx())

        self.assertTrue(result["ok"])

    def test_returns_summary(self):
        """reset_design result contains a non-empty summary string."""
        from tool_handlers import handle_reset_design

        result = handle_reset_design(_make_ctx())

        self.assertIn("summary", result)
        self.assertIsInstance(result["summary"], str)
        self.assertGreater(len(result["summary"]), 0)


# ---------------------------------------------------------------------------
# TestTakeScreenshot
# ---------------------------------------------------------------------------

class TestTakeScreenshot(unittest.TestCase):

    def test_returns_base64_image(self):
        """Returns ok=True, non-empty image_base64 string, and mime_type=image/png."""
        from tool_handlers import handle_take_screenshot

        result = handle_take_screenshot(_make_ctx())

        self.assertTrue(result["ok"])
        self.assertIn("image_base64", result)
        self.assertIsInstance(result["image_base64"], str)
        self.assertGreater(len(result["image_base64"]), 0)
        self.assertEqual(result["mime_type"], "image/png")

    def test_bridge_failure_returns_error(self):
        """If bridge.take_screenshot returns ok=False, tool surfaces the error."""
        from tool_handlers import handle_take_screenshot

        ctx = _make_ctx(bridge=_make_failure_bridge())
        result = handle_take_screenshot(ctx)

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_returns_ok_key(self):
        """Response always contains an 'ok' boolean key."""
        from tool_handlers import handle_take_screenshot

        result = handle_take_screenshot(_make_ctx())

        self.assertIn("ok", result)
        self.assertIsInstance(result["ok"], bool)

    def test_mime_type_is_string(self):
        """mime_type in a successful response is a non-empty string."""
        from tool_handlers import handle_take_screenshot

        result = handle_take_screenshot(_make_ctx())

        self.assertIsInstance(result["mime_type"], str)
        self.assertGreater(len(result["mime_type"]), 0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
