"""Compiler for create_chamfer operations."""


def compile_create_chamfer(op):
    distance_cm = float(op.params["distance_cm"])
    target = op.params.get("target", "selected_edges")
    prefer_face_edges = target == "selected_face_edges"
    prefer_face_edges_literal = "True" if prefer_face_edges else "False"

    return f"""
chamferEdges = adsk.core.ObjectCollection.create()
selectedFace = None

for i in range(ui.activeSelections.count):
    entity = ui.activeSelections.item(i).entity
    edge = adsk.fusion.BRepEdge.cast(entity)
    if edge:
        chamferEdges.add(edge)
        continue
    if not selectedFace:
        selectedFace = adsk.fusion.BRepFace.cast(entity)

if ({prefer_face_edges_literal} and selectedFace) or (chamferEdges.count == 0 and selectedFace):
    for i in range(selectedFace.edges.count):
        chamferEdges.add(selectedFace.edges.item(i))

if chamferEdges.count == 0:
    raise Exception("No edges available for chamfer. Select edges or a face.")

chamferFeatures = rootComp.features.chamferFeatures
chamferInput = chamferFeatures.createInput2()
chamferSets = chamferInput.chamferEdgeSets
distanceValue = adsk.core.ValueInput.createByReal({distance_cm:.6f})
chamferSets.addEqualDistanceChamferEdgeSet(chamferEdges, distanceValue, True)
chamferFeatures.add(chamferInput)
""".strip()

