"""Validation for structured CAD plans."""

from cad_capabilities import (
    CAPABILITY_VERSION,
    OPERATION_SCHEMAS,
    SUPPORTED_OPERATIONS,
    VALID_CHAMFER_MODES,
    VALID_CHAMFER_TARGETS,
    VALID_EXTRUDE_OPERATIONS,
    VALID_HOLE_EXTENT_MODES,
    VALID_HOLE_TARGETS,
)


def _has_selection_kind(selection_context, kind):
    if not isinstance(selection_context, dict):
        return False
    items = selection_context.get("items")
    if not isinstance(items, list):
        return False
    target = str(kind).strip().lower()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_kind = str(item.get("kind") or item.get("object_type") or "").strip().lower()
        if item_kind == target:
            return True
    return False


def _is_positive_number(value):
    return isinstance(value, (int, float)) and float(value) > 0


def validate_plan(plan, selection_context=None):
    issues = []

    if plan.version != CAPABILITY_VERSION:
        issues.append(
            f"Plan version '{plan.version}' is not supported; expected '{CAPABILITY_VERSION}'."
        )

    if not plan.operations:
        issues.append("Plan contains no operations.")
        return issues

    if len(plan.operations) > 12:
        issues.append("Plan exceeds max operation count (12).")

    for op in plan.operations:
        op_tag = f"{op.id}:{op.op}"
        if op.op not in SUPPORTED_OPERATIONS:
            issues.append(f"{op_tag} uses unsupported operation '{op.op}'.")
            continue

        schema = OPERATION_SCHEMAS.get(op.op, {})
        required = schema.get("required", [])
        for key in required:
            if key not in op.params:
                issues.append(f"{op_tag} missing required parameter '{key}'.")

        if op.op == "create_cube":
            size = op.params.get("size_cm")
            if not _is_positive_number(size):
                issues.append(f"{op_tag} requires size_cm > 0.")

        elif op.op == "extrude_profile":
            height = op.params.get("height_cm")
            if not _is_positive_number(height):
                issues.append(f"{op_tag} requires height_cm > 0.")
            operation = op.params.get("operation")
            if operation not in VALID_EXTRUDE_OPERATIONS:
                issues.append(
                    f"{op_tag} has invalid operation '{operation}'. "
                    f"Allowed: {sorted(VALID_EXTRUDE_OPERATIONS)}."
                )

        elif op.op == "create_hole":
            diameter = op.params.get("diameter_cm")
            if not _is_positive_number(diameter):
                issues.append(f"{op_tag} requires diameter_cm > 0.")

            extent_mode = op.params.get("extent_mode")
            if extent_mode not in VALID_HOLE_EXTENT_MODES:
                issues.append(
                    f"{op_tag} has invalid extent_mode '{extent_mode}'. "
                    f"Allowed: {sorted(VALID_HOLE_EXTENT_MODES)}."
                )
            if extent_mode == "distance":
                depth = op.params.get("depth_cm")
                if not _is_positive_number(depth):
                    issues.append(f"{op_tag} requires depth_cm > 0 for distance holes.")

            target = op.params.get("target")
            if target not in VALID_HOLE_TARGETS:
                issues.append(
                    f"{op_tag} has invalid target '{target}'. "
                    f"Allowed: {sorted(VALID_HOLE_TARGETS)}."
                )
            if target == "selected_face_center" and not _has_selection_kind(selection_context, "face"):
                issues.append(f"{op_tag} requires a selected face.")

        elif op.op == "create_chamfer":
            distance = op.params.get("distance_cm")
            if not _is_positive_number(distance):
                issues.append(f"{op_tag} requires distance_cm > 0.")

            mode = op.params.get("mode")
            if mode not in VALID_CHAMFER_MODES:
                issues.append(
                    f"{op_tag} has invalid mode '{mode}'. Allowed: {sorted(VALID_CHAMFER_MODES)}."
                )

            target = op.params.get("target")
            if target not in VALID_CHAMFER_TARGETS:
                issues.append(
                    f"{op_tag} has invalid target '{target}'. Allowed: {sorted(VALID_CHAMFER_TARGETS)}."
                )

            if target == "selected_edges" and not _has_selection_kind(selection_context, "edge"):
                # Allow fallback to selected face edges, but only if face is selected.
                if not _has_selection_kind(selection_context, "face"):
                    issues.append(f"{op_tag} requires selected edges or a selected face.")
            if target == "selected_face_edges" and not _has_selection_kind(selection_context, "face"):
                issues.append(f"{op_tag} requires a selected face.")

        elif op.op == "create_mounting_bracket":
            width = op.params.get("width_cm")
            leg_depth = op.params.get("leg_depth_cm")
            leg_height = op.params.get("leg_height_cm")
            thickness = op.params.get("thickness_cm")
            hole_diameter = op.params.get("hole_diameter_cm")
            hole_spacing = op.params.get("hole_spacing_cm")
            base_hole_count = op.params.get("base_hole_count")
            wall_hole_count = op.params.get("wall_hole_count")

            if not _is_positive_number(width):
                issues.append(f"{op_tag} requires width_cm > 0.")
            if not _is_positive_number(leg_depth):
                issues.append(f"{op_tag} requires leg_depth_cm > 0.")
            if not _is_positive_number(leg_height):
                issues.append(f"{op_tag} requires leg_height_cm > 0.")
            if not _is_positive_number(thickness):
                issues.append(f"{op_tag} requires thickness_cm > 0.")

            if _is_positive_number(thickness) and _is_positive_number(leg_depth):
                if thickness >= leg_depth:
                    issues.append(f"{op_tag} requires thickness_cm < leg_depth_cm.")
            if _is_positive_number(thickness) and _is_positive_number(leg_height):
                if thickness >= leg_height:
                    issues.append(f"{op_tag} requires thickness_cm < leg_height_cm.")

            if hole_diameter is not None and not _is_positive_number(hole_diameter):
                issues.append(f"{op_tag} requires hole_diameter_cm > 0 when provided.")
            if hole_spacing is not None and not _is_positive_number(hole_spacing):
                issues.append(f"{op_tag} requires hole_spacing_cm > 0 when provided.")

            for label, count in [("base_hole_count", base_hole_count), ("wall_hole_count", wall_hole_count)]:
                if not isinstance(count, int) or count < 0 or count > 6:
                    issues.append(f"{op_tag} requires {label} as an integer between 0 and 6.")

    return issues
