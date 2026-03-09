const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const contextText = document.getElementById("contextText");
const copyBtn = document.getElementById("copyBtn");
const injectBtn = document.getElementById("injectBtn");
const hint = document.getElementById("hint");
const workstreamBox = document.getElementById("workstreamBox");
const workstreamGoal = document.getElementById("workstreamGoal");
const workstreamState = document.getElementById("workstreamState");
const workstreamMeta = document.getElementById("workstreamMeta");

let currentContext = null;

async function load() {
  // Check status
  const status = await chrome.runtime.sendMessage({ type: "check_status" });

  if (status && status.ok) {
    statusDot.classList.add("connected");
    statusText.textContent = "Connected";

    // Fetch workstream info and context in parallel
    const [wsResult, ctxResult] = await Promise.all([
      chrome.runtime.sendMessage({ type: "get_workstreams" }),
      chrome.runtime.sendMessage({ type: "get_context" }),
    ]);

    // Show workstream info if available
    if (wsResult && wsResult.ok && wsResult.primary) {
      const ws = wsResult.primary;
      workstreamBox.style.display = "block";
      workstreamGoal.textContent = ws.inferred_goal || "Working...";
      if (ws.current_state) {
        workstreamState.textContent = ws.current_state;
        workstreamState.style.display = "block";
      }
      // Show meta info
      const meta = [];
      if (ws.persona && ws.persona !== "general") {
        meta.push(ws.persona);
      }
      if (ws.key_people && ws.key_people.length > 0) {
        meta.push(`${ws.key_people.length} people`);
      }
      if (ws.primary_artifacts && ws.primary_artifacts.length > 0) {
        meta.push(`${ws.primary_artifacts.length} artifacts`);
      }
      if (meta.length > 0) {
        workstreamMeta.innerHTML = meta.map(m => `<span>${m}</span>`).join("");
      }
    }

    // Show context
    if (ctxResult && ctxResult.ok && ctxResult.context) {
      currentContext = ctxResult.context;
      // Show first ~300 chars as preview
      const preview = ctxResult.context.length > 300
        ? ctxResult.context.slice(0, 300) + "..."
        : ctxResult.context;
      contextText.textContent = preview;
      contextText.classList.remove("empty");
      copyBtn.disabled = false;
      injectBtn.disabled = false;
    } else {
      contextText.textContent = "No context available yet. Keep working — lurk is watching.";
    }
  } else {
    statusDot.classList.remove("connected");
    statusText.textContent = "Not connected";
    contextText.textContent = "lurk HTTP API not reachable.";
    hint.innerHTML = 'Run <code>lurk serve-http</code> to start.';
  }
}

copyBtn.addEventListener("click", async () => {
  if (!currentContext) return;
  try {
    // Copy as plain text — it's already a natural language prompt
    await navigator.clipboard.writeText(currentContext);
    copyBtn.textContent = "Copied!";
    setTimeout(() => { copyBtn.textContent = "Copy context to clipboard"; }, 1500);
  } catch (e) {
    copyBtn.textContent = "Failed to copy";
    setTimeout(() => { copyBtn.textContent = "Copy context to clipboard"; }, 1500);
  }
});

injectBtn.addEventListener("click", async () => {
  if (!currentContext) return;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  chrome.tabs.sendMessage(tab.id, {
    type: "inject_context",
    context: currentContext,
  });

  injectBtn.textContent = "Injected!";
  setTimeout(() => {
    injectBtn.textContent = "Inject into active chat";
    window.close();
  }, 800);
});

load();
