# Fusion 360 Conversational CAD Assistant

A conversational AI assistant for Autodesk Fusion 360 that allows you to create and modify CAD designs using natural language.

## Overview

This project enables you to control Fusion 360 through natural language commands. Type instructions like "make a 10cm cube" or "add a 5cm hole on the top face," and watch your model update in real-time.

## Architecture

### Components

1. **Flask Server** (`server.py`)
   - Handles generation/reset requests from the Fusion panel and web UI.
   - Uses OpenAI API to generate Fusion 360 Python scripts.
   - Injects structured Fusion model state and selection context into prompts.
   - Cleans and wraps generated code in a safe `run(context)` function.

2. **Fusion Add-In** (`auto_runner/`)
   - Adds an in-Fusion side panel (`MCP AI Assistant`) for prompting edits.
   - Captures live model state and active selections.
   - Sends prompt + context to Flask server, executes returned scripts directly.
   - Maintains backward-compatible monitor mode for `fusion_auto_run.py`.

3. **File Watcher (Optional)** (`watcher.py`)
   - Optional compatibility path.
   - Monitors `generated_scripts/` and copies files to `auto_runner/fusion_auto_run.py`.

4. **Web UI (Optional)** (`templates/index.html`)
   - Legacy browser chat interface.
   - Useful for debugging server responses independent of Fusion.

## Setup

### Prerequisites

- Python 3.6+
- Autodesk Fusion 360
- OpenAI API key

### Installation

1. **Install Python dependencies:**
   ```bash
   pip install flask openai watchdog python-dotenv
   ```

2. **Set environment variables:**
   ```bash
   export OPENAI_API_KEY="your-api-key"
   export OPENAI_ORG_ID="your-org-id"
   export OPENAI_PROJECT_ID="your-project-id"
   ```
   Or create a local `.env` file in the project root:
   ```bash
   OPENAI_API_KEY="your-api-key"
   OPENAI_ORG_ID="your-org-id"
   OPENAI_PROJECT_ID="your-project-id"
   ```

3. **Install Fusion 360 Add-In:**
   - Open Fusion 360
   - Go to **Tools → Scripts and Add-Ins → Add-Ins**
   - Click the **+** button
   - Navigate to the `auto_runner` directory
   - Select and run the add-in

### Running (Phase 1: In-Fusion AI Panel)

1. **Start the Flask server:**
   ```bash
   python3 server.py
   ```

2. **Launch the add-in in Fusion 360:**
   - Open **Tools -> Scripts and Add-Ins -> Add-Ins**
   - Select `auto_runner`
   - Click **Run**

3. **Use the side panel:**
   - The `MCP AI Assistant` palette opens on the right.
   - Enter prompts and click **Apply Edit**.
   - Use **Refresh Context** to inspect current model and selection context.
   - Use **Reset Session + Design** to clear both conversation and geometry.

### Running (Optional Legacy File-Watcher Path)

1. Start the Flask server:
   ```bash
   python3 server.py
   ```
2. Start watcher:
   ```bash
   python3 watcher.py
   ```
3. Run Fusion add-in (`auto_runner`) and scripts copied into `fusion_auto_run.py` will execute.

## Features

- ✅ Stateful conversation with context memory
- ✅ Smart model cleanup (create vs modify detection)
- ✅ Automatic code sanitization and error handling
- ✅ Fusion in-app AI panel (side palette)
- ✅ Structured model state injected into prompt context
- ✅ Live active selection context injected into prompt context
- ✅ Unit conversion (mm, cm, m → cm)
- ✅ Real-time execution in Fusion 360
- ✅ Face detection for feature placement
- ✅ Boolean operations for cuts and holes

## Example Commands

- "Make a cube with size of 10cm"
- "Add a hole with diameter of 5 cm and depth of 2 cm on the top face"
- "Make it 5 cm taller"
- "Create a cylinder with radius 3cm and height 8cm"

## Current Limitations

- Hole cutting requires Boolean subtraction (work in progress on direction)
- Feature naming and parameter editing not yet implemented
- Limited to basic geometric operations

## Development

### Key Files

- `server.py` - Main Flask application and LLM integration
- `auto_runner/runtime.py` - Shared Fusion runtime (context capture, server calls, script execution)
- `watcher.py` - File system watcher
- `auto_runner/auto_runner.py` - Fusion 360 add-in entry point
- `auto_runner/commands/paletteShow/resources/html/index.html` - In-Fusion AI panel UI
- `auto_runner/commands/paletteShow/resources/html/static/palette.js` - Panel logic and Fusion messaging
- `auto_runner/fusion_auto_run.py` - Script executed by Fusion (auto-generated)
- `templates/index.html` - Web UI

### Debugging

- Check `auto_runner/log.txt` for Fusion execution logs
- Generated scripts are saved in `generated_scripts/` with timestamps
- Flask runs in debug mode by default

## License

MIT
