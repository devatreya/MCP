import unittest
from types import SimpleNamespace

from llm_adapter import create_text_completion, create_text_completion_with_fallback
from plan_generator import generate_plan


class _FakeChatCompletions:
    def __init__(self, behavior_by_model):
        self.behavior_by_model = behavior_by_model
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        behavior = self.behavior_by_model[kwargs["model"]]
        if isinstance(behavior, Exception):
            raise behavior
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=behavior),
                )
            ]
        )


class _FakeResponses:
    def __init__(self, output_text, errors=None):
        self.output_text = output_text
        self.errors = list(errors or [])
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(output_text=self.output_text)


class LlmAdapterTests(unittest.TestCase):
    def test_create_text_completion_retries_without_temperature_for_responses_models(self):
        responses = _FakeResponses(
            output_text='{"version":"v1","operations":[]}',
            errors=[RuntimeError("temperature is not supported for this model"), None],
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=_FakeChatCompletions({})),
            responses=responses,
        )

        result = create_text_completion(
            client=client,
            model_name="gpt-5.3-codex",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.2,
            max_tokens=200,
        )

        self.assertEqual(result, '{"version":"v1","operations":[]}')
        self.assertEqual(len(responses.calls), 2)
        self.assertIn("temperature", responses.calls[0])
        self.assertNotIn("temperature", responses.calls[1])

    def test_create_text_completion_with_fallback_uses_next_model_on_missing_model(self):
        chat = _FakeChatCompletions(
            {
                "missing-model": RuntimeError("model_not_found"),
                "gpt-4o": "print('ok')",
            }
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=chat),
            responses=_FakeResponses(output_text="unused"),
        )

        result, model_used = create_text_completion_with_fallback(
            client=client,
            model_names=["missing-model", "gpt-4o"],
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.2,
            max_tokens=200,
        )

        self.assertEqual(result, "print('ok')")
        self.assertEqual(model_used, "gpt-4o")
        self.assertEqual(len(chat.calls), 2)

    def test_generate_plan_raises_when_llm_planner_fails_without_explicit_fallback(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=_FakeChatCompletions({"gpt-4o": RuntimeError("planner offline")})
            ),
            responses=_FakeResponses(output_text="unused"),
        )

        with self.assertRaises(RuntimeError):
            generate_plan(
                user_prompt="make a cube",
                fusion_state_text="Bodies: 0",
                selection_state_text="No active selections.",
                client=client,
                model="gpt-4o",
            )

    def test_generate_plan_can_fallback_to_heuristics_when_explicitly_allowed(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=_FakeChatCompletions({"gpt-4o": RuntimeError("planner offline")})
            ),
            responses=_FakeResponses(output_text="unused"),
        )

        plan = generate_plan(
            user_prompt="make a cube",
            fusion_state_text="Bodies: 0",
            selection_state_text="No active selections.",
            client=client,
            model="gpt-4o",
            allow_heuristic_fallback=True,
        )

        self.assertEqual(plan["operations"][0]["op"], "create_cube")
        self.assertEqual(plan["metadata"]["planner"], "heuristic_fallback")


if __name__ == "__main__":
    unittest.main()
