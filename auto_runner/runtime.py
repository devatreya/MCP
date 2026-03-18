import json
import os
import re
import time
import traceback
import urllib.error
import urllib.request
import http.cookiejar

import adsk.core
import adsk.fusion

ADDIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(ADDIN_DIR, os.pardir))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated_scripts")
SCRIPT_FILE = os.path.join(ADDIN_DIR, "fusion_auto_run.py")
STATE_FILE = os.path.join(ADDIN_DIR, "state.json")
LOG_FILE = os.path.join(ADDIN_DIR, "log.txt")

_cookie_jar = http.cookiejar.CookieJar()
_http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_STEP_TAG_RE = re.compile(r"\[STEP:([^\]]+)\]")


def log(message: str):
    try:
        timestamp = time.strftime("%H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def _extract_step_tag(text):
    if not text:
        return None
    match = _STEP_TAG_RE.search(text)
    if not match:
        return None
    return match.group(1).strip() or None


def _build_retry_context(stage, failed_step=None, issues=None):
    context = {
        "stage": stage,
        "recommended_action": "Regenerate only the failing step and keep previous successful geometry steps unchanged.",
    }
    if failed_step:
        context["failed_step"] = failed_step
    if issues:
        context["issues"] = issues
    return context


def _point_dict(point):
    if not point:
        return None
    return {
        "x": _safe_float(point.x),
        "y": _safe_float(point.y),
        "z": _safe_float(point.z),
    }


def _vector_list(vector):
    if not vector:
        return None
    return [
        _safe_float(vector.x),
        _safe_float(vector.y),
        _safe_float(vector.z),
    ]


def _bbox_dict(bbox):
    if not bbox:
        return None
    minimum = _point_dict(bbox.minPoint)
    maximum = _point_dict(bbox.maxPoint)
    if not minimum or not maximum:
        return None
    size = {
        "x": _safe_float(maximum["x"] - minimum["x"]),
        "y": _safe_float(maximum["y"] - minimum["y"]),
        "z": _safe_float(maximum["z"] - minimum["z"]),
    }
    center = {
        "x": _safe_float((maximum["x"] + minimum["x"]) / 2.0),
        "y": _safe_float((maximum["y"] + minimum["y"]) / 2.0),
        "z": _safe_float((maximum["z"] + minimum["z"]) / 2.0),
    }
    return {
        "min": minimum,
        "max": maximum,
        "size": size,
        "center": center,
    }


def _design():
    app = adsk.core.Application.get()
    if not app:
        return None
    return adsk.fusion.Design.cast(app.activeProduct)


def capture_model_state():
    design = _design()
    if not design:
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": "No active Fusion design",
            "body_count": 0,
            "sketch_count": 0,
            "feature_count": 0,
            "units": "cm",
            "bodies": [],
        }

    root = design.rootComponent
    units = "cm"
    try:
        units = design.unitsManager.defaultLengthUnits
    except Exception:
        units = "cm"

    bodies = []
    for idx in range(root.bRepBodies.count):
        body = root.bRepBodies.item(idx)
        body_data = {
            "index": idx,
            "name": body.name,
            "face_count": body.faces.count,
            "edge_count": body.edges.count,
            "volume_cm3": _safe_float(getattr(body, "volume", 0.0)),
            "bbox": _bbox_dict(getattr(body, "boundingBox", None)),
        }

        # Analyse face surface types so the LLM knows if a body is cylindrical.
        # A cylinder has no flat "front" face — the agent must use a construction plane.
        try:
            flat_normals = []
            curved_face_count = 0
            for f_idx in range(body.faces.count):
                face = body.faces.item(f_idx)
                geom = face.geometry
                surf_type = getattr(geom, "surfaceType", None)
                # SurfaceTypes: 0=Plane, 1=Cylinder, 2=Cone, 3=Sphere, 4=Torus,
                #               5=EllipticalCylinder, 6=EllipticalCone, 7=NurbsSurface
                if surf_type == 0:  # Plane
                    n = _face_normal(face)
                    if n:
                        flat_normals.append(n)
                else:
                    curved_face_count += 1
            body_data["curved_face_count"] = curved_face_count
            body_data["flat_face_normals"] = flat_normals
            body_data["is_prismatic"] = (curved_face_count == 0)
            body_data["has_curved_faces"] = (curved_face_count > 0)
        except Exception:
            body_data["curved_face_count"] = 0
            body_data["flat_face_normals"] = []
            body_data["is_prismatic"] = True
            body_data["has_curved_faces"] = False

        bodies.append(body_data)

    # Capture origin construction planes so the LLM knows they're available
    origin_planes = []
    try:
        origin_planes = [
            {"name": root.xYConstructionPlane.name, "code": "rootComp.xYConstructionPlane", "label": "XY (Top/Bottom)"},
            {"name": root.xZConstructionPlane.name, "code": "rootComp.xZConstructionPlane", "label": "XZ (Front/Back)"},
            {"name": root.yZConstructionPlane.name, "code": "rootComp.yZConstructionPlane", "label": "YZ (Left/Right)"},
        ]
    except Exception:
        pass

    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "units": units,
        "body_count": root.bRepBodies.count,
        "sketch_count": root.sketches.count,
        "feature_count": root.features.count,
        "bodies": bodies,
        "origin_planes": origin_planes,
    }


def _face_normal(face):
    try:
        plane = adsk.core.Plane.cast(face.geometry)
        if plane:
            return _vector_list(plane.normal)
    except Exception:
        pass

    try:
        normal = getattr(face.geometry, "normal", None)
        if normal:
            return _vector_list(normal)
    except Exception:
        pass

    return None


def _selection_item(entity, index):
    item = {
        "index": index,
        "object_type": getattr(entity, "objectType", "Unknown"),
        "name": getattr(entity, "name", ""),
    }

    face = adsk.fusion.BRepFace.cast(entity)
    if face:
        item["kind"] = "face"
        item["body_name"] = getattr(face.body, "name", "")
        item["area_cm2"] = _safe_float(getattr(face, "area", 0.0))
        item["normal"] = _face_normal(face)
        item["bbox"] = _bbox_dict(getattr(face, "boundingBox", None))
        return item

    body = adsk.fusion.BRepBody.cast(entity)
    if body:
        item["kind"] = "body"
        item["volume_cm3"] = _safe_float(getattr(body, "volume", 0.0))
        item["face_count"] = body.faces.count
        item["bbox"] = _bbox_dict(getattr(body, "boundingBox", None))
        return item

    edge = adsk.fusion.BRepEdge.cast(entity)
    if edge:
        item["kind"] = "edge"
        item["length_cm"] = _safe_float(getattr(edge, "length", 0.0))
        start = None
        end = None
        try:
            start = _point_dict(edge.startVertex.geometry)
            end = _point_dict(edge.endVertex.geometry)
        except Exception:
            start = None
            end = None
        item["start"] = start
        item["end"] = end
        return item

    vertex = adsk.fusion.BRepVertex.cast(entity)
    if vertex:
        item["kind"] = "vertex"
        item["point"] = _point_dict(vertex.geometry)
        return item

    construction_plane = adsk.fusion.ConstructionPlane.cast(entity)
    if construction_plane:
        item["kind"] = "constructionplane"
        item["plane_name"] = getattr(construction_plane, "name", "")
        return item

    construction_axis = adsk.fusion.ConstructionAxis.cast(entity)
    if construction_axis:
        item["kind"] = "constructionaxis"
        item["axis_name"] = getattr(construction_axis, "name", "")
        return item

    sketch_point = adsk.fusion.SketchPoint.cast(entity)
    if sketch_point:
        item["kind"] = "sketch_point"
        item["point"] = _point_dict(sketch_point.geometry)
        return item

    item["kind"] = "unknown"
    return item


def capture_selection_context():
    app = adsk.core.Application.get()
    ui = app.userInterface if app else None
    if not ui:
        return {"count": 0, "items": []}

    active = ui.activeSelections
    items = []
    for idx in range(active.count):
        try:
            entity = active.item(idx).entity
            items.append(_selection_item(entity, idx))
        except Exception:
            items.append({"index": idx, "kind": "unknown", "error": "Failed to read selection"})

    return {
        "count": len(items),
        "items": items,
    }


def write_state_snapshot(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def capture_and_store_current_context():
    model_state = capture_model_state()
    write_state_snapshot(model_state)
    selection = capture_selection_context()
    return model_state, selection


def summarize_model_state(state):
    if not state:
        return "No model state available."

    if state.get("error"):
        return state.get("error")

    lines = []
    lines.append(
        f"Bodies: {state.get('body_count', 0)}, "
        f"Sketches: {state.get('sketch_count', 0)}, "
        f"Features: {state.get('feature_count', 0)}, "
        f"Units: {state.get('units', 'cm')}"
    )

    bodies = state.get("bodies", [])
    if not bodies:
        lines.append("No bodies in root component.")
        return "\n".join(lines)

    for body in bodies[:8]:
        bbox = body.get("bbox") or {}
        size = (bbox.get("size") or {})
        center = (bbox.get("center") or {})
        has_curved = body.get("has_curved_faces", False)
        curved_count = body.get("curved_face_count", 0)
        flat_normals = body.get("flat_face_normals") or []

        shape_hint = ""
        if has_curved:
            shape_hint = f" [CURVED BODY: {curved_count} curved face(s)"
            if flat_normals:
                normal_strs = [f"({n[0]:.2f},{n[1]:.2f},{n[2]:.2f})" for n in flat_normals[:4]]
                shape_hint += f", flat faces: {', '.join(normal_strs)}"
                shape_hint += " — for SIDE sketch use construction plane; top/bottom flat faces ARE searchable by normal]"
            else:
                shape_hint += ", NO flat faces — use construction plane for any sketch, do not search face normals]"
        else:
            if flat_normals:
                normal_strs = [f"({n[0]:.2f},{n[1]:.2f},{n[2]:.2f})" for n in flat_normals[:4]]
                shape_hint = f" [prismatic, flat face normals: {', '.join(normal_strs)}]"

        lines.append(
            f"- {body.get('name', 'Body')}: "
            f"size {size.get('x', 0):.2f}x{size.get('y', 0):.2f}x{size.get('z', 0):.2f} cm, "
            f"center {center.get('x', 0):.2f},{center.get('y', 0):.2f},{center.get('z', 0):.2f} cm, "
            f"faces {body.get('face_count', 0)}"
            f"{shape_hint}"
        )
    if len(bodies) > 8:
        lines.append(f"... {len(bodies) - 8} more bodies")

    # Include origin construction planes so LLM knows they're available
    origin_planes = state.get("origin_planes", [])
    if origin_planes:
        lines.append("Origin planes:")
        for p in origin_planes:
            lines.append(f"  - {p['name']} → {p['code']} ({p['label']})")

    return "\n".join(lines)


def summarize_selection_context(selection):
    if not selection:
        return "No selection context available."

    count = selection.get("count", 0)
    if count == 0:
        return "No active selections."

    lines = [f"Active selections: {count}"]
    for item in selection.get("items", [])[:8]:
        kind = item.get("kind", "unknown")
        index = item.get("index", 0)
        if kind == "face":
            normal = item.get("normal") or [0.0, 0.0, 0.0]
            lines.append(
                f"- [{index}] face on {item.get('body_name', 'body')}, "
                f"normal {normal[0]:.3f},{normal[1]:.3f},{normal[2]:.3f}, "
                f"area {item.get('area_cm2', 0):.2f} cm^2"
            )
        elif kind == "body":
            lines.append(
                f"- [{index}] body {item.get('name', 'Body')}, "
                f"volume {item.get('volume_cm3', 0):.2f} cm^3"
            )
        elif kind == "edge":
            lines.append(f"- [{index}] edge length {item.get('length_cm', 0):.2f} cm")
        elif kind == "vertex":
            point = item.get("point") or {}
            lines.append(
                f"- [{index}] vertex at {point.get('x', 0):.2f},{point.get('y', 0):.2f},{point.get('z', 0):.2f}"
            )
        elif kind == "constructionplane":
            plane_name = item.get("plane_name") or item.get("name", "unknown plane")
            lines.append(
                f"- [{index}] construction plane '{plane_name}' "
                f"(use: sketches.add(entity) where entity = ui.activeSelections.item({index}).entity)"
            )
        elif kind == "constructionaxis":
            axis_name = item.get("axis_name") or item.get("name", "unknown axis")
            lines.append(f"- [{index}] construction axis '{axis_name}'")
        else:
            lines.append(f"- [{index}] {kind}")

    if count > 8:
        lines.append(f"... {count - 8} more selections")

    return "\n".join(lines)


def save_script_snapshot(script_text, prefix="panel"):
    os.makedirs(GENERATED_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    file_name = f"{prefix}_{timestamp}.py"
    file_path = os.path.join(GENERATED_DIR, file_name)
    with open(file_path, "w") as f:
        f.write(script_text)
    return file_path


def execute_wrapped_script(script_text):
    app = adsk.core.Application.get()
    ui = app.userInterface if app else None

    injected_scope = {
        "adsk": adsk,
        "os": os,
        "time": time,
        "traceback": traceback,
    }

    try:
        exec(script_text, injected_scope)
    except Exception as exc:
        trace = traceback.format_exc()
        return {
            "ok": False,
            "error": f"Failed to compile script: {exc}",
            "traceback": trace,
            "retry_context": _build_retry_context(
                "script_compile",
                failed_step=_extract_step_tag(str(exc)) or _extract_step_tag(trace),
            ),
        }

    run_fn = injected_scope.get("run")
    if not callable(run_fn):
        return {
            "ok": False,
            "error": "Script did not define run(context).",
            "retry_context": _build_retry_context("script_compile"),
        }

    try:
        run_fn(None)
        state, selection = capture_and_store_current_context()
        return {
            "ok": True,
            "state": state,
            "selection": selection,
        }
    except Exception as exc:
        trace = traceback.format_exc()
        failed_step = _extract_step_tag(str(exc)) or _extract_step_tag(trace)
        error_str = str(exc)
        # SELECTION_REQUIRED is a controlled flow signal — skip the popup,
        # it will be handled gracefully by the step pipeline in the palette.
        is_selection_required = "SELECTION_REQUIRED:" in error_str
        if ui and not is_selection_required:
            try:
                ui.messageBox(f"Script execution error: {exc}")
            except Exception:
                pass
        return {
            "ok": False,
            "error": f"Script execution error: {error_str}",
            "traceback": trace,
            "retry_context": _build_retry_context("script_execution", failed_step=failed_step),
        }


def execute_script_file(path):
    try:
        with open(path, "r") as f:
            script_text = f.read()
    except Exception as exc:
        return {"ok": False, "error": f"Unable to read script file: {exc}"}
    return execute_wrapped_script(script_text)


def server_base_url():
    return os.getenv("MCP_SERVER_URL", "http://127.0.0.1:5000").rstrip("/")


def _post_json(path, payload, timeout=180):
    url = f"{server_base_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with _http.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        parsed = {}
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {}
        if isinstance(parsed, dict) and parsed:
            return {
                "ok": False,
                "error": parsed.get("error", f"Server returned HTTP {exc.code}: {exc.reason}"),
                "data": parsed,
            }
        return {
            "ok": False,
            "error": f"Server returned HTTP {exc.code}: {body or exc.reason}",
            "data": {"raw_body": body} if body else {},
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Could not reach server at {url}: {exc}",
        }

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {"status": "error", "error": f"Invalid JSON from server: {raw}"}

    if data.get("status") in {"success", "reset"}:
        return {"ok": True, "data": data}

    return {
        "ok": False,
        "error": data.get("error", "Server returned an error response."),
        "data": data,
    }


def request_generate(prompt, model_state, selection_context):
    payload = {
        "prompt": prompt,
        "fusion_state": model_state,
        "selection_context": selection_context,
        "source": "fusion_panel",
    }
    return _post_json("/generate", payload, timeout=240)


def request_reset():
    payload = {"source": "fusion_panel"}
    return _post_json("/reset", payload, timeout=120)


def monitor_script_file(stop_event, poll_seconds=1.5):
    last_mtime = None
    if os.path.exists(SCRIPT_FILE):
        try:
            last_mtime = os.path.getmtime(SCRIPT_FILE)
        except Exception:
            last_mtime = None

    log("Script monitor thread started")

    while not stop_event.is_set():
        try:
            if os.path.exists(SCRIPT_FILE):
                mtime = os.path.getmtime(SCRIPT_FILE)
                if last_mtime is None:
                    last_mtime = mtime
                elif mtime != last_mtime:
                    last_mtime = mtime
                    log("Detected fusion_auto_run.py change, executing")
                    result = execute_script_file(SCRIPT_FILE)
                    if not result.get("ok"):
                        log(f"Execution error: {result.get('error')}")
                        trace = result.get("traceback")
                        if trace:
                            log(trace)
                    else:
                        log("Execution complete")
        except Exception:
            log("Monitor loop exception")
            log(traceback.format_exc())

        stop_event.wait(poll_seconds)

    log("Script monitor thread stopped")
