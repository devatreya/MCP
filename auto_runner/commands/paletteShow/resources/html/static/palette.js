const contextBlock = document.getElementById("contextBlock");
const selectionBlock = document.getElementById("selectionBlock");
const chat = document.getElementById("chat");
const promptInput = document.getElementById("prompt");
const sendBtn = document.getElementById("sendBtn");
const refreshBtn = document.getElementById("refreshBtn");
const resetBtn = document.getElementById("resetBtn");
const statusBadge = document.getElementById("statusBadge");

let busy = false;

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

async function refreshContext() {
    if (busy) {
        return;
    }

    setBusy(true, "Refreshing");
    try {
        const result = await sendToFusion("requestContext");
        if (result.ok) {
            updateContextUI(result);
            setBusy(false, "Ready");
        } else {
            addMessage("error", result.error || "Context refresh failed.");
            setBusy(false, "Error");
        }
    } catch (error) {
        addMessage("error", `Context refresh crashed: ${error}`);
        setBusy(false, "Error");
    }
}

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
    if (!busy) {
        refreshContext();
    }
}, 5000);
