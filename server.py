from flask import Flask, request, jsonify, render_template, session
from openai import OpenAI
import os
from datetime import datetime
import re
import textwrap
import json
from cad_ir import plan_from_dict, plan_to_dict
from llm_adapter import create_text_completion
from plan_compiler import compile_plan_to_code
from plan_generator import generate_plan
from plan_normalizer import normalize_plan
from plan_validator import validate_plan
from fusion_api_knowledge import (
    apply_api_repairs,
    build_api_guidance,
    find_api_issues,
)

def _load_env_file_fallback(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except Exception:
        pass


def _load_environment():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        _load_env_file_fallback(".env")


_load_environment()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ORG_ID = os.getenv("OPENAI_ORG_ID")
OPENAI_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o").strip()
OPENAI_FALLBACK_MODEL = (os.getenv("OPENAI_FALLBACK_MODEL") or "gpt-4o").strip()
GENERATION_MODE = (os.getenv("GENERATION_MODE") or "legacy").strip().lower()
_missing_env = [k for k, v in [
    ("OPENAI_API_KEY", OPENAI_API_KEY),
    ("OPENAI_ORG_ID", OPENAI_ORG_ID),
    ("OPENAI_PROJECT_ID", OPENAI_PROJECT_ID),
] if not v]
if _missing_env:
    raise RuntimeError(
        "Missing required environment variables: "
        + ", ".join(_missing_env)
        + ". Set them in your shell or in .env at "
        + os.path.abspath(".env")
        + "."
    )

app = Flask(__name__)
app.secret_key = "fusion-mcp-session-key"

# Log server startup (append, don't clear)
log_file = "auto_runner/log.txt"
try:
    with open(log_file, "a") as f:
        f.write("\n" + "="*50 + "\n")
        f.write("🚀 Flask server started\n")
        f.write("="*50 + "\n")
except:
    pass

# Initialize OpenAI client
client = OpenAI(
    api_key=OPENAI_API_KEY,
    organization=OPENAI_ORG_ID,
    project=OPENAI_PROJECT_ID
)


def _model_candidates():
    ordered = []
    for name in [OPENAI_MODEL, OPENAI_FALLBACK_MODEL]:
        candidate = (name or "").strip()
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    if not ordered:
        ordered.append("gpt-4o")
    return ordered


def _is_model_not_found_error(exc):
    msg = str(exc).lower()
    return (
        "model_not_found" in msg
        or "does not exist" in msg
        or "do not have access" in msg
    )


def _create_chat_completion_with_fallback(messages, temperature=0.2, max_tokens=1500):
    errors = []
    for model_name in _model_candidates():
        try:
            content = create_text_completion(
                client=client,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return content, model_name
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            if not _is_model_not_found_error(exc) and "supported in v1/responses" not in str(exc).lower():
                break

    raise RuntimeError(
        "OpenAI generation failed. " + " | ".join(errors)
    )

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    
    # Don't clear log file - just mark the reset in the log
    log_file = "auto_runner/log.txt"
    try:
        with open(log_file, "a") as f:
            f.write("\n" + "-"*50 + "\n")
            f.write("🔄 Reset Design clicked\n")
            f.write("-"*50 + "\n")
    except:
        pass
    
    # Generate a safer cleanup script
    # Delete in correct order: features first (which deletes bodies), then orphaned sketches
    cleanup_script = """import adsk.core, adsk.fusion, traceback

def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        design = app.activeProduct
        root = design.rootComponent
        
        # Collect items to delete (safer than deleting in a loop)
        features_to_delete = []
        for i in range(root.features.count):
            features_to_delete.append(root.features.item(i))
        
        # Delete features (this will remove associated bodies)
        for feat in features_to_delete:
            try:
                feat.deleteMe()
            except:
                pass  # Skip if already deleted or can't delete
        
        # Delete remaining sketches
        sketches_to_delete = []
        for i in range(root.sketches.count):
            sketches_to_delete.append(root.sketches.item(i))
        
        for sketch in sketches_to_delete:
            try:
                sketch.deleteMe()
            except:
                pass
        
        # Delete any remaining bodies
        bodies_to_delete = []
        for i in range(root.bRepBodies.count):
            bodies_to_delete.append(root.bRepBodies.item(i))
        
        for body in bodies_to_delete:
            try:
                body.deleteMe()
            except:
                pass
                
    except Exception as e:
        if ui:
            ui.messageBox('❌ Reset Error: {}'.format(str(e)))
"""
    
    # Save the cleanup script
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"generated_scripts/reset_{timestamp}.py"
    with open(filename, "w") as f:
        f.write(cleanup_script)
    
    return jsonify({"status": "reset", "script": cleanup_script, "file": filename})

@app.route("/generate", methods=["POST"])
def generate_script():
    payload = request.get_json(silent=True) or {}
    user_prompt = (payload.get("prompt") or "").strip()
    if not user_prompt:
        return jsonify({"status": "error", "error": "Missing prompt"}), 400

    incoming_fusion_state = payload.get("fusion_state")
    incoming_selection_context = payload.get("selection_context")
    if requires_selection(user_prompt) and not has_selection(incoming_selection_context):
        return jsonify({
            "status": "error",
            "error": "This prompt requires a selected face/body in Fusion. Select geometry and retry."
        }), 400

    if "conversation" not in session:
        session["conversation"] = []
    if "model_summary" not in session:
        session["model_summary"] = []

    conversation = session["conversation"]
    model_summary = session["model_summary"]

    model_summary_text = "\n".join(f"- {item}" for item in model_summary)

    fusion_state_source = incoming_fusion_state if isinstance(incoming_fusion_state, dict) else load_fusion_state()
    fusion_state = summarize_fusion_state(fusion_state_source)
    selection_state = summarize_selection_context(incoming_selection_context)
    mode_used = "legacy"
    structured_plan_payload = None
    llm_model_used = None

    use_structured = GENERATION_MODE == "structured_v1" and should_use_structured_mode(user_prompt)
    if use_structured:
        try:
            conversation.append({"role": "user", "content": user_prompt})
            selection_for_validation = (
                incoming_selection_context if isinstance(incoming_selection_context, dict) else {"count": 0, "items": []}
            )
            generated_plan = generate_plan(
                user_prompt=user_prompt,
                fusion_state_text=fusion_state,
                selection_state_text=selection_state,
                client=client,
                model=OPENAI_MODEL,
            )
            plan = normalize_plan(plan_from_dict(generated_plan))
            validation_issues = validate_plan(plan, selection_context=selection_for_validation)
            if validation_issues:
                return jsonify({
                    "status": "error",
                    "error": "Structured plan failed validation.",
                    "details": validation_issues,
                    "retry_context": {
                        "stage": "structured_plan_validation",
                        "issues": validation_issues,
                        "prompt": user_prompt,
                        "recommended_action": "Regenerate only invalid operations and preserve valid operations.",
                    },
                }), 400
            structured_plan_payload = plan_to_dict(plan)
            cleaned_code = clean_generated_code(compile_plan_to_code(plan))
            mode_used = "structured_v1"
        except Exception as exc:
            return jsonify({
                "status": "error",
                "error": f"Structured planning failed: {exc}",
                "retry_context": {
                    "stage": "structured_plan_compile",
                    "prompt": user_prompt,
                    "recommended_action": "Regenerate a simpler plan with fewer operations.",
                },
            }), 400
    else:
        api_guidance = build_api_guidance(user_prompt, limit=4)
        system_prompt = (
            "You are a Python assistant for Fusion 360. "
            "Generate ONLY the body of Python code (no imports, no def run, no setup - those are added automatically). "
            "CRITICAL REQUIREMENTS:\n"
            "1. Do NOT write setup code (design/rootComp/sketches are already defined)\n"
            "2. Do NOT write 'def run(context):' or 'import' statements\n"
            "3. Write ONLY the core logic - no markdown, explanations, or comments\n"
            "4. Use centimeters for all measurements\n"
            "5. To find the TOP FACE of a cube, find the face with maximum Z normal:\n"
            "   topFace = None\n"
            "   for face in rootComp.bRepBodies.item(0).faces:\n"
            "       if face.geometry.normal.z > 0.9:\n"
            "           topFace = face\n"
            "           break\n"
            "6. For HOLES/CUTS use Boolean subtraction with Combine feature:\n"
            "   a) Prefer direct cut extrude: createInput(profile, CutFeatureOperation)\n"
            "   b) For selected-face cuts, direction must be robust:\n"
            "      - Try NegativeExtentDirection first\n"
            "      - Compare targetBody.volume before/after\n"
            "      - If unchanged, delete feature and retry PositiveExtentDirection\n"
            "   c) If using combine fallback, target body must be targetFace.body (NOT rootComp.bRepBodies.item(0))\n"
            "7. Center sketches at origin only when no selected face/location is provided.\n\n"
            "8. If there are active selections, prioritize them over heuristic face picking.\n"
            "9. Never recreate the whole model unless the user explicitly asks to reset/start over.\n\n"
            "10. Active selection collection is ui.activeSelections (NOT app.activeSelections).\n\n"
            "11. When sketching on targetFace, do NOT use Point3D(0,0,0) as hole center.\n"
            "    Compute face center in world coordinates and convert to sketch space:\n"
            "    faceBox = targetFace.boundingBox\n"
            "    holeCenterWorld = adsk.core.Point3D.create((faceBox.minPoint.x + faceBox.maxPoint.x)/2, (faceBox.minPoint.y + faceBox.maxPoint.y)/2, (faceBox.minPoint.z + faceBox.maxPoint.z)/2)\n"
            "    holeCenter = sketch.modelToSketchSpace(holeCenterWorld)\n"
            "    Then use addByCenterRadius(holeCenter, radius).\n\n"
            + api_guidance + "\n\n"
            "Fusion model state (authoritative, from add-in):\n"
            + fusion_state + "\n\n"
            "Active selection context:\n"
            + selection_state + "\n\n"
            "EXAMPLE - Cube centered at origin:\n"
            "xyPlane = rootComp.xYConstructionPlane\n"
            "sketch = sketches.add(xyPlane)\n"
            "lines = sketch.sketchCurves.sketchLines\n"
            "rect = lines.addTwoPointRectangle(adsk.core.Point3D.create(-5, -5, 0), adsk.core.Point3D.create(5, 5, 0))\n"
            "prof = sketch.profiles.item(0)\n"
            "extrudes = rootComp.features.extrudeFeatures\n"
            "extInput = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
            "distance = adsk.core.ValueInput.createByReal(10)\n"
            "extInput.setDistanceExtent(False, distance)\n"
            "extrudes.add(extInput)\n\n"
            "Current model state (from prompts):\n" + model_summary_text
        )

        conversation.append({"role": "user", "content": user_prompt})
        messages = [{"role": "system", "content": system_prompt}] + conversation

        if should_use_selected_face_hole_template(user_prompt, incoming_selection_context):
            cleaned_code = build_selected_face_hole_code(user_prompt)
        else:
            try:
                raw_code, llm_model_used = _create_chat_completion_with_fallback(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1500,
                )
                cleaned_code = clean_generated_code(raw_code)
            except Exception as exc:
                return jsonify({
                    "status": "error",
                    "error": str(exc),
                    "retry_context": {
                        "stage": "llm_generation",
                        "prompt": user_prompt,
                        "recommended_action": (
                            "Use an available model (for example OPENAI_MODEL=gpt-4o) "
                            "or enable structured mode for supported operations."
                        ),
                    },
                }), 400

    cleaned_code = add_execution_checkpoints(cleaned_code, user_prompt)
    api_issues = find_api_issues(cleaned_code)
    if api_issues:
        return jsonify({
            "status": "error",
            "error": "Generated script contained unsupported Fusion API usage.",
            "details": api_issues,
            "retry_context": {
                "stage": "preflight_api_lint",
                "issues": api_issues,
                "prompt": user_prompt,
                "recommended_action": "Regenerate only the failing API steps and preserve already-valid geometry operations.",
            },
        }), 400
    clear_model = should_clear_model(user_prompt, model_summary, fusion_state_source)
    wrapped_code = wrap_script_with_run(cleaned_code, clear_model=clear_model)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"generated_scripts/script_{timestamp}.py"
    with open(filename, "w") as f:
        f.write(wrapped_code)

    session["conversation"] = conversation + [{"role": "assistant", "content": cleaned_code}]
    session["model_summary"] = update_summary(model_summary, user_prompt)

    response_payload = {
        "status": "success",
        "script": wrapped_code,
        "file": filename,
        "generation_mode": mode_used,
    }
    if structured_plan_payload:
        response_payload["plan"] = structured_plan_payload
    if llm_model_used:
        response_payload["llm_model_used"] = llm_model_used
    return jsonify(response_payload)

def clean_generated_code(raw_code):
    # Remove markdown code fences
    raw = re.sub(r"```[^\n]*\n", "", raw_code)
    raw = re.sub(r"```", "", raw)
    # Repair common invalid selection access generated by the model.
    raw = re.sub(r"\bapp\s*\.\s*activeSelections\b", "ui.activeSelections", raw)
    raw = re.sub(r"adsk\.core\.Application\.get\(\)\s*\.\s*activeSelections", "ui.activeSelections", raw)
    raw = re.sub(
        r"targetFace\s*=\s*ui\.activeSelections\.item\(0\)\.entity",
        "targetFace = adsk.fusion.BRepFace.cast(ui.activeSelections.item(0).entity)",
        raw,
    )
    # Repair common wrong combine target body choice when a face selection is used.
    raw = re.sub(
        r"combineFeats\.createInput\(\s*rootComp\.bRepBodies\.item\(0\)\s*,\s*toolBodies\s*\)",
        "combineFeats.createInput(targetFace.body, toolBodies)",
        raw,
    )
    raw = re.sub(
        r"combineFeats\.createInput\(\s*root\.bRepBodies\.item\(0\)\s*,\s*toolBodies\s*\)",
        "combineFeats.createInput(targetFace.body, toolBodies)",
        raw,
    )
    raw = apply_api_repairs(raw)

    lines = raw.splitlines()
    cleaned = []
    skip_next_empty = False
    
    for line in lines:
        l = line.strip().lower()
        
        # Skip friendly text/boilerplate
        if l.startswith(("sure", "here is", "here's", "this script", "note:", "you can use")):
            skip_next_empty = True
            continue
        
        # Skip nested run() definitions (wrapper adds this)
        if "def run(" in l:
            skip_next_empty = True
            continue
            
        # Skip adsk imports (wrapper adds this)
        if l.startswith("import") and "adsk" in l:
            skip_next_empty = True
            continue
        
        # Skip setup code (wrapper adds this)
        if l in ["design = app.activeproduct", "rootcomp = design.rootcomponent", "sketches = rootcomp.sketches"]:
            skip_next_empty = True
            continue
        
        # Skip empty lines after removed content
        if not l and skip_next_empty:
            skip_next_empty = False
            continue
            
        cleaned.append(line)

    return "\n".join(cleaned)

def _sanitize_step_label(raw_label, max_len=72):
    label = (raw_label or "").strip()
    label = label.replace("\\", "/").replace('"', "'")
    label = re.sub(r"\s+", " ", label)
    if len(label) > max_len:
        return label[: max_len - 3].rstrip() + "..."
    return label

def add_execution_checkpoints(cleaned_code, user_prompt=""):
    lines = cleaned_code.splitlines()
    base_label = "begin generated script"
    if not any(line.strip() for line in lines):
        base_label = _sanitize_step_label(user_prompt) or "generated operation"
    out = [f'checkpoint("step_0: {base_label}")']
    step_count = 0

    for line in lines:
        stripped = line.strip()

        if line.startswith("#"):
            label = _sanitize_step_label(stripped.lstrip("#").strip())
            if label:
                step_count += 1
                out.append(f'checkpoint("step_{step_count}: {label}")')

        # Add checkpoints for top-level feature adds when comments are absent.
        if not line.startswith((" ", "\t")):
            m_add = re.search(r"\b(\w+)\.add\(", line)
            if m_add and "checkpoint(" not in line:
                step_count += 1
                out.append(f'checkpoint("step_{step_count}: {m_add.group(1)}.add feature")')

        out.append(line)

    return "\n".join(out)

def normalize_indentation(code: str, indent: int = 8) -> str:
    dedented = textwrap.dedent(code).strip()
    return "\n".join(" " * indent + line if line else "" for line in dedented.splitlines())

def wrap_script_with_run(cleaned_code, clear_model=True):
    cleanup_block = '''
design = app.activeProduct
root = design.rootComponent
for sketch in root.sketches:
    sketch.deleteMe()
for body in root.bRepBodies:
    body.deleteMe()
for feat in root.features:
    feat.deleteMe()
'''
    
    # Always include setup code to ensure variables are defined
    setup_block = '''
design = app.activeProduct
rootComp = design.rootComponent
sketches = rootComp.sketches
'''

    return (
        "import adsk.core, adsk.fusion, traceback\n\n"
        "def run(context):\n"
        "    app = adsk.core.Application.get()\n"
        "    ui = app.userInterface\n"
        "    _mcp_ctx = {'step': 'initializing'}\n"
        "    def checkpoint(step_name):\n"
        "        _mcp_ctx['step'] = str(step_name)\n"
        "    try:\n"
        "        checkpoint('setup')\n"
        + (normalize_indentation(cleanup_block, indent=8) + "\n\n" if clear_model else "")
        + normalize_indentation(setup_block, indent=8) + "\n\n"
        + normalize_indentation(cleaned_code, indent=8)
        + "\n"
        "    except Exception as e:\n"
        "        failed_step = _mcp_ctx.get('step', 'unknown')\n"
        "        enriched_error = '[STEP:{}] {}'.format(failed_step, str(e))\n"
        "        if ui:\n"
        "            ui.messageBox('❌ Runtime Error: {}'.format(enriched_error))\n"
        "        raise RuntimeError(enriched_error) from e\n"
    )

def should_clear_model(user_prompt, model_summary, fusion_state=None):
    p = user_prompt.lower()
    explicit_reset = [
        "reset",
        "start over",
        "start new",
        "new design",
        "clear design",
        "from scratch",
        "delete everything",
    ]
    if any(phrase in p for phrase in explicit_reset):
        return True
    has_geometry = False
    if isinstance(fusion_state, dict):
        has_geometry = fusion_state.get("body_count", 0) > 0

    if not model_summary and not has_geometry:
        seed_words = [
            "create",
            "make",
            "build",
            "new",
            "cube",
            "box",
            "cylinder",
            "sphere",
        ]
        return any(word in p for word in seed_words)
    return False

def has_selection(selection_context):
    if not isinstance(selection_context, dict):
        return False
    count = selection_context.get("count")
    if isinstance(count, int):
        return count > 0
    items = selection_context.get("items", [])
    return isinstance(items, list) and len(items) > 0

def requires_selection(user_prompt):
    p = user_prompt.lower()
    keywords = [
        "selected",
        "selected face",
        "selected body",
        "this face",
        "that face",
        "current face",
    ]
    return any(k in p for k in keywords)

def should_use_structured_mode(user_prompt):
    p = (user_prompt or "").lower()
    structured_keywords = [
        "cube",
        "box",
        "extrude",
        "hole",
        "drill",
        "bore",
        "chamfer",
        "bevel",
        "bracket",
        "mounting",
        "l bracket",
        "angle bracket",
    ]
    return any(keyword in p for keyword in structured_keywords)

def has_selected_face(selection_context):
    if not isinstance(selection_context, dict):
        return False
    items = selection_context.get("items", [])
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = (item.get("kind") or item.get("object_type") or "").strip().lower()
        if kind == "face":
            return True
    return False

def should_use_selected_face_hole_template(user_prompt, selection_context):
    p = user_prompt.lower()
    hole_intent = any(token in p for token in ["hole", "drill", "bore"])
    remove_intent = bool(
        re.search(r"\b(remove|delete|fill|patch|close)\b.*\b(hole|drill|bore)\b", p)
        or re.search(r"\b(hole|drill|bore)\b.*\b(remove|delete|fill|patch|close)\b", p)
    )
    if not hole_intent or remove_intent:
        return False
    if not has_selection(selection_context):
        return False
    if has_selected_face(selection_context):
        return True
    face_cues = ["selected face", "this face", "that face", "current face", "side face"]
    return any(cue in p for cue in face_cues)

def _to_cm(value_text, unit_text):
    value = float(value_text)
    return value / 10.0 if unit_text == "mm" else value

def _extract_tagged_measure_cm(text, keywords):
    keyword_group = "|".join(re.escape(k) for k in keywords)
    patterns = [
        rf"\b(?:{keyword_group})\b\s*(?:of\s*)?([0-9]+(?:\.[0-9]+)?)\s*(mm|cm)\b",
        rf"([0-9]+(?:\.[0-9]+)?)\s*(mm|cm)\s*(?:{keyword_group})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return _to_cm(m.group(1), m.group(2))
    return None

def _extract_hole_radius_cm(user_prompt):
    p = user_prompt.lower()

    m_size = re.search(r"\bm\s*([0-9]+(?:\.[0-9]+)?)\b", p)
    if m_size:
        diameter_mm = float(m_size.group(1))
        return diameter_mm / 20.0

    radius_cm = _extract_tagged_measure_cm(p, ["radius"])
    if radius_cm is not None:
        return radius_cm

    diameter_cm = _extract_tagged_measure_cm(p, ["diameter"])
    if diameter_cm is not None:
        return diameter_cm / 2.0

    return 0.2  # Default M4-style hole radius.

def _extract_hole_depth_cm(user_prompt):
    p = user_prompt.lower()
    if any(token in p for token in ["through", "all the way", "through all", "thru"]):
        return None

    depth_cm = _extract_tagged_measure_cm(p, ["depth", "deep"])
    if depth_cm is not None:
        return depth_cm

    if "blind" in p:
        return 2.0

    return None

def build_selected_face_hole_code(user_prompt):
    radius_cm = _extract_hole_radius_cm(user_prompt)
    depth_cm = _extract_hole_depth_cm(user_prompt)
    through_all = depth_cm is None

    depth_expr = "bodyMaxDim * 2.0" if through_all else f"{depth_cm:.6f}"

    template = f"""
if ui.activeSelections.count < 1:
    raise Exception("No active selection. Select a target face and retry.")
targetFace = adsk.fusion.BRepFace.cast(ui.activeSelections.item(0).entity)
if not targetFace:
    raise Exception("Selected entity is not a face.")
targetBody = targetFace.body

sketch = sketches.add(targetFace)
faceBoxForHole = targetFace.boundingBox
holeCenterWorld = adsk.core.Point3D.create(
    (faceBoxForHole.minPoint.x + faceBoxForHole.maxPoint.x) / 2.0,
    (faceBoxForHole.minPoint.y + faceBoxForHole.maxPoint.y) / 2.0,
    (faceBoxForHole.minPoint.z + faceBoxForHole.maxPoint.z) / 2.0
)
holeCenter = sketch.modelToSketchSpace(holeCenterWorld)
sketch.sketchCurves.sketchCircles.addByCenterRadius(holeCenter, {radius_cm:.6f})
expectedProfileArea = 3.141592653589793 * ({radius_cm:.6f} ** 2)
innerProf = None
bestProfileArea = 0.0
bestProfileScore = 1e99
for i in range(sketch.profiles.count):
    candidate = sketch.profiles.item(i)
    try:
        props = candidate.areaProperties(adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy)
        candidateArea = abs(props.area)
    except:
        continue
    profileScore = abs(candidateArea - expectedProfileArea)
    if profileScore < bestProfileScore:
        bestProfileScore = profileScore
        bestProfileArea = candidateArea
        innerProf = candidate
if not innerProf:
    raise Exception("Failed to resolve hole profile from sketch.")
if expectedProfileArea > 0 and abs(bestProfileArea - expectedProfileArea) > (expectedProfileArea * 0.5):
    raise Exception("Resolved profile area does not match requested hole size.")
extrudes = rootComp.features.extrudeFeatures
bodyBox = targetBody.boundingBox
bodyMaxDim = max(
    abs(bodyBox.maxPoint.x - bodyBox.minPoint.x),
    abs(bodyBox.maxPoint.y - bodyBox.minPoint.y),
    abs(bodyBox.maxPoint.z - bodyBox.minPoint.z)
)
depthCm = {depth_expr}
distance = adsk.core.ValueInput.createByReal(depthCm)
expectedHoleVolume = 3.141592653589793 * ({radius_cm:.6f} ** 2) * max(0.01, depthCm)
minExpectedDelta = max(1e-6, expectedHoleVolume * 0.02)
maxReasonableDelta = max(1e-5, expectedHoleVolume * 25.0)

def try_cut(direction):
    volBefore = targetBody.volume
    extInput = extrudes.createInput(innerProf, adsk.fusion.FeatureOperations.CutFeatureOperation)
    extentDef = adsk.fusion.DistanceExtentDefinition.create(distance)
    extInput.setOneSideExtent(extentDef, direction)
    cutFeat = extrudes.add(extInput)
    volAfter = targetBody.volume
    delta = volBefore - volAfter
    if delta > maxReasonableDelta:
        try:
            cutFeat.deleteMe()
        except:
            pass
        return False
    if delta < minExpectedDelta:
        try:
            cutFeat.deleteMe()
        except:
            pass
        return False
    return True

if not try_cut(adsk.fusion.ExtentDirections.NegativeExtentDirection):
    if not try_cut(adsk.fusion.ExtentDirections.PositiveExtentDirection):
        raise Exception("Failed to cut hole into selected face.")
"""
    return clean_generated_code(template)

def update_summary(current_summary, user_input):
    summary = current_summary.copy()
    user_input_lower = user_input.lower()

    if "cube" in user_input_lower or "box" in user_input_lower:
        # Extract dimensions
        size_match = re.findall(r"(\d+)\s*cm", user_input)
        if size_match:
            size = int(size_match[0])
            summary.append(f"Cube {size}x{size}x{size} cm, centered at origin, extends from Z=0 to Z={size}")
        else:
            summary.append("Cube added at origin")
    elif "hole" in user_input_lower:
        diameter_match = re.findall(r"diameter.*?(\d+)\s*cm", user_input)
        depth_match = re.findall(r"depth.*?(\d+)\s*cm", user_input)
        hole_desc = "Hole"
        if diameter_match:
            hole_desc += f" diameter {diameter_match[0]}cm"
        if depth_match:
            hole_desc += f" depth {depth_match[0]}cm"
        if any(token in user_input_lower for token in ["selected face", "this face", "that face", "current face", "side face"]):
            summary.append(hole_desc + " on selected face")
        else:
            summary.append(hole_desc)
    elif "remove" in user_input_lower and "hole" in user_input_lower:
        summary = [s for s in summary if "hole" not in s.lower()]
    elif "taller" in user_input_lower or "shorter" in user_input_lower:
        summary.append(f"Height adjustment: {user_input}")

    return summary

def load_fusion_state():
    state_path = os.path.join("auto_runner", "state.json")
    try:
        with open(state_path, "r") as f:
            return json.load(f)
    except Exception:
        return None

def summarize_fusion_state(state):
    if not state:
        return "No state snapshot available."
    lines = []
    timestamp = state.get("timestamp", "unknown")
    units = state.get("units", "cm")
    lines.append(f"Last updated: {timestamp}")
    lines.append(
        f"Bodies: {state.get('body_count', 0)}, "
        f"Sketches: {state.get('sketch_count', 0)}, "
        f"Features: {state.get('feature_count', 0)}, "
        f"Units: {units}"
    )
    for body in state.get("bodies", []):
        name = body.get("name", "Body")
        bbox = body.get("bbox") or {}
        size_dict = bbox.get("size") or {}
        center_dict = bbox.get("center") or {}
        size = body.get("size_cm") or [
            size_dict.get("x", 0),
            size_dict.get("y", 0),
            size_dict.get("z", 0),
        ]
        center = body.get("center_cm") or [
            center_dict.get("x", 0),
            center_dict.get("y", 0),
            center_dict.get("z", 0),
        ]
        faces = body.get("face_count", 0)
        lines.append(
            f"- {name}: size {size[0]:.2f}x{size[1]:.2f}x{size[2]:.2f} cm, "
            f"center {center[0]:.2f},{center[1]:.2f},{center[2]:.2f} cm, "
            f"faces {faces}"
        )
    return "\n".join(lines)

def summarize_selection_context(selection):
    if not selection:
        return "No active selections."

    if isinstance(selection, list):
        items = selection
        count = len(items)
    else:
        items = selection.get("items", []) if isinstance(selection, dict) else []
        count = selection.get("count", len(items)) if isinstance(selection, dict) else len(items)

    if count == 0:
        return "No active selections."

    lines = [f"Active selections: {count}"]
    for item in items[:8]:
        kind = item.get("kind", item.get("object_type", "unknown"))
        index = item.get("index", 0)
        if kind == "face":
            normal = item.get("normal") or [0, 0, 0]
            body_name = item.get("body_name", "body")
            area = item.get("area_cm2", 0)
            lines.append(
                f"- [{index}] face on {body_name}, normal {normal[0]:.3f},{normal[1]:.3f},{normal[2]:.3f}, area {area:.2f} cm^2"
            )
        elif kind == "body":
            lines.append(
                f"- [{index}] body {item.get('name', 'Body')}, volume {item.get('volume_cm3', 0):.2f} cm^3"
            )
        elif kind == "edge":
            lines.append(f"- [{index}] edge length {item.get('length_cm', 0):.2f} cm")
        elif kind == "vertex":
            point = item.get("point") or {}
            lines.append(
                f"- [{index}] vertex at {point.get('x', 0):.2f},{point.get('y', 0):.2f},{point.get('z', 0):.2f}"
            )
        else:
            lines.append(f"- [{index}] {kind}")

    if count > 8:
        lines.append(f"... {count - 8} more selections")

    return "\n".join(lines)

if __name__ == "__main__":
    app.run(debug=True)
