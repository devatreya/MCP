import adsk.core, adsk.fusion, traceback, os, time, json
import threading

running = True
AUTO_RUNNER_DIR = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(AUTO_RUNNER_DIR, "log.txt")
state_file = os.path.join(AUTO_RUNNER_DIR, "state.json")

def log(msg):
    try:
        timestamp = time.strftime("%H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception as e:
        pass  # Silently fail if logging fails

def capture_state_snapshot():
    app = adsk.core.Application.get()
    design = app.activeProduct
    if not design:
        return
    root = design.rootComponent
    units = None
    try:
        units = design.unitsManager.defaultLengthUnits
    except Exception:
        units = "cm"

    bodies = []
    for i in range(root.bRepBodies.count):
        body = root.bRepBodies.item(i)
        try:
            bbox = body.boundingBox
            minp = bbox.minPoint
            maxp = bbox.maxPoint
            size = [
                float(maxp.x - minp.x),
                float(maxp.y - minp.y),
                float(maxp.z - minp.z),
            ]
            center = [
                float((maxp.x + minp.x) / 2),
                float((maxp.y + minp.y) / 2),
                float((maxp.z + minp.z) / 2),
            ]
        except Exception:
            size = [0.0, 0.0, 0.0]
            center = [0.0, 0.0, 0.0]

        bodies.append(
            {
                "name": body.name,
                "size_cm": size,
                "center_cm": center,
                "face_count": body.faces.count,
            }
        )

    state = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "units": units,
        "body_count": root.bRepBodies.count,
        "sketch_count": root.sketches.count,
        "feature_count": root.features.count,
        "bodies": bodies,
    }

    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def monitor_script_file():
    app = adsk.core.Application.get()
    ui = app.userInterface
    script_path = os.path.join(AUTO_RUNNER_DIR, "fusion_auto_run.py")
    last_mtime = None

    log("🟢 Thread started")

    while running:
        try:
            if os.path.exists(script_path):
                mtime = os.path.getmtime(script_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    log("🔄 Script change detected.")

                    with open(script_path, "r") as f:
                        code = f.read()

                    try:
                        log("📝 Executing script...")
                        # Inject Fusion globals into exec scope
                        injected_scope = {
                            "adsk": adsk,
                            "os": os,
                            "time": time,
                            "traceback": traceback,
                        }

                        exec(code, injected_scope)

                        if "run" in injected_scope:
                            injected_scope["run"](None)
                            log("✅ run(context) executed successfully.")
                            capture_state_snapshot()
                            # Only show message box on errors, not on success
                        else:
                            log("⚠️ No run(context) function found in script.")
                            ui.messageBox("⚠️ Script loaded but no run(context) found.")

                    except Exception as e:
                        error_msg = f"❌ Script error: {str(e)}"
                        log(error_msg)
                        log(traceback.format_exc())
                        ui.messageBox(error_msg)

            time.sleep(3)

        except Exception as e:
            log("❌ Thread crashed:\n" + traceback.format_exc())

def run(context):
    global running
    running = True
    app = adsk.core.Application.get()
    ui = app.userInterface
    ui.messageBox("🚀 MCP auto-run started. Watching for script updates...")
    thread = threading.Thread(target=monitor_script_file)
    thread.daemon = True
    thread.start()

def stop(context):
    global running
    running = False
    log("🛑 Thread stopped.")
