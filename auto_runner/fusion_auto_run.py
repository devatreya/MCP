import adsk.core, adsk.fusion, traceback

def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        design = app.activeProduct
        root = design.rootComponent
        
        # Delete all sketches
        while root.sketches.count > 0:
            root.sketches.item(0).deleteMe()
        
        # Delete all bodies
        while root.bRepBodies.count > 0:
            root.bRepBodies.item(0).deleteMe()
        
        # Delete all features
        while root.features.count > 0:
            root.features.item(0).deleteMe()
        
        ui.messageBox('✅ Design cleared!')
    except Exception as e:
        if ui:
            ui.messageBox('❌ Reset Error: {}'.format(str(e)))
