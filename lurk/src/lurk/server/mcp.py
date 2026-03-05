"""MCP server — serves context to Claude Code, Cursor, and other MCP-compatible tools."""

from __future__ import annotations

import logging
from typing import Any

from ..context.model import ContextModel
from ..enrichment.pipeline import EnrichmentPipeline
from ..llm.config import load_llm_config
from ..llm.enhanced_prompt import generate_enhanced_prompt
from ..llm.provider import LLMProvider, create_provider
from ..server.prompt import generate_prompt
from ..store.database import ensure_schema, get_connection

logger = logging.getLogger("lurk.mcp")

# Global context model — initialized on server start
_model: ContextModel | None = None
_pipeline: EnrichmentPipeline | None = None
_llm_provider: LLMProvider | None = None


def _get_model() -> ContextModel:
    global _model, _pipeline, _llm_provider
    if _model is None:
        _model = ContextModel()
        _pipeline = EnrichmentPipeline()
        # Initialize LLM provider (optional)
        llm_config = load_llm_config()
        _llm_provider = create_provider(llm_config)
        if _llm_provider:
            logger.info("LLM provider: %s (%s)", _llm_provider.name, llm_config.model)
        # Load recent state from DB
        conn = get_connection()
        try:
            ensure_schema(conn)
            _model.load_from_db(conn)
        finally:
            conn.close()
    return _model


def _refresh() -> None:
    """Run one enrichment cycle and update the model."""
    global _pipeline, _model
    if _pipeline is None or _model is None:
        _get_model()
    assert _pipeline is not None and _model is not None

    conn = get_connection()
    try:
        from ..store.database import fetch_unenriched_events, mark_enriched, insert_enriched_event
        events = fetch_unenriched_events(conn, limit=50)
        if not events:
            return

        enriched_ids = []
        for event in events:
            _pipeline._enrich_event(conn, event)
            enriched_ids.append(event["id"])
            # Also update the in-memory model
            # Re-read the enriched event we just wrote
        mark_enriched(conn, enriched_ids)
        conn.commit()

        # Refresh model from recent enriched events
        from ..store.database import fetch_recent_enriched
        recent = fetch_recent_enriched(conn, hours=0.01, limit=50)  # Last ~30s
        for e in reversed(recent):
            _model.process_enriched_event(e)
    finally:
        conn.close()


def create_mcp_server():
    """Create and return the FastMCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "MCP server requires 'mcp' package. Install with: pip install 'lurk[mcp]'"
        )

    mcp = FastMCP("lurk")

    @mcp.tool()
    def get_current_context() -> dict[str, Any]:
        """What the user is doing right now — current app, file, project, activity, intent, and interruptibility."""
        _refresh()
        return _get_model().now.to_dict()

    @mcp.tool()
    def get_session_context() -> dict[str, Any]:
        """Full current work session — projects touched, files edited, research trail, focus blocks, and context switches."""
        _refresh()
        return _get_model().session.to_dict()

    @mcp.tool()
    def get_context_prompt(max_tokens: int = 250) -> str:
        """Natural language context preamble describing what the user is currently working on. Inject this into your system prompt for context-aware responses. Draws from the active workflow's accumulated context when available."""
        _refresh()
        model = _get_model()

        # If there's an active workflow with accumulated context, blend it in
        wf = model.workflows.get_active_workflow()
        prompt = generate_enhanced_prompt(model, _llm_provider, max_tokens=max_tokens)

        if wf and (wf.breadcrumbs or wf.agent_contributions or wf.research):
            wf_prompt = wf.generate_prompt(max_chars=400)
            if wf_prompt and len(prompt) + len(wf_prompt) < max_tokens * 4:
                prompt = prompt + " " + wf_prompt

        return prompt[:max_tokens * 4]

    @mcp.tool()
    def get_project_context(project_name: str = "") -> dict[str, Any]:
        """Context for a specific project — files, tickets, tools, languages, and time spent. If project_name is empty, uses the current project."""
        _refresh()
        model = _get_model()
        name = project_name or (model.now.project or "")
        if not name:
            return {"error": "No project detected. Provide a project_name."}
        return model.projects.get(name, {"error": f"Project '{name}' not found."})

    @mcp.tool()
    def get_agent_status() -> dict[str, Any]:
        """Status of all detected AI agents — active sessions, completed sessions, and workflow summary."""
        _refresh()
        return _get_model().agents.to_dict()

    @mcp.tool()
    def get_attention_queue() -> list[dict[str, Any]]:
        """Priority-sorted queue of agents needing human attention — errored, needs_review, blocked, or completed agents."""
        _refresh()
        return [item.to_dict() for item in _get_model().agents.get_attention_queue()]

    @mcp.tool()
    def get_agent_context_for_handoff(from_session_id: str, to_tool: str) -> dict[str, Any]:
        """Get a context briefing for handing off work from one AI agent to another. Includes files involved, duration, and a natural language summary."""
        _refresh()
        return _get_model().agents.get_handoff_context(from_session_id, to_tool)

    @mcp.tool()
    def get_workflow_summary() -> dict[str, Any]:
        """High-level summary of agent workflow — active count, pattern (idle/single_agent/parallel/multi_stream), and per-project breakdown."""
        _refresh()
        return _get_model().agents.get_workflow_summary()

    @mcp.tool()
    def get_workflows(include_completed: bool = False) -> list[dict[str, Any]]:
        """List all detected work workflows — topics the user is working on across tools. Each workflow tracks keywords, tools used, projects, files, and duration."""
        _refresh()
        return [wf.to_dict() for wf in _get_model().workflows.list_workflows(include_completed=include_completed)]

    @mcp.tool()
    def get_workflow_context(workflow_id: int) -> dict[str, Any]:
        """Get the full accumulated context for a workflow — what's being worked on, what each agent contributed, research done, code changes, documents involved, and the activity trail."""
        _refresh()
        model = _get_model()
        wf = model.workflows.get_workflow(workflow_id)
        if not wf:
            return {"error": f"Workflow {workflow_id} not found."}
        return wf.context_snapshot()

    @mcp.tool()
    def get_recent_code_changes(project: str = "", hours: float = 4) -> list[dict[str, Any]]:
        """Get the actual code that AI agents wrote — full diffs, new file contents, per-file changes. This is the real work product, not just file names or commit messages."""
        _refresh()
        from ..store.database import fetch_recent_code_snapshots
        conn = get_connection()
        try:
            return fetch_recent_code_snapshots(
                conn, project=project or None, hours=hours, limit=10,
            )
        finally:
            conn.close()

    @mcp.tool()
    def get_code_changes_summary(project: str = "") -> str:
        """Readable summary of the actual code that was written — functions added, logic changed, new files created. Use this to understand what was just built before switching to another tool or continuing work."""
        _refresh()
        model = _get_model()
        from ..observers.git_watcher import GitWatcher
        watcher = GitWatcher()
        watcher.auto_discover_from_model(model)
        watcher.check_all()
        text = watcher.build_change_context(project=project or None)
        if not text:
            from ..store.database import fetch_recent_code_snapshots
            conn = get_connection()
            try:
                snaps = fetch_recent_code_snapshots(conn, project=project or None, hours=4, limit=5)
            finally:
                conn.close()
            if not snaps:
                return "No recent code changes detected."
            parts = []
            for s in snaps:
                summary = s.get("summary", "")
                if summary:
                    parts.append(f"In {s.get('project', '?')} ({s.get('branch', '?')}):\n{summary}")
            return "\n\n---\n\n".join(parts) if parts else "No recent code changes detected."
        return text

    @mcp.tool()
    def get_agent_session_context() -> str:
        """Get the conversation context from the most recent AI agent session — what the user asked, what code was written, what files were modified, what errors were hit. This is the actual interaction that happened, not just metadata."""
        _refresh()
        from ..observers.session_watcher import SessionWatcher
        watcher = SessionWatcher()
        watcher.check_all()
        text = watcher.build_session_context()
        return text or "No active agent session detected."

    @mcp.tool()
    def get_active_workflow_prompt() -> str:
        """Get a synthesized context prompt for the active workflow. This is the key tool for understanding what the user is currently working on — it includes what they're doing, what agents have contributed, what they've researched, and what code was changed."""
        _refresh()
        model = _get_model()
        wf = model.workflows.get_active_workflow()
        if not wf:
            return "No active workflow detected."

        # Try LLM synthesis for a more natural prompt
        if _llm_provider:
            try:
                from ..llm.enhanced_prompt import SYSTEM_PROMPT
                snapshot = wf.context_snapshot()
                import json
                context_str = json.dumps(snapshot, indent=2, default=str)
                response = _llm_provider.generate(
                    f"Synthesize this workflow context into a 3-5 sentence briefing "
                    f"for another AI agent:\n\n{context_str}",
                    system=SYSTEM_PROMPT,
                    max_tokens=300,
                )
                if response and response.text:
                    return response.text
            except Exception:
                pass

        return wf.generate_prompt()

    return mcp


def run_mcp_server() -> None:
    """Run the MCP server (stdio transport)."""
    mcp = create_mcp_server()
    logger.info("MCP server starting (stdio transport)")
    mcp.run()
