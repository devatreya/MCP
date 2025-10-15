import adsk.core, adsk.fusion, traceback, os, time
import threading

running = True
log_file = "/Users/devatreya/Desktop/Projects/MCP/auto_runner/log.txt"

def log(msg):
    try:
        timestamp = time.strftime("%H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception as e:
        pass  # Silently fail if logging fails

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
