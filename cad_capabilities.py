"""Capability contract for structured CAD generation."""

CAPABILITY_VERSION = "v1"

SUPPORTED_OPERATIONS = {
    "create_cube",
    "extrude_profile",
    "create_hole",
    "create_chamfer",
    "create_mounting_bracket",
}

VALID_EXTRUDE_OPERATIONS = {
    "new_body",
    "join",
    "cut",
}

VALID_HOLE_EXTENT_MODES = {
    "distance",
    "through_all",
}

VALID_HOLE_TARGETS = {
    "selected_face_center",
}

VALID_CHAMFER_MODES = {
    "equal_distance",
}

VALID_CHAMFER_TARGETS = {
    "selected_edges",
    "selected_face_edges",
}

OPERATION_SCHEMAS = {
    "create_cube": {
        "required": ["size_cm"],
        "optional": ["origin_mode"],
        "defaults": {"origin_mode": "centered_xy"},
    },
    "extrude_profile": {
        "required": ["height_cm"],
        "optional": ["operation", "profile_index", "sketch_index"],
        "defaults": {"operation": "new_body", "profile_index": 0, "sketch_index": -1},
    },
    "create_hole": {
        "required": ["diameter_cm"],
        "optional": ["depth_cm", "extent_mode", "target"],
        "defaults": {"extent_mode": "distance", "target": "selected_face_center"},
    },
    "create_chamfer": {
        "required": ["distance_cm"],
        "optional": ["mode", "target"],
        "defaults": {"mode": "equal_distance", "target": "selected_edges"},
    },
    "create_mounting_bracket": {
        "required": ["width_cm", "leg_depth_cm", "leg_height_cm", "thickness_cm"],
        "optional": [
            "hole_diameter_cm",
            "hole_spacing_cm",
            "base_hole_count",
            "wall_hole_count",
        ],
        "defaults": {
            "hole_diameter_cm": 0.6,
            "hole_spacing_cm": 3.0,
            "base_hole_count": 2,
            "wall_hole_count": 2,
        },
    },
}


def list_supported_operations():
    return sorted(SUPPORTED_OPERATIONS)


def get_operation_schema(op_name):
    return OPERATION_SCHEMAS.get(op_name)
