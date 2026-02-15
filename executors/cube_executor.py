"""Compiler for create_cube operations."""


def compile_create_cube(op):
    size_cm = float(op.params["size_cm"])
    half_cm = size_cm / 2.0
    return f"""
xyPlane = rootComp.xYConstructionPlane
cubeSketch = sketches.add(xyPlane)
cubeLines = cubeSketch.sketchCurves.sketchLines
cubeLines.addTwoPointRectangle(
    adsk.core.Point3D.create({-half_cm:.6f}, {-half_cm:.6f}, 0),
    adsk.core.Point3D.create({half_cm:.6f}, {half_cm:.6f}, 0),
)
if cubeSketch.profiles.count < 1:
    raise Exception("Failed to create cube profile.")
cubeProfile = cubeSketch.profiles.item(0)
cubeExtrudes = rootComp.features.extrudeFeatures
cubeInput = cubeExtrudes.createInput(cubeProfile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
cubeDistance = adsk.core.ValueInput.createByReal({size_cm:.6f})
cubeInput.setDistanceExtent(False, cubeDistance)
cubeExtrudes.add(cubeInput)
""".strip()

