"""Compiler for create_hole operations using deterministic cut logic."""


def compile_create_hole(op):
    diameter_cm = float(op.params["diameter_cm"])
    radius_cm = diameter_cm / 2.0
    extent_mode = op.params.get("extent_mode", "distance")
    depth_cm = op.params.get("depth_cm")

    if extent_mode == "distance":
        depth_cm = float(depth_cm)
        extent_setup = (
            f"distanceValue = adsk.core.ValueInput.createByReal({depth_cm:.6f})\n"
            "    extentDef = adsk.fusion.DistanceExtentDefinition.create(distanceValue)\n"
            "    cutInput.setOneSideExtent(extentDef, direction)"
        )
        expected_depth_expr = f"max(0.01, {depth_cm:.6f})"
    else:
        extent_setup = (
            "extentDef = adsk.fusion.ThroughAllExtentDefinition.create()\n"
            "    cutInput.setOneSideExtent(extentDef, direction)"
        )
        expected_depth_expr = "max(0.01, bodyMaxDim)"

    return f"""
if ui.activeSelections.count < 1:
    raise Exception("No active selection. Select a target face and retry.")

targetFace = adsk.fusion.BRepFace.cast(ui.activeSelections.item(0).entity)
if not targetFace:
    raise Exception("Selected entity is not a face.")

targetBody = targetFace.body
holeSketch = sketches.add(targetFace)
faceBox = targetFace.boundingBox
holeCenterWorld = adsk.core.Point3D.create(
    (faceBox.minPoint.x + faceBox.maxPoint.x) / 2.0,
    (faceBox.minPoint.y + faceBox.maxPoint.y) / 2.0,
    (faceBox.minPoint.z + faceBox.maxPoint.z) / 2.0,
)
holeCenter = holeSketch.modelToSketchSpace(holeCenterWorld)
holeSketch.sketchCurves.sketchCircles.addByCenterRadius(holeCenter, {radius_cm:.6f})

expectedProfileArea = 3.141592653589793 * ({radius_cm:.6f} ** 2)
holeProfile = None
bestProfileArea = 0.0
bestProfileScore = 1e99
for i in range(holeSketch.profiles.count):
    candidateProfile = holeSketch.profiles.item(i)
    try:
        candidateProps = candidateProfile.areaProperties(adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy)
        candidateArea = abs(candidateProps.area)
    except:
        continue
    candidateScore = abs(candidateArea - expectedProfileArea)
    if candidateScore < bestProfileScore:
        bestProfileScore = candidateScore
        bestProfileArea = candidateArea
        holeProfile = candidateProfile
if not holeProfile:
    raise Exception("Failed to resolve hole profile from sketch.")
if expectedProfileArea > 0 and abs(bestProfileArea - expectedProfileArea) > (expectedProfileArea * 0.5):
    raise Exception("Resolved profile area does not match requested hole size.")

holeExtrudes = rootComp.features.extrudeFeatures
bodyBox = targetBody.boundingBox
bodyMaxDim = max(
    abs(bodyBox.maxPoint.x - bodyBox.minPoint.x),
    abs(bodyBox.maxPoint.y - bodyBox.minPoint.y),
    abs(bodyBox.maxPoint.z - bodyBox.minPoint.z),
)
expectedHoleVolume = 3.141592653589793 * ({radius_cm:.6f} ** 2) * {expected_depth_expr}
minExpectedDelta = max(1e-6, expectedHoleVolume * 0.02)

def _try_hole_cut(direction):
    beforeVolume = targetBody.volume
    cutInput = holeExtrudes.createInput(holeProfile, adsk.fusion.FeatureOperations.CutFeatureOperation)
    {extent_setup}
    cutFeature = holeExtrudes.add(cutInput)
    afterVolume = targetBody.volume
    delta = beforeVolume - afterVolume
    if delta < minExpectedDelta:
        try:
            cutFeature.deleteMe()
        except:
            pass
        return False
    return True

if not _try_hole_cut(adsk.fusion.ExtentDirections.NegativeExtentDirection):
    if not _try_hole_cut(adsk.fusion.ExtentDirections.PositiveExtentDirection):
        raise Exception("Failed to cut hole into selected face.")
""".strip()
