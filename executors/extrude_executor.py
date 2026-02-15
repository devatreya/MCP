"""Compiler for extrude_profile operations."""


_EXTRUDE_OPERATION_MAP = {
    "new_body": "adsk.fusion.FeatureOperations.NewBodyFeatureOperation",
    "join": "adsk.fusion.FeatureOperations.JoinFeatureOperation",
    "cut": "adsk.fusion.FeatureOperations.CutFeatureOperation",
}


def compile_extrude_profile(op):
    height_cm = float(op.params["height_cm"])
    operation = op.params.get("operation", "new_body")
    operation_expr = _EXTRUDE_OPERATION_MAP[operation]
    profile_index = int(op.params.get("profile_index", 0))
    sketch_index = int(op.params.get("sketch_index", -1))

    return f"""
if rootComp.sketches.count < 1:
    raise Exception("No sketches available for extrude_profile.")

extrudeSketchIndex = {sketch_index}
if extrudeSketchIndex < 0:
    extrudeSketchIndex = rootComp.sketches.count - 1
if extrudeSketchIndex >= rootComp.sketches.count:
    raise Exception("Requested sketch_index is out of range.")

extrudeSketch = rootComp.sketches.item(extrudeSketchIndex)
if extrudeSketch.profiles.count < 1:
    raise Exception("Selected sketch has no profiles to extrude.")

extrudeProfileIndex = {profile_index}
if extrudeProfileIndex < 0 or extrudeProfileIndex >= extrudeSketch.profiles.count:
    raise Exception("Requested profile_index is out of range.")

extrudeProfile = extrudeSketch.profiles.item(extrudeProfileIndex)
extrudeFeatures = rootComp.features.extrudeFeatures
extrudeInput = extrudeFeatures.createInput(extrudeProfile, {operation_expr})
extrudeDistance = adsk.core.ValueInput.createByReal({height_cm:.6f})
extrudeInput.setDistanceExtent(False, extrudeDistance)
extrudeFeatures.add(extrudeInput)
""".strip()

