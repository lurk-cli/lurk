"""lurk CLI — command-line interface for the context broker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="lurk",
    help="lurk — Context broker for AI tools",
    no_args_is_help=True,
)
console = Console()

LURK_DIR = Path.home() / ".lurk"
PID_FILE = LURK_DIR / "daemon.pid"
ENGINE_PID_FILE = LURK_DIR / "engine.pid"


@app.command()
def start():
    """Start the lurk daemon and intelligence engine."""
    LURK_DIR.mkdir(parents=True, exist_ok=True)

    # Check if daemon is already running
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            console.print(f"[yellow]lurk daemon already running (PID {pid})[/yellow]")
            return
        except ProcessLookupError:
            PID_FILE.unlink()

    # Find the daemon binary
    daemon_path = _find_daemon()
    if not daemon_path:
        console.print("[red]lurk-daemon binary not found. Build it with:[/red]")
        console.print("  cd daemon && swift build")
        raise typer.Exit(1)

    console.print("[bold]Starting lurk...[/bold]")

    # Start Swift daemon
    proc = subprocess.Popen(
        [daemon_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    PID_FILE.write_text(str(proc.pid))
    console.print(f"  [green]✓[/green] Daemon started (PID {proc.pid})")

    console.print()
    console.print("[bold green]lurk is running.[/bold green]")
    console.print()
    console.print("Quick start:")
    console.print("  Connect tools: [cyan]lurk connect[/cyan]")
    console.print("  View context:  [cyan]lurk context[/cyan]")
    console.print("  HTTP API:      [cyan]curl localhost:4141/context/now[/cyan]")


@app.command()
def stop():
    """Stop the lurk daemon and engine."""
    stopped = False

    for pidfile, name in [(PID_FILE, "daemon"), (ENGINE_PID_FILE, "engine")]:
        if pidfile.exists():
            pid = int(pidfile.read_text().strip())
            try:
                os.kill(pid, signal.SIGTERM)
                console.print(f"  [green]✓[/green] Stopped {name} (PID {pid})")
                stopped = True
            except ProcessLookupError:
                pass
            pidfile.unlink(missing_ok=True)

    if stopped:
        console.print("[bold]lurk stopped.[/bold]")
    else:
        console.print("[yellow]lurk is not running.[/yellow]")


@app.command()
def status():
    """Show current lurk status."""
    running = False
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            console.print(f"[green]◉ Daemon running[/green] (PID {pid})")
            running = True
        except ProcessLookupError:
            console.print("[yellow]○ Daemon not running[/yellow] (stale PID file)")
            PID_FILE.unlink()
    else:
        console.print("[yellow]○ Daemon not running[/yellow]")

    # Check DB
    db_path = LURK_DIR / "store.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        console.print(f"  Database: {db_path} ({size_mb:.1f} MB)")

        # Count events
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            unenriched = conn.execute(
                "SELECT COUNT(*) FROM events WHERE enriched = 0"
            ).fetchone()[0]
            console.print(f"  Events: {total} total, {unenriched} pending enrichment")
        except Exception:
            pass
        finally:
            conn.close()


@app.command()
def context(
    prompt: bool = typer.Option(False, "--prompt", "-p", help="Show natural language preamble"),
    raw: bool = typer.Option(False, "--raw", help="Show raw JSON"),
):
    """Show current context snapshot."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.context.model import ContextModel
    from lurk.store.database import ensure_schema, get_connection
    from lurk.server.prompt import generate_prompt
    from lurk.enrichment.pipeline import EnrichmentPipeline

    conn = get_connection()
    try:
        ensure_schema(conn)

        # Run enrichment first
        pipeline = EnrichmentPipeline()
        pipeline.run_once()

        # Build model
        model = ContextModel()
        model.load_from_db(conn)
    finally:
        conn.close()

    if prompt:
        text = generate_prompt(model)
        console.print(text)
        return

    data = model.to_dict()

    if raw:
        console.print(json.dumps(data, indent=2))
        return

    # Pretty print
    now = data["now"]
    console.print()
    console.print(f"[bold]{now.get('activity', 'unknown')}[/bold] in [cyan]{now.get('app', '?')}[/cyan]")

    if now.get("file"):
        console.print(f"  File: {now['file']}")
    if now.get("project"):
        console.print(f"  Project: {now['project']}")
    if now.get("language"):
        console.print(f"  Language: {now['language']}")
    if now.get("ticket"):
        console.print(f"  Ticket: {now['ticket']}")
    if now.get("intent"):
        console.print(f"  Intent: {now['intent']}")

    duration = now.get("duration_seconds", 0)
    if duration > 60:
        console.print(f"  Duration: {int(duration / 60)}m")

    console.print(f"  Input: {now.get('input_state', 'unknown')}")
    console.print(f"  Interruptibility: {now.get('interruptibility', '?')}")

    # Session summary
    session = data.get("session", {})
    if session:
        console.print()
        console.print("[bold]Session[/bold]")
        if session.get("projects_touched"):
            console.print(f"  Projects: {', '.join(session['projects_touched'])}")
        if session.get("files_edited"):
            console.print(f"  Files: {len(session['files_edited'])} edited")
        console.print(f"  Context switches: {session.get('context_switches', 0)}")
        if session.get("focus_blocks"):
            console.print(f"  Focus blocks: {len(session['focus_blocks'])}")
    console.print()


@app.command()
def log(
    hours: float = typer.Option(1, "--since", "-s", help="Hours of history to show"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max events to show"),
):
    """Show recent raw events."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.store.database import fetch_recent_raw_events, get_connection

    conn = get_connection()
    try:
        events = fetch_recent_raw_events(conn, hours=hours, limit=limit)
    finally:
        conn.close()

    if not events:
        console.print("[yellow]No events found.[/yellow]")
        return

    table = Table(title=f"Recent Events (last {hours}h)")
    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("App", style="cyan")
    table.add_column("Title / Data")

    from datetime import datetime

    for event in reversed(events):  # Show oldest first
        ts = datetime.fromtimestamp(event["ts"]).strftime("%H:%M:%S")
        etype = event.get("event_type", "?")
        app = event.get("app") or ""
        title = event.get("title") or ""
        if not title and event.get("data"):
            data = event["data"]
            if isinstance(data, dict):
                title = json.dumps(data)[:60]
        table.add_row(ts, etype, app, title[:80])

    console.print(table)


@app.command()
def agents():
    """Show active AI agent sessions and attention queue."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.context.model import ContextModel
    from lurk.store.database import ensure_schema, get_connection
    from lurk.enrichment.pipeline import EnrichmentPipeline

    conn = get_connection()
    try:
        ensure_schema(conn)
        EnrichmentPipeline().run_once()
        model = ContextModel()
        model.load_from_db(conn)
    finally:
        conn.close()

    agent_data = model.agents.to_dict()
    active = agent_data.get("active_sessions", {})

    if not active:
        console.print("[yellow]No active AI agents detected.[/yellow]")
    else:
        table = Table(title="Active AI Agents")
        table.add_column("Tool", style="bold")
        table.add_column("State")
        table.add_column("Project", style="cyan")
        table.add_column("Duration", justify="right")
        table.add_column("Task")

        for _key, session in active.items():
            duration = session.get("duration_seconds", 0)
            dur_str = f"{int(duration / 60)}m" if duration >= 60 else f"{duration}s"
            state = session.get("state", "?")
            state_style = {
                "working": "[green]working[/green]",
                "blocked": "[yellow]blocked[/yellow]",
                "needs_review": "[yellow]needs_review[/yellow]",
                "errored": "[red]errored[/red]",
                "completed": "[blue]completed[/blue]",
                "idle": "[dim]idle[/dim]",
            }.get(state, state)
            table.add_row(
                session.get("tool", "?"),
                state_style,
                session.get("project") or "",
                dur_str,
                session.get("task") or "",
            )
        console.print(table)

    # Show attention queue
    attention = [item.to_dict() for item in model.agents.get_attention_queue()]
    if attention:
        console.print()
        attn_table = Table(title="Attention Queue")
        attn_table.add_column("Priority", justify="center")
        attn_table.add_column("Agent")
        attn_table.add_column("Reason")
        attn_table.add_column("Waiting", justify="right")

        for item in attention:
            wait = item.get("time_waiting", 0)
            wait_str = f"{int(wait / 60)}m" if wait >= 60 else f"{int(wait)}s"
            attn_table.add_row(
                str(item.get("priority", "?")),
                item.get("tool", "?"),
                item.get("reason", ""),
                wait_str,
            )
        console.print(attn_table)

    # Show workflow summary
    summary = agent_data.get("summary", {})
    if summary.get("active_agents", 0) > 0:
        console.print()
        console.print(f"[bold]Workflow:[/bold] {summary.get('pattern', 'idle')} "
                      f"({summary.get('active_agents', 0)} active)")
    console.print()


@app.command(name="serve-mcp")
def serve_mcp():
    """Start the MCP server (stdio transport for Claude Code / Cursor)."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.server.mcp import run_mcp_server
    run_mcp_server()


@app.command(name="serve-http")
def serve_http(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(4141, help="Port to listen on"),
):
    """Start the HTTP API server at localhost:4141."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.server.http import ContextServer
    server = ContextServer(host=host, port=port)
    server.start()


@app.command()
def debug(
    title: str = typer.Argument(help="Window title string to parse"),
    app_name: str = typer.Option("Unknown", "--app", "-a", help="App name"),
    bundle_id: str = typer.Option("", "--bundle", "-b", help="Bundle ID"),
):
    """Run a window title through parsers and show extracted context."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.parsers.registry import ParserRegistry
    from lurk.enrichment.classifier import classify_activity, classify_interruptibility
    from lurk.enrichment.intent import IntentClassifier, EventRecord

    registry = ParserRegistry()
    ctx = registry.parse(title, app_name, bundle_id or None)

    console.print()
    console.print(f"[bold]Input[/bold]")
    console.print(f"  Title: [cyan]{title}[/cyan]")
    console.print(f"  App:   {app_name}")
    if bundle_id:
        console.print(f"  Bundle: {bundle_id}")

    console.print()
    console.print(f"[bold]Parser: {ctx.parser_name}[/bold]")

    fields = ctx.to_dict()
    for key, value in fields.items():
        if key in ("app", "parser_name"):
            continue
        console.print(f"  {key}: [green]{value}[/green]")

    # Show activity classification
    activity = classify_activity(app_name, title)
    console.print()
    console.print(f"[bold]Classification[/bold]")
    console.print(f"  Activity: {ctx.activity}")
    console.print(f"  Fallback activity: {activity}")
    console.print(f"  Interruptibility: {classify_interruptibility(ctx.activity, 600)}")
    console.print()


@app.command()
def search(
    query: str = typer.Argument(help="Search term"),
    hours: float = typer.Option(24, "--since", "-s", help="Hours of history to search"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """Search events by keyword."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.store.database import get_connection

    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT ts, event_type, app, title FROM events
            WHERE ts > ? AND (title LIKE ? OR app LIKE ?)
            ORDER BY ts DESC LIMIT ?""",
            (time.time() - hours * 3600, f"%{query}%", f"%{query}%", limit),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        console.print(f"[yellow]No events matching '{query}'[/yellow]")
        return

    table = Table(title=f"Search: '{query}' ({len(rows)} results)")
    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("App", style="cyan")
    table.add_column("Title")

    from datetime import datetime

    for row in rows:
        ts = datetime.fromtimestamp(row[0]).strftime("%m/%d %H:%M:%S")
        table.add_row(ts, row[1] or "", row[2] or "", (row[3] or "")[:80])

    console.print(table)


@app.command()
def projects():
    """List known projects with activity summary."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.context.model import ContextModel
    from lurk.store.database import ensure_schema, get_connection
    from lurk.enrichment.pipeline import EnrichmentPipeline

    conn = get_connection()
    try:
        ensure_schema(conn)
        EnrichmentPipeline().run_once()
        model = ContextModel()
        model.load_from_db(conn)
    finally:
        conn.close()

    project_data = model.projects.to_dict()
    if not project_data:
        console.print("[yellow]No projects detected yet.[/yellow]")
        return

    table = Table(title="Known Projects")
    table.add_column("Project", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("Languages")
    table.add_column("Tools")
    table.add_column("Tickets")
    table.add_column("Time", justify="right")

    from datetime import datetime

    for name, info in project_data.items():
        total_min = int(info.get("total_seconds", 0) / 60)
        time_str = f"{total_min}m" if total_min < 60 else f"{total_min // 60}h {total_min % 60}m"
        table.add_row(
            name,
            str(len(info.get("files", []))),
            ", ".join(info.get("languages", [])[:3]),
            ", ".join(info.get("tools", [])[:3]),
            ", ".join(info.get("tickets", [])[:3]),
            time_str,
        )

    console.print(table)


@app.command()
def delete(
    hours: float = typer.Option(0, "--since", "-s", help="Delete events from last N hours"),
    all_data: bool = typer.Option(False, "--all", help="Delete ALL data"),
):
    """Delete captured events."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    if not hours and not all_data:
        console.print("[red]Specify --since <hours> or --all[/red]")
        raise typer.Exit(1)

    if all_data:
        confirm = typer.confirm("Delete ALL lurk data? This cannot be undone")
        if not confirm:
            raise typer.Abort()

    from lurk.store.database import get_connection

    conn = get_connection()
    try:
        if all_data:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM enriched_events")
            conn.commit()
            console.print("[green]All data deleted.[/green]")
        else:
            since = time.time() - hours * 3600
            c1 = conn.execute("DELETE FROM events WHERE ts > ?", (since,))
            c2 = conn.execute("DELETE FROM enriched_events WHERE ts > ?", (since,))
            conn.commit()
            console.print(
                f"[green]Deleted {c1.rowcount} raw + {c2.rowcount} enriched events "
                f"from the last {hours}h.[/green]"
            )
    finally:
        conn.close()


@app.command()
def pause():
    """Pause observation (daemon keeps running but stops capturing)."""
    pause_file = LURK_DIR / "paused"
    pause_file.touch()
    console.print("[yellow]○ Observation paused[/yellow]")
    console.print("  Run [cyan]lurk resume[/cyan] to restart observation.")


@app.command()
def resume():
    """Resume observation after pause."""
    pause_file = LURK_DIR / "paused"
    if pause_file.exists():
        pause_file.unlink()
        console.print("[green]◉ Observation resumed[/green]")
    else:
        console.print("[green]◉ Already observing[/green]")


@app.command()
def config():
    """Open lurk config in your default editor."""
    config_path = LURK_DIR / "config.yaml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_DEFAULT_CONFIG)
        console.print(f"[green]Created default config at {config_path}[/green]")

    editor = os.environ.get("EDITOR", "nano")
    os.execvp(editor, [editor, str(config_path)])


_DEFAULT_CONFIG = """\
# lurk Configuration
# See https://github.com/anthropics/lurk for documentation

# Observation settings
observation:
  poll_interval: 3          # seconds between title polls
  idle_threshold: 120       # seconds before marking idle
  session_gap: 300          # seconds of idle to end a session

# Exclusion rules — events matching these are never captured
exclusions:
  apps: []                  # e.g., ["Messages", "FaceTime"]
  title_patterns: []        # e.g., ["*bank*", "*medical*"]

# Context file writing
context_files:
  enabled: true
  targets:
    - lurk_context            # .lurk-context.md (always recommended)
    # - claude_md            # CLAUDE.md
    # - cursorrules          # .cursorrules
  update_interval: 30       # seconds between file updates

# Context prompt settings
context_prompt:
  default_tokens: 250
  include_research_trail: true
  include_session_history: true

# LLM integration (optional)
llm:
  provider: none            # none | ollama | anthropic | openai
  model: ""                 # e.g., llama3.2:3b for ollama
  api_key: ""               # only for cloud providers

# HTTP API
http:
  host: "127.0.0.1"
  port: 4141

# Data retention
retention:
  raw_events_days: 30       # delete raw events older than this
  sessions_days: 365        # keep session summaries longer
"""


@app.command()
def install(
    daemon_path: Optional[str] = typer.Option(None, "--daemon", help="Path to lurk-daemon binary"),
):
    """Install lurk for auto-start on login."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.config.install import full_install, find_daemon_binary

    # Find daemon binary
    if daemon_path is None:
        daemon_path = find_daemon_binary()
        if daemon_path is None:
            daemon_path = _offer_build_daemon()
            if daemon_path is None:
                raise typer.Exit(1)

    console.print("[bold]Installing lurk...[/bold]")
    console.print()

    results = full_install(daemon_path)

    for step, ok in results.items():
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} {step.replace('_', ' ').title()}")

    if not results.get("accessibility"):
        console.print()
        console.print("[yellow]Accessibility permission needed:[/yellow]")
        console.print("  System Settings → Privacy & Security → Accessibility")
        console.print("  Add and enable lurk-daemon")

    if results.get("launchd"):
        console.print()
        console.print("[bold green]lurk installed![/bold green]")
        console.print("  It will start automatically on login.")
        console.print("  Run [cyan]lurk start[/cyan] to start it now.")


@app.command()
def uninstall(
    remove_data: bool = typer.Option(False, "--remove-data", help="Also delete all captured data"),
):
    """Uninstall lurk and remove auto-start."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.config.install import full_uninstall, is_installed

    if not is_installed():
        console.print("[yellow]lurk is not installed as a launch agent.[/yellow]")
        if not remove_data:
            return

    if remove_data:
        confirm = typer.confirm("This will delete all lurk data. Continue?")
        if not confirm:
            raise typer.Abort()

    results = full_uninstall(remove_data=remove_data)

    console.print("[bold]Uninstalling lurk...[/bold]")
    for step, ok in results.items():
        icon = "[green]✓[/green]" if ok else "[dim]—[/dim]"
        console.print(f"  {icon} {step.replace('_', ' ').title()}")

    console.print()
    console.print("[bold]lurk uninstalled.[/bold]")


@app.command()
def purge(
    days: int = typer.Option(0, "--older-than", help="Delete data older than N days"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted"),
):
    """Purge old data according to retention policy."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.config.retention import run_retention, get_db_stats
    from lurk.config.settings import load_config, RetentionConfig
    from lurk.store.database import get_connection

    config = load_config()
    retention = config.retention

    if days > 0:
        retention = RetentionConfig(
            raw_events_days=days,
            enriched_events_days=days,
            sessions_days=days,
        )

    conn = get_connection()
    try:
        # Show current stats
        stats = get_db_stats(conn)
        console.print(f"[bold]Database stats:[/bold]")
        console.print(f"  Raw events: {stats['events_count']:,}")
        console.print(f"  Enriched events: {stats['enriched_count']:,}")
        console.print(f"  Size: {stats['db_size_mb']:.1f} MB")
        console.print()

        if dry_run:
            import time
            cutoff = time.time() - retention.raw_events_days * 86400
            try:
                count = conn.execute("SELECT COUNT(*) FROM events WHERE ts < ?", (cutoff,)).fetchone()[0]
                console.print(f"  Would delete ~{count} raw events older than {retention.raw_events_days} days")
            except Exception:
                pass
            return

        results = run_retention(conn, retention)

        total = sum(results.values())
        if total > 0:
            console.print(f"[green]Purged {total:,} rows:[/green]")
            for table, count in results.items():
                if count > 0:
                    console.print(f"  {table}: {count:,}")
        else:
            console.print("[dim]Nothing to purge.[/dim]")
    finally:
        conn.close()


def _build_daemon() -> str | None:
    """Build the Swift daemon and copy to ~/.local/bin. Returns path or None."""
    # Look for daemon source — check relative to Python package, then ~/.lurk/src
    candidates = [
        Path(__file__).parent.parent.parent.parent.parent / "daemon",
        Path.home() / ".lurk" / "src" / "daemon",
    ]
    daemon_src = None
    for c in candidates:
        if (c / "Package.swift").exists():
            daemon_src = c
            break

    if daemon_src is None:
        console.print("[red]Cannot find daemon source (daemon/Package.swift).[/red]")
        return None

    console.print(f"  Building from {daemon_src}...")
    result = subprocess.run(
        ["swift", "build", "-c", "release"],
        cwd=str(daemon_src),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Build failed:[/red]\n{result.stderr}")
        return None

    built = daemon_src / ".build" / "release" / "lurk-daemon"
    if not built.exists():
        console.print("[red]Build succeeded but binary not found.[/red]")
        return None

    dest = Path.home() / ".local" / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    dest_path = dest / "lurk-daemon"
    import shutil
    shutil.copy2(str(built), str(dest_path))
    dest_path.chmod(0o755)
    console.print(f"  [green]✓[/green] Installed to {dest_path}")
    return str(dest_path)


def _offer_build_daemon() -> str | None:
    """When daemon not found, offer to build it. Returns path or None."""
    import shutil as _shutil
    console.print("[yellow]lurk-daemon not found.[/yellow]")

    if not _shutil.which("swift"):
        console.print("  Swift is required to build the daemon.")
        console.print("  Install Xcode Command Line Tools:")
        console.print("    [cyan]xcode-select --install[/cyan]")
        console.print()
        console.print("  Then run: [cyan]lurk install[/cyan]")
        return None

    if not typer.confirm("  Build the daemon now?", default=True):
        console.print("  Build manually:")
        console.print("    [cyan]cd daemon && swift build -c release[/cyan]")
        return None

    return _build_daemon()


@app.command()
def connect(
    tool: Optional[str] = typer.Argument(None, help="Tool to connect (claude-code, cursor, codex)"),
):
    """Connect lurk to an AI tool's MCP integration."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.config.connect import (
        SUPPORTED_TOOLS,
        connect_tool,
        detect_installed_tools,
        is_connected,
    )

    if tool is None:
        # Interactive — show detected tools and let user pick
        detected = detect_installed_tools()
        if not detected:
            console.print("[yellow]No supported AI tools detected.[/yellow]")
            console.print(f"  Supported: {', '.join(SUPPORTED_TOOLS.values())}")
            return

        console.print("[bold]Detected AI tools:[/bold]")
        for t in detected:
            name = SUPPORTED_TOOLS[t]
            connected = is_connected(t)
            status = "[green]connected[/green]" if connected else "[dim]not connected[/dim]"
            console.print(f"  {name}: {status}")

        console.print()
        unconnected = [t for t in detected if not is_connected(t)]
        if not unconnected:
            console.print("[green]All detected tools are already connected.[/green]")
            return

        for t in unconnected:
            name = SUPPORTED_TOOLS[t]
            if typer.confirm(f"  Connect {name}?", default=True):
                ok, msg = connect_tool(t)
                icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                console.print(f"  {icon} {msg}")
        return

    # Direct tool connection
    tool = tool.lower()
    if tool not in SUPPORTED_TOOLS:
        console.print(f"[red]Unknown tool: {tool}[/red]")
        console.print(f"  Supported: {', '.join(SUPPORTED_TOOLS)}")
        raise typer.Exit(1)

    ok, msg = connect_tool(tool)
    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"  {icon} {msg}")


@app.command()
def onboard(
    install_daemon: bool = typer.Option(False, "--install-daemon", help="Build and install the native daemon"),
):
    """Interactive guided setup — build daemon, configure permissions, connect AI tools, start lurk."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from lurk.config.install import find_daemon_binary, full_install, check_accessibility
    from lurk.config.connect import detect_installed_tools, connect_tool, is_connected, SUPPORTED_TOOLS

    console.print("[bold]lurk onboard[/bold]")
    console.print()

    # Step 1: Find or build daemon
    daemon_path = find_daemon_binary()
    if daemon_path:
        console.print(f"  [green]✓[/green] Daemon found at {daemon_path}")
    else:
        if not install_daemon:
            import shutil as _shutil
            if not _shutil.which("swift"):
                console.print("[red]Swift not found. Install Xcode Command Line Tools:[/red]")
                console.print("  [cyan]xcode-select --install[/cyan]")
                raise typer.Exit(1)

            console.print("  Daemon binary not found.")
            if not typer.confirm("  Build it now?", default=True):
                raise typer.Exit(0)

        daemon_path = _build_daemon()
        if daemon_path is None:
            raise typer.Exit(1)

    # Step 2: Install launchd plist
    console.print()
    console.print("  Configuring auto-start...")
    results = full_install(daemon_path)
    for step_name, ok in results.items():
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {icon} {step_name.replace('_', ' ').title()}")

    # Step 3: Check accessibility
    console.print()
    if not check_accessibility():
        console.print("[yellow]Accessibility permission needed.[/yellow]")
        console.print("  Opening System Settings...")
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            capture_output=True,
        )
        console.print("  Add [cyan]lurk-daemon[/cyan] and toggle it on.")
        console.print("  Then verify with: [cyan]lurk status[/cyan]")

    # Step 4: Detect and connect AI tools
    console.print()
    console.print("[bold]Detecting AI tools...[/bold]")
    detected = detect_installed_tools()

    if detected:
        for tool in detected:
            name = SUPPORTED_TOOLS[tool]
            if is_connected(tool):
                console.print(f"  [green]✓[/green] {name} already connected")
            else:
                if typer.confirm(f"  Connect lurk to {name}?", default=True):
                    ok, msg = connect_tool(tool)
                    icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                    console.print(f"  {icon} {msg}")
                else:
                    console.print(f"  [dim]—[/dim] Skipped {name} (run [cyan]lurk connect {tool}[/cyan] later)")
    else:
        console.print("  [dim]No AI tools detected. Connect later with [cyan]lurk connect[/cyan][/dim]")

    # Step 5: Start
    console.print()
    start()

    console.print()
    console.print("[bold green]lurk is ready.[/bold green]")
    console.print("  [cyan]lurk context[/cyan]      see what lurk observes")
    console.print("  [cyan]lurk agents[/cyan]       see active AI agents")
    console.print("  [cyan]lurk connect[/cyan]      connect more tools later")


# Keep `setup` as an alias for `onboard` for backwards compatibility
@app.command(hidden=True)
def setup():
    """Alias for onboard."""
    onboard(install_daemon=False)


def _find_daemon() -> str | None:
    """Find the lurk-daemon binary."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from lurk.config.install import find_daemon_binary
    return find_daemon_binary()


if __name__ == "__main__":
    app()
