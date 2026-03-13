"""Tests for the new pipeline: intent extraction, selection validation, and code generation."""

import unittest
from unittest.mock import MagicMock, patch

from intent_extractor import IntentResult, extract_intent, ALLOWED_FAMILIES
from selection_validator import validate_intent
from fusion_api_knowledge import get_cards_for_family, find_api_issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_intent(
    operation_family="extrude",
    human_intent="Extrude the sketch 5 cm",
    params=None,
    required_selections=None,
):
    return IntentResult(
        operation_family=operation_family,
        human_intent=human_intent,
        params=params or {},
        required_selections=required_selections or [],
    )


def _face_selection():
    return {"count": 1, "items": [{"kind": "face"}]}


def _edge_selection():
    return {"count": 1, "items": [{"kind": "edge"}]}


def _body_selection():
    return {"count": 1, "items": [{"kind": "body"}]}


def _no_selection():
    return {"count": 0, "items": []}


# ---------------------------------------------------------------------------
# Test: IntentResult structure
# ---------------------------------------------------------------------------

class TestIntentResult(unittest.TestCase):
    def test_all_families_are_in_allowed_set(self):
        expected = {
            "sketch", "extrude", "revolve", "sweep",
            "fillet_chamfer", "hole", "shell", "pattern",
            "mirror", "boolean", "transform", "unknown",
        }
        self.assertEqual(ALLOWED_FAMILIES, expected)

    def test_intent_result_defaults(self):
        intent = IntentResult(operation_family="extrude", human_intent="test")
        self.assertEqual(intent.params, {})
        self.assertEqual(intent.required_selections, [])
        self.assertEqual(intent.raw, {})


# ---------------------------------------------------------------------------
# Test: Intent extraction via mocked LLM
# ---------------------------------------------------------------------------

class TestExtractIntent(unittest.TestCase):
    def _mock_client(self, json_response):
        """Build a mock OpenAI client that returns json_response as text."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json_response
        mock_client.chat.completions.create.return_value = mock_response
        return mock_client

    def test_hole_intent_extracted(self):
        payload = (
            '{"operation_family":"hole","human_intent":"Drill a 5mm hole on the selected face",'
            '"params":{"diameter_cm":0.5},"required_selections":["face"]}'
        )
        client = self._mock_client(payload)
        result = extract_intent("add a 5mm hole", "Bodies: 1", "1 face selected", client, "gpt-4o")
        self.assertEqual(result.operation_family, "hole")
        self.assertIn("face", result.required_selections)
        self.assertAlmostEqual(result.params.get("diameter_cm"), 0.5)

    def test_fillet_intent_extracted(self):
        payload = (
            '{"operation_family":"fillet_chamfer","human_intent":"Add a 2mm fillet to all top edges",'
            '"params":{"radius_cm":0.2},"required_selections":["edge"]}'
        )
        client = self._mock_client(payload)
        result = extract_intent("fillet all top edges 2mm", "Bodies: 1", "No selection", client, "gpt-4o")
        self.assertEqual(result.operation_family, "fillet_chamfer")
        self.assertAlmostEqual(result.params.get("radius_cm"), 0.2)

    def test_unknown_family_falls_back(self):
        payload = '{"operation_family":"gibberish","human_intent":"do something","params":{},"required_selections":[]}'
        client = self._mock_client(payload)
        result = extract_intent("write me a poem", "Bodies: 0", "No selection", client, "gpt-4o")
        self.assertEqual(result.operation_family, "unknown")

    def test_missing_json_raises(self):
        client = self._mock_client("Sorry, I cannot help with that.")
        with self.assertRaises(ValueError):
            extract_intent("test", "", "", client, "gpt-4o")


# ---------------------------------------------------------------------------
# Test: Selection validator — selection requirements
# ---------------------------------------------------------------------------

class TestSelectionValidatorSelections(unittest.TestCase):
    def test_hole_without_face_fails(self):
        intent = _make_intent("hole", required_selections=["face"])
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(any("face" in i.lower() for i in issues))

    def test_hole_with_face_passes(self):
        intent = _make_intent("hole", required_selections=["face"])
        issues = validate_intent(intent, _face_selection())
        self.assertEqual(issues, [])

    def test_shell_without_face_fails(self):
        intent = _make_intent("shell", required_selections=["face"])
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(any("face" in i.lower() for i in issues))

    def test_fillet_chamfer_needing_edge_with_edge_selection_passes(self):
        intent = _make_intent("fillet_chamfer", required_selections=["edge"])
        issues = validate_intent(intent, _edge_selection())
        self.assertEqual(issues, [])

    def test_fillet_chamfer_needing_edge_with_no_selection_fails(self):
        intent = _make_intent("fillet_chamfer", required_selections=["edge"])
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(len(issues) > 0)

    def test_fillet_chamfer_with_no_required_selection_passes(self):
        # "fillet all edges" — user doesn't need to select anything
        intent = _make_intent("fillet_chamfer", required_selections=[])
        issues = validate_intent(intent, _no_selection())
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Test: Selection validator — dimension checks
# ---------------------------------------------------------------------------

class TestSelectionValidatorDimensions(unittest.TestCase):
    def test_zero_depth_fails(self):
        intent = _make_intent("hole", params={"diameter_cm": 0.5, "depth_cm": 0})
        issues = validate_intent(intent, _face_selection())
        self.assertTrue(any("depth_cm" in i for i in issues))

    def test_negative_dimension_fails(self):
        intent = _make_intent("extrude", params={"height_cm": -2.0})
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(any("height_cm" in i for i in issues))

    def test_excessively_large_dimension_warns(self):
        intent = _make_intent("extrude", params={"height_cm": 1000.0})
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(any("unusually large" in i for i in issues))

    def test_valid_dimensions_pass(self):
        intent = _make_intent("extrude", params={"height_cm": 5.0})
        issues = validate_intent(intent, _no_selection())
        self.assertEqual(issues, [])


# ---------------------------------------------------------------------------
# Test: Unknown family is rejected
# ---------------------------------------------------------------------------

class TestSelectionValidatorUnknown(unittest.TestCase):
    def test_unknown_family_rejected(self):
        intent = _make_intent("unknown")
        issues = validate_intent(intent, _no_selection())
        self.assertTrue(len(issues) > 0)
        self.assertTrue(any("interpret" in i.lower() for i in issues))


# ---------------------------------------------------------------------------
# Test: get_cards_for_family — correct API cards injected
# ---------------------------------------------------------------------------

class TestGetCardsForFamily(unittest.TestCase):
    def test_hole_family_gets_hole_cards(self):
        cards = get_cards_for_family("hole")
        combined = "\n".join(cards)
        self.assertIn("hole", combined.lower())

    def test_fillet_chamfer_family_gets_fillet_cards(self):
        cards = get_cards_for_family("fillet_chamfer")
        combined = "\n".join(cards)
        self.assertIn("fillet", combined.lower())

    def test_revolve_family_gets_revolve_cards(self):
        cards = get_cards_for_family("revolve")
        combined = "\n".join(cards)
        self.assertIn("revolve", combined.lower())

    def test_pattern_family_gets_pattern_cards(self):
        cards = get_cards_for_family("pattern")
        combined = "\n".join(cards)
        self.assertIn("pattern", combined.lower())

    def test_unknown_family_returns_empty(self):
        cards = get_cards_for_family("unknown")
        self.assertEqual(cards, [])

    def test_mirror_family_gets_mirror_cards(self):
        cards = get_cards_for_family("mirror")
        combined = "\n".join(cards)
        self.assertIn("mirror", combined.lower())


if __name__ == "__main__":
    unittest.main()
