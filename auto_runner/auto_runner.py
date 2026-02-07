import json
import os
import threading
import traceback

import adsk.core

import config
import runtime

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_ai_panel"
CMD_NAME = "Open AI Panel"
CMD_DESCRIPTION = "Open the MCP AI side panel"
PALETTE_ID = config.sample_palette_id
PALETTE_NAME = "MCP AI Assistant"
PALETTE_DOCKING = adsk.core.PaletteDockingStates.PaletteDockStateRight

WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_ID = "SolidScriptsAddinsPanel"
COMMAND_BESIDE_ID = "ScriptsManagerCommand"
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands", "paletteShow", "resources", "")
PALETTE_URL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "commands",
    "paletteShow",
    "resources",
    "html",
    "index.html",
).replace("\\", "/")

_handlers = []
_monitor_stop_event = threading.Event()
_monitor_thread = None


def _log_error(context):
    runtime.log(f"{context}: {traceback.format_exc()}")


def _add_handler(event, handler):
    event.add(handler)
    _handlers.append(handler)


def _format_context_response(model_state, selection):
    return {
        "state": model_state,
        "selection": selection,
        "state_summary": runtime.summarize_model_state(model_state),
        "selection_summary": runtime.summarize_selection_context(selection),
    }


def _handle_prompt(prompt):
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Prompt is empty."}

    model_state, selection = runtime.capture_and_store_current_context()

    generate_result = runtime.request_generate(prompt, model_state, selection)
    if not generate_result.get("ok"):
        return {"ok": False, "error": generate_result.get("error", "Failed to generate script.")}

    payload = generate_result.get("data", {})
    script = payload.get("script", "")
    if not script:
        return {"ok": False, "error": "Server response did not include a script."}

    saved_script_file = None
    try:
        saved_script_file = runtime.save_script_snapshot(script, prefix="panel")
    except Exception:
        runtime.log("Failed to save panel script snapshot")

    execute_result = runtime.execute_wrapped_script(script)
    if not execute_result.get("ok"):
        return {
            "ok": False,
            "error": execute_result.get("error", "Script execution failed."),
            "traceback": execute_result.get("traceback", ""),
            "script_file": saved_script_file,
        }

    response_context = _format_context_response(
        execute_result.get("state", model_state),
        execute_result.get("selection", selection),
    )

    return {
        "ok": True,
        "assistant_message": "Edit applied successfully.",
        "script_file": saved_script_file,
        "raw_response": payload,
        **response_context,
    }


def _handle_reset_session():
    reset_result = runtime.request_reset()
    if not reset_result.get("ok"):
        return {"ok": False, "error": reset_result.get("error", "Failed to reset session.")}

    payload = reset_result.get("data", {})
    script = payload.get("script", "")

    if script:
        execute_result = runtime.execute_wrapped_script(script)
        if not execute_result.get("ok"):
            return {
                "ok": False,
                "error": execute_result.get("error", "Reset script failed."),
                "traceback": execute_result.get("traceback", ""),
            }

    model_state, selection = runtime.capture_and_store_current_context()
    return {
        "ok": True,
        "assistant_message": "Conversation and design reset completed.",
        **_format_context_response(model_state, selection),
    }


def _show_palette():
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
        _add_handler(palette.closed, PaletteClosedHandler())
        _add_handler(palette.navigatingURL, PaletteNavigatingHandler())
        _add_handler(palette.incomingFromHTML, PaletteIncomingHandler())
        runtime.log("AI palette created")

    if palette.dockingState == adsk.core.PaletteDockingStates.PaletteDockStateFloating:
        palette.dockingState = PALETTE_DOCKING

    palette.isVisible = True


def _ensure_ui_command():
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is None:
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESCRIPTION, ICON_FOLDER)
        _add_handler(cmd_def.commandCreated, CommandCreatedHandler())

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
    if panel:
        control = panel.controls.itemById(CMD_ID)
        if control is None:
            control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)
        control.isPromoted = True


def _clear_ui():
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

    if _monitor_thread and _monitor_thread.is_alive():
        return

    _monitor_stop_event.clear()
    _monitor_thread = threading.Thread(
        target=runtime.monitor_script_file,
        args=(_monitor_stop_event,),
        daemon=True,
    )
    _monitor_thread.start()


def _stop_monitor_thread():
    _monitor_stop_event.set()


def _execute_command_by_id(command_id):
    command_def = ui.commandDefinitions.itemById(command_id)
    if command_def:
        command_def.execute()


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _add_handler(args.command.execute, CommandExecuteHandler())
            _add_handler(args.command.destroy, CommandDestroyHandler())
        except Exception:
            _log_error("Command created handler failed")


class CommandExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        del args
        try:
            _show_palette()
        except Exception:
            _log_error("Command execute handler failed")


class CommandDestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        del args


class PaletteClosedHandler(adsk.core.UserInterfaceGeneralEventHandler):
    def notify(self, args):
        del args
        runtime.log("AI palette closed")


class PaletteNavigatingHandler(adsk.core.NavigationEventHandler):
    def notify(self, args):
        try:
            url = args.navigationURL or ""
            if url.startswith("http"):
                args.launchExternally = True
        except Exception:
            _log_error("Palette navigating handler failed")


class PaletteIncomingHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        response = {"ok": False, "error": "Unknown action."}

        try:
            action = args.action
            payload = json.loads(args.data) if args.data else {}

            if action == "submitPrompt":
                response = _handle_prompt(payload.get("prompt"))
            elif action == "requestContext":
                model_state, selection = runtime.capture_and_store_current_context()
                response = {"ok": True, **_format_context_response(model_state, selection)}
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
    try:
        _ensure_ui_command()
        _start_monitor_thread()
        runtime.capture_and_store_current_context()
        runtime.log("Add-in started")
        _execute_command_by_id(CMD_ID)
    except Exception:
        _log_error("Add-in startup failed")
        ui.messageBox(f"Add-in startup failed:\n{traceback.format_exc()}")


def stop(context):
    del context
    try:
        _stop_monitor_thread()
        _clear_ui()
        runtime.log("Add-in stopped")
    except Exception:
        _log_error("Add-in stop failed")

    global _handlers
    _handlers = []
