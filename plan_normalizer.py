"""Normalization pass for structured CAD plans."""

import re

from cad_capabilities import get_operation_schema
from cad_ir import PlanIR, PlanOperation


_UNIT_TO_CM = {
    "cm": 1.0,
    "mm": 0.1,
    "m": 100.0,
    "in": 2.54,
    "inch": 2.54,
    "inches": 2.54,
}

_HOLE_TARGET_ALIASES = {
    "selected_face": "selected_face_center",
    "face": "selected_face_center",
    "this_face": "selected_face_center",
    "this face": "selected_face_center",
    "current_face": "selected_face_center",
    "current face": "selected_face_center",
}


def _to_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        match = re.match(r"^(-?[0-9]+(?:\.[0-9]+)?)$", text)
        if match:
            return float(match.group(1))
    return None


def _to_cm(value):
    # Supports scalar numbers, "12 mm" strings, and {"value": 12, "unit": "mm"}.
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        text = value.strip().lower()
        match = re.match(r"^(-?[0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|in|inch|inches)$", text)
        if match:
            mag = float(match.group(1))
            unit = match.group(2)
            return mag * _UNIT_TO_CM[unit]
        as_float = _to_float(text)
        if as_float is not None:
            return as_float

    if isinstance(value, dict):
        mag = _to_float(value.get("value"))
        unit = str(value.get("unit") or "cm").strip().lower()
        if mag is not None and unit in _UNIT_TO_CM:
            return mag * _UNIT_TO_CM[unit]

    return None


def _normalized_params(op_name, params):
    schema = get_operation_schema(op_name) or {}
    defaults = schema.get("defaults") or {}
    out = dict(defaults)
    out.update(params or {})

    if op_name == "create_cube":
        size_cm = _to_cm(out.get("size_cm"))
        if size_cm is not None:
            out["size_cm"] = size_cm

    elif op_name == "extrude_profile":
        height_cm = _to_cm(out.get("height_cm"))
        if height_cm is not None:
            out["height_cm"] = height_cm
        profile_index = _to_float(out.get("profile_index"))
        sketch_index = _to_float(out.get("sketch_index"))
        if profile_index is not None:
            out["profile_index"] = int(profile_index)
        if sketch_index is not None:
            out["sketch_index"] = int(sketch_index)
        if "operation" in out and isinstance(out["operation"], str):
            out["operation"] = out["operation"].strip().lower()

    elif op_name == "create_hole":
        diameter_cm = _to_cm(out.get("diameter_cm"))
        if diameter_cm is None:
            radius_cm = _to_cm(out.get("radius_cm"))
            if radius_cm is not None:
                diameter_cm = radius_cm * 2.0
        if diameter_cm is not None:
            out["diameter_cm"] = diameter_cm

        depth_cm = _to_cm(out.get("depth_cm"))
        if depth_cm is not None:
            out["depth_cm"] = depth_cm

        if "extent_mode" in out and isinstance(out["extent_mode"], str):
            out["extent_mode"] = out["extent_mode"].strip().lower()
        if out.get("through_all") is True:
            out["extent_mode"] = "through_all"

        if "target" in out and isinstance(out["target"], str):
            target = out["target"].strip().lower()
            out["target"] = _HOLE_TARGET_ALIASES.get(target, target)

    elif op_name == "create_chamfer":
        distance_cm = _to_cm(out.get("distance_cm"))
        if distance_cm is not None:
            out["distance_cm"] = distance_cm
        if "mode" in out and isinstance(out["mode"], str):
            out["mode"] = out["mode"].strip().lower()
        if "target" in out and isinstance(out["target"], str):
            out["target"] = out["target"].strip().lower()

    elif op_name == "create_mounting_bracket":
        for key in [
            "width_cm",
            "leg_depth_cm",
            "leg_height_cm",
            "thickness_cm",
            "hole_diameter_cm",
            "hole_spacing_cm",
        ]:
            value = _to_cm(out.get(key))
            if value is not None:
                out[key] = value

        base_hole_count = _to_float(out.get("base_hole_count"))
        wall_hole_count = _to_float(out.get("wall_hole_count"))
        if base_hole_count is not None:
            out["base_hole_count"] = int(base_hole_count)
        if wall_hole_count is not None:
            out["wall_hole_count"] = int(wall_hole_count)

    return out


def normalize_plan(plan):
    if not isinstance(plan, PlanIR):
        raise TypeError("normalize_plan expects PlanIR")

    normalized_ops = []
    for idx, op in enumerate(plan.operations, start=1):
        op_id = str(op.id or f"op_{idx}").strip() or f"op_{idx}"
        normalized_ops.append(
            PlanOperation(
                id=op_id,
                op=str(op.op or "").strip(),
                params=_normalized_params(op.op, op.params),
            )
        )

    return PlanIR(
        version=str(plan.version or "").strip() or "v1",
        operations=normalized_ops,
        metadata=dict(plan.metadata or {}),
    )
