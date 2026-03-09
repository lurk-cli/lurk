<p align="center">
  <img src="https://raw.githubusercontent.com/lurk-cli/lurk/main/assets/lurk.png" alt="lurk" width="400" />
</p>

<p align="center"><strong>Stop re-explaining yourself to AI.</strong></p>

<p align="center">
  <a href="#get-started">Get Started</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#commands">Commands</a> &middot;
  <a href="#mcp-tools">MCP Tools</a> &middot;
  <a href="#your-data-stays-yours">Privacy</a>
</p>

---

<!-- TODO: hero demo GIF/video here -->

```
$ lurk context -p

I'm working on implementing JWT authentication for the API.

Current state: The middleware is partially complete — token validation works
but the refresh endpoint isn't written yet.

Key context:
- Using JWT with RS256 over session auth because the API is stateless
- Token policy: 1-hour access tokens, 30-day refresh (agreed with Sarah in Slack)
- Key files: auth/middleware.ts, server/http.ts
- Asked Claude Code to scaffold the middleware — it built token validation but skipped refresh
- Discussed token rotation approach with Gemini, decided on sliding window

People involved: Sarah Chen (API design), Mike (will review PR)

[What I need help with: ]
```

You open claude, gemini, or ChatGPT and you spend 5 minutes explaining what you're working on before you can ask your actual question.

lurk fixes that. It watches your desktop — VS Code, Chrome, Slack, Notion, Figma, Terminal, and 30+ apps — and builds a running understanding of what you're working on. When you need AI help, `lurk context` gives you a ready-to-paste prompt with full context. Or just copy it: `lurk context -c`.

It works for everyone. Developers get file paths, git branches, and code changes. PMs get stakeholder names, document context, and decision logs. Designers get Figma references. Marketers get conversation summaries from Slack and WhatsApp.

**100% local. No cloud. No telemetry. No accounts. Your data never leaves your machine.**

---

## The Problem

Every conversation with AI starts from zero.

You've been deep in a problem for two hours — reading docs, editing code, discussing in Slack, reviewing in Figma. Then you open an AI chat and have to reconstruct all of that from memory. You forget half the context. The AI gives a generic answer. You spend another 5 minutes correcting it.

This isn't just a developer problem. A PM bouncing between Google Docs, Slack, and Linear has the same issue. A CMO coordinating a launch across WhatsApp, Teams, and email has it worse.

**The context exists. It's on your screen. You just can't give it to AI fast enough.**

## The Fix

lurk runs in the background and learns what you're working on — not by logging events, but by understanding them. It clusters your activity into **workstreams** (coherent threads of work) and tracks goals, decisions, people, and artifacts across every app you use.

When you need AI, the context is already assembled.

**Cold-start elimination.** `lurk context` gives you a prompt with everything an AI needs — what you're working on, key decisions, who's involved, what's changed. Paste it and ask your question.

**Workstream awareness.** lurk doesn't just know you're in VS Code. It knows you're implementing JWT auth, you decided on RS256 after reading the docs, and Sarah approved the token policy in Slack.

**Cross-app intelligence.** A Slack conversation, a Google Doc, a code editor, and a browser tab — if they're about the same thing, lurk connects them into one workstream.

**Works for any role.** Developer, PM, designer, marketer — lurk adapts to what you're doing. No configuration needed.

**Clipboard-ready.** `lurk context -c` copies the prompt straight to your clipboard. Open claude.ai, paste, ask.

---

## Get Started

**Requirements:** macOS 13+, Python 3.11+

```bash
pip install 'lurk[all]'
lurk
```

That's it. One command builds the daemon, starts everything, and opens Accessibility/Screen Recording settings if needed.

```bash
lurk              # start (or check status if already running)
lurk context -p   # see what lurk knows about your current work
lurk context -c   # copy context to clipboard — paste into any AI
lurk stop         # stop everything
```

### Connecting AI tools

lurk also serves context directly to AI tools via MCP:

```bash
lurk connect      # auto-detect and connect Claude Code, Cursor, etc.
lurk serve-mcp    # start MCP server manually
lurk serve-http   # start HTTP API on localhost:4141
```

### Smarter context with a local LLM

lurk works without any LLM, but with [Ollama](https://ollama.com) running locally, it can cluster your activity into workstreams and generate richer context:

```bash
ollama pull llama3.2:3b    # small, fast, runs on any Mac
lurk                       # lurk auto-detects Ollama
```

---

## How It Works

```
 Your Screen                    lurk                         AI Chat
┌──────────┐     ┌──────────────────────────┐     ┌──────────────────┐
│ VS Code  │     │ Observe → Enrich →       │     │ claude.ai        │
│ Chrome   │────▶│ Cluster → Synthesize     │────▶│ gemini.com       │
│ Slack    │     │                          │     │ ChatGPT          │
│ Figma    │     │ "You're implementing JWT │     │ Claude Code      │
│ Docs     │     │  auth, decided on RS256, │     │ Cursor           │
│ Terminal │     │  Sarah approved tokens"  │     │ Any AI tool      │
└──────────┘     └──────────────────────────┘     └──────────────────┘
```

**1. Observe.** A native macOS daemon watches your desktop every 3 seconds — active app, window title, input state, screen content via OCR. A browser extension captures page context. Git and session watchers track what you and your AI agents build.

**2. Enrich.** Raw events are parsed into structured context — file names, projects, languages, tickets, agent states, conversation participants, document structure, decisions mentioned.

**3. Cluster.** Activity is grouped into **workstreams** using a local LLM. A workstream is a coherent thread of work — it might span VS Code, Stack Overflow, a Google Doc, and a Slack thread, all connected because they're about the same thing.

**4. Synthesize.** When you ask for context, lurk generates a natural language prompt from the active workstream — adapted to your role, including only what's relevant.

### What lurk captures

| Signal              | Source            | Example                                  |
| ------------------- | ----------------- | ---------------------------------------- |
| Active app + window | macOS APIs        | `auth-middleware.ts — api — VS Code`     |
| Screen content      | OCR via Vision    | Code, chat messages, document text       |
| Conversations       | Messaging OCR     | Who said what in Slack, WhatsApp, Teams  |
| Document structure  | Document OCR      | Headings, key content, editing position  |
| Input state         | Event taps        | Typing, idle, mouse-only                 |
| Code changes        | Git watcher       | Actual diffs, branches, uncommitted work |
| Agent sessions      | Session watcher   | Claude Code conversation history         |
| Page content        | Browser extension | Viewport text, selections                |

### What lurk understands

| Insight                | How                                                    |
| ---------------------- | ------------------------------------------------------ |
| What you're working on | Workstream clustering via local LLM                    |
| Key decisions made     | Extracted from conversations and agent sessions        |
| People involved        | Names from chats, meetings, documents                  |
| Files and projects     | Parsed from editors, terminals, git                    |
| Activity type          | Coding, researching, communicating, designing, writing |
| Research trail         | Docs pages, Stack Overflow, GitHub issues visited      |
| AI agent states        | Claude Code working, Cursor generating, ChatGPT active |

---

## Commands

```
lurk                  Start everything (or show status if running)
lurk stop             Stop everything
lurk status           What's running, connected tools, event counts

lurk context          Current context snapshot
lurk context -p       Cold-start prompt — paste into any AI chat
lurk context -c       Copy context to clipboard

lurk agents           Active AI agents and attention queue
lurk workflows        Active workflows with context trail
lurk changes          Actual code diffs written by agents
lurk projects         Detected projects with activity stats
lurk log              Recent raw events
lurk search <term>    Search event history

lurk pause            Pause observation
lurk resume           Resume observation
lurk connect          Auto-detect and connect AI tools

lurk install          Set up auto-start on login
lurk config           Open config in $EDITOR
lurk purge            Clean up old data
```

---

## MCP Tools

When connected via MCP, AI agents get direct access to your context:

| Tool                            | What it does                                                |
| ------------------------------- | ----------------------------------------------------------- |
| `get_cold_start_prompt`         | Ready-to-use prompt with full workstream context            |
| `get_context_prompt`            | Natural language briefing — inject into system prompts      |
| `get_current_context`           | What the user is doing right now                            |
| `get_session_context`           | Full work session — projects, files, research, focus blocks |
| `get_workstreams`               | All active workstreams with goals, state, and artifacts     |
| `get_workflows`                 | Detected workflows with topics, tools, and context          |
| `get_workflow_context`          | Full accumulated context for a specific workflow            |
| `get_active_workflow_prompt`    | Synthesized prompt from the active workflow                 |
| `add_workflow_context`          | Feed back decisions, findings, blockers, summaries          |
| `get_recent_code_changes`       | Actual diffs of what agents wrote                           |
| `get_code_changes_summary`      | Readable summary of recent code changes                     |
| `get_agent_status`              | All tracked AI agent sessions and states                    |
| `get_attention_queue`           | Agents needing human attention, priority-sorted             |
| `get_agent_context_for_handoff` | Context briefing for agent-to-agent handoff                 |
| `get_project_context`           | Deep context for a specific project                         |

### The feedback loop

Agents don't just consume context — they contribute to it:

```python
# Claude Code records a decision
add_workflow_context(type="decision", content="JWT with RS256 — API is stateless")

# Next agent's prompt includes:
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
lurk serve-http    # starts on localhost:4141

curl localhost:4141/context/cold-start        # cold-start prompt (plain text)
curl localhost:4141/context/prompt            # natural language context
curl localhost:4141/context/now               # current activity (JSON)
curl localhost:4141/workstreams               # active workstreams
curl localhost:4141/workflows                 # detected workflows
curl localhost:4141/agents                    # agent status
curl localhost:4141/changes/summary           # what agents built

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

Built-in observers: **git watcher** (actual diffs), **session watcher** (Claude Code logs), **screenshot observer** (OCR with conversation and document extraction), **browser extension** (page content).

---

## Your Data Stays Yours

lurk is **100% local**. Everything runs on your machine. Nothing is sent anywhere.

|                  |                                                                        |
| ---------------- | ---------------------------------------------------------------------- |
| **Storage**      | SQLite at `~/.lurk/store.db` — on your disk, nowhere else              |
| **Network**      | Zero outbound connections (unless you opt into cloud LLM prompts)      |
| **Telemetry**    | None. No analytics, no crash reports, no usage tracking                |
| **Accounts**     | None. No sign-up, no login, no cloud dashboard                         |
| **Servers**      | MCP and HTTP run on `localhost` only — not exposed to the network      |
| **Exclusions**   | Block sensitive apps and title patterns: `["Messages", "*bank*"]`      |
| **Controls**     | `lurk pause` to stop instantly, `lurk delete --all` to wipe everything |
| **Sanitization** | Emails, auth tokens, and identifiers stripped before storage           |

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
    Observers/                  Title, input, screen capture, calendar
    Store/                      SQLite database, buffered event writer
    Sanitize/                   Title sanitization

lurk/                           Python context engine
  src/lurk/
    cli/                        Typer CLI
    context/                    Context model, sessions, workstreams, workflows
    enrichment/                 Parser pipeline, classifiers, agent detection
    observers/                  Git watcher, session watcher, screenshot observer
    parsers/                    30+ app parsers, messaging OCR, document OCR
    server/                     MCP server, HTTP API, prompt synthesis
    store/                      Database access layer
    llm/                        Workstream engine, cold-start synthesis, Ollama

extension/                      Chrome extension for page context
```

---

## License

MIT
