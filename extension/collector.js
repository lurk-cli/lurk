/**
 * collector.js — Captures page-level context from Google Workspace sites
 * and sends it to the lurk HTTP API for enrichment.
 *
 * Runs on: Docs, Sheets, Slides, Gmail, Calendar, Drive
 * Captures: current section, selection, document structure, active sheet tab
 * Does NOT capture: full document content, email bodies, or personal data
 */
(() => {
  const API_BASE = "http://127.0.0.1:4141";
  const COLLECT_INTERVAL = 10_000; // 10s between captures
  const hostname = window.location.hostname;

  const collectors = {
    "docs.google.com": collectDocs,
    "sheets.google.com": collectSheets,
    "slides.google.com": collectSlides,
    "mail.google.com": collectGmail,
    "calendar.google.com": collectCalendar,
    "drive.google.com": collectDrive,
  };

  const collect = collectors[hostname];
  if (!collect) return;

  async function send(data) {
    try {
      await fetch(`${API_BASE}/context/enrich`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "extension",
          hostname,
          url: window.location.href,
          timestamp: Date.now() / 1000,
          ...data,
        }),
      });
    } catch (e) {
      // lurk API not running — silently skip
    }
  }

  function collectDocs() {
    const data = { type: "document" };

    // Document title
    const titleEl = document.querySelector(".docs-title-input");
    if (titleEl) data.document_name = titleEl.value || titleEl.textContent;

    // Current heading context — find the heading nearest to cursor
    const cursor = document.querySelector(".kix-cursor");
    if (cursor) {
      const cursorRect = cursor.getBoundingClientRect();
      // Walk up from cursor to find containing heading
      const headings = document.querySelectorAll(
        ".kix-paragraphrenderer [style*='font-size:2'], " +
        ".kix-paragraphrenderer [style*='font-weight:700']"
      );
      let nearestHeading = null;
      let nearestDist = Infinity;
      headings.forEach((h) => {
        const rect = h.getBoundingClientRect();
        const dist = cursorRect.top - rect.top;
        // Heading should be above cursor and closer than previous
        if (dist >= 0 && dist < nearestDist) {
          nearestDist = dist;
          nearestHeading = h.textContent.trim();
        }
      });
      if (nearestHeading) data.current_section = nearestHeading;
    }

    // Current selection text (what user has highlighted)
    const sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) {
      const selected = sel.toString().trim();
      // Cap at 500 chars to avoid sending huge selections
      data.selection = selected.length > 500 ? selected.slice(0, 500) + "..." : selected;
    }

    // Document outline / structure — grab headings for context
    const outlineItems = document.querySelectorAll(".navigation-item-content");
    if (outlineItems.length > 0) {
      data.outline = Array.from(outlineItems)
        .map((el) => el.textContent.trim())
        .filter(Boolean)
        .slice(0, 20);
    }

    // Word count if visible
    const wordCount = document.querySelector(".docs-character-count");
    if (wordCount) data.word_count = wordCount.textContent;

    return data;
  }

  function collectSheets() {
    const data = { type: "spreadsheet" };

    // Document title
    const titleEl = document.querySelector("#doc-title input, .docs-title-input");
    if (titleEl) data.document_name = titleEl.value || titleEl.textContent;

    // Active sheet tab
    const activeTab = document.querySelector(".docs-sheet-tab.docs-sheet-active-tab .docs-sheet-tab-name");
    if (activeTab) data.active_sheet = activeTab.textContent.trim();

    // All sheet tabs for context
    const tabs = document.querySelectorAll(".docs-sheet-tab-name");
    if (tabs.length > 0) {
      data.sheet_tabs = Array.from(tabs)
        .map((el) => el.textContent.trim())
        .filter(Boolean);
    }

    // Selected cell / range indicator
    const cellRef = document.querySelector("#t-name-box input, .waffle-name-box input");
    if (cellRef) data.selected_cell = cellRef.value;

    // Formula bar content (what's in the active cell)
    const formulaBar = document.querySelector("#t-formula-bar-input, .cell-input");
    if (formulaBar) {
      const formula = formulaBar.textContent.trim();
      if (formula) data.cell_content = formula.length > 200 ? formula.slice(0, 200) + "..." : formula;
    }

    return data;
  }

  function collectSlides() {
    const data = { type: "presentation" };

    // Document title
    const titleEl = document.querySelector("#doc-title input, .docs-title-input");
    if (titleEl) data.document_name = titleEl.value || titleEl.textContent;

    // Current slide number
    const slideNum = document.querySelector(".punch-viewer-slide-number, .docs-material-gm-pane .goog-toolbar-button[aria-label*='slide']");
    if (slideNum) data.current_slide = slideNum.textContent.trim();

    // Slide count
    const filmstrip = document.querySelectorAll(".punch-filmstrip-thumbnail");
    if (filmstrip.length > 0) data.total_slides = filmstrip.length;

    // Speaker notes content
    const notes = document.querySelector(".punch-viewer-speakernotes-text, .docs-noteseditor .kix-paragraphrenderer");
    if (notes) {
      const noteText = notes.textContent.trim();
      if (noteText) data.speaker_notes = noteText.length > 300 ? noteText.slice(0, 300) + "..." : noteText;
    }

    return data;
  }

  function collectGmail() {
    const data = { type: "email" };

    // Detect mode: inbox, reading, composing
    const composeWindows = document.querySelectorAll(".AD, .nH .aO");
    if (composeWindows.length > 0) {
      data.mode = "composing";

      // Subject of compose
      const subject = document.querySelector("input[name='subjectbox']");
      if (subject && subject.value) data.subject = subject.value;

      // To field
      const toField = document.querySelector("input[name='to'], textarea[name='to']");
      if (toField && toField.value) data.recipient_count = toField.value.split(",").length;
    } else {
      // Check if reading a thread
      const threadSubject = document.querySelector("h2.hP");
      if (threadSubject) {
        data.mode = "reading";
        data.subject = threadSubject.textContent.trim();

        // Count messages in thread
        const messages = document.querySelectorAll(".kv, .h7");
        if (messages.length > 0) data.thread_length = messages.length;
      } else {
        data.mode = "triage";

        // Count unread
        const unreadBadge = document.querySelector(".bsU");
        if (unreadBadge) data.unread_count = unreadBadge.textContent;
      }
    }

    return data;
  }

  function collectCalendar() {
    const data = { type: "calendar" };

    // Current view (day, week, month)
    const viewButtons = document.querySelectorAll("[data-view]");
    viewButtons.forEach((btn) => {
      if (btn.getAttribute("aria-pressed") === "true" || btn.classList.contains("active")) {
        data.view = btn.getAttribute("data-view") || btn.textContent.trim().toLowerCase();
      }
    });

    // Any open event details
    const eventTitle = document.querySelector("[data-eventchip] span, .FAxxKc");
    if (eventTitle) data.focused_event = eventTitle.textContent.trim();

    return data;
  }

  function collectDrive() {
    const data = { type: "file_management" };

    // Current folder
    const breadcrumb = document.querySelectorAll(".a-p-Db-JB-We");
    if (breadcrumb.length > 0) {
      data.current_folder = Array.from(breadcrumb)
        .map((el) => el.textContent.trim())
        .filter(Boolean)
        .join(" / ");
    }

    return data;
  }

  // Run collection loop
  function tick() {
    const data = collect();
    if (data) send(data);
  }

  // Initial capture after page loads
  setTimeout(tick, 2000);

  // Periodic capture
  setInterval(tick, COLLECT_INTERVAL);

  // Also capture on visibility change (user switches back to this tab)
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) setTimeout(tick, 500);
  });
})();
