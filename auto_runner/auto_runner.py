import json
import os
import sys
import threading
import traceback

import adsk.core

ADDIN_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(ADDIN_DIR, "lib")
LOG_PATH = os.path.join(ADDIN_DIR, "log.txt")

if ADDIN_DIR not in sys.path:
    sys.path.insert(0, ADDIN_DIR)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


_config = None
_runtime = None
_futil = None
_import_errors = []


def _bootstrap_log(message):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"[BOOT] {message}\n")
    except Exception:
        pass


try:
    import config as _config
except Exception:
    _import_errors.append(f"config import failed: {traceback.format_exc()}")

try:
    import runtime as _runtime
except Exception:
    _import_errors.append(f"runtime import failed: {traceback.format_exc()}")

try:
    import fusionAddInUtils as _futil
except Exception:
    _import_errors.append(f"fusionAddInUtils import failed: {traceback.format_exc()}")

for err in _import_errors:
    _bootstrap_log(err)


def _config_value(name, default):
    if _config is None:
        return default
    return getattr(_config, name, default)


ADDIN_NAME = _config_value("ADDIN_NAME", os.path.basename(ADDIN_DIR))
COMPANY_NAME = _config_value("COMPANY_NAME", "ACME")
PALETTE_ID = _config_value("sample_palette_id", f"{COMPANY_NAME}_{ADDIN_NAME}_palette_id")

CMD_ID = f"{COMPANY_NAME}_{ADDIN_NAME}_ai_panel"
CMD_NAME = "Open AI Panel"
CMD_DESCRIPTION = "Open the MCP AI side panel"
PALETTE_NAME = "MCP AI Assistant"
PALETTE_DOCKING = adsk.core.PaletteDockingStates.PaletteDockStateRight

WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
COMMAND_BESIDE_ID = "ScriptsManagerCommand"
ICON_FOLDER = os.path.join(ADDIN_DIR, "commands", "paletteShow", "resources", "")
PALETTE_URL = os.path.join(
    ADDIN_DIR,
    "commands",
    "paletteShow",
    "resources",
    "html",
    "index.html",
).replace("\\", "/")

_handlers = []
_monitor_stop_event = threading.Event()
_monitor_thread = None
app = None
ui = None


def _log(message):
    if _runtime is not None:
        _runtime.log(message)
    else:
        _bootstrap_log(message)


def _log_error(context):
    _log(f"{context}: {traceback.format_exc()}")


def _add_handler(event, callback):
    if _futil is not None:
        _futil.add_handler(event, callback, local_handlers=_handlers)


def _format_context_response(model_state, selection):
    if _runtime is None:
        return {
            "state": model_state,
            "selection": selection,
            "state_summary": "Runtime unavailable.",
            "selection_summary": "Runtime unavailable.",
        }
    return {
        "state": model_state,
        "selection": selection,
        "state_summary": _runtime.summarize_model_state(model_state),
        "selection_summary": _runtime.summarize_selection_context(selection),
    }


def _handle_prompt(prompt):
    if _runtime is None:
        return {"ok": False, "error": "Runtime module is unavailable. Check auto_runner/log.txt."}

    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Prompt is empty."}

    model_state, selection = _runtime.capture_and_store_current_context()

    generate_result = _runtime.request_generate(prompt, model_state, selection)
    if not generate_result.get("ok"):
        data = generate_result.get("data", {}) if isinstance(generate_result.get("data"), dict) else {}
        return {
            "ok": False,
            "error": generate_result.get("error", "Failed to generate script."),
            "details": data.get("details", []),
            "retry_context": data.get("retry_context"),
        }

    payload = generate_result.get("data", {})

    # ── Step pipeline: pass step plan back to JS for sequential execution ─────
    if payload.get("generation_mode") == "step_pipeline":
        return {
            "ok": True,
            "mode": "step_pipeline",
            "steps": payload.get("steps", []),
            "step_count": payload.get("step_count", 0),
            "assistant_message": payload.get(
                "assistant_message", "Step plan generated. Executing steps…"
            ),
            **_format_context_response(model_state, selection),
        }

    # ── Single-script path ─────────────────────────────────────────────────────
    script = payload.get("script", "")
    if not script:
        return {"ok": False, "error": "Server response did not include a script."}

    saved_script_file = None
    try:
        saved_script_file = _runtime.save_script_snapshot(script, prefix="panel")
    except Exception:
        _log("Failed to save panel script snapshot")

    execute_result = _runtime.execute_wrapped_script(script)
    if not execute_result.get("ok"):
        return {
            "ok": False,
            "error": execute_result.get("error", "Script execution failed."),
            "traceback": execute_result.get("traceback", ""),
            "script_file": saved_script_file,
            "retry_context": execute_result.get("retry_context"),
        }

    return {
        "ok": True,
        "assistant_message": (
            f"Edit applied successfully ({payload.get('generation_mode', 'legacy')})."
            if isinstance(payload, dict)
            else "Edit applied successfully."
        ),
        "script_file": saved_script_file,
        "raw_response": payload,
        **_format_context_response(
            execute_result.get("state", model_state),
            execute_result.get("selection", selection),
        ),
    }


def _handle_execute_step(script, step_id):
    """Execute a single step script and return the result to the JS step loop."""
    if _runtime is None:
        return {"ok": False, "error": "Runtime module is unavailable."}
    if not script:
        return {"ok": False, "error": "No script provided for step execution."}

    saved = None
    try:
        saved = _runtime.save_script_snapshot(script, prefix=f"step_{step_id}")
    except Exception:
        pass

    result = _runtime.execute_wrapped_script(script)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error", "Step execution failed."),
            "traceback": result.get("traceback", ""),
            "script_file": saved,
            "retry_context": result.get("retry_context"),
        }

    return {
        "ok": True,
        "script_file": saved,
        **_format_context_response(result.get("state", {}), result.get("selection", {})),
    }


def _handle_reset_session():
    if _runtime is None:
        return {"ok": False, "error": "Runtime module is unavailable. Check auto_runner/log.txt."}

    reset_result = _runtime.request_reset()
    if not reset_result.get("ok"):
        return {"ok": False, "error": reset_result.get("error", "Failed to reset session.")}

    payload = reset_result.get("data", {})
    script = payload.get("script", "")

    if script:
        execute_result = _runtime.execute_wrapped_script(script)
        if not execute_result.get("ok"):
            return {
                "ok": False,
                "error": execute_result.get("error", "Reset script failed."),
                "traceback": execute_result.get("traceback", ""),
            }

    model_state, selection = _runtime.capture_and_store_current_context()
    return {
        "ok": True,
        "assistant_message": "Conversation and design reset completed.",
        **_format_context_response(model_state, selection),
    }


def _show_palette():
    if not ui:
        return

    palette = ui.palettes.itemById(PALETTE_ID)
    if palette is None:
        palette = ui.palettes.add(
            id=PALETTE_ID,
            name=PALETTE_NAME,
            htmlFileURL=PALETTE_URL,
            isVisible=True,
            showCloseButton=True,
            isResizable=True,
            width=460,
            height=760,
            useNewWebBrowser=True,
        )
        _log("AI palette created")

    _add_handler(palette.closed, _palette_closed)
    _add_handler(palette.navigatingURL, _palette_navigating)
    _add_handler(palette.incomingFromHTML, _palette_incoming)

    if palette.dockingState == adsk.core.PaletteDockingStates.PaletteDockStateFloating:
        palette.dockingState = PALETTE_DOCKING

    palette.isVisible = True


def _ensure_ui_command():
    if not ui:
        return

    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is None:
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESCRIPTION, ICON_FOLDER)

    _add_handler(cmd_def.commandCreated, _command_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
    if panel:
        control = panel.controls.itemById(CMD_ID)
        if control is None:
            control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)
        control.isPromoted = True


def _clear_ui():
    if not ui:
        return

    try:
        palette = ui.palettes.itemById(PALETTE_ID)
        if palette:
            palette.deleteMe()
    except Exception:
        _log_error("Failed to delete palette")

    try:
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
        command_control = panel.controls.itemById(CMD_ID) if panel else None
        if command_control:
            command_control.deleteMe()
    except Exception:
        _log_error("Failed to delete command control")

    try:
        command_definition = ui.commandDefinitions.itemById(CMD_ID)
        if command_definition:
            command_definition.deleteMe()
    except Exception:
        _log_error("Failed to delete command definition")


def _start_monitor_thread():
    global _monitor_thread

    if _runtime is None:
        _log("Runtime unavailable, monitor thread not started")
        return

    if _monitor_thread and _monitor_thread.is_alive():
        return

    _monitor_stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_runtime.monitor_script_file,
        args=(_monitor_stop_event,),
        daemon=True,
    )
    _monitor_thread.start()


def _stop_monitor_thread():
    _monitor_stop_event.set()


def _execute_command_by_id(command_id):
    if not ui:
        return
    command_def = ui.commandDefinitions.itemById(command_id)
    if command_def:
        command_def.execute()


def _command_created(args: adsk.core.CommandCreatedEventArgs):
    try:
        _add_handler(args.command.execute, _command_execute)
        _add_handler(args.command.destroy, _command_destroy)
    except Exception:
        _log_error("Command created handler failed")


def _command_execute(args: adsk.core.CommandEventArgs):
    del args
    try:
        _show_palette()
    except Exception:
        _log_error("Command execute handler failed")


def _command_destroy(args: adsk.core.CommandEventArgs):
    del args


def _palette_closed(args: adsk.core.UserInterfaceGeneralEventArgs):
    del args
    _log("AI palette closed")


def _palette_navigating(args: adsk.core.NavigationEventArgs):
    try:
        url = args.navigationURL or ""
        if url.startswith("http"):
            args.launchExternally = True
    except Exception:
        _log_error("Palette navigating handler failed")


def _palette_incoming(args: adsk.core.HTMLEventArgs):
    response = {"ok": False, "error": "Unknown action."}

    try:
        action = args.action
        if action != "requestContext":
            _log(f"Palette action received: {action}")
        payload = json.loads(args.data) if args.data else {}

        if action == "submitPrompt":
            response = _handle_prompt(payload.get("prompt"))
        elif action == "executeStep":
            response = _handle_execute_step(
                payload.get("script", ""),
                payload.get("step_id", "unknown"),
            )
        elif action == "requestContext":
            if _runtime is not None:
                model_state, selection = _runtime.capture_and_store_current_context()
                response = {"ok": True, **_format_context_response(model_state, selection)}
            else:
                response = {"ok": False, "error": "Runtime module is unavailable."}
        elif action == "resetSession":
            response = _handle_reset_session()

    except Exception:
        response = {
            "ok": False,
            "error": "Unexpected error while processing palette message.",
            "traceback": traceback.format_exc(),
        }
        _log_error("Palette incoming handler failed")

    args.returnData = json.dumps(response)


def run(context):
    del context
    global app, ui
    app = adsk.core.Application.get()
    ui = app.userInterface if app else None

    try:
        if _import_errors:
            for err in _import_errors:
                _log(err)

        _ensure_ui_command()
        _start_monitor_thread()
        if _runtime is not None:
            _runtime.capture_and_store_current_context()
        _log("Add-in started")
        _execute_command_by_id(CMD_ID)
    except Exception:
        _log_error("Add-in startup failed")
        if ui:
            ui.messageBox(f"Add-in startup failed:\n{traceback.format_exc()}")


def stop(context):
    del context
    try:
        _stop_monitor_thread()
        _clear_ui()
        _log("Add-in stopped")
    except Exception:
        _log_error("Add-in stop failed")

    global _handlers
    _handlers = []
