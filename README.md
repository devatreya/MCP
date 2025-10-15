# Fusion 360 Conversational CAD Assistant

A conversational AI assistant for Autodesk Fusion 360 that allows you to create and modify CAD designs using natural language.

## Overview

This project enables you to control Fusion 360 through natural language commands. Type instructions like "make a 10cm cube" or "add a 5cm hole on the top face," and watch your model update in real-time.

## Architecture

### Components

1. **Flask Server** (`server.py`)
   - Handles HTTP requests from the web UI
   - Uses OpenAI API to generate Fusion 360 Python scripts
   - Maintains conversation history and model state
   - Cleans and wraps generated code

2. **File Watcher** (`watcher.py`)
   - Monitors `generated_scripts/` directory
   - Automatically copies new scripts to `auto_runner/fusion_auto_run.py`

3. **Fusion Add-In** (`auto_runner/`)
   - Background thread that monitors `fusion_auto_run.py`
   - Executes scripts safely when changes are detected
   - Logs execution results

4. **Web UI** (`templates/index.html`)
   - Simple chat interface
   - Shows conversation history
   - Reset button to clear design and session

## Setup

### Prerequisites

- Python 3.6+
- Autodesk Fusion 360
- OpenAI API key

### Installation

1. **Install Python dependencies:**
   ```bash
   pip install flask openai watchdog
   ```

2. **Set environment variables:**
   ```bash
   export OPENAI_API_KEY="your-api-key"
   export OPENAI_ORG_ID="your-org-id"
   export OPENAI_PROJECT_ID="your-project-id"
   ```

3. **Install Fusion 360 Add-In:**
   - Open Fusion 360
   - Go to **Tools → Scripts and Add-Ins → Add-Ins**
   - Click the **+** button
   - Navigate to the `auto_runner` directory
   - Select and run the add-in

### Running

1. **Start the file watcher:**
   ```bash
   python3 watcher.py
   ```

2. **Start the Flask server:**
   ```bash
   python3 server.py
   ```

3. **Open the web UI:**
   - Navigate to http://localhost:5000

4. **Start creating!**
   - Type natural language commands
   - Watch your design update in Fusion 360

## Features

- ✅ Stateful conversation with context memory
- ✅ Smart model cleanup (create vs modify detection)
- ✅ Automatic code sanitization and error handling
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
- `watcher.py` - File system watcher
- `auto_runner/auto_runner.py` - Fusion 360 add-in entry point
- `auto_runner/fusion_auto_run.py` - Script executed by Fusion (auto-generated)
- `templates/index.html` - Web UI

### Debugging

- Check `auto_runner/log.txt` for Fusion execution logs
- Generated scripts are saved in `generated_scripts/` with timestamps
- Flask runs in debug mode by default

## License

MIT

## Contributing

Contributions welcome! Please open an issue or PR.

