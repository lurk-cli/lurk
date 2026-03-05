"""HTTP API server — serves context via REST at localhost:4141."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from ..context.model import ContextModel
from ..enrichment.pipeline import EnrichmentPipeline
from ..llm.config import load_llm_config
from ..llm.enhanced_prompt import generate_enhanced_prompt
from ..llm.provider import LLMProvider, create_provider
from ..server.prompt import generate_prompt
from ..store.database import ensure_schema, get_connection

logger = logging.getLogger("lurk.http")


def _build_page_context_summary(ext_ctx: dict) -> str:
    """Build a natural language summary from extension-captured page context."""
    parts: list[str] = []
    doc_type = ext_ctx.get("type", "")
    doc_name = ext_ctx.get("document_name", "")

    if doc_type == "document":
        if ext_ctx.get("current_section"):
            parts.append(f"Currently in the section \"{ext_ctx['current_section']}\".")
        if ext_ctx.get("selection"):
            sel = ext_ctx["selection"]
            if len(sel) > 100:
                sel = sel[:100] + "..."
            parts.append(f"Selected text: \"{sel}\"")
        if ext_ctx.get("outline"):
            outline = ext_ctx["outline"]
            if len(outline) > 8:
                parts.append(f"Document has {len(outline)} sections: {', '.join(outline[:6])}, and more.")
            else:
                parts.append(f"Document sections: {', '.join(outline)}.")

    elif doc_type == "spreadsheet":
        if ext_ctx.get("active_sheet"):
            parts.append(f"Working on sheet \"{ext_ctx['active_sheet']}\".")
        if ext_ctx.get("sheet_tabs") and len(ext_ctx["sheet_tabs"]) > 1:
            parts.append(f"Spreadsheet has {len(ext_ctx['sheet_tabs'])} tabs: {', '.join(ext_ctx['sheet_tabs'][:5])}.")
        if ext_ctx.get("selected_cell"):
            cell_info = f"Selected cell: {ext_ctx['selected_cell']}"
            if ext_ctx.get("cell_content"):
                cell_info += f" containing \"{ext_ctx['cell_content']}\""
            parts.append(cell_info + ".")

    elif doc_type == "presentation":
        slide_info = []
        if ext_ctx.get("current_slide"):
            slide_info.append(f"on slide {ext_ctx['current_slide']}")
        if ext_ctx.get("total_slides"):
            slide_info.append(f"of {ext_ctx['total_slides']} total")
        if slide_info:
            parts.append(f"Currently {' '.join(slide_info)}.")
        if ext_ctx.get("speaker_notes"):
            parts.append(f"Slide notes: \"{ext_ctx['speaker_notes'][:150]}\"")

    elif doc_type == "email":
        mode = ext_ctx.get("mode", "")
        if mode == "composing":
            if ext_ctx.get("subject"):
                parts.append(f"Composing an email: \"{ext_ctx['subject']}\".")
            else:
                parts.append("Composing a new email.")
        elif mode == "reading":
            if ext_ctx.get("subject"):
                parts.append(f"Reading email thread: \"{ext_ctx['subject']}\".")
            if ext_ctx.get("thread_length"):
                parts.append(f"Thread has {ext_ctx['thread_length']} messages.")
        elif mode == "triage":
            if ext_ctx.get("unread_count"):
                parts.append(f"Triaging inbox ({ext_ctx['unread_count']} unread).")

    elif doc_type == "calendar":
        if ext_ctx.get("focused_event"):
            parts.append(f"Looking at event: \"{ext_ctx['focused_event']}\".")

    # Active prompt context — what the user is currently typing in an AI chat
    if ext_ctx.get("active_prompt"):
        prompt_ts = ext_ctx.get("active_prompt_ts", 0)
        if time.time() - prompt_ts < 30:  # only if recent
            app = ext_ctx.get("active_prompt_app", "an AI chat")
            preview = ext_ctx["active_prompt"]
            if len(preview) > 150:
                preview = preview[:150] + "..."
            parts.append(f"Currently typing in {app}: \"{preview}\"")

    return " ".join(parts)


def _format_capture_source(cap: dict) -> str:
    """Format a capture's source for display in a prompt."""
    hostname = cap.get("hostname", "")
    title = cap.get("page_title", "")
    app = cap.get("app", "")
    capture_type = cap.get("capture_type", "")

    if capture_type == "typing":
        return app or hostname or "typing"

    # Use hostname for web captures
    if hostname:
        # Clean up common domains
        domain_labels = {
            "mail.google.com": "Gmail",
            "docs.google.com": "Google Doc",
            "sheets.google.com": "Google Sheet",
            "slides.google.com": "Google Slides",
            "calendar.google.com": "Google Calendar",
            "github.com": "GitHub",
            "stackoverflow.com": "Stack Overflow",
            "linear.app": "Linear",
        }
        label = domain_labels.get(hostname, hostname.split(".")[0].title())
        if title and len(title) < 60:
            return f"{label}: {title}"
        return label

    return app or title or "Unknown"


def _extract_capture_summary(cap: dict) -> str:
    """Extract the most useful content from a capture for prompt inclusion."""
    # Priority: typing > viewport text > page content summary
    typing = cap.get("typing_text")
    if typing and typing.strip():
        text = typing.strip()
        if len(text) > 200:
            text = text[:200] + "..."
        return f'Typed: "{text}"'

    # Use viewport text (what was on screen during engagement)
    viewport = cap.get("viewport_text")
    if viewport and viewport.strip():
        # Take first meaningful chunk
        lines = [l.strip() for l in viewport.split("\n") if l.strip()]
        content = " ".join(lines)
        if len(content) > 300:
            content = content[:300] + "..."
        return content

    # Fall back to page content
    page = cap.get("page_content")
    if page and page.strip():
        lines = [l.strip() for l in page.split("\n") if l.strip()]
        content = " ".join(lines[:5])
        if len(content) > 300:
            content = content[:300] + "..."
        return content

    # Fall back to page title
    title = cap.get("page_title")
    if title:
        return title

    return ""


class ContextServer:
    """HTTP server that serves the context model and runs enrichment in the background."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4141) -> None:
        self.host = host
        self.port = port
        self.model = ContextModel()
        self.pipeline = EnrichmentPipeline()
        self._stop_event = threading.Event()
        self._extension_context: dict[str, Any] = {}  # latest page context from extension
        self._extension_lock = threading.Lock()
        # Workflow clustering
        from ..context.workflows import WorkflowClusterer
        self.clusterer = WorkflowClusterer()
        # Git watcher — observes what coding agents actually change
        from ..observers.git_watcher import GitWatcher
        self.git_watcher = GitWatcher()
        # Session watcher — reads agent conversation logs
        from ..observers.session_watcher import SessionWatcher
        self.session_watcher = SessionWatcher()
        # Initialize LLM provider (optional)
        llm_config = load_llm_config()
        self.llm_provider: LLMProvider | None = create_provider(llm_config)
        if self.llm_provider:
            logger.info("LLM provider: %s (%s)", self.llm_provider.name, llm_config.model)

    def start(self) -> None:
        """Start the HTTP server and background enrichment."""
        # Initialize DB schema
        conn = get_connection()
        try:
            ensure_schema(conn)
            self.model.load_from_db(conn)
            self.clusterer.load_from_db(conn)
        finally:
            conn.close()

        # Auto-discover git repos from known projects
        self.git_watcher.auto_discover_from_model(self.model)

        # Start enrichment thread
        enrichment_thread = threading.Thread(
            target=self._enrichment_loop, daemon=True, name="enrichment"
        )
        enrichment_thread.start()

        # Start git watcher thread
        git_thread = threading.Thread(
            target=self._git_watch_loop, daemon=True, name="git-watcher"
        )
        git_thread.start()

        # Start session watcher thread
        session_thread = threading.Thread(
            target=self._session_watch_loop, daemon=True, name="session-watcher"
        )
        session_thread.start()

        # Start HTTP server
        self._run_http()

    def _process_extension_context(self, data: dict) -> None:
        """Store page-level context received from the browser extension."""
        source = data.get("source", "extension")

        if source == "extension_input":
            # AI chat typing activity — update activity scoring
            app_hint = data.get("app", data.get("hostname", ""))
            ts = data.get("timestamp", time.time())
            self.model.now.record_extension_input(app_hint, ts)
            # Store prompt preview for intent detection
            preview = data.get("prompt_preview", "")
            if preview:
                with self._extension_lock:
                    self._extension_context["active_prompt"] = preview
                    self._extension_context["active_prompt_app"] = app_hint
                    self._extension_context["active_prompt_ts"] = ts
            logger.debug("Extension input: %s (%d chars)", app_hint, data.get("prompt_length", 0))
            return

        with self._extension_lock:
            self._extension_context = data
            logger.debug("Extension context: %s %s", data.get("type"), data.get("document_name", ""))

    def get_extension_context(self) -> dict[str, Any]:
        """Get the latest extension-captured page context."""
        with self._extension_lock:
            # Expire after 60s of no updates
            ts = self._extension_context.get("timestamp", 0)
            if time.time() - ts > 60:
                return {}
            return dict(self._extension_context)

    def _process_capture(self, data: dict) -> dict:
        """Process a viewport/typing capture from the extension."""
        from ..context.workflows import extract_keywords
        from ..store.database import insert_capture, get_connection as get_conn

        # Extract keywords and compute engagement score
        keywords = extract_keywords(data)
        dwell = data.get("dwell_seconds", 0)
        scroll_depth = data.get("scroll_depth", 0)
        has_typing = bool(data.get("typing_text") or data.get("text_preview"))

        engagement = (
            min(1.0, dwell / 60) * 0.4 +  # dwell time (caps at 60s)
            min(1.0, scroll_depth / 80) * 0.3 +  # scroll depth (caps at 80%)
            (0.3 if has_typing else 0)  # typing bonus
        )

        data["topics"] = keywords
        data["engagement_score"] = round(engagement, 3)

        # Assign to workflow
        conn = get_conn()
        try:
            workflow_id = self.clusterer.assign_workflow(data, conn)
            data["workflow_id"] = workflow_id

            # Store capture
            capture_id = insert_capture(conn, data)

            wf = self.clusterer.get_workflow(workflow_id)
            return {
                "ok": True,
                "capture_id": capture_id,
                "workflow_id": workflow_id,
                "workflow_label": wf.label if wf else None,
                "topics": keywords[:5],
            }
        finally:
            conn.close()

    def _build_workflow_prompt(self, wf) -> str:
        """Build a context prompt from the workflow's accumulated context.

        Uses the workflow's own prompt generation (from accumulated breadcrumbs,
        agent contributions, research, code changes, documents). Falls back to
        LLM synthesis if available, for a more natural result.
        """
        # Try LLM synthesis first
        if self.llm_provider:
            try:
                from ..llm.enhanced_prompt import SYSTEM_PROMPT
                snapshot = wf.context_snapshot()
                context_str = json.dumps(snapshot, indent=2, default=str)
                user_prompt = (
                    "Synthesize this workflow context into a natural briefing "
                    "(3-5 sentences) that another AI agent can use to understand "
                    "what the user is working on, what's been done so far, and "
                    "what they're currently focused on:\n\n" + context_str
                )
                response = self.llm_provider.generate(
                    user_prompt, system=SYSTEM_PROMPT, max_tokens=300
                )
                if response and response.text:
                    return response.text
            except Exception:
                pass

        # Fall back to rules-based prompt from the workflow itself
        prompt = wf.generate_prompt()
        if prompt:
            return prompt

        return "No context accumulated for this workflow yet."

    def _enrichment_loop(self) -> None:
        """Background loop that enriches events and updates the model."""
        logger.info("Enrichment loop started")
        while not self._stop_event.is_set():
            try:
                count = self.pipeline.run_once()
                if count > 0:
                    # Reload recent enriched events into model
                    conn = get_connection()
                    try:
                        from ..store.database import fetch_recent_enriched
                        recent = fetch_recent_enriched(conn, hours=0.01, limit=50)
                        for event in reversed(recent):
                            self.model.process_enriched_event(event)
                            # Auto-discover git repos from file paths in events
                            self.git_watcher.register_from_enriched_event(event)
                    finally:
                        conn.close()
            except Exception:
                logger.exception("Error in enrichment loop")
            self._stop_event.wait(timeout=3.0)

    def _git_watch_loop(self) -> None:
        """Background loop that captures actual code written by agents."""
        from ..store.database import insert_code_snapshot
        logger.info("Git watcher started")
        while not self._stop_event.is_set():
            try:
                snapshots = self.git_watcher.check_all()
                if snapshots:
                    conn = get_connection()
                    try:
                        for snap in snapshots:
                            from ..context.workflows import _WORD_RE, _STOP_WORDS
                            keywords = [snap.project]
                            if snap.branch and snap.branch not in ("main", "master"):
                                keywords.append(snap.branch)
                            for fd in snap.file_diffs[:5]:
                                stem = fd.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                                if len(stem) > 2:
                                    keywords.append(stem)

                            workflow_id = None
                            if keywords:
                                workflow_id = self.clusterer._match_or_create(keywords, conn)
                                wf = self.clusterer.get_workflow(workflow_id)
                                if wf:
                                    wf.add_project(snap.project)
                                    for fd in snap.file_diffs:
                                        wf.add_file(fd.path)
                                    # Feed code changes as structured context
                                    for fd in snap.file_diffs[:5]:
                                        if fd.new_file:
                                            wf.add_code_change(f"created {fd.path}")
                                        elif fd.additions:
                                            wf.add_code_change(f"modified {fd.path}")

                            data = snap.to_dict()
                            data["workflow_id"] = workflow_id
                            insert_code_snapshot(conn, data)

                            logger.info(
                                "Code snapshot: %s %s (+%d/-%d) %d files [wf:%s]",
                                snap.project, snap.change_type,
                                snap.total_additions, snap.total_deletions,
                                len(snap.file_diffs), workflow_id,
                            )
                    finally:
                        conn.close()
            except Exception:
                logger.exception("Error in git watcher loop")
            self._stop_event.wait(timeout=10.0)

    def _session_watch_loop(self) -> None:
        """Background loop that reads agent conversation logs."""
        logger.info("Session watcher started")
        while not self._stop_event.is_set():
            try:
                updated = self.session_watcher.check_all()
                if updated:
                    conn = get_connection()
                    try:
                        for session in updated:
                            from ..context.workflows import _WORD_RE, _STOP_WORDS
                            keywords = [session.project]
                            if session.branch and session.branch not in ("main", "master"):
                                keywords.append(session.branch)
                            for msg in session.user_messages[-3:]:
                                words = _WORD_RE.findall(msg.lower())
                                keywords.extend(w for w in words if w not in _STOP_WORDS and len(w) > 2)

                            if keywords:
                                workflow_id = self.clusterer._match_or_create(keywords[:15], conn)
                                wf = self.clusterer.get_workflow(workflow_id)
                                if wf:
                                    wf.add_project(session.project)
                                    wf.add_tool("Claude Code")
                                    for fe in session.files_edited[-5:]:
                                        wf.add_file(fe.file_path)
                                    # Feed agent summary as structured contribution
                                    summary = session.summary_text()
                                    if summary:
                                        wf.add_agent_contribution("Claude Code", summary)

                            logger.info(
                                "Session update: %s (%s) — %d msgs, %d edits, %d tool calls",
                                session.project, session.session_id[:8],
                                len(session.user_messages), session.total_edits,
                                session.total_tool_calls,
                            )
                    finally:
                        conn.close()
            except Exception:
                logger.exception("Error in session watcher loop")
            self._stop_event.wait(timeout=15.0)

    def _run_http(self) -> None:
        """Run the HTTP server."""
        try:
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse, PlainTextResponse
            from starlette.routing import Route
            import uvicorn
        except ImportError:
            # Fallback to a simple HTTP server
            self._run_simple_http()
            return

        # CORS middleware for browser extension access
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        async def status(request):
            return JSONResponse({
                "status": "ok",
                "version": "0.1.0",
                "daemon": True,
            })

        async def context_now(request):
            return JSONResponse(self.model.now.to_dict())

        async def context_session(request):
            return JSONResponse(self.model.session.to_dict())

        async def context_prompt(request):
            max_tokens = int(request.query_params.get("max_tokens", 250))
            tool = request.query_params.get("for", "coding")
            ext_ctx = self.get_extension_context()
            text = generate_enhanced_prompt(self.model, self.llm_provider, max_tokens=max_tokens, tool=tool)
            # Append deep page context from extension if available
            if ext_ctx:
                page_summary = _build_page_context_summary(ext_ctx)
                if page_summary:
                    text = text + " " + page_summary
            return PlainTextResponse(text)

        async def context_project(request):
            name = request.path_params.get("name", "")
            if not name:
                name = self.model.now.project or ""
            return JSONResponse(self.model.projects.get(name, {}))

        async def context_full(request):
            return JSONResponse(self.model.to_dict())

        async def agents_status(request):
            return JSONResponse(self.model.agents.to_dict())

        async def agents_attention(request):
            return JSONResponse([item.to_dict() for item in self.model.agents.get_attention_queue()])

        async def agents_handoff(request):
            from_id = request.query_params.get("from", "")
            to_tool = request.query_params.get("to", "")
            if not from_id:
                return JSONResponse({"error": "Provide 'from' query param (session_id)"}, status_code=400)
            return JSONResponse(self.model.agents.get_handoff_context(from_id, to_tool))

        async def agents_workflow(request):
            return JSONResponse(self.model.agents.get_workflow_summary())

        async def context_enrich(request):
            """Receive page-level context from the browser extension."""
            try:
                body = await request.json()
                self._process_extension_context(body)
                return JSONResponse({"ok": True})
            except Exception as e:
                logger.warning("Extension enrich error: %s", e)
                return JSONResponse({"error": str(e)}, status_code=400)

        async def context_capture(request):
            """Receive viewport/typing captures from the extension."""
            try:
                body = await request.json()
                result = self._process_capture(body)
                return JSONResponse(result)
            except Exception as e:
                logger.warning("Capture error: %s", e)
                return JSONResponse({"error": str(e)}, status_code=400)

        async def workflow_prompt(request):
            """Get a pre-built prompt from the active workflow's knowledge trail."""
            workflow_id = request.query_params.get("id")
            if workflow_id:
                wf = self.clusterer.get_workflow(int(workflow_id))
            else:
                wf = self.clusterer.get_active_workflow()
            if not wf:
                return PlainTextResponse("No active workflow detected yet. Browse some pages and lurk will pick up the trail.")
            prompt = self._build_workflow_prompt(wf)
            return PlainTextResponse(prompt)

        async def agent_sessions(request):
            """Get recent AI agent conversation sessions."""
            sessions = self.session_watcher.get_recent_sessions(limit=5)
            return JSONResponse([s.to_dict() for s in sessions])

        async def agent_session_context(request):
            """Get the actual conversation context from the active agent session."""
            session_id = request.query_params.get("id")
            text = self.session_watcher.build_session_context(session_id=session_id)
            if not text:
                return PlainTextResponse("No active agent session detected.")
            return PlainTextResponse(text)

        async def code_changes(request):
            """Get recent code snapshots — actual diffs of what agents wrote."""
            project = request.query_params.get("project")
            hours = float(request.query_params.get("hours", 4))
            limit = int(request.query_params.get("limit", 10))
            from ..store.database import fetch_recent_code_snapshots
            conn = get_connection()
            try:
                snaps = fetch_recent_code_snapshots(conn, project=project, hours=hours, limit=limit)
            finally:
                conn.close()
            return JSONResponse(snaps)

        async def code_changes_summary(request):
            """Get the actual code that was written, as readable context."""
            project = request.query_params.get("project")
            text = self.git_watcher.build_change_context(project=project)
            if not text:
                return PlainTextResponse("No recent code changes detected.")
            return PlainTextResponse(text)

        async def workflows_list(request):
            """List all workflows for extension popup."""
            include_completed = request.query_params.get("all", "false") == "true"
            wfs = self.clusterer.list_workflows(include_completed=include_completed)
            # Also include model-tracked workflows
            model_wfs = self.model.workflows.list_workflows(include_completed=include_completed)
            # Merge — model workflows may have more enriched-event data
            seen_ids = {wf.id for wf in wfs}
            for mwf in model_wfs:
                if mwf.id not in seen_ids:
                    wfs.append(mwf)
            wfs.sort(key=lambda w: w.updated_ts, reverse=True)
            return JSONResponse([wf.to_dict() for wf in wfs])

        app = Starlette(
            routes=[
                Route("/status", status),
                Route("/context/now", context_now),
                Route("/context/session", context_session),
                Route("/context/prompt", context_prompt),
                Route("/context/project/{name:str}", context_project),
                Route("/context/enrich", context_enrich, methods=["POST"]),
                Route("/context/capture", context_capture, methods=["POST"]),
                Route("/context/workflow-prompt", workflow_prompt),
                Route("/workflows", workflows_list),
                Route("/sessions", agent_sessions),
                Route("/sessions/context", agent_session_context),
                Route("/changes", code_changes),
                Route("/changes/summary", code_changes_summary),
                Route("/context", context_full),
                Route("/agents", agents_status),
                Route("/agents/attention", agents_attention),
                Route("/agents/handoff", agents_handoff),
                Route("/agents/workflow", agents_workflow),
                Route("/", lambda r: PlainTextResponse("lurk context broker v0.1.0")),
            ],
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=["*"],
                    allow_methods=["GET", "POST", "OPTIONS"],
                    allow_headers=["*"],
                ),
            ],
        )

        logger.info("HTTP API starting at http://%s:%d", self.host, self.port)
        uvicorn.run(app, host=self.host, port=self.port, log_level="warning")

    def _run_simple_http(self) -> None:
        """Fallback HTTP server using stdlib."""
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import urllib.parse

        model = self.model
        llm_provider = self.llm_provider
        server_self = self  # reference for extension context

        class Handler(BaseHTTPRequestHandler):
            def _cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors_headers()
                self.end_headers()

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                params = urllib.parse.parse_qs(parsed.query)

                if path == "/status":
                    self._json_response({"status": "ok", "version": "0.1.0", "daemon": True})
                elif path == "/context/now":
                    self._json_response(model.now.to_dict())
                elif path == "/context/session":
                    self._json_response(model.session.to_dict())
                elif path == "/context/prompt":
                    max_tokens = int(params.get("max_tokens", [250])[0])
                    tool = params.get("for", ["coding"])[0]
                    text = generate_enhanced_prompt(model, llm_provider, max_tokens=max_tokens, tool=tool)
                    ext_ctx = server_self.get_extension_context()
                    if ext_ctx:
                        page_summary = _build_page_context_summary(ext_ctx)
                        if page_summary:
                            text = text + " " + page_summary
                    self._text_response(text)
                elif path.startswith("/context/project/"):
                    name = path.split("/context/project/", 1)[-1]
                    self._json_response(model.projects.get(name, {}))
                elif path == "/context":
                    self._json_response(model.to_dict())
                elif path == "/agents":
                    self._json_response(model.agents.to_dict())
                elif path == "/agents/attention":
                    self._json_response([item.to_dict() for item in model.agents.get_attention_queue()])
                elif path == "/agents/handoff":
                    from_id = params.get("from", [""])[0]
                    to_tool = params.get("to", [""])[0]
                    self._json_response(model.agents.get_handoff_context(from_id, to_tool))
                elif path == "/agents/workflow":
                    self._json_response(model.agents.get_workflow_summary())
                elif path == "/context/workflow-prompt":
                    wf_id = params.get("id", [None])[0]
                    if wf_id:
                        wf = server_self.clusterer.get_workflow(int(wf_id))
                    else:
                        wf = server_self.clusterer.get_active_workflow()
                    if not wf:
                        self._text_response("No active workflow detected yet.")
                    else:
                        self._text_response(server_self._build_workflow_prompt(wf))
                elif path == "/workflows":
                    include_all = params.get("all", ["false"])[0] == "true"
                    wfs = server_self.clusterer.list_workflows(include_completed=include_all)
                    model_wfs = model.workflows.list_workflows(include_completed=include_all)
                    seen_ids = {w.id for w in wfs}
                    for mwf in model_wfs:
                        if mwf.id not in seen_ids:
                            wfs.append(mwf)
                    wfs.sort(key=lambda w: w.updated_ts, reverse=True)
                    self._json_response([w.to_dict() for w in wfs])
                elif path == "/sessions":
                    sessions = server_self.session_watcher.get_recent_sessions(limit=5)
                    self._json_response([s.to_dict() for s in sessions])
                elif path == "/sessions/context":
                    sid = params.get("id", [None])[0]
                    text = server_self.session_watcher.build_session_context(session_id=sid)
                    self._text_response(text or "No active agent session detected.")
                elif path == "/changes":
                    from ..store.database import fetch_recent_code_snapshots
                    proj = params.get("project", [None])[0]
                    hrs = float(params.get("hours", [4])[0])
                    lim = int(params.get("limit", [10])[0])
                    c = get_connection()
                    try:
                        snaps = fetch_recent_code_snapshots(c, project=proj, hours=hrs, limit=lim)
                    finally:
                        c.close()
                    self._json_response(snaps)
                elif path == "/changes/summary":
                    proj = params.get("project", [None])[0]
                    text = server_self.git_watcher.build_change_context(project=proj)
                    self._text_response(text or "No recent code changes detected.")
                elif path == "/":
                    self._text_response("lurk context broker v0.1.0")
                else:
                    self.send_error(404)

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}

                if path == "/context/enrich":
                    server_self._process_extension_context(body)
                    self._json_response({"ok": True})
                elif path == "/context/capture":
                    result = server_self._process_capture(body)
                    self._json_response(result)
                else:
                    self.send_error(404)

            def _json_response(self, data):
                body = json.dumps(data, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _text_response(self, text):
                body = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass  # Suppress request logs

        server = HTTPServer((self.host, self.port), Handler)
        logger.info("HTTP API starting at http://%s:%d (stdlib fallback)", self.host, self.port)
        print(f"[lurk] Context API ready at http://{self.host}:{self.port}")
        server.serve_forever()
