import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_ORG_ID", "test-org")
os.environ.setdefault("OPENAI_PROJECT_ID", "test-project")


def _load_server_module():
    sys.modules.pop("server", None)
    with patch("openai.OpenAI", return_value=SimpleNamespace()):
        return importlib.import_module("server")


class ServerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _load_server_module()

    def test_generate_returns_llm_generation_retry_context(self):
        with self.server.app.test_client() as client:
            with patch.object(self.server, "GENERATION_MODE", "legacy"):
                with patch.object(
                    self.server,
                    "create_text_completion_with_fallback",
                    side_effect=RuntimeError("model_not_found"),
                ):
                    response = client.post(
                        "/generate",
                        json={
                            "prompt": "make a cube",
                            "fusion_state": {"body_count": 0},
                            "selection_context": {"count": 0, "items": []},
                        },
                    )

        data = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["retry_context"]["stage"], "llm_generation")

    def test_generate_returns_structured_plan_generation_retry_context(self):
        with self.server.app.test_client() as client:
            with patch.object(self.server, "GENERATION_MODE", "structured_v1"):
                with patch.object(
                    self.server,
                    "generate_plan",
                    side_effect=RuntimeError("planner offline"),
                ):
                    response = client.post(
                        "/generate",
                        json={
                            "prompt": "make a cube",
                            "fusion_state": {"body_count": 0},
                            "selection_context": {"count": 0, "items": []},
                        },
                    )

        data = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["retry_context"]["stage"], "structured_plan_generation")


if __name__ == "__main__":
    unittest.main()
