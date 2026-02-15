"""Compiler for create_mounting_bracket operations."""


def compile_create_mounting_bracket(op):
    width_cm = float(op.params["width_cm"])
    leg_depth_cm = float(op.params["leg_depth_cm"])
    leg_height_cm = float(op.params["leg_height_cm"])
    thickness_cm = float(op.params["thickness_cm"])
    hole_diameter_cm = float(op.params.get("hole_diameter_cm", 0.6))
    hole_radius_cm = hole_diameter_cm / 2.0
    hole_spacing_cm = float(op.params.get("hole_spacing_cm", 3.0))
    base_hole_count = int(op.params.get("base_hole_count", 2))
    wall_hole_count = int(op.params.get("wall_hole_count", 2))

    return f"""
bracketPlane = rootComp.yZConstructionPlane
bracketExtrudes = rootComp.features.extrudeFeatures

def _pick_profile_by_area(sketch_obj, expected_area):
    best = None
    best_score = 1e99
    for i in range(sketch_obj.profiles.count):
        candidate = sketch_obj.profiles.item(i)
        try:
            props = candidate.areaProperties(adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy)
            area_value = abs(props.area)
        except:
            continue
        score = abs(area_value - expected_area)
        if score < best_score:
            best_score = score
            best = candidate
    if not best:
        raise Exception("Failed to find expected bracket profile.")
    return best

# Horizontal leg (depth x thickness) on YZ plane.
baseSketch = sketches.add(bracketPlane)
baseLines = baseSketch.sketchCurves.sketchLines
baseLines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0),
    adsk.core.Point3D.create({leg_depth_cm:.6f}, {thickness_cm:.6f}, 0),
)
baseProfile = _pick_profile_by_area(baseSketch, {leg_depth_cm:.6f} * {thickness_cm:.6f})
baseInput = bracketExtrudes.createInput(baseProfile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
baseDistance = adsk.core.ValueInput.createByReal({width_cm:.6f})
baseInput.setDistanceExtent(False, baseDistance)
bracketExtrudes.add(baseInput)

# Vertical leg (thickness x height) joined to form an L bracket section.
wallSketch = sketches.add(bracketPlane)
wallLines = wallSketch.sketchCurves.sketchLines
wallLines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0),
    adsk.core.Point3D.create({thickness_cm:.6f}, {leg_height_cm:.6f}, 0),
)
wallProfile = _pick_profile_by_area(wallSketch, {thickness_cm:.6f} * {leg_height_cm:.6f})
wallInput = bracketExtrudes.createInput(wallProfile, adsk.fusion.FeatureOperations.JoinFeatureOperation)
wallDistance = adsk.core.ValueInput.createByReal({width_cm:.6f})
wallInput.setDistanceExtent(False, wallDistance)
bracketExtrudes.add(wallInput)

if rootComp.bRepBodies.count < 1:
    raise Exception("Mounting bracket body was not created.")
bracketBody = rootComp.bRepBodies.item(rootComp.bRepBodies.count - 1)

def _pick_face_by_axis(body_obj, axis_name, positive=True):
    axis_idx = 0 if axis_name == "x" else 1 if axis_name == "y" else 2
    best_face = None
    best_area = -1.0
    for i in range(body_obj.faces.count):
        face = body_obj.faces.item(i)
        plane = adsk.core.Plane.cast(face.geometry)
        if not plane:
            continue
        normal = plane.normal
        component = normal.x if axis_idx == 0 else normal.y if axis_idx == 1 else normal.z
        if positive and component < 0.85:
            continue
        if (not positive) and component > -0.85:
            continue
        area_val = 0.0
        try:
            area_val = abs(face.area)
        except:
            area_val = 0.0
        if area_val > best_area:
            best_area = area_val
            best_face = face
    return best_face

def _cut_profile_through_all(profile_obj, body_obj):
    def _try_direction(direction):
        vol_before = body_obj.volume
        cut_input = bracketExtrudes.createInput(profile_obj, adsk.fusion.FeatureOperations.CutFeatureOperation)
        extent_def = adsk.fusion.ThroughAllExtentDefinition.create()
        cut_input.setOneSideExtent(extent_def, direction)
        cut_feat = bracketExtrudes.add(cut_input)
        vol_after = body_obj.volume
        if (vol_before - vol_after) <= 1e-6:
            try:
                cut_feat.deleteMe()
            except:
                pass
            return False
        return True

    if not _try_direction(adsk.fusion.ExtentDirections.NegativeExtentDirection):
        if not _try_direction(adsk.fusion.ExtentDirections.PositiveExtentDirection):
            raise Exception("Failed to cut bracket hole through target face.")

def _add_holes_on_face(target_face, hole_count):
    if not target_face or hole_count <= 0:
        return

    hole_sketch = sketches.add(target_face)
    face_box = target_face.boundingBox
    center_world = adsk.core.Point3D.create(
        (face_box.minPoint.x + face_box.maxPoint.x) / 2.0,
        (face_box.minPoint.y + face_box.maxPoint.y) / 2.0,
        (face_box.minPoint.z + face_box.maxPoint.z) / 2.0,
    )

    effective_spacing = {hole_spacing_cm:.6f}
    if hole_count > 1:
        max_span = {width_cm:.6f} - ({hole_diameter_cm:.6f} * 1.5)
        if max_span <= 0:
            effective_spacing = 0.0
        else:
            effective_spacing = min(effective_spacing, max_span / float(hole_count - 1))

    for hole_idx in range(hole_count):
        if hole_count == 1:
            offset = 0.0
        else:
            offset = (hole_idx - (hole_count - 1) / 2.0) * effective_spacing
        point_world = adsk.core.Point3D.create(
            center_world.x + offset,
            center_world.y,
            center_world.z,
        )
        point_sketch = hole_sketch.modelToSketchSpace(point_world)
        hole_sketch.sketchCurves.sketchCircles.addByCenterRadius(point_sketch, {hole_radius_cm:.6f})

    expected_area = 3.141592653589793 * ({hole_radius_cm:.6f} ** 2)
    candidates = []
    for i in range(hole_sketch.profiles.count):
        profile_obj = hole_sketch.profiles.item(i)
        try:
            props = profile_obj.areaProperties(adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy)
            area_value = abs(props.area)
        except:
            continue
        score = abs(area_value - expected_area)
        if score <= max(expected_area * 0.75, 1e-6):
            candidates.append((score, profile_obj))

    if not candidates:
        raise Exception("Failed to resolve bracket hole profiles from sketch.")
    candidates.sort(key=lambda item: item[0])

    for _, profile_obj in candidates[:hole_count]:
        _cut_profile_through_all(profile_obj, bracketBody)

baseHoleFace = _pick_face_by_axis(bracketBody, "z", positive=True)
wallHoleFace = _pick_face_by_axis(bracketBody, "y", positive=True)
if not wallHoleFace:
    wallHoleFace = _pick_face_by_axis(bracketBody, "y", positive=False)

_add_holes_on_face(baseHoleFace, {base_hole_count})
_add_holes_on_face(wallHoleFace, {wall_hole_count})
""".strip()

