/**
 * viewport.js — Universal viewport-aware content capture.
 *
 * Runs on ALL pages. Captures what's on screen when the user is engaged
 * (dwelling + scrolling). Groups content by headers and metadata.
 *
 * Engagement gating:
 * - Must dwell on page for >15s with scroll or click activity
 * - Captures viewport content at the moment of engagement
 * - Re-captures on significant scroll (new content in view)
 * - Tracks typing into any input as high-signal intent
 */
(() => {
  const API_BASE = "http://127.0.0.1:4141";
  const DWELL_THRESHOLD = 15_000; // 15s before considering engaged
  const SCROLL_DEBOUNCE = 2_000; // 2s after scroll stops to capture
  const CAPTURE_COOLDOWN = 10_000; // min 10s between captures
  const TYPING_REPORT_INTERVAL = 3_000;

  let pageLoadTime = Date.now();
  let lastScrollTime = 0;
  let lastCaptureTime = 0;
  let lastTypingReport = 0;
  let scrollCount = 0;
  let clickCount = 0;
  let totalScrollDistance = 0;
  let lastScrollY = window.scrollY;
  let engaged = false;
  let captureTimer = null;

  // --- Engagement detection ---

  function checkEngagement() {
    const dwellTime = Date.now() - pageLoadTime;
    const hasInteracted = scrollCount > 1 || clickCount > 0;
    if (dwellTime > DWELL_THRESHOLD && hasInteracted && !engaged) {
      engaged = true;
      captureViewport("dwell_engaged");
    }
  }

  window.addEventListener("scroll", () => {
    const now = Date.now();
    const delta = Math.abs(window.scrollY - lastScrollY);
    totalScrollDistance += delta;
    lastScrollY = window.scrollY;
    lastScrollTime = now;
    scrollCount++;

    // Capture after scroll settles (new content in view)
    clearTimeout(captureTimer);
    captureTimer = setTimeout(() => {
      if (engaged && now - lastCaptureTime > CAPTURE_COOLDOWN) {
        captureViewport("scroll_settled");
      }
    }, SCROLL_DEBOUNCE);

    checkEngagement();
  }, { passive: true });

  document.addEventListener("click", () => {
    clickCount++;
    checkEngagement();
  }, { passive: true });

  // Periodic engagement check for pages where user reads without scrolling
  setInterval(checkEngagement, 5000);

  // --- Typing detection (any input on any page) ---

  document.addEventListener("input", (e) => {
    const target = e.target;
    if (!target) return;

    const now = Date.now();
    if (now - lastTypingReport < TYPING_REPORT_INTERVAL) return;
    lastTypingReport = now;

    const text = target.value || target.textContent || "";
    if (!text.trim()) return;

    // Determine input type
    let inputType = "unknown";
    if (target.matches("textarea, input[type='text'], input[type='search'], input:not([type])")) {
      inputType = "form_input";
    } else if (target.matches("[contenteditable], .ProseMirror, .ql-editor")) {
      inputType = "rich_editor";
    } else if (target.closest("form[action*='search'], [role='search']")) {
      inputType = "search";
    }

    send({
      type: "typing",
      input_type: inputType,
      text_preview: text.slice(0, 300),
      text_length: text.length,
      field_label: getFieldLabel(target),
    });
  }, { passive: true, capture: true });

  function getFieldLabel(el) {
    // Try to find a label for this input
    if (el.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) return label.textContent.trim().slice(0, 50);
    }
    if (el.placeholder) return el.placeholder.slice(0, 50);
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").slice(0, 50);
    return null;
  }

  // --- Viewport content capture ---

  function captureViewport(trigger) {
    lastCaptureTime = Date.now();

    const data = {
      type: "viewport_capture",
      trigger,
      page_title: document.title,
      url: window.location.href,
      hostname: window.location.hostname,
      dwell_seconds: Math.round((Date.now() - pageLoadTime) / 1000),
      scroll_distance: Math.round(totalScrollDistance),
      scroll_depth: getScrollDepth(),
      // Page structure
      headers: extractHeaders(),
      meta: extractMeta(),
      // Viewport content
      viewport_text: extractViewportText(),
      // Full page key content (engagement-gated)
      page_content: extractPageContent(),
    };

    send(data);
  }

  function getScrollDepth() {
    const docHeight = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight
    );
    const viewportHeight = window.innerHeight;
    const scrolled = window.scrollY + viewportHeight;
    return Math.round((scrolled / docHeight) * 100);
  }

  function extractHeaders() {
    const headers = [];
    document.querySelectorAll("h1, h2, h3").forEach((h) => {
      const text = h.textContent.trim();
      if (text && text.length < 200) {
        headers.push({
          level: parseInt(h.tagName[1]),
          text,
          visible: isInViewport(h),
        });
      }
    });
    return headers.slice(0, 30);
  }

  function extractMeta() {
    const meta = {};
    // OpenGraph / meta description
    const desc = document.querySelector('meta[name="description"], meta[property="og:description"]');
    if (desc) meta.description = desc.content.slice(0, 300);
    const ogTitle = document.querySelector('meta[property="og:title"]');
    if (ogTitle) meta.og_title = ogTitle.content;
    const ogType = document.querySelector('meta[property="og:type"]');
    if (ogType) meta.og_type = ogType.content;
    // Author
    const author = document.querySelector('meta[name="author"]');
    if (author) meta.author = author.content;
    // Published date
    const pubDate = document.querySelector('meta[property="article:published_time"], meta[name="date"]');
    if (pubDate) meta.published = pubDate.content;
    return meta;
  }

  function extractViewportText() {
    // Get text content currently visible in the viewport
    const elements = document.querySelectorAll("p, li, td, th, pre, code, blockquote, dd, dt, figcaption");
    const visible = [];
    let charCount = 0;
    const MAX_CHARS = 2000;

    for (const el of elements) {
      if (charCount > MAX_CHARS) break;
      if (!isInViewport(el)) continue;

      const text = el.textContent.trim();
      if (text.length < 5) continue;
      // Skip nav/footer/sidebar content
      if (el.closest("nav, footer, aside, header, [role='navigation'], [role='banner']")) continue;

      visible.push(text);
      charCount += text.length;
    }

    return visible.join("\n").slice(0, MAX_CHARS);
  }

  function extractPageContent() {
    // Extract main content of the page (reader-mode style)
    // Prioritize: article, main, [role=main], then fall back to body
    const mainEl =
      document.querySelector("article") ||
      document.querySelector("main") ||
      document.querySelector("[role='main']") ||
      document.querySelector(".post-content, .entry-content, .article-body, .content") ||
      null;

    if (!mainEl) {
      // Fall back to largest text block
      return extractLargestTextBlock();
    }

    // Get text with structure preserved via headers
    const parts = [];
    let charCount = 0;
    const MAX_CHARS = 5000;

    const walker = document.createTreeWalker(
      mainEl,
      NodeFilter.SHOW_ELEMENT,
      {
        acceptNode: (node) => {
          const tag = node.tagName.toLowerCase();
          if (["script", "style", "nav", "footer", "aside", "noscript"].includes(tag)) {
            return NodeFilter.FILTER_REJECT;
          }
          if (["p", "h1", "h2", "h3", "h4", "li", "pre", "blockquote", "td", "th", "dd"].includes(tag)) {
            return NodeFilter.FILTER_ACCEPT;
          }
          return NodeFilter.FILTER_SKIP;
        },
      }
    );

    let node;
    while ((node = walker.nextNode()) && charCount < MAX_CHARS) {
      const text = node.textContent.trim();
      if (text.length < 3) continue;

      const tag = node.tagName.toLowerCase();
      if (tag.startsWith("h")) {
        parts.push(`\n## ${text}`);
      } else {
        parts.push(text);
      }
      charCount += text.length;
    }

    return parts.join("\n").slice(0, MAX_CHARS);
  }

  function extractLargestTextBlock() {
    // Find the element with the most text content (likely the main article)
    let best = null;
    let bestLen = 0;

    document.querySelectorAll("div, section, article").forEach((el) => {
      // Skip tiny elements and known non-content
      if (el.closest("nav, footer, aside, header")) return;
      const text = el.textContent.trim();
      // Prefer elements that have a good text-to-html ratio
      const ratio = text.length / (el.innerHTML.length || 1);
      const score = text.length * ratio;
      if (score > bestLen && text.length > 200) {
        bestLen = score;
        best = el;
      }
    });

    if (!best) return "";

    // Extract paragraphs from the best block
    const parts = [];
    let charCount = 0;
    best.querySelectorAll("p, li, pre, blockquote, h1, h2, h3, h4").forEach((el) => {
      if (charCount > 5000) return;
      const text = el.textContent.trim();
      if (text.length < 3) return;
      const tag = el.tagName.toLowerCase();
      if (tag.startsWith("h")) {
        parts.push(`\n## ${text}`);
      } else {
        parts.push(text);
      }
      charCount += text.length;
    });

    return parts.join("\n").slice(0, 5000);
  }

  function isInViewport(el) {
    const rect = el.getBoundingClientRect();
    return (
      rect.top < window.innerHeight &&
      rect.bottom > 0 &&
      rect.left < window.innerWidth &&
      rect.right > 0
    );
  }

  // --- Send to lurk API ---

  async function send(data) {
    try {
      await fetch(`${API_BASE}/context/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "viewport",
          timestamp: Date.now() / 1000,
          ...data,
        }),
      });
    } catch (e) {
      // lurk API not running — silently skip
    }
  }

  // --- Capture on page unload (user is leaving) ---

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && engaged) {
      // User switched away — final capture with full engagement metrics
      send({
        type: "page_exit",
        page_title: document.title,
        url: window.location.href,
        hostname: window.location.hostname,
        dwell_seconds: Math.round((Date.now() - pageLoadTime) / 1000),
        scroll_distance: Math.round(totalScrollDistance),
        scroll_depth: getScrollDepth(),
        scroll_count: scrollCount,
        click_count: clickCount,
        engaged: true,
      });
    }
  });
})();
