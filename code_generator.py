"""Fusion 360 code generation: takes an IntentResult and produces executable Fusion Python."""

import json

from fusion_api_knowledge import get_cards_for_family
from llm_adapter import create_text_completion_with_fallback
from rag.retriever import retrieve as rag_retrieve

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

PLANE SELECTION WORKFLOW (when user selects a construction plane or flat face to sketch on):
- Get entity:        entity = ui.activeSelections.item(0).entity
- Try ConstructionPlane first (user clicked a browser plane):
    sketchPlane = adsk.fusion.ConstructionPlane.cast(entity)
- Fallback to BRepFace (user clicked a flat face):
    if not sketchPlane: sketchPlane = adsk.fusion.BRepFace.cast(entity)
- Guard:             if not sketchPlane: raise Exception("Selected entity is not a plane or face.")
- Use it:            sketch = sketches.add(sketchPlane)
Use this pattern whenever the step description says "sketch on selected plane" or
"requires_selection for sketch plane" — do NOT hardcode rootComp.xZConstructionPlane.

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

FUSION 360 COORDINATE SYSTEM — ViewCube mapping (ALWAYS use these):
  "Front"  face / view  →  XZ plane  →  rootComp.xZConstructionPlane   (normal = +Y)
  "Back"   face / view  →  XZ plane  →  rootComp.xZConstructionPlane   (normal = -Y)
  "Right"  face / view  →  YZ plane  →  rootComp.yZConstructionPlane   (normal = +X)
  "Left"   face / view  →  YZ plane  →  rootComp.yZConstructionPlane   (normal = -X)
  "Top"    face / view  →  XY plane  →  rootComp.xYConstructionPlane   (normal = +Z)
  "Bottom" face / view  →  XY plane  →  rootComp.xYConstructionPlane   (normal = -Z)
These mappings are FIXED in every Fusion 360 file — never query the camera.

FINDING FACES PROGRAMMATICALLY (flat-faced bodies like boxes only):
- Top face:    flat face with normal.z > 0.9  AND  centroid at max Z
- Bottom face: flat face with normal.z < -0.9 AND  centroid at min Z
- Front face:  flat face with normal.y > 0.9  AND  centroid at max Y
- Use hasattr(face.geometry, 'normal') to test if a face is planar before reading normal.
- MANDATORY FALLBACK PATTERN — always use this structure when searching for any face:
    target_face = None
    for face in body.faces:
        if hasattr(face.geometry, 'normal') and face.geometry.normal.y > 0.9:
            target_face = face; break
    # If not found programmatically, try the user's active selection (works on retry)
    if target_face is None and ui.activeSelections.count > 0:
        entity = ui.activeSelections.item(0).entity
        target_face = adsk.fusion.BRepFace.cast(entity)
        if not target_face:
            # User may have selected a construction plane — use it as sketch plane directly
            sketchPlane = adsk.fusion.ConstructionPlane.cast(entity)
            if sketchPlane:
                sketch = sketches.add(sketchPlane)
                # ... draw the profile here and continue, do NOT use target_face below
    if target_face is None:
        raise Exception("SELECTION_REQUIRED: Could not find [face]. Please select it.")
  This pattern means retries always succeed if the user selected the right geometry.

CURVED BODY OPERATIONS (cylinders, cones, spheres — NO flat side faces exist):
- NEVER search for a "front face" on a cylinder — use the construction plane directly.
- Slot / cutout on "front" of cylinder  →  sketch = sketches.add(rootComp.xZConstructionPlane)
- Slot / cutout on "right" of cylinder  →  sketch = sketches.add(rootComp.yZConstructionPlane)
- Holes through "bottom" of cylinder    →  find bottom flat face by normal.z < -0.9 (it IS flat)
- Holes through "top" of cylinder       →  find top flat face by normal.z > 0.9 (it IS flat)
- For cut-extrudes on construction planes: use SymmetricExtentDefinition or try both directions.

FILLET ON CYLINDER TOP/BOTTOM EDGES — CRITICAL PATTERN (use this, never face-based approach):
Do NOT search for a face and then get its edges — use Pattern B from fillet-edge-sets directly:
  body = rootComp.bRepBodies.item(0)
  edges = adsk.core.ObjectCollection.create()
  for edge in body.edges:
      if adsk.core.Circle3D.cast(edge.geometry) or adsk.core.Arc3D.cast(edge.geometry):
          mid = edge.pointOnEdge
          if mid.z < 0.1:          # bottom edges (adjust threshold for top: mid.z > height - 0.1)
              edges.add(edge)
  # Fallback: if programmatic search found nothing, try activeSelections
  if edges.count == 0 and ui.activeSelections.count > 0:
      for _i in range(ui.activeSelections.count):
          _e = adsk.fusion.BRepEdge.cast(ui.activeSelections.item(_i).entity)
          if _e:
              edges.add(_e)
  if edges.count == 0:
      raise Exception("SELECTION_REQUIRED: Could not find edges to fillet. Please select the edges.")
This pattern works for any cylinder regardless of model state context. Adapt `mid.z < 0.1` for
bottom and `mid.z > (body_height - 0.1)` for top by reading body.boundingBox.maxPoint.z.
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
    lint_feedback=None,
):
    """
    Generate Fusion 360 Python code body for the given intent.
    lint_feedback: list of issue strings from a previous failed attempt — injected as a
                   follow-up user message so the model self-corrects on retry.
    Returns (code_str, model_used).
    """
    api_cards = get_cards_for_family(intent_result.operation_family)
    api_section = "\n\n".join(api_cards) if api_cards else "(No specific API reference for this operation family.)"

    # RAG: retrieve relevant API doc chunks (broad context, lower priority than curated cards)
    rag_chunks = []
    try:
        rag_chunks = rag_retrieve(intent_result.human_intent, client, k=6)
    except Exception:
        pass  # graceful degradation — RAG is additive, never blocks code gen

    params_text = (
        json.dumps(intent_result.params, indent=2) if intent_result.params else "{}"
    )

    # Build prompt: RAG context FIRST (broad), then curated cards AFTER (override/take priority)
    rag_section = ""
    if rag_chunks:
        rag_section = (
            "\n\nRELEVANT API DOCUMENTATION (retrieved from official Autodesk docs):\n"
            + "\n\n".join(rag_chunks)
            + "\n"
        )

    system_prompt = (
        _SYSTEM_PROMPT_BASE
        + rag_section
        + "\n\nFUSION API REFERENCE FOR THIS OPERATION (curated, highest priority):\n"
        + api_section
        + _SYSTEM_PROMPT_SUFFIX
    )

    user_content = (
        f"CAD Intent: {intent_result.human_intent}\n\n"
        f"Extracted parameters (all lengths in cm):\n{params_text}\n\n"
        f"Fusion model state:\n{fusion_state_text}\n\n"
        f"Active selection context:\n{selection_state_text}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # If a previous attempt failed lint, feed the errors back as a follow-up
    # so the model knows exactly what to fix on retry.
    if lint_feedback:
        issues_text = "\n".join(f"- {issue}" for issue in lint_feedback)
        messages.append({
            "role": "assistant",
            "content": "(previous attempt — contained API errors, rewriting)",
        })
        messages.append({
            "role": "user",
            "content": (
                "Your previous code failed the API lint check with these issues:\n"
                + issues_text
                + "\n\nRewrite the code from scratch, avoiding every one of the above issues. "
                "Use edge.pointOnEdge instead of getPointAtParameter. "
                "Use Circle3D.cast()/Arc3D.cast() to detect circular edges. "
                "Do NOT call SurfaceEvaluator methods at all."
            ),
        })

    # Composite (multi-step) prompts generate significantly more code — allow extra tokens.
    max_tokens = 2500 if intent_result.operation_family == "composite" else 1200

    model_names = [m for m in [model, fallback_model] if m]
    raw_code, model_used = create_text_completion_with_fallback(
        client=client,
        model_names=model_names,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
    )
    return raw_code, model_used
