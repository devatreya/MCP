import re


API_CARDS = [
    {
        "id": "extrude-feature-input",
        "keywords": [
            "extrude",
            "distance",
            "start extent",
            "flange",
            "gusset",
            "boss",
            "join",
            "cut",
        ],
        "content": (
            "[extrude-feature-input] Use `extrudes.createInput(profile, FeatureOperations.*)` "
            "then set extent with either:\n"
            "- `extInput.setDistanceExtent(False, distanceValueInput)`\n"
            "- `extInput.setOneSideExtent(DistanceExtentDefinition.create(distanceValueInput), direction)`\n"
            "For offset starts use property assignment:\n"
            "- `extInput.startExtent = OffsetStartDefinition.create(offsetValueInput)`\n"
            "Do not use `setStartExtent(...)`."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ExtrudeFeatureInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ExtrudeFeatureInput_startExtent.htm",
        ],
    },
    {
        "id": "through-all-cut",
        "keywords": [
            "through all",
            "through",
            "hole",
            "slot",
            "cut",
            "drill",
        ],
        "content": (
            "[through-all-cut] Through cuts should use:\n"
            "- `extInput.setOneSideExtent(ThroughAllExtentDefinition.create(), direction)`\n"
            "Direction must still point into the target body."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ThroughAllExtentDefinition_create.htm",
        ],
    },
    {
        "id": "selected-face-sketch",
        "keywords": [
            "selected face",
            "this face",
            "current face",
            "face",
            "side face",
            "top face",
        ],
        "content": (
            "[selected-face-sketch] For selected face workflows:\n"
            "- `targetFace = BRepFace.cast(ui.activeSelections.item(0).entity)`\n"
            "- `sketch = sketches.add(targetFace)`\n"
            "- Compute center from face bounding box, convert with `modelToSketchSpace(...)`."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Sketches_add.htm",
        ],
    },
    {
        "id": "combine-target-body",
        "keywords": [
            "combine",
            "boolean",
            "subtract",
            "cut body",
        ],
        "content": (
            "[combine-target-body] For boolean subtract with selected face/body context:\n"
            "- target must be `targetFace.body` (or explicitly chosen body), not always `bRepBodies.item(0)`."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/CombineFeatures.htm",
        ],
    },
    {
        "id": "fillet-chamfer",
        "keywords": [
            "fillet",
            "chamfer",
            "edge",
            "round",
        ],
        "content": (
            "[fillet-chamfer] Use feature collections:\n"
            "- `filletFeats = rootComp.features.filletFeatures`\n"
            "- `chamferFeats = rootComp.features.chamferFeatures`\n"
            "Collect valid edges first; guard empty edge lists before adding feature."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/FilletFeatureInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ChamferFeatureInput.htm",
        ],
    },
    {
        "id": "pattern-holes",
        "keywords": [
            "bolt pattern",
            "pattern",
            "array",
            "holes",
            "pcd",
        ],
        "content": (
            "[pattern-holes] Prefer creating one seed cut and patterning features "
            "with rectangular/circular pattern features where possible, rather than ad-hoc repeated sketches."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/RectangularPatternFeatures_createInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/CircularPatternFeatures_createInput.htm",
        ],
    },
    {
        "id": "construction-planes",
        "keywords": [
            "construction plane",
            "offset plane",
            "midplane",
            "reference plane",
            "datum",
        ],
        "content": (
            "[construction-planes] Use `constructionPlanes.createInput()` and explicit setters such as:\n"
            "- `setByOffset(planarEntity, ValueInput)`\n"
            "- `setByThreePoints(p1, p2, p3)`\n"
            "- `setByAngle(linearEntity, angle, planarEntity)`\n"
            "Do not rely on unsupported helper methods."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ConstructionPlaneInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ConstructionPlaneInput_setByOffset.htm",
        ],
    },
    {
        "id": "hole-features",
        "keywords": [
            "hole",
            "drill",
            "counterbore",
            "countersink",
            "tap",
        ],
        "content": (
            "[hole-features] VERIFIED working hole-on-face workflow (live-tested in Fusion 360):\n"
            "1. Get face:    `targetFace = adsk.fusion.BRepFace.cast(ui.activeSelections.item(0).entity)`\n"
            "2. Sketch:      `sketch = sketches.add(targetFace)`\n"
            "3. Face centre in sketch space (Z must be forced to 0):\n"
            "                `box = targetFace.boundingBox`\n"
            "                `worldPt = adsk.core.Point3D.create((box.minPoint.x+box.maxPoint.x)/2, (box.minPoint.y+box.maxPoint.y)/2, (box.minPoint.z+box.maxPoint.z)/2)`\n"
            "                `sketchPt2D = sketch.modelToSketchSpace(worldPt)`\n"
            "                `sketchPt2D = adsk.core.Point3D.create(sketchPt2D.x, sketchPt2D.y, 0)`  # force Z=0\n"
            "4. Add point:   `sketchPoint = sketch.sketchPoints.add(sketchPt2D)`\n"
            "5. Hole input:  `holeFeats = rootComp.features.holeFeatures`\n"
            "                `holeInput = holeFeats.createSimpleInput(adsk.core.ValueInput.createByReal(diameterCm))`\n"
            "6. Position:    `holeInput.setPositionBySketchPoint(sketchPoint)`  # ONE argument only\n"
            "7. Extent — CRITICAL: through-all uses PositiveExtentDirection (NOT Negative):\n"
            "   Through all: `holeInput.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)`\n"
            "   Blind:       `holeInput.setDistanceExtent(adsk.core.ValueInput.createByReal(depthCm))`\n"
            "8. Finalise:    `holeFeats.add(holeInput)`\n"
            "NEVER use NegativeExtentDirection with setAllExtent — causes InternalValidationError.\n"
            "NEVER use setPositionByPoint or setOneSideExtent on HoleFeatureInput."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/HoleFeatures_createSimpleInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/HoleFeatureInput.htm",
        ],
    },
    {
        "id": "slots",
        "keywords": [
            "slot",
            "slotted",
            "obround",
        ],
        "content": (
            "[slots] Prefer sketch slot APIs on Sketch:\n"
            "- `sketch.addCenterToCenterSlot(...)` or related sketch slot creators.\n"
            "Avoid calling nonexistent `SketchLines.addCenterPointSlot(...)`."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/Sketch_addCenterToCenterSlot.htm",
        ],
    },
    {
        "id": "fillet-edge-sets",
        "keywords": [
            "fillet",
            "radius",
            "round edges",
        ],
        "content": (
            "[fillet-edge-sets] New fillet API style uses `FilletFeatureInput.edgeSetInputs` and then:\n"
            "- `edgeSetInputs.addConstantRadiusEdgeSet(...)`\n"
            "- `edgeSetInputs.addVariableRadiusEdgeSet(...)`\n"
            "Avoid retired direct add-methods on FilletFeatureInput."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/FilletFeatureInput_edgeSetInputs.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/FilletEdgeSetInputs_addConstantRadiusEdgeSet.htm",
        ],
    },
    {
        "id": "chamfer-edge-sets",
        "keywords": [
            "chamfer",
            "bevel",
        ],
        "content": (
            "[chamfer-edge-sets] Create chamfers via `ChamferFeatureInput.chamferEdgeSets` and set type by:\n"
            "- `addEqualDistanceChamferEdgeSet(...)`\n"
            "- `addTwoDistancesChamferEdgeSet(...)`\n"
            "- `addDistanceAndAngleChamferEdgeSet(...)`\n"
            "Avoid retired direct chamfer methods."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ChamferFeatureInput_chamferEdgeSets.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ChamferEdgeSets_addEqualDistanceChamferEdgeSet.htm",
        ],
    },
    {
        "id": "revolve-feature",
        "keywords": [
            "revolve",
            "rotate",
            "revolution",
            "cylinder",
            "lathe",
        ],
        "content": (
            "[revolve-feature] Revolve a sketch profile around an axis:\n"
            "- `revolveFeats = rootComp.features.revolveFeatures`\n"
            "- `revolveInput = revolveFeats.createInput(profile, axisEntity, FeatureOperations.*)`\n"
            "- `revolveInput.setAngleExtent(isTwoSided, adsk.core.ValueInput.createByReal(angleRadians))`\n"
            "  For full revolution: angleRadians = 3.14159265 * 2\n"
            "- Axis entity: use rootComp.xConstructionAxis / yConstructionAxis / zConstructionAxis,\n"
            "  or a linear BRepEdge cast as ConstructionAxis is NOT valid — use a construction axis.\n"
            "- `revolveFeats.add(revolveInput)` to finalise."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/RevolveFeatures_createInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/RevolveFeatureInput_setAngleExtent.htm",
        ],
    },
    {
        "id": "shell-feature",
        "keywords": [
            "shell",
            "hollow",
            "thin wall",
            "pocket",
        ],
        "content": (
            "[shell-feature] Hollow a body by removing a selected face and setting wall thickness:\n"
            "ALWAYS use the selected face from ui.activeSelections as the face to remove — never guess heuristically.\n"
            "1. Get selected face: `faceToRemove = adsk.fusion.BRepFace.cast(ui.activeSelections.item(0).entity)`\n"
            "2. Build faces collection:\n"
            "   `inputFaces = adsk.core.ObjectCollection.create()`\n"
            "   `inputFaces.add(faceToRemove)`\n"
            "3. Create shell input: `shellFeats = rootComp.features.shellFeatures`\n"
            "   `shellInput = shellFeats.createInput(inputFaces, False)`  # False = shell inward\n"
            "4. Set thickness: `shellInput.insideThickness = adsk.core.ValueInput.createByReal(thicknessCm)`\n"
            "5. Finalise: `shellFeats.add(shellInput)`\n"
            "The selected face is the one that gets REMOVED (opened up). All other faces become thin walls.\n"
            "NEVER create the inputFaces collection without adding the selected face — an empty collection shells with no opening."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ShellFeatures_createInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ShellFeatureInput.htm",
        ],
    },
    {
        "id": "sweep-feature",
        "keywords": [
            "sweep",
            "extrude along path",
            "path extrude",
            "pipe",
            "tube",
        ],
        "content": (
            "[sweep-feature] Sweep a profile along a sketch path:\n"
            "- `sweepFeats = rootComp.features.sweepFeatures`\n"
            "- Path must be a `Path` object: `path = sweepFeats.createPath(sketchCurve)`\n"
            "  where sketchCurve is a SketchCurve (e.g. a sketch line or arc entity)\n"
            "- `sweepInput = sweepFeats.createInput(profile, path, FeatureOperations.NewBodyFeatureOperation)`\n"
            "- `sweepFeats.add(sweepInput)`\n"
            "Profile sketch and path sketch must be separate sketches."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/SweepFeatures_createInput.htm",
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/SweepFeatureInput.htm",
        ],
    },
    {
        "id": "mirror-feature",
        "keywords": [
            "mirror",
            "symmetry",
            "reflect",
        ],
        "content": (
            "[mirror-feature] Mirror features or bodies across a plane:\n"
            "- `mirrorFeats = rootComp.features.mirrorFeatures`\n"
            "- Collect features to mirror:\n"
            "  `inputFeatures = adsk.core.ObjectCollection.create()\n"
            "  inputFeatures.add(lastFeature)`\n"
            "- Mirror plane: use rootComp.xYConstructionPlane / xZConstructionPlane / yZConstructionPlane\n"
            "- `mirrorInput = mirrorFeats.createInput(inputFeatures, mirrorPlane)`\n"
            "- `mirrorFeats.add(mirrorInput)`"
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/MirrorFeatures_createInput.htm",
        ],
    },
    {
        "id": "move-body",
        "keywords": [
            "move",
            "translate",
            "offset body",
            "shift",
        ],
        "content": (
            "[move-body] Translate a body using a MoveFeature:\n"
            "- `moveFeats = rootComp.features.moveFeatures`\n"
            "- Collect bodies:\n"
            "  `bodies = adsk.core.ObjectCollection.create(); bodies.add(targetBody)`\n"
            "- Build transform matrix:\n"
            "  `transform = adsk.core.Matrix3D.create()\n"
            "  transform.translation = adsk.core.Vector3D.create(xCm, yCm, zCm)`\n"
            "- `moveInput = moveFeats.createInput(bodies, transform)`\n"
            "- `moveFeats.add(moveInput)`"
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/MoveFeatures_createInput.htm",
        ],
    },
    {
        "id": "pattern-linear",
        "keywords": [
            "linear pattern",
            "rectangular pattern",
            "array",
            "repeat",
        ],
        "content": (
            "[pattern-linear] Create a linear (rectangular) pattern of a feature:\n"
            "- `patFeats = rootComp.features.rectangularPatternFeatures`\n"
            "- `inputFeatures = adsk.core.ObjectCollection.create(); inputFeatures.add(seedFeature)`\n"
            "- Direction entity: rootComp.xConstructionAxis / yConstructionAxis / zConstructionAxis\n"
            "  or a linear BRepEdge\n"
            "- `patInput = patFeats.createInput(\n"
            "      inputFeatures, dirAxis,\n"
            "      adsk.core.ValueInput.createByReal(count),\n"
            "      adsk.core.ValueInput.createByReal(spacingCm),\n"
            "      adsk.fusion.PatternDistanceType.SpacingPatternDistanceType)`\n"
            "- `patFeats.add(patInput)`\n"
            "Count must be >= 1. Spacing is distance between instances in cm."
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/RectangularPatternFeatures_createInput.htm",
        ],
    },
    {
        "id": "pattern-circular",
        "keywords": [
            "circular pattern",
            "polar pattern",
            "rotational array",
            "bolt circle",
        ],
        "content": (
            "[pattern-circular] Create a circular pattern of a feature:\n"
            "- `patFeats = rootComp.features.circularPatternFeatures`\n"
            "- `inputFeatures = adsk.core.ObjectCollection.create(); inputFeatures.add(seedFeature)`\n"
            "- Rotation axis: rootComp.xConstructionAxis / yConstructionAxis / zConstructionAxis\n"
            "- `patInput = patFeats.createInput(inputFeatures, rotationAxis)`\n"
            "- `patInput.quantity = adsk.core.ValueInput.createByReal(count)`\n"
            "- `patInput.totalAngle = adsk.core.ValueInput.createByString('360 deg')`  # or partial angle\n"
            "- `patInput.isSymmetric = False`\n"
            "- `patFeats.add(patInput)`"
        ),
        "sources": [
            "https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/CircularPatternFeatures_createInput.htm",
        ],
    },
]


API_REPAIR_RULES = [
    (
        r"(\w+)\.setOneSideExtent\(\s*(adsk\.fusion\.ExtentDirections\.[A-Za-z]+)\s*,\s*([^)]+)\)",
        r"\1.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(\3), \2)",
    ),
    (
        r"(\w+)\.setStartExtent\(\s*([^\n]+?)\s*\)",
        r"\1.startExtent = \2",
    ),
]


API_ISSUE_RULES = [
    (
        r"\.\s*setStartExtent\s*\(",
        "Use `extInput.startExtent = ...` instead of `setStartExtent(...)`.",
    ),
    (
        r"\.\s*setOneSideExtent\(\s*adsk\.fusion\.ExtentDirections\.",
        "Invalid `setOneSideExtent` argument order. Use `(extentDefinition, direction)`.",
    ),
    (
        r"\.\s*addCenterPointSlot\s*\(",
        "Use `sketch.addCenterToCenterSlot(...)` instead of `SketchLines.addCenterPointSlot(...)`.",
    ),
]

CREATE_INPUT_RETURN_TYPES = {
    ("ExtrudeFeatures", "createInput"): "ExtrudeFeatureInput",
    ("FilletFeatures", "createInput"): "FilletFeatureInput",
    ("ChamferFeatures", "createInput"): "ChamferFeatureInput",
    ("ChamferFeatures", "createInput2"): "ChamferFeatureInput",
    ("HoleFeatures", "createSimpleInput"): "HoleFeatureInput",
    ("RectangularPatternFeatures", "createInput"): "RectangularPatternFeatureInput",
    ("CircularPatternFeatures", "createInput"): "CircularPatternFeatureInput",
    ("ConstructionPlanes", "createInput"): "ConstructionPlaneInput",
}

OBJECT_METHOD_ALLOWLIST = {
    "ExtrudeFeatures": {"createInput", "add", "addSimple"},
    "ExtrudeFeatureInput": {
        "setDistanceExtent",
        "setOneSideExtent",
        "setTwoSidesExtent",
        "setSymmetricExtent",
        "setThinExtrude",
        "setThinExtrude2",
        "setOneSideToExtent",
    },
    "Sketch": {"modelToSketchSpace", "addCenterToCenterSlot", "addCenterPointArcSlot", "addTwoPointRectangleSlot"},
    "SketchLines": {"addByTwoPoints", "addTwoPointRectangle", "addCenterPointRectangle", "addDistanceChamfer", "addAngleChamfer"},
    "ConstructionPlanes": {"createInput", "add"},
    "ConstructionPlaneInput": {
        "setByOffset",
        "setByThreePoints",
        "setByAngle",
        "setByDistanceOnPath",
        "setByTwoEdges",
        "setByTwoPlanes",
        "setByTangent",
        "setByTangentAtPoint",
        "setByPlane",
    },
    "RectangularPatternFeatures": {"createInput", "add"},
    "RectangularPatternFeatureInput": set(),
    "CircularPatternFeatures": {"createInput", "add"},
    "CircularPatternFeatureInput": set(),
    "FilletFeatures": {"createInput", "add"},
    "FilletFeatureInput": set(),
    "FilletEdgeSetInputs": {"addConstantRadiusEdgeSet", "addVariableRadiusEdgeSet", "addChordLengthEdgeSet"},
    "ChamferFeatures": {"createInput", "createInput2", "add"},
    "ChamferFeatureInput": set(),
    "ChamferEdgeSets": {"addEqualDistanceChamferEdgeSet", "addTwoDistancesChamferEdgeSet", "addDistanceAndAngleChamferEdgeSet"},
    "HoleFeatures": {"createSimpleInput", "createCounterboreInput", "createCountersinkInput", "add"},
    "HoleFeatureInput": {
        "setDistanceExtent",
        "setAllExtent",
        "setPositionByPoint",
        "setPositionBySketchPoint",
        "setPositionBySketchPoints",
        "setPositionOnEdge",
        "setPositionByPlaneAndOffsets",
        "setToSimpleHole",
        "setToTappedHole",
        "setToClearanceHole",
    },
}

HIGH_CONFIDENCE_METHOD_HINTS = {
    ("ExtrudeFeatureInput", "setStartExtent"): "Use `startExtent` property assignment, not `setStartExtent(...)`.",
    ("SketchLines", "addCenterPointSlot"): "Use `sketch.addCenterToCenterSlot(...)` for slot creation.",
}

OBJECT_TYPE_LINE_PATTERNS = [
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.extrudeFeatures\b"), "ExtrudeFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.filletFeatures\b"), "FilletFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.chamferFeatures\b"), "ChamferFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.holeFeatures\b"), "HoleFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.rectangularPatternFeatures\b"), "RectangularPatternFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.features\.circularPatternFeatures\b"), "CircularPatternFeatures"),
    (re.compile(r"^\s*(\w+)\s*=\s*[\w\.]+\.constructionPlanes\b"), "ConstructionPlanes"),
    (re.compile(r"^\s*(\w+)\s*=\s*sketches\.add\s*\("), "Sketch"),
    (re.compile(r"^\s*(\w+)\s*=\s*\w+\.sketchCurves\.sketchLines\b"), "SketchLines"),
    (re.compile(r"^\s*(\w+)\s*=\s*\w+\.edgeSetInputs\b"), "FilletEdgeSetInputs"),
    (re.compile(r"^\s*(\w+)\s*=\s*\w+\.chamferEdgeSets\b"), "ChamferEdgeSets"),
]


def _tokenize(text):
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _infer_object_types(code):
    inferred = {}
    lines = code.splitlines()

    for line in lines:
        for pattern, object_type in OBJECT_TYPE_LINE_PATTERNS:
            m = pattern.search(line)
            if m:
                inferred[m.group(1)] = object_type

        m = re.search(r"^\s*(\w+)\s*=\s*(\w+)\.(createInput2?|createSimpleInput)\s*\(", line)
        if m:
            out_var, base_var, call_name = m.group(1), m.group(2), m.group(3)
            base_type = inferred.get(base_var)
            out_type = CREATE_INPUT_RETURN_TYPES.get((base_type, call_name))
            if out_type:
                inferred[out_var] = out_type

    return inferred


def _repair_slot_calls(code):
    repaired = code
    owners = {}
    for m in re.finditer(r"^\s*(\w+)\s*=\s*(\w+)\.sketchCurves\.sketchLines\s*$", repaired, re.MULTILINE):
        owners[m.group(1)] = m.group(2)

    for lines_var, sketch_var in owners.items():
        repaired = re.sub(
            rf"\b{re.escape(lines_var)}\.addCenterPointSlot\(",
            f"{sketch_var}.addCenterToCenterSlot(",
            repaired,
        )

    return repaired


def _find_unknown_method_issues(code):
    issues = []
    inferred = _infer_object_types(code)
    sensitive_prefixes = ("set", "add", "create")

    for line_no, line in enumerate(code.splitlines(), start=1):
        for obj, method in re.findall(r"\b([A-Za-z_]\w*)\.(\w+)\s*\(", line):
            object_type = inferred.get(obj)
            if not object_type:
                continue
            allowed = OBJECT_METHOD_ALLOWLIST.get(object_type)
            if allowed is None:
                continue
            if method in allowed:
                continue
            if not method.startswith(sensitive_prefixes):
                continue

            hint = HIGH_CONFIDENCE_METHOD_HINTS.get((object_type, method))
            if hint:
                issues.append(f"Line {line_no}: {hint}")
            else:
                issues.append(
                    f"Line {line_no}: `{object_type}.{method}()` is not in the allowlist for this assistant."
                )

    return issues


def retrieve_api_cards(user_prompt, limit=4):
    prompt_lower = (user_prompt or "").lower()
    prompt_tokens = _tokenize(prompt_lower)
    scored = []

    for card in API_CARDS:
        score = 0
        for keyword in card["keywords"]:
            k = keyword.lower()
            if " " in k:
                if k in prompt_lower:
                    score += 3
            elif k in prompt_tokens:
                score += 1
        if score > 0:
            scored.append((score, card["id"], card))

    scored.sort(reverse=True)
    selected = [item[2] for item in scored[:limit]]

    if selected:
        return selected

    default_ids = ["extrude-feature-input", "selected-face-sketch", "through-all-cut"]
    defaults = []
    for card_id in default_ids:
        for card in API_CARDS:
            if card["id"] == card_id:
                defaults.append(card)
                break
    return defaults[:limit]


def build_api_guidance(user_prompt, limit=4):
    cards = retrieve_api_cards(user_prompt, limit=limit)
    if not cards:
        return ""
    rendered = []
    for card in cards:
        content = card["content"]
        sources = card.get("sources") or []
        if sources:
            content += "\nDocs: " + ", ".join(sources[:2])
        rendered.append(content)
    return "Fusion API reference snippets (authoritative):\n" + "\n\n".join(rendered)


def apply_api_repairs(code):
    repaired = _repair_slot_calls(code)
    for pattern, replacement in API_REPAIR_RULES:
        repaired = re.sub(pattern, replacement, repaired)
    return repaired


def find_api_issues(code):
    issues = []
    for pattern, message in API_ISSUE_RULES:
        if re.search(pattern, code):
            issues.append(message)
    issues.extend(_find_unknown_method_issues(code))
    return sorted(set(issues))


# ---------------------------------------------------------------------------
# Family-based card retrieval (used by new pipeline code_generator.py)
# ---------------------------------------------------------------------------

_FAMILY_TO_CARD_IDS = {
    "sketch": ["selected-face-sketch", "construction-planes", "extrude-feature-input"],
    "extrude": ["extrude-feature-input", "through-all-cut", "selected-face-sketch"],
    "revolve": ["revolve-feature", "extrude-feature-input"],
    "sweep": ["sweep-feature"],
    "fillet_chamfer": ["fillet-chamfer", "fillet-edge-sets", "chamfer-edge-sets"],
    "hole": ["hole-features", "through-all-cut", "selected-face-sketch"],
    "shell": ["shell-feature"],
    "pattern": ["pattern-linear", "pattern-circular", "pattern-holes"],
    "mirror": ["mirror-feature"],
    "boolean": ["combine-target-body", "extrude-feature-input"],
    "transform": ["move-body"],
    # composite: inject all major cards so the LLM can handle any combination of ops
    "composite": [
        "selected-face-sketch",
        "extrude-feature-input",
        "through-all-cut",
        "fillet-chamfer",
        "fillet-edge-sets",
        "hole-features",
        "shell-feature",
        "pattern-linear",
        "pattern-circular",
        "mirror-feature",
        "combine-target-body",
        "move-body",
    ],
    "unknown": [],
}

_CARD_BY_ID = {card["id"]: card for card in API_CARDS}


def get_cards_for_family(operation_family):
    """Return a list of formatted API reference strings for the given operation_family."""
    card_ids = _FAMILY_TO_CARD_IDS.get(operation_family, [])
    rendered = []
    for card_id in card_ids:
        card = _CARD_BY_ID.get(card_id)
        if not card:
            continue
        content = card["content"]
        sources = card.get("sources") or []
        if sources:
            content += "\nDocs: " + ", ".join(sources[:2])
        rendered.append(content)
    return rendered
