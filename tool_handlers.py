"""
tool_handlers.py — One synchronous handler per MCP tool.

All handlers are synchronous functions; the async MCP server calls them via
asyncio.to_thread (or directly if already in a sync context).

Import tree:
    intent_extractor  → extract_intent, IntentResult
    step_planner      → plan_steps, StepPlan
    code_generator    → generate_cad_code
    selection_validator → validate_intent
    fusion_api_knowledge → find_api_issues, apply_api_repairs
    llm_adapter       → create_text_completion_with_fallback
    script_utils      → clean_generated_code, wrap_script_with_run,
                        add_execution_checkpoints, summarize_fusion_state,
                        summarize_selection_context, should_clear_model
    bridge_client     → run_bridge_call
    bridge_config     → MSG_* constants
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bridge_client import run_bridge_call
from bridge_config import (
    MSG_EXECUTE_SCRIPT,
    MSG_GET_STATE,
    MSG_RESET,
    MSG_TAKE_SCREENSHOT,
)
from code_generator import FUSION_CODING_CONVENTIONS, generate_cad_code
from fusion_api_knowledge import apply_api_repairs, build_api_guidance, find_api_issues
from intent_extractor import IntentResult, extract_intent
from llm_adapter import create_text_completion_with_fallback
from script_utils import (
    add_execution_checkpoints,
    clean_generated_code,
    should_clear_model,
    summarize_fusion_state,
    summarize_selection_context,
    wrap_script_with_run,
)
from rag.retriever import retrieve as rag_retrieve
from selection_validator import validate_intent
from step_planner import plan_steps


# ---------------------------------------------------------------------------
# HandlerContext — carries shared dependencies into every handler
# ---------------------------------------------------------------------------

@dataclass
class HandlerContext:
    client: Any                    # OpenAI client instance
    model: str                     # primary model name
    fallback_model: str            # fallback model name
    bridge: Any                    # BridgeClient or MockBridgeClient
    conversation_history: dict     # session_id → list[dict] (role/content messages)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_LINT_RETRIES = 2
_MAX_RUNTIME_RETRIES = 2


def _get_state(ctx: HandlerContext) -> dict:
    """Fetch the current Fusion 360 state via the bridge."""
    response = run_bridge_call(ctx.bridge.get_state())
    return response


def _body_names(state: dict) -> set:
    return {b.get("name", "") for b in state.get("bodies", [])}


def _compute_diff(before_state: dict, after_state: dict) -> dict:
    """Compute what changed between two Fusion 360 state snapshots."""
    before_names = _body_names(before_state)
    after_names = _body_names(after_state)

    bodies_added = list(after_names - before_names)
    bodies_removed = list(before_names - after_names)

    # Bodies present in both — check for bbox / volume changes
    bodies_modified = []
    before_map = {b.get("name", ""): b for b in before_state.get("bodies", [])}
    after_map = {b.get("name", ""): b for b in after_state.get("bodies", [])}
    for name in before_names & after_names:
        b_before = before_map[name]
        b_after = after_map[name]
        # Compare volume if available
        vol_before = b_before.get("volume_cm3")
        vol_after = b_after.get("volume_cm3")
        if vol_before is not None and vol_after is not None:
            if abs(vol_before - vol_after) > 1e-6:
                bodies_modified.append(name)
                continue
        # Fall back to bbox size comparison
        bbox_before = b_before.get("bbox") or {}
        bbox_after = b_after.get("bbox") or {}
        size_before = bbox_before.get("size") or {}
        size_after = bbox_after.get("size") or {}
        for axis in ("x", "y", "z"):
            if abs(size_before.get(axis, 0) - size_after.get(axis, 0)) > 1e-6:
                bodies_modified.append(name)
                break

    feature_count_change = (
        after_state.get("feature_count", 0) - before_state.get("feature_count", 0)
    )

    return {
        "bodies_added": bodies_added,
        "bodies_removed": bodies_removed,
        "bodies_modified": bodies_modified,
        "feature_count_change": feature_count_change,
    }


def _run_geometry_pipeline(
    ctx: HandlerContext,
    prompt: str,
    selection_context: dict | None,
    force_no_clear: bool = False,
) -> dict:
    """Shared pipeline for create_geometry and modify_geometry.

    Steps:
      1. Get current state from bridge.
      2. Build state/selection text summaries.
      3. Extract intent.
      4. Validate intent.
      5a. Composite → step pipeline.
      5b. Single-op → lint-retry loop.
      6. Execute via bridge.
      7. Get new state, compute diff.
      8. Return result dict.
    """
    # Step 1: get current state
    state_response = _get_state(ctx)
    before_state = state_response.get("data", {}).get("state", {}) if state_response.get("ok") else {}

    # Step 2: build text summaries
    fusion_state_text = summarize_fusion_state(before_state)
    selection_context_dict = selection_context if isinstance(selection_context, dict) else {}
    selection_state_text = summarize_selection_context(selection_context_dict)

    # Use "default" as the session key if no better scoping is available
    conversation = ctx.conversation_history.get("default", [])
    conversation.append({"role": "user", "content": prompt})
    ctx.conversation_history["default"] = conversation

    # Step 3: extract intent
    try:
        intent = extract_intent(
            user_prompt=prompt,
            fusion_state_text=fusion_state_text,
            selection_state_text=selection_state_text,
            client=ctx.client,
            model=ctx.model,
            fallback_model=ctx.fallback_model,
            conversation_history=conversation,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Intent extraction failed: {exc}",
            "retry_context": {
                "stage": "intent_extraction",
                "prompt": prompt,
                "recommended_action": "Retry with a clearer description of the CAD operation.",
            },
        }

    # Step 4: validate intent
    validation_issues = validate_intent(intent, selection_context_dict)
    if validation_issues:
        return {
            "ok": False,
            "error": validation_issues[0],
            "details": validation_issues,
            "retry_context": {
                "stage": "intent_validation",
                "issues": validation_issues,
                "prompt": prompt,
            },
        }

    # Step 5a: composite → step pipeline
    if intent.operation_family == "composite":
        return _run_step_pipeline(
            ctx=ctx,
            intent=intent,
            fusion_state_text=fusion_state_text,
            selection_state_text=selection_state_text,
            before_state=before_state,
            prompt=prompt,
        )

    # Step 5b: single-op — lint-retry loop
    cleaned_code = None
    lint_issues = []
    lint_feedback = None
    for _attempt in range(_MAX_LINT_RETRIES + 1):
        try:
            raw_code, _model_used = generate_cad_code(
                intent_result=intent,
                fusion_state_text=fusion_state_text,
                selection_state_text=selection_state_text,
                client=ctx.client,
                model=ctx.model,
                fallback_model=ctx.fallback_model,
                lint_feedback=lint_feedback,
            )
            cleaned_code = clean_generated_code(raw_code)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Code generation failed: {exc}",
                "retry_context": {
                    "stage": "code_generation",
                    "prompt": prompt,
                },
            }
        lint_issues = find_api_issues(cleaned_code)
        if not lint_issues:
            break
        lint_feedback = lint_issues

    if lint_issues:
        return {
            "ok": False,
            "error": f"Generated script still had API issues after {_MAX_LINT_RETRIES} retries.",
            "details": lint_issues,
            "retry_context": {
                "stage": "preflight_api_lint",
                "issues": lint_issues,
                "prompt": prompt,
            },
        }

    cleaned_code = add_execution_checkpoints(cleaned_code, prompt)

    # Determine whether to wipe the model first
    if force_no_clear:
        clear = False
    else:
        clear = should_clear_model(prompt, before_state)

    wrapped = wrap_script_with_run(cleaned_code, clear_model=clear)

    # Step 6: execute via bridge — with runtime-error retry loop.
    # If Fusion raises a Python exception we get the full traceback back.
    # Feed it to generate_cad_code as lint_feedback so the LLM can fix the
    # specific line rather than regenerating blind.
    exec_response = run_bridge_call(ctx.bridge.execute_script(wrapped))

    for _runtime_attempt in range(_MAX_RUNTIME_RETRIES):
        if exec_response.get("ok"):
            break

        runtime_error = exec_response.get("error") or "Script execution failed."
        traceback_str = exec_response.get("data", {}).get("traceback") or ""
        runtime_feedback = [
            f"Runtime error from Fusion 360:\n{runtime_error}"
            + (f"\n\nFull traceback:\n{traceback_str}" if traceback_str else "")
        ]

        try:
            raw_code, _model_used = generate_cad_code(
                intent_result=intent,
                fusion_state_text=fusion_state_text,
                selection_state_text=selection_state_text,
                client=ctx.client,
                model=ctx.model,
                fallback_model=ctx.fallback_model,
                lint_feedback=runtime_feedback,
            )
            cleaned_code = clean_generated_code(raw_code)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Code generation failed during runtime-retry: {exc}",
                "retry_context": {"stage": "code_generation_runtime_retry"},
            }

        cleaned_code = add_execution_checkpoints(cleaned_code, prompt)
        wrapped = wrap_script_with_run(cleaned_code, clear_model=False)
        exec_response = run_bridge_call(ctx.bridge.execute_script(wrapped))

    if not exec_response.get("ok"):
        return {
            "ok": False,
            "error": exec_response.get("error") or "Script execution failed.",
            "retry_context": {"stage": "execution"},
        }

    # Step 7: get new state and diff
    after_response = _get_state(ctx)
    after_state = after_response.get("data", {}).get("state", {}) if after_response.get("ok") else {}
    diff = _compute_diff(before_state, after_state)

    summary_parts = []
    if diff["bodies_added"]:
        summary_parts.append(f"Added bodies: {', '.join(diff['bodies_added'])}")
    if diff["bodies_removed"]:
        summary_parts.append(f"Removed bodies: {', '.join(diff['bodies_removed'])}")
    if diff["bodies_modified"]:
        summary_parts.append(f"Modified bodies: {', '.join(diff['bodies_modified'])}")
    if diff["feature_count_change"] != 0:
        summary_parts.append(f"Feature count change: {diff['feature_count_change']:+d}")
    summary = "; ".join(summary_parts) if summary_parts else "Script executed successfully."

    return {
        "ok": True,
        "diff": diff,
        "state": after_state,
        "summary": summary,
    }


def _run_step_pipeline(
    ctx: HandlerContext,
    intent: IntentResult,
    fusion_state_text: str,
    selection_state_text: str,
    before_state: dict,
    prompt: str,
) -> dict:
    """Decompose a composite intent into steps, generate per-step code, execute each."""
    # Plan steps
    try:
        steps = plan_steps(
            human_intent=intent.human_intent,
            params=intent.params,
            fusion_state_text=fusion_state_text,
            client=ctx.client,
            model=ctx.model,
            fallback_model=ctx.fallback_model,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Step planning failed: {exc}",
            "retry_context": {
                "stage": "step_planning",
                "prompt": prompt,
                "recommended_action": (
                    "Try rephrasing the prompt or breaking it into individual operations."
                ),
            },
        }

    step_payloads = []
    for step in steps:
        # Build an IntentResult so code_generator gets the right API cards
        _sel_req = (
            ["face"] if step.requires_selection and step.family in ("shell", "hole")
            else ["plane"] if step.requires_selection and step.family in ("extrude", "sketch", "revolve", "sweep")
            else ["edge"] if step.requires_selection and step.family == "fillet_chamfer"
            else ["body"] if step.requires_selection and step.family == "boolean"
            else []
        )
        step_intent = IntentResult(
            operation_family=step.family,
            human_intent=step.description,
            params=step.params,
            required_selections=_sel_req,
        )
        step_selection_ctx = (
            selection_state_text if step.requires_selection
            else (
                "No user selection — find all target geometry programmatically "
                "(use bounding box, face normals, body queries, feature history)."
            )
        )

        # Lint-retry loop per step
        cleaned = None
        api_issues = []
        lint_feedback = None
        for _attempt in range(_MAX_LINT_RETRIES + 1):
            try:
                raw_code, _model_used = generate_cad_code(
                    intent_result=step_intent,
                    fusion_state_text=fusion_state_text,
                    selection_state_text=step_selection_ctx,
                    client=ctx.client,
                    model=ctx.model,
                    fallback_model=ctx.fallback_model,
                    lint_feedback=lint_feedback,
                )
                cleaned = clean_generated_code(raw_code)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": f"Code generation failed for {step.step_id} ({step.family}): {exc}",
                    "retry_context": {
                        "stage": "step_code_generation",
                        "step_id": step.step_id,
                    },
                }
            api_issues = find_api_issues(cleaned)
            if not api_issues:
                break
            lint_feedback = api_issues

        if api_issues:
            return {
                "ok": False,
                "error": f"Script for {step.step_id} still had API issues after {_MAX_LINT_RETRIES} retries.",
                "details": api_issues,
                "retry_context": {
                    "stage": "preflight_api_lint",
                    "step_id": step.step_id,
                    "issues": api_issues,
                },
            }

        cleaned = add_execution_checkpoints(cleaned, step.description)
        wrapped = wrap_script_with_run(cleaned, clear_model=False)

        step_payloads.append({
            "step_id": step.step_id,
            "family": step.family,
            "description": step.description,
            "script": wrapped,
            "requires_selection": step.requires_selection,
            "selection_prompt": step.selection_prompt,
        })

    # Execute each step in sequence
    step_results = []
    current_state = before_state
    for step_payload in step_payloads:
        if step_payload["requires_selection"]:
            # Pause — return what we have so the caller can prompt the user
            step_results.append({
                "step_id": step_payload["step_id"],
                "status": "awaiting_selection",
                "selection_prompt": step_payload["selection_prompt"],
            })
            continue

        exec_response = run_bridge_call(ctx.bridge.execute_script(step_payload["script"]))
        if not exec_response.get("ok"):
            step_results.append({
                "step_id": step_payload["step_id"],
                "status": "error",
                "error": exec_response.get("error") or "Script execution failed.",
            })
            # Continue with remaining steps but record the failure
            continue

        # Refresh state after each step
        after_response = _get_state(ctx)
        current_state = (
            after_response.get("data", {}).get("state", {})
            if after_response.get("ok")
            else current_state
        )
        step_results.append({
            "step_id": step_payload["step_id"],
            "status": "ok",
        })

    diff = _compute_diff(before_state, current_state)
    return {
        "ok": True,
        "diff": diff,
        "state": current_state,
        "summary": f"Completed {len(step_payloads)}-step composite operation.",
        "steps": step_results,
    }


# ---------------------------------------------------------------------------
# Tool 1: create_geometry
# ---------------------------------------------------------------------------

def handle_create_geometry(
    ctx: HandlerContext,
    prompt: str,
    selection_context: dict | None = None,
) -> dict:
    """MCP tool: create_geometry — generate and execute Fusion 360 code from a prompt."""
    return _run_geometry_pipeline(
        ctx=ctx,
        prompt=prompt,
        selection_context=selection_context,
        force_no_clear=False,
    )


# ---------------------------------------------------------------------------
# Tool 2: modify_geometry
# ---------------------------------------------------------------------------

def handle_modify_geometry(
    ctx: HandlerContext,
    prompt: str,
    selection_context: dict | None = None,
) -> dict:
    """MCP tool: modify_geometry — modify existing Fusion 360 geometry from a prompt.

    Identical pipeline to create_geometry except we never clear the model
    (we are editing existing geometry, not starting fresh).
    """
    return _run_geometry_pipeline(
        ctx=ctx,
        prompt=prompt,
        selection_context=selection_context,
        force_no_clear=True,
    )


# ---------------------------------------------------------------------------
# Tool 3: get_design_state
# ---------------------------------------------------------------------------

def handle_get_design_state(ctx: HandlerContext) -> dict:
    """MCP tool: get_design_state — return the current Fusion 360 model state."""
    response = run_bridge_call(ctx.bridge.get_state())
    state = response.get("data", {}).get("state", {}) if response.get("ok") else {}
    return {
        "state": state,
        "summary": summarize_fusion_state(state),
    }


# ---------------------------------------------------------------------------
# Tool 4: verify_geometry
# ---------------------------------------------------------------------------

def handle_verify_geometry(ctx: HandlerContext, constraints: list[dict]) -> dict:
    """MCP tool: verify_geometry — check a list of geometric constraints against the model.

    Supported constraint types:
      {"type": "body_count", "expected": N}
      {"type": "body_exists", "name": str}
      {"type": "bounding_box", "body": str, "size_cm": {"x":f,"y":f,"z":f}, "tolerance": f}
      {"type": "feature_exists", "feature_type": str}
    """
    response = run_bridge_call(ctx.bridge.get_state())
    state = response.get("data", {}).get("state", {}) if response.get("ok") else {}

    bodies = state.get("bodies", [])
    body_map = {b.get("name", ""): b for b in bodies}
    features = state.get("features", [])

    results = []
    for constraint in constraints:
        ctype = constraint.get("type", "")

        if ctype == "body_count":
            expected = constraint.get("expected")
            actual = len(bodies)
            passed = actual == expected
            results.append({
                "constraint": constraint,
                "passed": passed,
                "expected": expected,
                "actual": actual,
            })

        elif ctype == "body_exists":
            name = constraint.get("name", "")
            passed = name in body_map
            results.append({
                "constraint": constraint,
                "passed": passed,
                "expected": name,
                "actual": list(body_map.keys()),
            })

        elif ctype == "bounding_box":
            body_name = constraint.get("body", "")
            expected_size = constraint.get("size_cm") or {}
            tolerance = float(constraint.get("tolerance", 0.01))

            body = body_map.get(body_name)
            if body is None:
                results.append({
                    "constraint": constraint,
                    "passed": False,
                    "expected": expected_size,
                    "actual": None,
                    "delta": None,
                })
                continue

            bbox = body.get("bbox") or {}
            size_dict = bbox.get("size") or {}
            size_list = body.get("size_cm") or [
                size_dict.get("x", 0),
                size_dict.get("y", 0),
                size_dict.get("z", 0),
            ]
            actual_size = {
                "x": size_list[0] if isinstance(size_list, list) and len(size_list) > 0 else size_dict.get("x", 0),
                "y": size_list[1] if isinstance(size_list, list) and len(size_list) > 1 else size_dict.get("y", 0),
                "z": size_list[2] if isinstance(size_list, list) and len(size_list) > 2 else size_dict.get("z", 0),
            }
            delta = {
                axis: abs(actual_size.get(axis, 0) - expected_size.get(axis, 0))
                for axis in ("x", "y", "z")
            }
            passed = all(d <= tolerance for d in delta.values())
            results.append({
                "constraint": constraint,
                "passed": passed,
                "expected": expected_size,
                "actual": actual_size,
                "delta": delta,
            })

        elif ctype == "volume":
            body_name = constraint.get("body", "")
            expected_vol = float(constraint.get("volume_cm3", 0))
            tolerance = float(constraint.get("tolerance", 0.01))
            body = next((b for b in bodies if b.get("name") == body_name), None)
            if body is None:
                results.append({
                    "constraint": constraint,
                    "passed": False,
                    "expected": expected_vol,
                    "actual": None,
                    "error": f"Body {body_name!r} not found",
                })
            else:
                actual_vol = float(body.get("volume_cm3", 0))
                delta = abs(actual_vol - expected_vol)
                results.append({
                    "constraint": constraint,
                    "passed": delta <= tolerance,
                    "expected": expected_vol,
                    "actual": actual_vol,
                    "delta": delta,
                })

        elif ctype == "feature_exists":
            feature_type = (constraint.get("feature_type") or "").strip().lower()
            # features list may contain dicts with a "type" or "family" key
            found = any(
                str(f.get("type", f.get("family", ""))).strip().lower() == feature_type
                for f in features
            )
            results.append({
                "constraint": constraint,
                "passed": found,
                "expected": feature_type,
                "actual": [str(f.get("type", f.get("family", ""))) for f in features],
            })

        else:
            results.append({
                "constraint": constraint,
                "passed": False,
                "expected": None,
                "actual": None,
                "error": f"Unknown constraint type: {ctype!r}",
            })

    overall_passed = all(r["passed"] for r in results)
    return {
        "passed": overall_passed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Tool 5: reset_design
# ---------------------------------------------------------------------------

def handle_reset_design(ctx: HandlerContext) -> dict:
    """MCP tool: reset_design — wipe the current design and clear conversation history."""
    response = run_bridge_call(ctx.bridge.reset())

    # Clear conversation history for all sessions
    ctx.conversation_history.clear()

    state = response.get("data", {}).get("state", {}) if response.get("ok") else {}
    return {
        "ok": response.get("ok", False),
        "state": state,
        "summary": "Design reset.",
    }


# ---------------------------------------------------------------------------
# Tool 6: take_screenshot
# ---------------------------------------------------------------------------

def handle_take_screenshot(ctx: HandlerContext) -> dict:
    """MCP tool: take_screenshot — capture the current Fusion 360 viewport."""
    response = run_bridge_call(ctx.bridge.take_screenshot())

    if not response.get("ok"):
        return {
            "ok": False,
            "error": response.get("error") or "Screenshot failed.",
        }

    image_b64 = response.get("data", {}).get("image_base64", "")
    return {
        "ok": True,
        "image_base64": image_b64,
        "mime_type": "image/png",
    }


# ---------------------------------------------------------------------------
# Tool 7: execute_script  (Approach B — Claude writes Fusion Python directly)
# ---------------------------------------------------------------------------

def handle_execute_script(
    ctx: HandlerContext,
    script: str,
    description: str = "",
) -> dict:
    """MCP tool: execute_script — run raw Fusion 360 Python written by Claude.

    Claude provides the code body only (no 'def run(context):' boilerplate,
    no import statements). The handler wraps it, ships it to Fusion, and
    returns a structured result with state diff and any error/traceback so
    Claude can self-correct in the next turn.

    Pre-injected variables Claude can use directly:
        app, ui, design, rootComp, sketches
    All distances must be in centimetres.
    """
    # Snapshot state before execution
    before_response = run_bridge_call(ctx.bridge.get_state())
    before_state = (
        before_response.get("data", {}).get("state", {})
        if before_response.get("ok") else {}
    )

    # Clean and wrap the raw code body
    cleaned = clean_generated_code(script)
    wrapped = wrap_script_with_run(cleaned, clear_model=False)

    # Execute in Fusion
    exec_response = run_bridge_call(ctx.bridge.execute_script(wrapped))

    if not exec_response.get("ok"):
        error_msg = exec_response.get("error") or "Script execution failed."
        traceback_str = exec_response.get("data", {}).get("traceback", "")
        return {
            "ok": False,
            "error": error_msg,
            "traceback": traceback_str or None,
            "state_summary": summarize_fusion_state(before_state),
            "description": description,
        }

    # Snapshot state after execution
    after_response = run_bridge_call(ctx.bridge.get_state())
    after_state = (
        after_response.get("data", {}).get("state", {})
        if after_response.get("ok") else before_state
    )

    return {
        "ok": True,
        "state_summary": summarize_fusion_state(after_state),
        "diff": _compute_diff(before_state, after_state),
        "description": description,
    }


# ---------------------------------------------------------------------------
# Tool 8: get_api_guidance  (Approach B — Claude queries Fusion API knowledge)
# ---------------------------------------------------------------------------

def handle_get_api_guidance(ctx: HandlerContext, topic: str) -> dict:
    """MCP tool: get_api_guidance — return Fusion 360 API guidance for a topic.

    Combines three sources:
      1. FUSION_CODING_CONVENTIONS — coordinate system, pre-injected vars, selection
         workflow, curved body rules, fillet patterns (from code_generator.py)
      2. Curated API cards — hand-verified patterns for the topic family
         (from fusion_api_knowledge.build_api_guidance)
      3. RAG doc snippets — top-k chunks from the Autodesk API docs vector index
         (from rag/retriever.retrieve)

    Claude should call this before writing a script for an unfamiliar operation
    to get accurate API patterns and avoid hallucinated method names.
    """
    parts = []

    # 1. Coding conventions (coordinate system, pre-injected vars, etc.)
    parts.append("## Fusion 360 Coding Conventions\n\n" + FUSION_CODING_CONVENTIONS.strip())

    # 2. Curated API cards for this topic
    cards_text = build_api_guidance(topic)
    if cards_text and cards_text.strip():
        parts.append("## Curated API Patterns\n\n" + cards_text.strip())

    # 3. RAG doc snippets — best-effort; skip if retriever unavailable
    try:
        rag_chunks = rag_retrieve(topic, ctx.client, k=4)
        if rag_chunks:
            parts.append("## Reference Documentation\n\n" + "\n\n".join(rag_chunks))
    except Exception:
        pass  # RAG is optional; don't fail the tool if the index isn't built

    return {"guidance": "\n\n---\n\n".join(parts)}
