<p align="center">
  <img src="https://raw.githubusercontent.com/lurk-cli/lurk/main/assets/lurk.png" alt="lurk" width="400" />
</p>

<p align="center"><strong>Your AI agents have no idea what each other are doing.</strong></p>

<p align="center">
  <a href="#get-started">Get Started</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#commands">Commands</a> &middot;
  <a href="#mcp-tools">MCP Tools</a> &middot;
  <a href="#your-data-stays-yours">Privacy</a>
</p>

---

<!-- TODO: hero demo GIF here -->

```
$ lurk context -p

The user is working on the api project (TypeScript). Key decisions: chose JWT
with RS256 over session auth. Claude Code: built JWT middleware with token
rotation. Code changes: created auth/middleware.ts; modified server/http.ts.
They recently researched JWT refresh token rotation on Stack Overflow.
Related ticket: AUTH-142.
```

lurk silently observes your desktop — VS Code, iTerm, Chrome, Slack, Notion, Figma, Gmail, Linear, and 30+ apps — and makes that context available to every AI agent you use.

When Claude Code finishes a refactor, it feeds back what it decided. When you switch to ChatGPT, ChatGPT already knows what was built, what decisions were made, and what's still open. When Cursor picks up the thread, it doesn't start from zero.

**100% local. No cloud. No telemetry. No accounts. Your data never leaves your machine.**

---

## What this looks like

```
$ lurk agents

Active AI Agents
┌──────────────┬──────────────┬───────────┬──────────┬──────────────────────────┐
│ Tool         │ State        │ Project   │ Duration │ Task                     │
├──────────────┼──────────────┼───────────┼──────────┼──────────────────────────┤
│ Claude Code  │ needs_review │ api       │ 12m      │ auth refactor            │
│ Cursor Agent │ working      │ dashboard │ 6m       │ component generation     │
│ OpenClaw     │ working      │ api       │ 41m      │ triage + issue responses │
└──────────────┴──────────────┴───────────┴──────────┴──────────────────────────┘
```

<!-- TODO: agents demo GIF -->

```
$ lurk workflows

┌────┬─────────────────────┬────────────┬──────────────────────────────────────────┐
│ ID │ Workflow             │ Tools      │ Context                                  │
├────┼─────────────────────┼────────────┼──────────────────────────────────────────┤
│  3 │ api / auth / jwt     │ CC, Cursor │ JWT middleware, token rotation, RS256     │
│  7 │ Q3 revenue review    │ Docs, Chat │ spreadsheet analysis, email thread        │
└────┴─────────────────────┴────────────┴──────────────────────────────────────────┘
```

<!-- TODO: workflows demo GIF -->

```
$ lurk changes

In api (branch: feat/auth):
  Created auth/middleware.ts (typescript):
    export async function authMiddleware(req, res, next) {
      const token = req.headers.authorization?.split(' ')[1];
      const payload = await verifyJWT(token, { algorithms: ['RS256'] });
      req.user = payload;
      next();
    }
  Modified server/http.ts:
    Added: app.use('/api', authMiddleware)
    Removed: // TODO: add auth
```

<!-- TODO: changes demo GIF -->

---

## The Problem

Claude Code doesn't know what you just read in Slack. Cursor doesn't know Claude Code just rewrote the auth module it depends on. OpenClaw is filing issues against code that Claude Code already fixed.

Every tool is an island. You're the only one holding context across all of them, and you're losing it every time you switch windows.

## The Fix

lurk watches what's already on your screen and makes that context available to every tool — without any of them needing to talk to each other.

**Cross-agent context.** Switch from Claude Code to Cursor — Cursor instantly knows what was just refactored, which files changed, and what decisions were made.

**Evolving workflows.** Context isn't a snapshot, it's a thread. Each agent builds on what the last one contributed. Decisions, findings, blockers, and summaries accumulate across tools.

**Agent attention queue.** Claude Code finished 8 minutes ago. Cursor is still generating. OpenClaw is running fine. `lurk agents` shows who needs you and in what order.

**Works for everything.** Coding, research, email, spreadsheets, design, writing — lurk sees all of it and connects the dots.

No integrations. No plugins. No API keys to configure.

---

## Get Started

**Requirements:** macOS 13+

```bash
pip install 'lurk[all]'
lurk
```

That's it. One command builds the daemon, starts everything, connects your AI tools, and opens Accessibility settings if needed. No wizards, no prompts.

```bash
lurk           # start (or check status if already running)
lurk stop      # stop everything
lurk status    # what's running and connected
lurk context   # see what lurk observes right now
```

Need to connect more tools later? `lurk connect` auto-detects and connects them.

### Quick copy

```bash
lurk copy                  # copy context to clipboard
lurk copy --watch          # auto-update every 30s
```

---

## How It Works

```
Observations ──→ Workflow Context ──→ Prompt Generation ──→ Agent
     ↑                                                        │
     │                                                        │
     └──────────── Agent Output Feedback ←─────────────────────┘
```

**1. Observe.** A native macOS daemon watches your desktop every 3 seconds — active app, window title, input state, display layout. A browser extension captures page content. Git and session watchers track what agents actually build.

**2. Enrich.** Raw events are parsed into structured context — file names, projects, languages, tickets, agent states, activity types, research topics.

**3. Cluster.** Activity is grouped into **workflows** by topic overlap. A workflow might span Claude Code, Stack Overflow, a Google Doc, and a Slack thread — all connected because they're about the same thing.

**4. Serve.** Any AI tool can request context via MCP or HTTP. The prompt includes everything the workflow has accumulated.

**5. Feedback.** Agents write back what they decided, found, or built. The next agent starts with full context, not a blank slate.

### What lurk observes

| Signal         | Source            | Example                                |
| -------------- | ----------------- | -------------------------------------- |
| Active app     | macOS APIs        | VS Code, iTerm2, Chrome, Slack, Notion |
| Window title   | Accessibility API | `auth-middleware.ts — api — VS Code`   |
| Input state    | Event taps        | typing, idle, mouse-only               |
| Display layout | CoreGraphics      | which app on which monitor             |
| Code changes   | Git watcher       | actual diffs, not just commit messages |
| Agent sessions | Session watcher   | Claude Code conversation logs          |
| Page content   | Browser extension | viewport text, typing, selections      |

### What lurk infers

| Signal             | How                                                             |
| ------------------ | --------------------------------------------------------------- |
| Files and projects | Parsed from VS Code, Cursor, Xcode, JetBrains, terminal titles  |
| Languages          | File extensions — `auth-middleware.ts` → TypeScript             |
| Activity type      | Coding, researching, communicating, designing, writing, meeting |
| AI agent states    | Claude Code working, Cursor generating, ChatGPT active          |
| Intent             | Debugging, implementing, reviewing, researching                 |
| Tickets            | JIRA/Linear IDs from branches, editor titles, browser tabs      |
| Research trail     | Stack Overflow, MDN, GitHub issues visited                      |
| Google Workspace   | Docs, Sheets, Slides names; Gmail compose/triage/reading        |

### Agents detected automatically

| Agent       | Signal                                                   |
| ----------- | -------------------------------------------------------- |
| Claude Code | Terminal: `claude — Thinking...`, `claude — Allow tool?` |
| Cursor      | Window title during composer/agent mode                  |
| Codex       | Terminal: `codex` CLI patterns                           |
| ChatGPT     | Browser tab: `ChatGPT`                                   |
| Copilot     | Editor integration and browser tab patterns              |
| Aider       | Terminal: `aider — Thinking`                             |
| Goose       | Terminal title patterns                                  |

---

## Commands

```
lurk                  Start everything (or show status if running)
lurk stop             Stop everything
lurk status           What's running, connected tools, event counts

lurk context          Current context snapshot
lurk context -p       Natural language context prompt
lurk agents           Active AI agents and attention queue
lurk workflows        Active workflows with context trail
lurk changes          Actual code diffs written by agents
lurk projects         Detected projects with activity stats
lurk log              Recent raw events
lurk search <term>    Search event history

lurk pause            Pause observation
lurk resume           Resume observation
lurk copy             Copy context to clipboard
lurk connect          Auto-detect and connect AI tools

lurk install          Set up auto-start on login
lurk config           Open config in $EDITOR
lurk purge            Clean up old data
```

---

## MCP Tools

When connected via MCP, agents can call:

| Tool                            | What it does                                                                 |
| ------------------------------- | ---------------------------------------------------------------------------- |
| `get_context_prompt`            | Natural language briefing with workflow context — inject into system prompts |
| `get_current_context`           | What the user is doing right now                                             |
| `get_session_context`           | Full work session — projects, files, research, focus blocks                  |
| `get_workflows`                 | All detected workflows with topics, tools, and context                       |
| `get_workflow_context`          | Full accumulated context for a specific workflow                             |
| `get_active_workflow_prompt`    | Synthesized prompt from the active workflow                                  |
| `add_workflow_context`          | **Feed back** decisions, findings, blockers, summaries, questions            |
| `get_recent_code_changes`       | Actual diffs of what agents wrote                                            |
| `get_code_changes_summary`      | Readable summary of recent code changes                                      |
| `get_agent_session_context`     | What happened in the last agent conversation                                 |
| `get_agent_status`              | All tracked AI agent sessions and states                                     |
| `get_attention_queue`           | Agents needing human attention, priority-sorted                              |
| `get_agent_context_for_handoff` | Context briefing for agent-to-agent handoff                                  |
| `get_project_context`           | Deep context for a specific project                                          |
| `get_workflow_summary`          | High-level view of concurrent work streams                                   |

### The feedback loop

Agents don't just consume context — they contribute to it:

```python
# Claude Code records a decision
add_workflow_context(type="decision", content="JWT with RS256 — API is stateless")

# Next agent's get_context_prompt() includes:
# "Key decisions: JWT with RS256 for auth."
```

| Type       | Purpose                                 |
| ---------- | --------------------------------------- |
| `decision` | Architectural or design choice made     |
| `finding`  | Research discovery or technical insight |
| `blocker`  | Something blocking progress             |
| `summary`  | What was just accomplished              |
| `question` | Open question for follow-up             |

---

## HTTP API

```bash
curl localhost:4141/context/prompt           # natural language context
curl localhost:4141/context/workflow-prompt   # active workflow context
curl localhost:4141/context/now              # current activity (JSON)
curl localhost:4141/workflows                # list workflows
curl localhost:4141/agents                   # agent status
curl localhost:4141/changes/summary          # what agents built

# Feed back context from an agent
curl -X POST localhost:4141/context/feedback \
  -H 'Content-Type: application/json' \
  -d '{"type": "decision", "content": "Chose JWT over session auth"}'
```

---

## Extending lurk

### Custom observers

lurk uses a generic `WorkflowObserver` protocol. Adding a new context source is one class:

```python
from lurk.observers import WorkflowObserver, WorkflowUpdate

class SlackObserver:
    def check(self) -> list[WorkflowUpdate]:
        return [WorkflowUpdate(
            keywords=["project-alpha", "deployment"],
            breadcrumb="discussing deployment in #engineering",
            tool="Slack",
        )]
```

Built-in observers: **git watcher** (actual diffs), **session watcher** (Claude Code logs), **browser extension** (page content).

### LLM-enhanced context

Optional — lurk works without it, but an LLM can synthesize richer context:

```bash
export LURK_LLM_PROVIDER=anthropic
export LURK_LLM_MODEL=claude-haiku-4-5-20251001
export ANTHROPIC_API_KEY=sk-...
```

---

## Your Data Stays Yours

lurk is **100% local**. Everything runs on your machine. Nothing is sent anywhere.

|                  |                                                                                   |
| ---------------- | --------------------------------------------------------------------------------- |
| **Storage**      | SQLite database at `~/.lurk/store.db` — on your disk, nowhere else                |
| **Network**      | Zero outbound connections (unless you opt into LLM-enhanced prompts)              |
| **Telemetry**    | None. No analytics, no crash reports, no usage tracking                           |
| **Accounts**     | None. No sign-up, no login, no cloud dashboard                                    |
| **Servers**      | MCP and HTTP run on `localhost` only — not exposed to the network                 |
| **Exclusions**   | Block sensitive apps and title patterns: `["Messages", "*bank*", "*medical*"]`    |
| **Controls**     | `lurk pause` to stop instantly, `lurk delete --all` to wipe everything            |
| **Sanitization** | Emails, auth tokens, and personal identifiers stripped from titles before storage |

You own your context. You can inspect it (`lurk log`), export it, delete it, or nuke it. There is no "other copy."

---

## Configuration

`~/.lurk/config.yaml`

```yaml
observation:
  poll_interval: 3 # seconds between captures
  idle_threshold: 120 # seconds before marking idle
  session_gap: 300 # seconds of inactivity to end a session

exclusions:
  apps: ["Messages", "FaceTime"]
  title_patterns: ["*bank*", "*medical*"]

retention:
  raw_events_days: 30 # auto-purge old events
  sessions_days: 365
```

---

## Architecture

```
daemon/                         Swift macOS daemon
  Sources/LurkDaemon/
    App/                        AppDelegate, MenuBarController
    Observers/                  Title, input, workspace, calendar observers
    Store/                      SQLite database, buffered event writer
    IPC/                        Unix socket server
    Sanitize/                   Title sanitization

lurk/                           Python context engine
  src/lurk/
    cli/                        Typer CLI
    config/                     Settings, installation, retention
    context/                    Context model, sessions, projects, agents, workflows
    enrichment/                 Parser pipeline, classifiers, agent detection
    observers/                  WorkflowObserver protocol, git watcher, session watcher
    parsers/                    Per-app title parsers (30+ apps)
    server/                     MCP server, HTTP API, prompt generation, feedback
    store/                      Database access layer
    llm/                        Optional LLM integration
    sanitize/                   Title sanitization rules

extension/                      Chrome extension for browser-based AI tools
```

---

## License

MIT
