import unittest

from cad_ir import plan_from_dict
from fusion_api_knowledge import find_api_issues
from plan_compiler import compile_plan_to_code
from plan_generator import generate_plan
from plan_normalizer import normalize_plan
from plan_validator import validate_plan


class StructuredPipelineTests(unittest.TestCase):
    def test_bracket_planner_and_compiler_stay_allowlisted(self):
        plan_dict = generate_plan(
            user_prompt="create a mounting bracket 100x60x60x6 mm with m6 holes",
            fusion_state_text="Bodies: 0",
            selection_state_text="No active selections.",
            client=None,
        )
        plan = normalize_plan(plan_from_dict(plan_dict))
        self.assertEqual(plan.operations[0].op, "create_mounting_bracket")
        issues = validate_plan(plan, selection_context={"count": 0, "items": []})
        self.assertEqual(issues, [])
        code = compile_plan_to_code(plan)
        self.assertEqual(find_api_issues(code), [])

    def test_bracket_validator_rejects_bad_thickness(self):
        plan = normalize_plan(
            plan_from_dict(
                {
                    "version": "v1",
                    "operations": [
                        {
                            "id": "op_1",
                            "op": "create_mounting_bracket",
                            "params": {
                                "width_cm": 8.0,
                                "leg_depth_cm": 4.0,
                                "leg_height_cm": 5.0,
                                "thickness_cm": 4.0,
                                "base_hole_count": 2,
                                "wall_hole_count": 2,
                            },
                        }
                    ],
                }
            )
        )
        issues = validate_plan(plan, selection_context={"count": 0, "items": []})
        self.assertTrue(any("thickness_cm < leg_depth_cm" in issue for issue in issues))

    def test_hole_compiler_stays_allowlisted(self):
        plan = normalize_plan(
            plan_from_dict(
                {
                    "version": "v1",
                    "operations": [
                        {
                            "id": "op_1",
                            "op": "create_hole",
                            "params": {
                                "diameter_cm": 0.5,
                                "depth_cm": 1.2,
                                "extent_mode": "distance",
                                "target": "selected_face_center",
                            },
                        }
                    ],
                }
            )
        )
        issues = validate_plan(plan, selection_context={"count": 1, "items": [{"kind": "face"}]})
        self.assertEqual(issues, [])
        code = compile_plan_to_code(plan)
        self.assertEqual(find_api_issues(code), [])
        self.assertIn("areaProperties", code)
        self.assertNotIn("holeProfile = holeSketch.profiles.item(0)", code)

    def test_chamfer_compiler_stays_allowlisted(self):
        plan = normalize_plan(
            plan_from_dict(
                {
                    "version": "v1",
                    "operations": [
                        {
                            "id": "op_1",
                            "op": "create_chamfer",
                            "params": {
                                "distance_cm": 0.2,
                                "mode": "equal_distance",
                                "target": "selected_edges",
                            },
                        }
                    ],
                }
            )
        )
        issues = validate_plan(plan, selection_context={"count": 1, "items": [{"kind": "edge"}]})
        self.assertEqual(issues, [])
        code = compile_plan_to_code(plan)
        self.assertEqual(find_api_issues(code), [])

    def test_validator_requires_selected_face_for_hole(self):
        plan = normalize_plan(
            plan_from_dict(
                {
                    "version": "v1",
                    "operations": [
                        {
                            "id": "op_1",
                            "op": "create_hole",
                            "params": {
                                "diameter_cm": 0.5,
                                "depth_cm": 1.0,
                                "extent_mode": "distance",
                                "target": "selected_face_center",
                            },
                        }
                    ],
                }
            )
        )
        issues = validate_plan(plan, selection_context={"count": 0, "items": []})
        self.assertTrue(any("requires a selected face" in issue for issue in issues))

    def test_heuristic_planner_handles_cube_prompt(self):
        plan_dict = generate_plan(
            user_prompt="make a cube of 100mm",
            fusion_state_text="Bodies: 0",
            selection_state_text="No active selections.",
            client=None,
        )
        plan = normalize_plan(plan_from_dict(plan_dict))
        self.assertGreaterEqual(len(plan.operations), 1)
        self.assertEqual(plan.operations[0].op, "create_cube")
        self.assertAlmostEqual(plan.operations[0].params.get("size_cm"), 10.0, places=6)


if __name__ == "__main__":
    unittest.main()
