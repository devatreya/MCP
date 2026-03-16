"""Selection and dimension validation for extracted CAD intents."""

# Param keys whose values must be positive numbers (dimensions).
_DIMENSION_KEYS = {
    "radius_cm",
    "diameter_cm",
    "depth_cm",
    "height_cm",
    "width_cm",
    "length_cm",
    "thickness_cm",
    "distance_cm",
    "spacing_cm",
    "offset_cm",
    "circumradius_cm",
    "size_cm",
    "angle_deg",
    "count",
}

# Max sane dimension in cm (500 cm = 5 metres).
_MAX_DIM_CM = 500.0

# operation_family → required selection kind(s).
# Value is a list of acceptable kinds (any match passes).
# None means no selection required.
_FAMILY_SELECTION_RULES = {
    "hole": ["face"],
    "shell": ["face"],
    "boolean": ["body", "face"],
}


def _has_selection_kind(selection_context, *kinds):
    """Return True if any of the listed kinds appears in selection_context.

    'plane' is an alias that matches 'constructionplane', 'plane', and 'face'
    so that sketch-plane selections work regardless of how Fusion reports them.
    """
    if not isinstance(selection_context, dict):
        return False
    items = selection_context.get("items")
    if not isinstance(items, list):
        return False
    target_kinds = {str(k).strip().lower() for k in kinds}
    # 'plane' is satisfied by any planar entity the user can click
    plane_aliases = {"constructionplane", "plane", "face"}
    if "plane" in target_kinds:
        target_kinds |= plane_aliases
    for item in items:
        if not isinstance(item, dict):
            continue
        item_kind = str(item.get("kind") or item.get("object_type") or "").strip().lower()
        if item_kind in target_kinds:
            return True
    return False


def _has_any_selection(selection_context):
    if not isinstance(selection_context, dict):
        return False
    count = selection_context.get("count")
    if isinstance(count, int) and count > 0:
        return True
    items = selection_context.get("items", [])
    return isinstance(items, list) and len(items) > 0


def validate_intent(intent_result, selection_context):
    """
    Validate that the selection context and extracted params satisfy the intent.
    Returns a list of issue strings. Empty list means valid.
    """
    issues = []
    family = intent_result.operation_family
    params = intent_result.params or {}
    required_selections = intent_result.required_selections or []

    # --- Unknown family: reject immediately ---
    if family == "unknown":
        issues.append(
            "Unable to interpret this as a Fusion 360 CAD operation. "
            "Please rephrase or be more specific about the geometry you want to create or modify."
        )
        return issues

    # --- Family-level selection requirements ---
    rule = _FAMILY_SELECTION_RULES.get(family)
    if rule:
        if not _has_selection_kind(selection_context, *rule):
            kind_str = " or ".join(rule)
            issues.append(
                f"The '{family}' operation requires a selected {kind_str} in Fusion 360. "
                f"Select the geometry and retry."
            )

    # --- fillet_chamfer: check if intent needs specific edges or faces ---
    if family == "fillet_chamfer":
        if "edge" in required_selections and not _has_selection_kind(selection_context, "edge", "face"):
            issues.append(
                "This chamfer/fillet operation requires selected edges or a face. "
                "Select the target edges or face in Fusion 360 and retry."
            )
        elif "face" in required_selections and not _has_selection_kind(selection_context, "face"):
            issues.append(
                "This chamfer/fillet operation requires a selected face. "
                "Select the target face in Fusion 360 and retry."
            )

    # --- transform: check body selection if explicitly required ---
    if family == "transform" and "body" in required_selections:
        if not _has_selection_kind(selection_context, "body"):
            issues.append(
                "This transform operation requires a selected body. "
                "Select the body in Fusion 360 and retry."
            )

    # --- Dimension validation ---
    for key, value in params.items():
        if key not in _DIMENSION_KEYS:
            continue
        if not isinstance(value, (int, float)):
            continue

        if key == "angle_deg":
            if value <= 0 or value > 360:
                issues.append(f"Parameter '{key}' must be between 0 and 360 degrees (got {value}).")
        elif key == "count":
            if not isinstance(value, int) or value < 1:
                issues.append(f"Parameter '{key}' must be a positive integer (got {value}).")
        else:
            if value <= 0:
                issues.append(f"Parameter '{key}' must be greater than 0 (got {value} cm).")
            elif value > _MAX_DIM_CM:
                issues.append(
                    f"Parameter '{key}' value {value} cm seems unusually large (>{_MAX_DIM_CM} cm). "
                    "Verify the unit — did you mean mm instead of cm?"
                )

    return issues
