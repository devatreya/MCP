import adsk.core, adsk.fusion, traceback

log_file = "/Users/devatreya/Desktop/Projects/MCP/auto_runner/log.txt"

def log(msg):
    try:
        import time
        timestamp = time.strftime("%H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except:
        pass

def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        log("===== DIAGNOSTIC HOLE CUTTING =====")
        design = app.activeProduct
        rootComp = design.rootComponent
        sketches = rootComp.sketches

        log(f"Bodies before: {rootComp.bRepBodies.count}")
        targetBody = rootComp.bRepBodies.item(0)
        log(f"Target body: {targetBody.name}")
        
        topFace = None
        for face in targetBody.faces:
            if face.geometry.normal.z > 0.9:
                topFace = face
                break
        
        if not topFace:
            log("ERROR: No top face found")
            ui.messageBox("No top face!")
            return
            
        log("Top face found, creating sketch...")
        sketch = sketches.add(topFace)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), 2.5)
        log(f"Circle created, profiles: {sketch.profiles.count}")
        
        innerProf = sketch.profiles.item(0)
        log(f"Profile area: {innerProf.areaProperties().area}")
        
        extrudes = rootComp.features.extrudeFeatures
        extInput = extrudes.createInput(innerProf, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        log("Extrude input created")
        
        distance = adsk.core.ValueInput.createByReal(2)
        log(f"Distance value: {distance.realValue}")
        
        extInput.setOneSideExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection, distance)
        log("setOneSideExtent called with NegativeExtentDirection")
        
        log("About to add extrude...")
        extResult = extrudes.add(extInput)
        
        if extResult:
            log(f"Extrude SUCCESS: {extResult.name}")
        else:
            log("Extrude FAILED: returned None")
            ui.messageBox("Extrude returned None!")
            return
        
        log(f"Bodies after extrude: {rootComp.bRepBodies.count}")
        
        if rootComp.bRepBodies.count < 2:
            log("ERROR: No cutting body was created!")
            ui.messageBox("Cutting body not created!")
            return
            
        toolBody = rootComp.bRepBodies.item(rootComp.bRepBodies.count - 1)
        log(f"Tool body: {toolBody.name}")
        
        combineFeats = rootComp.features.combineFeatures
        toolBodies = adsk.core.ObjectCollection.create()
        toolBodies.add(toolBody)
        log(f"Tool bodies collection: {toolBodies.count}")
        
        combineInput = combineFeats.createInput(targetBody, toolBodies)
        combineInput.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        combineInput.isKeepToolBodies = False
        log("Combine input configured")
        
        log("About to add combine...")
        combineResult = combineFeats.add(combineInput)
        
        if combineResult:
            log(f"Combine SUCCESS: {combineResult.name}")
        else:
            log("Combine FAILED: returned None")
        
        log(f"Final bodies: {rootComp.bRepBodies.count}")
        log("===== COMPLETE =====")
        ui.messageBox("✅ Check log for details")
        
    except Exception as e:
        log(f"EXCEPTION: {str(e)}")
        log(traceback.format_exc())
        if ui:
            ui.messageBox(f'❌ Error: {str(e)}')
