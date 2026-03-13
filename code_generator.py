"""Fusion 360 code generation: takes an IntentResult and produces executable Fusion Python."""

import json

from fusion_api_knowledge import get_cards_for_family
from llm_adapter import create_text_completion_with_fallback

_SYSTEM_PROMPT_BASE = """\
You are a Fusion 360 Python code generator.
Write executable Fusion 360 API code for the described CAD operation.

CRITICAL RULES — violating these will cause runtime errors:
1. Do NOT write import statements (adsk.core and adsk.fusion are already imported)
2. Do NOT write def run() or any function definitions
3. Do NOT write setup code — these variables are already defined:
   - app  (adsk.core.Application)
   - ui   (app.userInterface)
   - design   (app.activeProduct, type adsk.fusion.Design)
   - rootComp (design.rootComponent, type adsk.fusion.Component)
   - sketches (rootComp.sketches)
4. Use CENTIMETERS for all distances (1 cm = 10 mm)
5. Do NOT wrap code in markdown fences
6. Write ONLY the code body — no explanations, no comments unless they label major steps

SELECTION WORKFLOW (use this exact pattern when you need selected geometry):
- Count check:    if ui.activeSelections.count < 1: raise Exception("No selection")
- Get entity:     entity = ui.activeSelections.item(0).entity
- Cast to face:   face = adsk.fusion.BRepFace.cast(entity)
- Cast to edge:   edge = adsk.fusion.BRepEdge.cast(entity)
- Cast to body:   body = adsk.fusion.BRepBody.cast(entity)
- All selected:   [ui.activeSelections.item(i).entity for i in range(ui.activeSelections.count)]

FACE CENTER WORKFLOW (when sketching on a selected face):
- sketch = sketches.add(targetFace)
- box = targetFace.boundingBox
- worldPt = adsk.core.Point3D.create(
      (box.minPoint.x + box.maxPoint.x) / 2,
      (box.minPoint.y + box.maxPoint.y) / 2,
      (box.minPoint.z + box.maxPoint.z) / 2)
- sketchPt = sketch.modelToSketchSpace(worldPt)

CUT DIRECTION ROBUSTNESS (for any cut/hole operation):
- Try adsk.fusion.ExtentDirections.NegativeExtentDirection first
- If volume unchanged after add(), delete the feature and retry PositiveExtentDirection
"""

_SYSTEM_PROMPT_SUFFIX = """\

Write the complete code body to accomplish this operation.
"""


def generate_cad_code(
    intent_result,
    fusion_state_text,
    selection_state_text,
    client,
    model,
    fallback_model=None,
):
    """
    Generate Fusion 360 Python code body for the given intent.
    Returns (code_str, model_used).
    """
    api_cards = get_cards_for_family(intent_result.operation_family)
    api_section = "\n\n".join(api_cards) if api_cards else "(No specific API reference for this operation family.)"

    params_text = (
        json.dumps(intent_result.params, indent=2) if intent_result.params else "{}"
    )

    system_prompt = (
        _SYSTEM_PROMPT_BASE
        + "\n\nFUSION API REFERENCE FOR THIS OPERATION:\n"
        + api_section
        + _SYSTEM_PROMPT_SUFFIX
    )

    user_content = (
        f"CAD Intent: {intent_result.human_intent}\n\n"
        f"Extracted parameters (all lengths in cm):\n{params_text}\n\n"
        f"Fusion model state:\n{fusion_state_text}\n\n"
        f"Active selection context:\n{selection_state_text}"
    )

    # Composite (multi-step) prompts generate significantly more code — allow extra tokens.
    max_tokens = 2500 if intent_result.operation_family == "composite" else 1200

    model_names = [m for m in [model, fallback_model] if m]
    raw_code, model_used = create_text_completion_with_fallback(
        client=client,
        model_names=model_names,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return raw_code, model_used
