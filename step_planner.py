"""Step planner: decomposes a composite CAD intent into an ordered sequence of single-family steps."""

import json
from dataclasses import dataclass, field
from typing import List

from llm_adapter import create_text_completion_with_fallback
from intent_extractor import ALLOWED_FAMILIES

# Step families: every single-op family except the meta-families
_STEP_FAMILIES = ALLOWED_FAMILIES - {"composite", "unknown"}

_SYSTEM_PROMPT = """\
You are a Fusion 360 operation sequencer.
Given a multi-step CAD goal, output ONLY strict JSON with this exact shape:
{
  "steps": [
    {
      "step_id": "step_1",
      "family": "<operation_family>",
      "description": "<concise one-sentence description of exactly what this step does>",
      "params": { <numeric params in cm, e.g. "width_cm": 8.0, "radius_cm": 0.2> },
      "requires_selection": false,
      "selection_prompt": ""
    }
  ]
}

Allowed family values (each step must be exactly one):
- "sketch"       : creating 2D sketch geometry
- "extrude"      : extruding a sketch to create, join, or cut solid geometry
- "revolve"      : revolving a profile around an axis
- "sweep"        : sweeping a profile along a path
- "fillet_chamfer": rounding or beveling edges
- "hole"         : drilling holes in a face
- "shell"        : hollowing out a body by removing a face
- "pattern"      : repeating features linearly or circularly
- "mirror"       : mirroring features or bodies
- "boolean"      : combining, subtracting, or intersecting two bodies
- "transform"    : moving or rotating a body

Ordering rules:
- Steps must be in logical dependency order (create geometry before modifying it).
- Typical order for enclosures: extrude → shell → fillet → hole → pattern
- Never duplicate an operation — if the user asks for "4 holes in each corner", that is
  ONE hole step followed by ONE pattern step, not four separate hole steps.

requires_selection rules (determines whether the add-in pauses for user input):
- "shell"        : ALWAYS true  — user must pick which face to open.
                   selection_prompt: "Select the face you want to open/remove"
- "hole"         : ALWAYS true  — user must pick which face to drill into.
                   selection_prompt: "Select the face to drill the holes into"
- "fillet_chamfer" on ALL edges: false — code iterates all body edges.
- "fillet_chamfer" on specific edges: true — user picks those edges.
                   selection_prompt: "Select the edges to fillet"
- "extrude" / "revolve" / "sweep" creating from scratch: false — code sketches on XY plane.
- "boolean"      : true  — user picks the tool body.
                   selection_prompt: "Select the body to subtract/join"
- "pattern" / "mirror": false — code finds the feature programmatically.
- Default        : false

selection_prompt: a clear, friendly instruction shown to the user in the chat panel.
  Set to "" when requires_selection is false.

params: all lengths in cm. Examples: 80mm → 8.0, 2.5mm → 0.25, M3 screw → diameter 0.32.
Do not output Python code, markdown, or any explanation outside the JSON object.
"""


@dataclass
class StepPlan:
    step_id: str
    family: str
    description: str
    params: dict = field(default_factory=dict)
    requires_selection: bool = False
    selection_prompt: str = ""


def _extract_json(text: str):
    """Pull the first complete {...} JSON object out of arbitrary text."""
    if not text:
        return None
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


def plan_steps(
    human_intent: str,
    params: dict,
    fusion_state_text: str,
    client,
    model: str,
    fallback_model: str = None,
) -> List[StepPlan]:
    """Call the LLM to decompose a composite CAD goal into an ordered list of StepPlan objects."""
    user_content = (
        f"Goal: {human_intent}\n\n"
        f"Extracted dimensions / params: {json.dumps(params)}\n\n"
        f"Current Fusion model state:\n{fusion_state_text}"
    )
    model_names = [m for m in [model, fallback_model] if m]
    raw_text, _ = create_text_completion_with_fallback(
        client=client,
        model_names=model_names,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=800,
    )

    raw_json = _extract_json(raw_text)
    if not raw_json:
        raise ValueError(
            f"Step planner did not return valid JSON. Got: {(raw_text or '')[:200]}"
        )

    parsed = json.loads(raw_json)
    steps_raw = parsed.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"Step planner returned no steps. Parsed: {parsed}")

    steps = []
    for i, raw_step in enumerate(steps_raw):
        family = str(raw_step.get("family") or "unknown").strip().lower()
        if family not in _STEP_FAMILIES:
            family = "unknown"
        steps.append(
            StepPlan(
                step_id=str(raw_step.get("step_id") or f"step_{i + 1}"),
                family=family,
                description=str(raw_step.get("description") or "").strip(),
                params=(
                    raw_step.get("params")
                    if isinstance(raw_step.get("params"), dict)
                    else {}
                ),
                requires_selection=bool(raw_step.get("requires_selection", False)),
                selection_prompt=str(raw_step.get("selection_prompt") or "").strip(),
            )
        )

    return steps
