import adsk.core, adsk.fusion, traceback

def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        design = app.activeProduct
        rootComp = design.rootComponent
        sketches = rootComp.sketches

        targetBody = rootComp.bRepBodies.item(0)
        topFace = None
        for face in targetBody.faces:
            if face.geometry.normal.z > 0.9:
                topFace = face
                break
        sketch = sketches.add(topFace)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), 2.5)
        innerProf = sketch.profiles.item(0)
        extrudes = rootComp.features.extrudeFeatures
        extInput = extrudes.createInput(innerProf, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        distance = adsk.core.ValueInput.createByReal(2)
        extInput.setOneSideExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection, distance)
        extrudes.add(extInput)
        toolBody = rootComp.bRepBodies.item(rootComp.bRepBodies.count - 1)
        combineFeats = rootComp.features.combineFeatures
        toolBodies = adsk.core.ObjectCollection.create()
        toolBodies.add(toolBody)
        combineInput = combineFeats.createInput(targetBody, toolBodies)
        combineInput.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        combineInput.isKeepToolBodies = False
        combineFeats.add(combineInput)
    except Exception as e:
        if ui:
            ui.messageBox('❌ Runtime Error: {}'.format(str(e)))
