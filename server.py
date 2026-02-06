from flask import Flask, request, jsonify, render_template, session
from openai import OpenAI
import os
from datetime import datetime
import re
import textwrap
import json

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

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
    api_key=os.getenv("OPENAI_API_KEY"),
    organization=os.getenv("OPENAI_ORG_ID"),
    project=os.getenv("OPENAI_PROJECT_ID")
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
    
    return jsonify({"status": "reset"})

@app.route("/generate", methods=["POST"])
def generate_script():
    user_prompt = request.json["prompt"]

    if "conversation" not in session:
        session["conversation"] = []
    if "model_summary" not in session:
        session["model_summary"] = []

    conversation = session["conversation"]
    model_summary = session["model_summary"]

    model_summary_text = "\n".join(f"- {item}" for item in model_summary)

    fusion_state = summarize_fusion_state(load_fusion_state())
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
        "   a) Create cutting body with POSITIVE distance and NegativeExtentDirection\n"
        "      distance = adsk.core.ValueInput.createByReal(2)  # POSITIVE VALUE\n"
        "      extInput.setOneSideExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection, distance)\n"
        "   b) Get the newly created tool body (last body in collection)\n"
        "   c) Use combineFeatures with CutFeatureOperation to subtract it\n"
        "   d) Set isKeepToolBodies = False to remove the cutting body\n"
        "7. Center sketches at origin (0,0,0) for simplicity\n\n"
        "Fusion model state (authoritative, from add-in):\n"
        + fusion_state + "\n\n"
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
        "EXAMPLE - Hole on top (diameter 5cm, depth 2cm) using Boolean cut:\n"
        "targetBody = rootComp.bRepBodies.item(0)\n"
        "topFace = None\n"
        "for face in targetBody.faces:\n"
        "    if face.geometry.normal.z > 0.9:\n"
        "        topFace = face\n"
        "        break\n"
        "sketch = sketches.add(topFace)\n"
        "sketch.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), 2.5)\n"
        "innerProf = sketch.profiles.item(0)\n"
        "extrudes = rootComp.features.extrudeFeatures\n"
        "extInput = extrudes.createInput(innerProf, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
        "distance = adsk.core.ValueInput.createByReal(2)\n"
        "extInput.setOneSideExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection, distance)\n"
        "extrudes.add(extInput)\n"
        "toolBody = rootComp.bRepBodies.item(rootComp.bRepBodies.count - 1)\n"
        "combineFeats = rootComp.features.combineFeatures\n"
        "toolBodies = adsk.core.ObjectCollection.create()\n"
        "toolBodies.add(toolBody)\n"
        "combineInput = combineFeats.createInput(targetBody, toolBodies)\n"
        "combineInput.operation = adsk.fusion.FeatureOperations.CutFeatureOperation\n"
        "combineInput.isKeepToolBodies = False\n"
        "combineFeats.add(combineInput)\n\n"
        "Current model state (from prompts):\n" + model_summary_text
    )

    conversation.append({"role": "user", "content": user_prompt})
    messages = [{"role": "system", "content": system_prompt}] + conversation

    response = client.chat.completions.create(
        model="gpt-4o",  # Upgraded to GPT-4o for better code generation
        messages=messages,
        temperature=0.2,
        max_tokens=1500  # Increased for more complex operations
    )

    raw_code = response.choices[0].message.content.strip()
    cleaned_code = clean_generated_code(raw_code)
    clear_model = should_clear_model(user_prompt, model_summary)
    wrapped_code = wrap_script_with_run(cleaned_code, clear_model=clear_model)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"generated_scripts/script_{timestamp}.py"
    with open(filename, "w") as f:
        f.write(wrapped_code)

    session["conversation"] = conversation + [{"role": "assistant", "content": cleaned_code}]
    session["model_summary"] = update_summary(model_summary, user_prompt)

    return jsonify({"status": "success", "script": wrapped_code, "file": filename})

def clean_generated_code(raw_code):
    # Remove markdown code fences
    raw = re.sub(r"```[^\n]*\n", "", raw_code)
    raw = re.sub(r"```", "", raw)

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
        "    try:\n"
        + (normalize_indentation(cleanup_block, indent=8) + "\n\n" if clear_model else "")
        + normalize_indentation(setup_block, indent=8) + "\n\n"
        + normalize_indentation(cleaned_code, indent=8)
        + "\n"
        "    except Exception as e:\n"
        "        if ui:\n"
        "            ui.messageBox('❌ Runtime Error: {}'.format(str(e)))\n"
    )

def should_clear_model(user_prompt, model_summary):
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
    if not model_summary:
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
        summary.append(hole_desc + " on top face")
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
        size = body.get("size_cm") or [0, 0, 0]
        center = body.get("center_cm") or [0, 0, 0]
        faces = body.get("face_count", 0)
        lines.append(
            f"- {name}: size {size[0]:.2f}x{size[1]:.2f}x{size[2]:.2f} cm, "
            f"center {center[0]:.2f},{center[1]:.2f},{center[2]:.2f} cm, "
            f"faces {faces}"
        )
    return "\n".join(lines)

if __name__ == "__main__":
    app.run(debug=True)
