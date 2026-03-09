"""Cold-start prompt synthesis — generates copy-pasteable context for AI tools.

Takes workstream data (LLM-inferred coherent threads of work) and produces
prompts that let users paste context into claude.ai, gemini.com, etc. without
re-explaining what they're working on.

Two formats:
- Human: natural language for pasting into chat interfaces
- XML: structured output for MCP/tool consumption (Claude Code, Cursor)
- Fallback: simpler format when no workstreams exist yet
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context.model import ContextModel
    from ..context.workstreams import Workstream


def format_cold_start_human(
    workstream: Workstream,
    model: ContextModel,
    secondary_workstreams: list[Workstream] | None = None,
) -> str:
    """Generate a natural language cold-start prompt for pasting into AI chat.

    Reads like a person catching up a colleague. Adapts tone based on the
    workstream's persona (developer, pm, designer, marketer, general).
    """
    parts: list[str] = []
    persona = workstream.persona or "general"

    # Lead with the goal — the most important line
    parts.append(f"I'm working on {workstream.inferred_goal}.")

    # Current state
    if workstream.current_state:
        parts.append(f"\nCurrent state: {workstream.current_state}")

    # Key context — decisions, constraints, important details
    context_bullets = _build_context_bullets(workstream, model, persona)
    if context_bullets:
        parts.append("\nKey context:")
        for bullet in context_bullets:
            parts.append(f"- {bullet}")

    # People involved
    if workstream.key_people:
        people_str = ", ".join(workstream.key_people[:5])
        parts.append(f"\nPeople involved: {people_str}")

    # Communications
    comms = _format_communications(workstream)
    if comms:
        parts.append(f"\n{comms}")

    # Secondary workstreams
    if secondary_workstreams:
        active_secondary = [
            ws for ws in secondary_workstreams
            if ws.inferred_goal and ws.id != workstream.id
        ]
        if active_secondary:
            secondary_goals = "; ".join(
                ws.inferred_goal for ws in active_secondary[:2]
            )
            parts.append(f"\nAlso in the background: {secondary_goals}.")

    # Always end with the placeholder
    parts.append("\n[What I need help with: ]")

    return "\n".join(parts)


def format_cold_start_xml(
    workstream: Workstream,
    model: ContextModel,
    secondary_workstreams: list[Workstream] | None = None,
) -> str:
    """Generate XML-tagged output for MCP/tool consumption.

    Used by Claude Code, Cursor, and other tools that parse structured context.
    """
    lines: list[str] = []
    persona = workstream.persona or "general"

    lines.append("<user_context>")
    lines.append("  <primary_workstream>")
    lines.append(f"    <goal>{_xml_escape(workstream.inferred_goal)}</goal>")
    lines.append(f"    <persona>{_xml_escape(persona)}</persona>")

    if workstream.current_state:
        lines.append(f"    <state>{_xml_escape(workstream.current_state)}</state>")

    # Decisions
    if workstream.key_decisions:
        lines.append("    <decisions>")
        for decision in workstream.key_decisions[:5]:
            lines.append(f"      <decision>{_xml_escape(decision)}</decision>")
        lines.append("    </decisions>")

    # Artifacts
    artifacts = workstream.primary_artifacts
    if artifacts:
        lines.append("    <artifacts>")
        for artifact in artifacts[:8]:
            lines.append(f'      <file status="in-progress">{_xml_escape(artifact)}</file>')
        lines.append("    </artifacts>")

    # People
    if workstream.key_people:
        lines.append("    <people>")
        for person in workstream.key_people[:5]:
            lines.append(f'      <person role="collaborator">{_xml_escape(person)}</person>')
        lines.append("    </people>")

    # Code context (developer persona)
    if persona in ("developer", "general"):
        code_lines = _build_code_context_xml(workstream, model)
        if code_lines:
            lines.append("    <code_context>")
            lines.extend(f"      {line}" for line in code_lines)
            lines.append("    </code_context>")

    # Communications
    if workstream.related_communications:
        lines.append("    <communications>")
        for comm in workstream.related_communications[:3]:
            channel = comm.get("channel", "")
            with_person = comm.get("with", "")
            summary = comm.get("summary", "")
            attrs = ""
            if channel:
                attrs += f' channel="{_xml_escape(channel)}"'
            if with_person:
                attrs += f' with="{_xml_escape(with_person)}"'
            lines.append(f"      <conversation{attrs}>")
            if summary:
                lines.append(f"        {_xml_escape(summary)}")
            lines.append("      </conversation>")
        lines.append("    </communications>")

    lines.append("  </primary_workstream>")

    # Secondary workstreams
    if secondary_workstreams:
        active_secondary = [
            ws for ws in secondary_workstreams
            if ws.inferred_goal and ws.id != workstream.id
        ]
        if active_secondary:
            lines.append("  <secondary_workstreams>")
            for ws in active_secondary[:2]:
                lines.append("    <workstream>")
                lines.append(f"      <goal>{_xml_escape(ws.inferred_goal)}</goal>")
                if ws.current_state:
                    lines.append(f"      <state>{_xml_escape(ws.current_state)}</state>")
                lines.append("    </workstream>")
            lines.append("  </secondary_workstreams>")

    # Session info
    session_lines = _build_session_info_xml(workstream, model)
    if session_lines:
        lines.append("  <session_info>")
        lines.extend(f"    {line}" for line in session_lines)
        lines.append("  </session_info>")

    lines.append("</user_context>")

    return "\n".join(lines)


def format_cold_start_fallback(model: ContextModel) -> str:
    """Generate a cold-start prompt when no workstreams exist yet.

    Falls back to whatever data the model has — current snapshot, session info,
    recent activity. Still useful for giving an AI basic orientation.
    """
    parts: list[str] = []
    now = model.now
    session = model.session

    # What are they doing right now?
    current = _describe_current_activity(now)
    if current:
        duration_min = int(now.duration_seconds / 60) if now.duration_seconds > 60 else 0
        if duration_min > 0:
            parts.append(f"I've been {current} for the last {duration_min} minutes.")
        else:
            parts.append(f"I'm currently {current}.")

    # Recent activity trail
    narrative = session.narrative()
    if narrative:
        parts.append(f"\nRecent activity: {narrative}.")

    # Project info
    project_info = _describe_project_info(now)
    if project_info:
        parts.append(f"\n{project_info}")

    # Research
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-3:] if r.topic]
        if topics:
            parts.append(f"\nRecently researched: {', '.join(topics)}.")

    # Tools in use
    if now.tools_active and len(now.tools_active) > 1:
        tools_str = ", ".join(now.tools_active[:6])
        parts.append(f"\nTools in use: {tools_str}.")

    # Always end with the placeholder
    parts.append("\n[What I need help with: ]")

    if len(parts) <= 2:
        # Very minimal data — just the placeholder with a generic opener
        return "I'm starting a new work session.\n\n[What I need help with: ]"

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_context_bullets(
    workstream: Workstream,
    model: ContextModel,
    persona: str,
) -> list[str]:
    """Build key context bullet points adapted to persona."""
    bullets: list[str] = []

    # Decisions are relevant for all personas
    for decision in workstream.key_decisions[:3]:
        bullets.append(decision)

    if persona == "developer":
        # Files, branches, recent code changes
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:4])
            bullets.append(f"Key files: {files_str}")
        if workstream.git_branches:
            bullets.append(f"Branch: {', '.join(workstream.git_branches[:2])}")
        # Recent code changes from the workflow
        wf = _get_linked_workflow(workstream, model)
        if wf and wf.code_changes:
            for change in wf.code_changes[-2:]:
                bullets.append(change)

    elif persona == "pm":
        # Stakeholders, timeline, artifacts
        if workstream.primary_artifacts:
            docs_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Key documents: {docs_str}")
        if workstream.supporting_research:
            topics = [r.get("topic", "") for r in workstream.supporting_research[-2:] if r.get("topic")]
            if topics:
                bullets.append(f"Research: {', '.join(topics)}")

    elif persona == "designer":
        # Design files, feedback
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Design files: {files_str}")

    elif persona == "marketer":
        # Campaign details, channels
        if workstream.primary_artifacts:
            assets_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Assets: {assets_str}")

    else:
        # General — balanced mix
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Key files: {files_str}")
        if workstream.git_branches:
            bullets.append(f"Branch: {', '.join(workstream.git_branches[:2])}")

    # Projects (all personas)
    if workstream.projects:
        projects_str = ", ".join(workstream.projects[:3])
        bullets.append(f"Project: {projects_str}")

    # Tools used (all personas, only if interesting)
    if len(workstream.tools_used) > 2:
        tools_str = ", ".join(workstream.tools_used[:5])
        bullets.append(f"Using: {tools_str}")

    return bullets


def _format_communications(workstream: Workstream) -> str:
    """Format recent communications into natural language."""
    if not workstream.related_communications:
        return ""

    comms = workstream.related_communications[-2:]
    parts: list[str] = []
    for comm in comms:
        summary = comm.get("summary", "")
        with_person = comm.get("with", "")
        channel = comm.get("channel", "")
        if summary:
            prefix = ""
            if with_person:
                prefix = f"Discussed with {with_person}: "
            elif channel:
                prefix = f"In {channel}: "
            parts.append(f"{prefix}{summary}")

    if not parts:
        return ""
    return "Recent conversations: " + "; ".join(parts) + "."


def _build_code_context_xml(workstream: Workstream, model: ContextModel) -> list[str]:
    """Build XML lines for code context."""
    lines: list[str] = []

    if workstream.git_branches:
        lines.append(f"<branch>{_xml_escape(workstream.git_branches[0])}</branch>")

    # Get code changes from linked workflow
    wf = _get_linked_workflow(workstream, model)
    if wf and wf.code_changes:
        recent_changes = "; ".join(wf.code_changes[-3:])
        lines.append(f"<recent_changes>{_xml_escape(recent_changes)}</recent_changes>")

    # Current branch/project from snapshot
    now = model.now
    if not workstream.git_branches and now.branch:
        lines.append(f"<branch>{_xml_escape(now.branch)}</branch>")

    return lines


def _build_session_info_xml(workstream: Workstream, model: ContextModel) -> list[str]:
    """Build XML lines for session info."""
    lines: list[str] = []
    session = model.session

    duration_min = int(session.duration_seconds / 60)
    if duration_min > 0:
        lines.append(f"<active_duration>{duration_min} minutes</active_duration>")

    tools = workstream.tools_used or model.now.tools_active
    if tools:
        tools_str = ", ".join(tools[:6])
        lines.append(f"<tools_used>{_xml_escape(tools_str)}</tools_used>")

    return lines


def _get_linked_workflow(workstream: Workstream, model: ContextModel):
    """Get the workflow linked to this workstream, if any."""
    if not workstream.workflow_ids:
        # Fall back to the active workflow
        if hasattr(model, "workflows"):
            return model.workflows.get_active_workflow()
        return None
    # Try to find the most recent linked workflow
    if hasattr(model, "workflows"):
        for wid in reversed(workstream.workflow_ids):
            wf = model.workflows.get_workflow(wid)
            if wf:
                return wf
    return None


def _describe_current_activity(now) -> str:
    """Describe what the user is currently doing from the snapshot."""
    doc = getattr(now, "document_name", None)
    file = getattr(now, "file", None)
    project = getattr(now, "project", None)
    app = now.app or ""

    if file and project:
        lang = f" ({now.language})" if getattr(now, "language", None) else ""
        return f"working on {file} in the {project} project{lang}"

    if doc and app:
        return f"working on \"{doc}\" in {app}"

    if project and app:
        return f"working in {app} on the {project} project"

    if app:
        activity = getattr(now, "activity", "")
        if activity and activity not in ("unknown", "idle"):
            return f"{activity} in {app}"
        return f"working in {app}"

    return ""


def _describe_project_info(now) -> str:
    """Describe project/branch info from the snapshot."""
    project = getattr(now, "project", None)
    branch = getattr(now, "branch", None)

    if project and branch:
        return f"Project: {project} (branch: {branch})"
    if project:
        return f"Project: {project}"
    return ""


def _xml_escape(text: str) -> str:
    """Escape special characters for XML output."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
