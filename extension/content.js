(() => {
  // Site configs: how to find and interact with each AI chat's input field
  const SITES = {
    "chatgpt.com": {
      inputSelector: "#prompt-textarea",
      type: "contenteditable",
    },
    "chat.openai.com": {
      inputSelector: "#prompt-textarea",
      type: "contenteditable",
    },
    "claude.ai": {
      inputSelector: "[contenteditable='true'].ProseMirror, div.ProseMirror",
      type: "prosemirror",
    },
    "gemini.google.com": {
      inputSelector: ".ql-editor, rich-textarea .textarea",
      type: "contenteditable",
    },
    "copilot.microsoft.com": {
      inputSelector: "#searchbox, textarea",
      type: "textarea",
    },
    "perplexity.ai": {
      inputSelector: "textarea",
      type: "textarea",
    },
    "www.perplexity.ai": {
      inputSelector: "textarea",
      type: "textarea",
    },
  };

  const hostname = window.location.hostname;
  const siteConfig = SITES[hostname];
  if (!siteConfig) return;

  const API_BASE = "http://127.0.0.1:4141";
  let button = null;
  let loading = false;

  // --- Input activity tracking ---
  // Reports to lurk when user is actively typing in an AI chat
  let lastInputReport = 0;
  const INPUT_REPORT_INTERVAL = 3000; // report every 3s while typing
  let currentPromptText = "";

  function reportInputActivity() {
    const now = Date.now();
    if (now - lastInputReport < INPUT_REPORT_INTERVAL) return;
    lastInputReport = now;

    const input = document.querySelector(siteConfig.inputSelector);
    const text = input ? (input.value || input.textContent || "").trim() : "";
    currentPromptText = text;

    // Send typing activity to lurk
    fetch(`${API_BASE}/context/enrich`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "extension_input",
        hostname,
        timestamp: now / 1000,
        type: "ai_chat_input",
        app: document.title.split(" - ")[0].split(" — ")[0].trim(),
        is_typing: true,
        prompt_length: text.length,
        // Send first 200 chars of prompt for intent detection (not the full thing)
        prompt_preview: text.length > 0 ? text.slice(0, 200) : "",
      }),
    }).catch(() => {}); // ignore errors silently
  }

  // Listen for typing in the chat input
  document.addEventListener("keydown", (e) => {
    const input = document.querySelector(siteConfig.inputSelector);
    if (!input) return;
    // Check if the keydown target is inside the chat input
    if (input.contains(e.target) || e.target === input) {
      reportInputActivity();
    }
  }, true);

  function createButton() {
    const btn = document.createElement("div");
    btn.id = "lurk-inject-btn";
    btn.title = "Add context from lurk";
    btn.innerHTML = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
    btn.addEventListener("click", handleClick);
    document.body.appendChild(btn);
    return btn;
  }

  function positionButton() {
    const input = document.querySelector(siteConfig.inputSelector);
    if (!input || !button) return;

    const rect = input.getBoundingClientRect();
    // Position at top-right of the input area
    button.style.top = `${window.scrollY + rect.top - 36}px`;
    button.style.left = `${window.scrollX + rect.right - 36}px`;
    button.style.display = "flex";
  }

  async function handleClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (loading) return;

    loading = true;
    button.classList.add("lurk-loading");

    try {
      const response = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: "get_context" }, resolve);
      });

      if (response && response.ok && response.context) {
        injectContext(response.context);
        showToast("Context added");
      } else {
        showToast("Can't reach lurk — is lurk serve-http running?", true);
      }
    } catch (err) {
      showToast("Failed to fetch context", true);
    } finally {
      loading = false;
      button.classList.remove("lurk-loading");
    }
  }

  function injectContext(contextText) {
    const input = document.querySelector(siteConfig.inputSelector);
    if (!input) return;

    // Cold-start prompt is already natural language — no wrapper tags needed
    const prefix = `${contextText}\n\n`;

    if (siteConfig.type === "textarea") {
      const existing = input.value || "";
      input.value = prefix + existing;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    } else if (siteConfig.type === "prosemirror") {
      // ProseMirror (Claude.ai) — insert via clipboard paste simulation
      input.focus();
      const existing = input.textContent || "";
      // Create a paragraph node via execCommand for ProseMirror compatibility
      document.execCommand("insertText", false, prefix + existing);
      if (input.textContent === existing) {
        // Fallback: direct text manipulation
        input.textContent = prefix + existing;
        input.dispatchEvent(new InputEvent("input", { bubbles: true }));
      }
      // Move cursor to end
      const sel = window.getSelection();
      sel.selectAllChildren(input);
      sel.collapseToEnd();
    } else {
      // contenteditable (ChatGPT, Gemini)
      input.focus();
      const existing = input.textContent || "";
      // Use execCommand for contenteditable to trigger framework reactivity
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, prefix + existing);
      if (input.textContent === existing) {
        input.textContent = prefix + existing;
        input.dispatchEvent(new InputEvent("input", { bubbles: true }));
      }
      const sel = window.getSelection();
      sel.selectAllChildren(input);
      sel.collapseToEnd();
    }
  }

  function showToast(message, isError = false) {
    const existing = document.getElementById("lurk-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.id = "lurk-toast";
    toast.textContent = message;
    if (isError) toast.classList.add("lurk-toast-error");
    document.body.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add("lurk-toast-visible"));
    setTimeout(() => {
      toast.classList.remove("lurk-toast-visible");
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  // Initialize: create button and watch for input field appearing
  function init() {
    button = createButton();

    // Position when input appears (SPAs load async)
    const observer = new MutationObserver(() => {
      const input = document.querySelector(siteConfig.inputSelector);
      if (input) positionButton();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // Reposition on scroll/resize
    window.addEventListener("scroll", positionButton, { passive: true });
    window.addEventListener("resize", positionButton, { passive: true });

    // Initial position attempt
    setTimeout(positionButton, 500);
    setTimeout(positionButton, 2000);
  }

  // Listen for inject_context from popup
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === "inject_context" && msg.context) {
      injectContext(msg.context);
      showToast("Context added");
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
