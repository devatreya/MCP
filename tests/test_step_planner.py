"""Tests for step_planner.py — mocked LLM, no real API calls."""

import json
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from step_planner import plan_steps, StepPlan, _STEP_FAMILIES
from intent_extractor import ALLOWED_FAMILIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(json_response: str):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json_response
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


def _plan(json_response: str):
    client = _make_client(json_response)
    return plan_steps(
        human_intent="Make an open-top electronics box",
        params={"width_cm": 8.0, "height_cm": 4.0},
        fusion_state_text="Bodies: 0",
        client=client,
        model="gpt-4o",
    )


# ---------------------------------------------------------------------------
# Test: _STEP_FAMILIES
# ---------------------------------------------------------------------------

class TestStepFamilies(unittest.TestCase):
    def test_composite_excluded(self):
        self.assertNotIn("composite", _STEP_FAMILIES)

    def test_unknown_excluded(self):
        self.assertNotIn("unknown", _STEP_FAMILIES)

    def test_all_single_op_families_included(self):
        expected = ALLOWED_FAMILIES - {"composite", "unknown"}
        self.assertEqual(_STEP_FAMILIES, expected)


# ---------------------------------------------------------------------------
# Test: basic plan parsing
# ---------------------------------------------------------------------------

class TestPlanSteps(unittest.TestCase):
    _VALID_PAYLOAD = json.dumps({
        "steps": [
            {"step_id": "step_1", "family": "extrude",       "description": "Create 80×60×40mm box",    "params": {"width_cm": 8.0}, "requires_selection": False, "selection_prompt": ""},
            {"step_id": "step_2", "family": "shell",          "description": "Shell to 2.5mm walls",     "params": {"thickness_cm": 0.25}, "requires_selection": True,  "selection_prompt": "Select the face to open"},
            {"step_id": "step_3", "family": "fillet_chamfer", "description": "Fillet all edges 2mm",     "params": {"radius_cm": 0.2}, "requires_selection": False, "selection_prompt": ""},
            {"step_id": "step_4", "family": "hole",           "description": "Drill 4 corner M3 holes",  "params": {"diameter_cm": 0.32}, "requires_selection": True,  "selection_prompt": "Select the bottom face"},
        ]
    })

    def test_returns_list_of_step_plans(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertIsInstance(steps, list)
        self.assertEqual(len(steps), 4)
        for s in steps:
            self.assertIsInstance(s, StepPlan)

    def test_step_ids_preserved(self):
        steps = _plan(self._VALID_PAYLOAD)
        ids = [s.step_id for s in steps]
        self.assertEqual(ids, ["step_1", "step_2", "step_3", "step_4"])

    def test_families_correct(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertEqual(steps[0].family, "extrude")
        self.assertEqual(steps[1].family, "shell")
        self.assertEqual(steps[2].family, "fillet_chamfer")
        self.assertEqual(steps[3].family, "hole")

    def test_requires_selection_flags(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertFalse(steps[0].requires_selection)  # extrude
        self.assertTrue(steps[1].requires_selection)   # shell
        self.assertFalse(steps[2].requires_selection)  # fillet all edges
        self.assertTrue(steps[3].requires_selection)   # hole

    def test_selection_prompts(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertEqual(steps[0].selection_prompt, "")
        self.assertEqual(steps[1].selection_prompt, "Select the face to open")
        self.assertEqual(steps[3].selection_prompt, "Select the bottom face")

    def test_params_preserved(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertAlmostEqual(steps[0].params.get("width_cm"), 8.0)
        self.assertAlmostEqual(steps[1].params.get("thickness_cm"), 0.25)
        self.assertAlmostEqual(steps[2].params.get("radius_cm"), 0.2)

    def test_descriptions_preserved(self):
        steps = _plan(self._VALID_PAYLOAD)
        self.assertIn("80", steps[0].description)
        self.assertIn("2.5", steps[1].description)


# ---------------------------------------------------------------------------
# Test: robustness / edge cases
# ---------------------------------------------------------------------------

class TestPlanStepsEdgeCases(unittest.TestCase):
    def test_unknown_family_falls_back_to_unknown(self):
        payload = json.dumps({
            "steps": [
                {"step_id": "step_1", "family": "teleportation", "description": "Warp body",
                 "params": {}, "requires_selection": False, "selection_prompt": ""}
            ]
        })
        steps = _plan(payload)
        self.assertEqual(steps[0].family, "unknown")

    def test_missing_params_defaults_to_empty_dict(self):
        payload = json.dumps({
            "steps": [
                {"step_id": "step_1", "family": "extrude", "description": "Extrude",
                 "requires_selection": False, "selection_prompt": ""}
            ]
        })
        steps = _plan(payload)
        self.assertEqual(steps[0].params, {})

    def test_missing_step_id_auto_generated(self):
        payload = json.dumps({
            "steps": [
                {"family": "extrude", "description": "Extrude",
                 "params": {}, "requires_selection": False, "selection_prompt": ""}
            ]
        })
        steps = _plan(payload)
        self.assertEqual(steps[0].step_id, "step_1")

    def test_json_wrapped_in_markdown_fence_still_parsed(self):
        inner = json.dumps({
            "steps": [
                {"step_id": "step_1", "family": "extrude", "description": "Extrude",
                 "params": {}, "requires_selection": False, "selection_prompt": ""}
            ]
        })
        payload = f"```json\n{inner}\n```"
        steps = _plan(payload)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].family, "extrude")

    def test_empty_steps_list_raises(self):
        payload = json.dumps({"steps": []})
        with self.assertRaises(ValueError):
            _plan(payload)

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            _plan("Sorry, I cannot plan this operation.")

    def test_requires_selection_defaults_false_when_missing(self):
        payload = json.dumps({
            "steps": [
                {"step_id": "step_1", "family": "extrude", "description": "Extrude",
                 "params": {}}
            ]
        })
        steps = _plan(payload)
        self.assertFalse(steps[0].requires_selection)

    def test_single_step_plan(self):
        payload = json.dumps({
            "steps": [
                {"step_id": "step_1", "family": "revolve", "description": "Revolve profile",
                 "params": {"angle_deg": 360}, "requires_selection": False, "selection_prompt": ""}
            ]
        })
        steps = _plan(payload)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].family, "revolve")


if __name__ == "__main__":
    unittest.main()
