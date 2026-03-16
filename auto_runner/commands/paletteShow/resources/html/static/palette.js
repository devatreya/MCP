const contextBlock = document.getElementById("contextBlock");
const selectionBlock = document.getElementById("selectionBlock");
const chat = document.getElementById("chat");
const promptInput = document.getElementById("prompt");
const sendBtn = document.getElementById("sendBtn");
const refreshBtn = document.getElementById("refreshBtn");
const resetBtn = document.getElementById("resetBtn");
const statusBadge = document.getElementById("statusBadge");

let busy = false;
let refreshInFlight = false;
let lastContextRefreshAt = 0;
let _stepContinueResolve = null;   // resolves when user clicks Continue during selection pause

const PASSIVE_REFRESH_MS = 15000;

function setBusy(nextBusy, label) {
    busy = nextBusy;
    sendBtn.disabled = busy;
    refreshBtn.disabled = busy;
    resetBtn.disabled = busy;
    statusBadge.textContent = label || (busy ? "Working" : "Idle");
}

function addMessage(role, content) {
    const item = document.createElement("div");
    item.className = `msg ${role}`;
    item.textContent = content;
    chat.appendChild(item);
    chat.scrollTop = chat.scrollHeight;
}

function formatRetryContext(retryContext) {
    if (!retryContext || typeof retryContext !== "object") {
        return "";
    }

    const parts = [];
    if (retryContext.stage) {
        parts.push(`Stage: ${retryContext.stage}`);
    }
    if (retryContext.failed_step) {
        parts.push(`Failed step: ${retryContext.failed_step}`);
    }
    if (Array.isArray(retryContext.issues) && retryContext.issues.length > 0) {
        parts.push(`Issues: ${retryContext.issues.join(" | ")}`);
    }
    if (retryContext.recommended_action) {
        parts.push(`Recovery: ${retryContext.recommended_action}`);
    }
    return parts.join("\n");
}

function updateContextUI(payload) {
    contextBlock.textContent = payload.state_summary || "No model context available.";
    selectionBlock.textContent = payload.selection_summary || "No selection context available.";
    lastContextRefreshAt = Date.now();
}

async function sendToFusion(action, payload = {}) {
    if (!window.adsk || typeof adsk.fusionSendData !== "function") {
        return {
            ok: false,
            error: "Fusion bridge is not ready in this panel.",
        };
    }

    let raw;
    try {
        raw = await adsk.fusionSendData(action, JSON.stringify(payload));
    } catch (error) {
        return {
            ok: false,
            error: `Fusion bridge call failed for ${action}: ${error}`,
        };
    }

    try {
        return JSON.parse(raw || "{}");
    } catch (error) {
        return {
            ok: false,
            error: `Invalid Fusion response for ${action}: ${raw}`,
        };
    }
}

async function refreshContext(options = {}) {
    const background = Boolean(options.background);
    const quiet = Boolean(options.quiet);

    if (busy || refreshInFlight) {
        return;
    }

    refreshInFlight = true;
    if (!background) {
        setBusy(true, "Refreshing");
    }

    try {
        const result = await sendToFusion("requestContext");
        if (result.ok) {
            updateContextUI(result);
            if (!background) {
                setBusy(false, "Ready");
            }
        } else {
            if (!quiet) {
                addMessage("error", result.error || "Context refresh failed.");
            }
            if (!background) {
                setBusy(false, "Error");
            }
        }
    } catch (error) {
        if (!quiet) {
            addMessage("error", `Context refresh crashed: ${error}`);
        }
        if (!background) {
            setBusy(false, "Error");
        }
    } finally {
        refreshInFlight = false;
    }
}

// ── Step pipeline helpers ───────────────────────────────────────────────────

function addSelectionPrompt(stepId, promptText) {
    // Create a selection-prompt card with a Continue button
    const card = document.createElement("div");
    card.className = "msg step-selection";
    card.id = `sel-card-${stepId}`;

    const msg = document.createElement("span");
    msg.textContent = `⚡ ${promptText}`;
    card.appendChild(msg);

    const btn = document.createElement("button");
    btn.textContent = "Continue";
    btn.className = "continue-btn";
    btn.addEventListener("click", () => {
        btn.disabled = true;
        btn.textContent = "✓ Selected";
        card.classList.add("done");
        if (_stepContinueResolve) {
            const resolve = _stepContinueResolve;
            _stepContinueResolve = null;
            resolve();
        }
    });
    card.appendChild(btn);
    chat.appendChild(card);
    chat.scrollTop = chat.scrollHeight;
}

function waitForStepContinue() {
    return new Promise((resolve) => {
        _stepContinueResolve = resolve;
    });
}

const MAX_SELECTION_RETRIES = 3;  // max times user can re-try a selection before hard fail

async function runStepPipeline(steps) {
    addMessage("assistant", `Running ${steps.length} steps…`);

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        const label = `Step ${i + 1}/${steps.length}: ${step.description}`;

        // If this step needs the user to select geometry, pause and wait
        if (step.requires_selection) {
            setBusy(true, `Step ${i + 1} — Select`);
            const prompt = step.selection_prompt || "Select the required geometry in Fusion, then click Continue.";
            addSelectionPrompt(step.step_id, prompt);
            await waitForStepContinue();
        }

        setBusy(true, `Step ${i + 1}/${steps.length}`);
        addMessage("assistant", `⏳ ${label}`);

        // Execute with retry loop — if the step fails due to wrong/missing selection,
        // re-prompt the user instead of hard-failing
        let result;
        let selectionAttempts = 0;

        while (true) {
            try {
                result = await sendToFusion("executeStep", {
                    script: step.script,
                    step_id: step.step_id,
                });
            } catch (err) {
                addMessage("error", `Step crashed: ${err}`);
                setBusy(false, "Error");
                return;
            }

            if (result.ok) {
                break;  // success — move to next step
            }

            const errMsg = result.error || "Step failed.";

            // Only retry selection for steps that were DESIGNED to read user
            // selections (requires_selection=true).  For steps where the script
            // does its own geometry search (requires_selection=false), re-running
            // the same script won't help — the user's selection is never read.
            const isSelectionRetriable = step.requires_selection && (
                result.needs_selection ||
                errMsg.includes("not a plane") ||
                errMsg.includes("not a face") ||
                errMsg.includes("No selection") ||
                errMsg.includes("Could not find") ||
                errMsg.includes("not found") ||
                errMsg.includes("is not valid") ||
                errMsg.includes("Cast failed") ||
                errMsg.includes("NoneType")
            );

            if (isSelectionRetriable && selectionAttempts < MAX_SELECTION_RETRIES) {
                selectionAttempts++;
                const selPrompt = result.selection_prompt ||
                    step.selection_prompt ||
                    "The previous selection didn't work. Please select the correct geometry in Fusion 360, then click Continue.";
                const retryMsg = selectionAttempts === 1
                    ? `⚠️ ${label} — selection needed`
                    : `⚠️ ${label} — wrong selection, please try again (attempt ${selectionAttempts}/${MAX_SELECTION_RETRIES})`;
                addMessage("error", retryMsg);
                setBusy(true, `Step ${i + 1} — Re-select`);
                addSelectionPrompt(step.step_id + `_retry${selectionAttempts}`, selPrompt);
                await waitForStepContinue();
                setBusy(true, `Step ${i + 1}/${steps.length}`);
                addMessage("assistant", `⏳ Retrying ${label}`);
                continue;  // retry the same step
            }

            // Non-critical failures (no meaningful shape change) — skip and continue
            const isSkippable = (
                errMsg.includes("no meaningful shape change") ||
                errMsg.includes("ASM_RBI_NO_LUMP_LEFT") ||
                errMsg.includes("does not cause a meaningful shape change")
            );
            addMessage("error", `✗ ${label}`);
            if (isSkippable) {
                addMessage("error", `⚠️ Skipped: ${errMsg.split("\n")[0]}`);
                break;  // skip this step, continue pipeline
            } else {
                addMessage("error", errMsg);
                const retryText = formatRetryContext(result.retry_context);
                if (retryText) addMessage("error", retryText);
                if (result.traceback) addMessage("error", result.traceback);
                setBusy(false, "Error");
                return;
            }
        }

        if (result.ok) {
            addMessage("assistant", `✓ ${label}`);
            updateContextUI(result);
        }
    }

    addMessage("assistant", `All ${steps.length} steps completed.`);
    setBusy(false, "Ready");
}

// ── Main prompt submission ──────────────────────────────────────────────────

async function submitPrompt() {
    if (busy) {
        return;
    }

    const prompt = promptInput.value.trim();
    if (!prompt) {
        return;
    }

    addMessage("user", prompt);
    promptInput.value = "";
    setBusy(true, "Generating");

    try {
        const result = await sendToFusion("submitPrompt", { prompt });
        if (!result.ok) {
            const errorMessage = result.error || "Prompt execution failed.";
            addMessage("error", errorMessage);
            if (Array.isArray(result.details) && result.details.length > 0) {
                addMessage("error", `Details: ${result.details.join(" | ")}`);
            }
            const retryText = formatRetryContext(result.retry_context);
            if (retryText) {
                addMessage("error", retryText);
            }
            if (result.traceback) {
                addMessage("error", result.traceback);
            }
            setBusy(false, "Error");
            return;
        }

        // Step pipeline: hand off to sequential executor
        if (result.mode === "step_pipeline") {
            addMessage("assistant", result.assistant_message || "Step plan ready.");
            await runStepPipeline(result.steps || []);
            return;
        }

        // Single-script result
        addMessage("assistant", result.assistant_message || "Edit applied.");
        updateContextUI(result);

        if (result.script_file) {
            addMessage("assistant", `Saved script: ${result.script_file}`);
        }

        setBusy(false, "Ready");
    } catch (error) {
        addMessage("error", `Prompt request crashed: ${error}`);
        setBusy(false, "Error");
    }
}

async function resetSession() {
    if (busy) {
        return;
    }

    setBusy(true, "Resetting");
    try {
        const result = await sendToFusion("resetSession");
        if (!result.ok) {
            addMessage("error", result.error || "Reset failed.");
            setBusy(false, "Error");
            return;
        }

        chat.innerHTML = "";
        addMessage("assistant", result.assistant_message || "Reset complete.");
        updateContextUI(result);
        setBusy(false, "Ready");
    } catch (error) {
        addMessage("error", `Reset request crashed: ${error}`);
        setBusy(false, "Error");
    }
}

sendBtn.addEventListener("click", submitPrompt);
refreshBtn.addEventListener("click", refreshContext);
resetBtn.addEventListener("click", resetSession);

promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitPrompt();
    }
});

window.fusionJavaScriptHandler = {
    handle(action, data) {
        try {
            if (action === "refreshContext") {
                const payload = JSON.parse(data || "{}");
                updateContextUI(payload);
                return "OK";
            }
            return `Unhandled action: ${action}`;
        } catch (error) {
            return `Handler error: ${error}`;
        }
    },
};

refreshContext();
setInterval(() => {
    if (busy || refreshInFlight) {
        return;
    }
    if (document.hidden || !document.hasFocus()) {
        return;
    }
    if ((Date.now() - lastContextRefreshAt) < PASSIVE_REFRESH_MS) {
        return;
    }
    refreshContext({ background: true, quiet: true });
}, 5000);

window.addEventListener("focus", () => {
    refreshContext({ background: true, quiet: true });
});

document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
        refreshContext({ background: true, quiet: true });
    }
});
