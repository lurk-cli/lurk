const API_BASE = "http://127.0.0.1:4141";

// Fetch cold-start prompt — the primary context format for AI chats
async function fetchColdStart() {
  try {
    const res = await fetch(`${API_BASE}/context/cold-start`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    if (text && text.trim() && !text.includes("starting a new work session")) {
      return { ok: true, context: text.trim() };
    }
    return { ok: false, error: "no workstream context yet" };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Fetch basic context prompt (fallback when no workstreams)
async function fetchContext() {
  try {
    const res = await fetch(`${API_BASE}/context/prompt?max_tokens=250`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    return { ok: true, context: text.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Fetch workflow-aware prompt
async function fetchWorkflowPrompt() {
  try {
    const res = await fetch(`${API_BASE}/context/workflow-prompt`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    return { ok: true, context: text.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Fetch the best available context: cold-start > workflow > basic
async function fetchFullContext() {
  try {
    // Try cold-start first (workstream-aware, natural language)
    const coldStart = await fetchColdStart();
    if (coldStart.ok && coldStart.context) {
      return coldStart;
    }

    // Fall back to workflow + basic
    const [workflow, basic] = await Promise.all([
      fetchWorkflowPrompt(),
      fetchContext(),
    ]);

    if (workflow.ok && workflow.context && !workflow.context.startsWith("No active workflow")) {
      let combined = workflow.context;
      if (basic.ok && basic.context) {
        combined = `${basic.context}\n\n${workflow.context}`;
      }
      return { ok: true, context: combined };
    }

    if (basic.ok) return basic;
    return workflow;
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Fetch workstream summary for popup display
async function fetchWorkstreams() {
  try {
    const res = await fetch(`${API_BASE}/workstreams`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return { ok: true, ...data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Check if lurk API is reachable
async function checkStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return { ok: true, ...data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Handle messages from content script and popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "get_context") {
    fetchFullContext().then(sendResponse);
    return true;
  }
  if (msg.type === "get_basic_context") {
    fetchContext().then(sendResponse);
    return true;
  }
  if (msg.type === "get_cold_start") {
    fetchColdStart().then(async (result) => {
      if (result.ok) {
        sendResponse(result);
      } else {
        // Fall back to full context
        const full = await fetchFullContext();
        sendResponse(full);
      }
    });
    return true;
  }
  if (msg.type === "get_workstreams") {
    fetchWorkstreams().then(sendResponse);
    return true;
  }
  if (msg.type === "check_status") {
    checkStatus().then(sendResponse);
    return true;
  }
});

// Update badge based on lurk status
async function updateBadge() {
  const status = await checkStatus();
  if (status.ok) {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "lurk — context ready" });
  } else {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#888" });
    chrome.action.setTitle({ title: "lurk — not connected (is lurk serve-http running?)" });
  }
}

// Check status periodically
updateBadge();
setInterval(updateBadge, 30000);
