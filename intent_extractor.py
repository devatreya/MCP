"""Intent extraction: classifies user prompt into a structured CAD intent via LLM."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from llm_adapter import create_text_completion_with_fallback

ALLOWED_FAMILIES = {
    "sketch",
    "extrude",
    "revolve",
    "sweep",
    "fillet_chamfer",
    "hole",
    "shell",
    "pattern",
    "mirror",
    "boolean",
    "transform",
    "unknown",
}

_SYSTEM_PROMPT = """\
You are a Fusion 360 CAD intent classifier.
Given a user prompt, output ONLY strict JSON with this exact shape:
{
  "operation_family": "<family>",
  "human_intent": "<one sentence describing the CAD operation to perform>",
  "params": { <key dimension/option params extracted from prompt, all lengths in cm> },
  "required_selections": ["face"] | ["edge"] | ["body"] | []
}

Allowed operation_family values:
- "sketch": creating or modifying 2D sketch geometry
- "extrude": extruding a sketch profile to create, join, or cut solid geometry
- "revolve": revolving a sketch profile around an axis to create solid geometry
- "sweep": sweeping a profile along a path curve
- "fillet_chamfer": adding fillets (rounded edges) or chamfers (beveled edges)
- "hole": drilling, boring, or creating holes in a face
- "shell": hollowing out a body by removing one or more faces
- "pattern": creating linear or circular repeating patterns of features
- "mirror": mirroring features or bodies across a plane
- "boolean": combining, subtracting, or intersecting two bodies
- "transform": moving, rotating, or scaling a body
- "unknown": prompt cannot be mapped to a Fusion 360 geometric operation

Rules:
- Convert all lengths to centimeters in params (e.g. "5mm" → 0.5)
- required_selections: list only geometry types the operation REQUIRES from the user
  - hole → always ["face"]
  - shell → always ["face"]
  - fillet/chamfer on specific edges → ["edge"]
  - fillet/chamfer on all edges of a face → ["face"]
  - boolean subtract/intersect → ["body"]
  - If user says "all edges" or "all faces" without a specific selection → []
- Keep params minimal: only values explicitly stated in the prompt
- Do not output Python code, markdown, or explanations
"""


def _extract_json_object(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


@dataclass
class IntentResult:
    operation_family: str
    human_intent: str
    params: dict = field(default_factory=dict)
    required_selections: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def extract_intent(
    user_prompt,
    fusion_state_text,
    selection_state_text,
    client,
    model,
    fallback_model=None,
):
    """Call the LLM to classify the user's prompt into a structured IntentResult."""
    model_names = [m for m in [model, fallback_model] if m]
    user_content = (
        f"Prompt: {user_prompt}\n\n"
        f"Fusion model state:\n{fusion_state_text}\n\n"
        f"Active selection context:\n{selection_state_text}"
    )
    raw_text, _ = create_text_completion_with_fallback(
        client=client,
        model_names=model_names,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=400,
    )
    raw_json = _extract_json_object(raw_text)
    if not raw_json:
        raise ValueError(f"Intent extractor did not return JSON. Got: {raw_text[:200]}")

    parsed = json.loads(raw_json)

    family = str(parsed.get("operation_family") or "unknown").strip().lower()
    if family not in ALLOWED_FAMILIES:
        family = "unknown"

    return IntentResult(
        operation_family=family,
        human_intent=str(parsed.get("human_intent") or user_prompt).strip(),
        params=parsed.get("params") if isinstance(parsed.get("params"), dict) else {},
        required_selections=parsed.get("required_selections")
        if isinstance(parsed.get("required_selections"), list)
        else [],
        raw=parsed,
    )
