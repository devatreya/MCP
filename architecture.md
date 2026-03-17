# Architecture: Fusion 360 MCP Server

## Overview

This project lets AI agents (Claude Code, Codex, or any MCP-compatible client) drive Autodesk Fusion 360 directly via the **Model Context Protocol (MCP)**. Users describe what they want to build in natural language; the agent calls structured tools that generate and execute Fusion 360 Python API code inside the running application.

The system has two sides that communicate over a local WebSocket:

- **Host side** — the MCP server, running as a normal Python process on the user's machine
- **Fusion side** — a Python add-in running inside the Fusion 360 process, exposing a WebSocket bridge

```
┌────────────────────────────────────────────────────────────────┐
│  AI Agent (Claude Code, Codex, etc.)                           │
│  Speaks: Model Context Protocol (MCP)                          │
└─────────────────────────┬──────────────────────────────────────┘
                          │  stdio / SSE  (MCP protocol)
┌─────────────────────────▼──────────────────────────────────────┐
│  mcp_server.py  (Host Machine)                                 │
│  ┌─────────────────┐   ┌─────────────────┐                    │
│  │  tool_handlers  │   │  bridge_client  │                    │
│  │  (LLM pipeline) │──▶│  (WS client)    │                    │
│  └─────────────────┘   └────────┬────────┘                    │
│                                  │  WebSocket  localhost:8765  │
└──────────────────────────────────┼─────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────┐
│  Fusion 360 Process                                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  auto_runner add-in                                      │  │
│  │  ┌──────────────┐   ┌──────────────┐   ┌─────────────┐  │  │
│  │  │  ws_bridge   │──▶│  UI thread   │──▶│  runtime   │  │  │
│  │  │  (WS server) │   │  dispatcher  │   │  (exec)    │  │  │
│  │  └──────────────┘   └──────────────┘   └─────────────┘  │  │
│  │                                                           │  │
│  │  ┌────────────────────────────────────────────────────┐  │  │
│  │  │  Palette UI  (human-facing panel, runs in parallel)│  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Fusion 360 Engine  (C++ core, design API)                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## MCP Tools (the public API)

Five tools are exposed. Any MCP-compatible agent can call them.

| Tool | Input | Output | Description |
|---|---|---|---|
| `create_geometry` | `prompt: str`, `selection_context?: object` | `{ok, state, summary, steps?, error?}` | Create new geometry from a natural language description |
| `modify_geometry` | `prompt: str`, `selection_context?: object` | `{ok, state, summary, steps?, error?}` | Modify existing geometry |
| `get_design_state` | — | `{state: object, summary: str}` | Read the current design tree, bodies, bounding boxes |
| `reset_design` | — | `{ok, state, summary}` | Delete all geometry and reset the session |
| `take_screenshot` | — | `{ok, image_base64, mime_type}` | Capture the current viewport as a PNG |

`create_geometry` and `modify_geometry` are semantically separate tools so agents can build accurate mental models of what they're doing (creating vs. editing), but both feed through the same internal pipeline.

---

## Host Side: LLM Pipeline

When `create_geometry` or `modify_geometry` is called, the following pipeline runs synchronously before any code reaches Fusion 360:

```
User prompt
    │
    ▼
intent_extractor.py  ─────────────────────────────────────────
  extract_intent()                                            │
  → IntentResult {                                            │
      operation_family,   ← one of 13 families               │
      human_intent,       ← one-sentence description          │
      params,             ← dimensions, all in cm             │
      required_selections ← ["face"] | ["edge"] | []          │
    }                                                         │
    │                                                         │
    ▼                                                         │
selection_validator.py                                        │
  validate_intent()                                           │
  → list[str]  (empty = valid, else error messages)           │
    │                                                         │
    ├── if operation_family == "composite"                    │
    │       ▼                                                 │
    │   step_planner.py                                       │
    │   plan_steps()                                          │
    │   → list[StepPlan] {                                    │
    │       step_id, family, description,                     │
    │       params, requires_selection,                       │
    │       selection_prompt                                   │
    │     }                                                   │
    │       ▼                                                 │
    │   (for each step)                                       │
    │       ▼                                                 │
    └── code_generator.py  ◄───────────────────────────────┘
          generate_cad_code()
          → (code: str, model_used: str)

          Inside code_generator:
            rag/retriever.py    → top-6 relevant API doc chunks (vector search)
            fusion_api_knowledge.py → curated API cards for this operation family
            llm_adapter.py      → LLM call with fallback model support
            find_api_issues()   → lint check on generated code
            (retry loop if lint fails, up to 2 retries)
    │
    ▼
script_utils.py
  clean_generated_code()        ← strip markdown, repair selection patterns
  wrap_script_with_run()        ← wrap in def run(context): with adsk setup
  add_execution_checkpoints()   ← inject checkpoint() calls for step tracking
    │
    ▼
bridge_client.py
  send { "type": "execute_script", "payload": { "script": "..." } }
    │
    ▼ WebSocket localhost:8765
```

### Key data structures

**IntentResult** (`intent_extractor.py`):
```python
@dataclass
class IntentResult:
    operation_family: str     # "extrude" | "hole" | "fillet_chamfer" | "composite" | ...
    human_intent: str         # "Extrude the top face by 5cm"
    params: dict              # {"height_cm": 5.0, "diameter_cm": 1.0, ...}
    required_selections: list # ["face"] | ["edge"] | ["body"] | []
    raw: dict                 # full LLM JSON response
```

**StepPlan** (`step_planner.py`):
```python
@dataclass
class StepPlan:
    step_id: str              # "step_1", "step_2", ...
    family: str               # never "composite" or "unknown"
    description: str          # detailed enough for face-finding heuristics
    params: dict
    requires_selection: bool
    selection_prompt: str     # what to ask user if requires_selection is true
```

### Operation families

The 13 families the intent extractor classifies into:

| Family | Description |
|---|---|
| `sketch` | Create a 2D sketch profile |
| `extrude` | Extrude a sketch into a 3D body |
| `revolve` | Revolve a profile around an axis |
| `sweep` | Sweep a profile along a path |
| `fillet_chamfer` | Round or bevel edges |
| `hole` | Drill a hole in a face |
| `shell` | Hollow out a body |
| `pattern` | Linear or circular pattern of features |
| `mirror` | Mirror features across a plane |
| `boolean` | Join, cut, or intersect bodies |
| `transform` | Move or rotate a body |
| `composite` | Multiple operations (routes to step planner) |
| `unknown` | Non-CAD request (rejected with helpful error) |

---

## Fusion Side: WebSocket Bridge

### Thread architecture

Fusion 360's Python API is single-threaded — all API calls must run on the UI thread. The bridge runs its WebSocket server on a background thread but marshals execution back to the UI thread using Fusion's custom event system.

```
Background thread (ws_bridge)         UI thread (Fusion main loop)
────────────────────────────          ────────────────────────────
receive WebSocket message
parse JSON envelope
put request in pending_requests{}
app.fireCustomEvent(eventId, json)  ──▶  CustomEventHandler.notify()
                                         extract payload
                                         call runtime.py function
                                         put result in response_queue
read result from response_queue    ◄──
send JSON response over WebSocket
```

### Message protocol

All messages over the WebSocket use this envelope:

**Request** (host → Fusion):
```json
{
  "type": "execute_script | get_state | reset | take_screenshot",
  "request_id": "<uuid4>",
  "payload": { ... }
}
```

**Response** (Fusion → host):
```json
{
  "request_id": "<uuid4>",
  "ok": true,
  "data": { ... },
  "error": null
}
```

**Payload shapes by type:**

| Type | Request payload | Response data |
|---|---|---|
| `execute_script` | `{"script": "<python string>"}` | `{"state": {...}, "selection": {...}}` |
| `get_state` | `{}` | `{"state": {...}, "selection": {...}}` |
| `reset` | `{}` | `{"state": {...}}` |
| `take_screenshot` | `{}` | `{"image_base64": "...", "mime_type": "image/png"}` |

### Bridge config (`bridge_config.py`)

```python
BRIDGE_PORT            = 8765
RECONNECT_INTERVAL_S   = 2
MAX_RECONNECT_ATTEMPTS = 10
REQUEST_TIMEOUT_S      = 120
```

### runtime.py — the execution engine

`auto_runner/runtime.py` is the core of the Fusion side. It is **not modified** by the MCP migration — the bridge is a thin wrapper around its public functions:

| Function | What it does |
|---|---|
| `execute_wrapped_script(script)` | `exec()` the script in the Fusion Python namespace, captures new state on success |
| `capture_model_state()` | Returns dict: body count, body names, bounding boxes, face/edge counts, volumes |
| `capture_selection_context()` | Returns dict: selected entities (faces, edges, bodies) with geometry properties |
| `capture_and_store_current_context()` | Calls both above, saves to `state.json`, returns both |
| `summarize_model_state(state)` | Human-readable text summary (for LLM prompts) |
| `summarize_selection_context(ctx)` | Human-readable text summary |

### Design state shape

The `state` object returned by `capture_model_state()`:

```json
{
  "timestamp": "2026-03-17T12:00:00",
  "units": "cm",
  "body_count": 2,
  "sketch_count": 1,
  "feature_count": 5,
  "bodies": [
    {
      "index": 0,
      "name": "Body1",
      "face_count": 6,
      "edge_count": 12,
      "volume_cm3": 125.0,
      "bbox": {
        "min": {"x": 0, "y": 0, "z": 0},
        "max": {"x": 5, "y": 5, "z": 5},
        "size": {"x": 5, "y": 5, "z": 5},
        "center": {"x": 2.5, "y": 2.5, "z": 2.5}
      }
    }
  ],
  "origin_planes": [
    {"name": "XY Plane", "code": "rootComp.xYConstructionPlane", "label": "Top"},
    {"name": "XZ Plane", "code": "rootComp.xZConstructionPlane", "label": "Front"},
    {"name": "YZ Plane", "code": "rootComp.yZConstructionPlane", "label": "Right"}
  ]
}
```

---

## Knowledge Systems

### Curated API cards (`fusion_api_knowledge.py`)

18 hand-verified cards covering the most error-prone Fusion API patterns. Each card has:
- A short ID (e.g., `extrude-feature-input`)
- Keywords for retrieval
- Verified code examples
- Source URLs from Autodesk docs

Cards are selected per operation family via `get_cards_for_family(family)` and injected into the code generator prompt as the highest-priority reference. They exist to prevent the LLM from repeating known API mistakes (deprecated methods, wrong argument order, etc.).

The same module also provides:
- `find_api_issues(code)` — static lint: detects ~6 known anti-patterns
- `apply_api_repairs(code)` — auto-fix common issues before sending to Fusion

### RAG system (`rag/`)

Vector search over scraped Autodesk API documentation. Used to supplement the curated cards with broader coverage.

```
Build time:  scraper.py → chunker.py → embedder.py → store.py → rag_index/
Query time:  retriever.retrieve(query, client, k=6)
             → cosine similarity → top-6 chunks (threshold: 0.15)
             → graceful degradation if index missing
```

Embedding model: `text-embedding-3-small` (1536 dimensions).
The RAG chunks are injected before curated cards in the prompt — cards always take precedence.

---

## Generated Script Lifecycle

1. LLM generates a raw code body (no imports, no `def run()`, no setup)
2. `clean_generated_code()` — strips markdown fences, repairs broken selection access patterns
3. `apply_api_repairs()` — auto-fixes known API mistakes
4. `find_api_issues()` — lint check; if issues found, LLM retries with feedback (up to 2 retries)
5. `add_execution_checkpoints()` — injects `checkpoint(n)` calls for step progress tracking
6. `wrap_script_with_run()` — wraps in `def run(context):` with standard adsk setup variables pre-injected
7. Script is saved to `generated_scripts/<prefix>_<timestamp>.py` for debugging
8. Sent to Fusion bridge via `execute_script` message

**Pre-injected variables** (available in every generated script):
```python
app = adsk.core.Application.get()
ui = app.userInterface
design = adsk.fusion.Design.cast(app.activeProduct)
rootComp = design.rootComponent
sketches = rootComp.sketches
extrudes = rootComp.features.extrudeFeatures
# ... all major feature collections
```

---

## Human-Facing Panel (unchanged)

The Fusion 360 palette UI (`auto_runner/commands/paletteShow/resources/html/`) continues to work alongside the MCP interface. Users can still type prompts directly into the panel in Fusion. The palette and the MCP bridge are independent paths that both call `runtime.py` on the UI thread.

---

## File Map

```
MCP/
├── mcp_server.py           ← NEW: MCP server entry point (replaces Flask as agent interface)
├── tool_handlers.py        ← NEW: one handler per MCP tool; orchestrates the pipeline
├── bridge_client.py        ← NEW: async WebSocket client connecting to ws_bridge
├── bridge_config.py        ← NEW: shared constants (port, timeouts, reconnect policy)
├── script_utils.py         ← NEW: extracted helpers (clean, wrap, checkpoint, summarize)
│
├── server.py               ← UNCHANGED: original Flask server (kept for backward compat)
├── intent_extractor.py     ← UNCHANGED
├── step_planner.py         ← UNCHANGED
├── code_generator.py       ← UNCHANGED
├── selection_validator.py  ← UNCHANGED
├── fusion_api_knowledge.py ← UNCHANGED
├── llm_adapter.py          ← UNCHANGED
├── cad_ir.py               ← UNCHANGED
├── cad_capabilities.py     ← UNCHANGED
│
├── rag/
│   ├── retriever.py        ← UNCHANGED
│   ├── embedder.py         ← UNCHANGED
│   ├── store.py            ← UNCHANGED
│   ├── chunker.py          ← UNCHANGED
│   └── build.py            ← UNCHANGED
│
├── auto_runner/
│   ├── ws_bridge.py        ← NEW: WebSocket server (runs inside Fusion, on bg thread)
│   ├── auto_runner.py      ← MODIFIED: start/stop ws_bridge alongside palette
│   ├── runtime.py          ← UNCHANGED (zero changes — bridge wraps this)
│   ├── fusion_auto_run.py  ← UNCHANGED
│   └── commands/paletteShow/resources/html/  ← UNCHANGED
│
└── tests/
    ├── conftest.py              ← NEW: shared pytest fixtures (mock bridge, mock OpenAI)
    ├── test_mcp_tools.py        ← NEW: unit tests for all 5 tool handlers
    ├── test_bridge_protocol.py  ← NEW: Contract 1 compliance tests for ws_bridge
    ├── test_llm_adapter.py      ← UNCHANGED
    ├── test_server_routes.py    ← UNCHANGED
    ├── test_step_planner.py     ← UNCHANGED
    └── test_structured_pipeline.py ← UNCHANGED
```

---

## Build Subagents

Development is split into three parallel agents (plus one mandatory setup phase):

### Phase 0 — Contracts (sequential, done first)
Write `bridge_config.py` with the three contracts above. No other agent can start until this is done.

### Agent A — MCP Server + Tool Handlers
Creates: `mcp_server.py`, `tool_handlers.py`, `bridge_client.py`, `script_utils.py`
Reuses: all existing pipeline modules unchanged
Depends on: Contracts 1, 2, 3

### Agent B — Fusion WebSocket Bridge
Creates: `auto_runner/ws_bridge.py`
Modifies: `auto_runner/auto_runner.py`
Reuses: `auto_runner/runtime.py` entirely unchanged
Depends on: Contracts 1, 3

### Agent C — Tests
Creates: `tests/conftest.py`, `tests/test_mcp_tools.py`, `tests/test_bridge_protocol.py`
Depends on: Contracts 1, 2 (and iterates as A and B stabilize)

Agents A, B, and C run in parallel after Phase 0 completes.

---

## Verification Steps

1. **Unit tests (no Fusion needed):**
   ```
   pytest tests/test_mcp_tools.py tests/test_bridge_protocol.py
   ```

2. **MCP smoke test:**
   ```
   claude mcp add fusion -- python mcp_server.py
   # in a new session: call get_design_state
   ```

3. **Live E2E:**
   - Load add-in in Fusion 360
   - Run `python smoke_test_e2e.py`
   - Confirms: tool call → WebSocket → Fusion → state returned

4. **Backward compatibility:**
   ```
   python server.py
   curl -X POST localhost:5000/generate -d '{"prompt":"make a cube"}'
   ```
   Must still return a valid script response.
