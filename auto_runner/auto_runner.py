import adsk.core, adsk.fusion, traceback, os, time
import threading

running = True
log_file = "/Users/devatreya/Desktop/Projects/MCP/auto_runner/log.txt"

def log(msg):
    with open(log_file, "a") as f:
        f.write(msg + "\n")

def monitor_script_file():
    app = adsk.core.Application.get()
    ui = app.userInterface
    script_path = "/Users/devatreya/Desktop/Projects/MCP/auto_runner/fusion_auto_run.py"
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
                            ui.messageBox("✅ fusion_auto_run.py executed.")
                        else:
                            log("⚠️ No run(context) function found in script.")
                            ui.messageBox("⚠️ Script loaded but no run(context) found.")

                    except Exception as e:
                        ui.messageBox(f"❌ Script error:\n{e}")
                        log("❌ Script exec error: " + str(e))

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
