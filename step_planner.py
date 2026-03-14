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

CRITICAL — model state awareness:
- READ the "Current Fusion model state" carefully before planning steps.
- If the model already has bodies, features, and faces, do NOT recreate the body from scratch.
  Only plan steps for the NEW operations the user is requesting.
- If the model is already shelled (look for thin walls, high face count relative to body count,
  or shell features in the state), do NOT add another shell step — shelling an already-hollow
  body will fail.
- If the model already has fillets, do NOT add redundant fillet steps unless the user explicitly
  asks for NEW fillets on different edges.
- Focus on what the user is ADDING or CHANGING, not what already exists.

CRITICAL — sketch merging rules:
- NEVER create a standalone "sketch" step followed by an "extrude" step. An extrude step
  ALWAYS creates its own sketch internally. Merge them into ONE "extrude" step.
  Bad:  step_1: sketch (draw circle), step_2: extrude (extrude it)
  Good: step_1: extrude (sketch a circle with 40mm diameter on XY plane and extrude 60mm)
- The same applies to "revolve" and "sweep" — each includes its own sketch creation.
- A standalone "sketch" step is ONLY valid when the user ONLY wants a sketch with no
  subsequent 3D operation (e.g. "draw a layout sketch").

requires_selection rules (determines whether the add-in pauses for user input):
- "shell": true ONLY if the prompt does NOT specify which face to remove.
  If the prompt explicitly names the face (e.g. "remove the top face", "keep the top open",
  "open the bottom"), set requires_selection: false — code finds the face programmatically
  using face normals and bounding box analysis (top = max Z normal, bottom = min Z normal,
  front = max Y normal, etc.).
  If the prompt is ambiguous (just "shell it"), set requires_selection: true.
  selection_prompt: "Select the face you want to open/remove"
- "hole": true ONLY if the target face is ambiguous.
  If the prompt says "holes through the bottom face" or "holes on top face", set false —
  code finds the face programmatically. If ambiguous, set true.
  selection_prompt: "Select the face to drill the holes into"
- "fillet_chamfer" on ALL edges: false — code iterates all body edges.
- "fillet_chamfer" on specific edges (e.g. "inner edges of the slot"): false — code can
  identify edges programmatically using feature history or geometric filtering.
  Only set true if truly ambiguous or user says "select the edges".
  selection_prompt: "Select the edges to fillet"
- "extrude" / "revolve" / "sweep" creating from scratch: false — code sketches on XY plane.
- "extrude" as a cut on a specific face: false if face is named ("front face", "side face").
  The code should use construction planes or face normals to find the right face.
- "boolean"      : true  — user picks the tool body.
                   selection_prompt: "Select the body to subtract/join"
- "pattern" / "mirror": false — code finds the feature programmatically.
- Default        : false

CRITICAL — finding faces programmatically:
When requires_selection is false, the step description MUST include enough detail for code
to locate the geometry. Examples:
  - "Shell the body to 0.2 cm wall by removing the top face (face with highest Z centroid and upward normal)"
  - "Cut a slot on the front face (face with Y-normal closest to +Y direction)"
  - "Drill holes through the bottom face (face with lowest Z centroid and downward normal)"

CRITICAL — working with curved bodies (cylinders, spheres, etc.):
- Cylinders do NOT have flat "front" or "side" faces — they have curved surfaces.
- To create a sketch cut on a curved body, use a CONSTRUCTION PLANE (e.g. XZ plane for
  "front", YZ plane for "side") and sketch there, then cut-extrude through the body.
- The step description MUST mention using a construction plane when the target body is
  curved. Example: "Create a rectangular slot on the front of the cylinder by sketching
  on the XZ construction plane and cutting through the body."

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
