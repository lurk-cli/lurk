# Privacy Policy

**Last updated:** March 9, 2026

## Overview

lurk is a local-only desktop observability tool. Your data never leaves your machine.

## What the Chrome extension collects

The lurk Chrome extension captures the following data from your browser to provide work context:

- **Active tab title and URL** — to understand what you're working on
- **Viewport text** — visible page content for context enrichment
- **Typing activity in AI chat sites** — prompt length and preview (first 200 characters) to detect intent
- **Google Workspace metadata** — document titles, sheet names, slide counts, email subjects (not full document content)

## Where your data goes

All captured data is sent exclusively to the lurk desktop application running on your local machine at `localhost:4141`.

- **No data is sent to any external server**
- **No data is sent to us or any third party**
- **No analytics, telemetry, or tracking of any kind**
- **No user accounts or authentication**

## Data storage

All data is stored locally in a SQLite database on your machine at `~/.lurk/store.db`. You can inspect, export, or delete it at any time:

- `lurk log` — view stored events
- `lurk purge` — delete old data
- `lurk delete --all` — wipe everything

## Data retention

By default, raw events are retained for 30 days and sessions for 365 days. You can configure these in `~/.lurk/config.yaml`.

## Exclusions

You can exclude sensitive apps and URL patterns from being captured:

```yaml
# ~/.lurk/config.yaml
exclusions:
  apps: ["Messages", "FaceTime"]
  title_patterns: ["*bank*", "*medical*"]
```

## Sensitive data handling

lurk automatically strips emails, auth tokens, and personal identifiers from captured data before storage.

## Third-party services

lurk makes no network requests to any external service. The only optional exception is if you configure a cloud LLM provider (e.g., Anthropic API) for enhanced context synthesis — this is disabled by default and requires explicit configuration.

## Your rights

Your data is yours. It exists only on your machine. There is no "other copy." You can delete all of it at any time.

## Changes to this policy

Updates will be posted to this page. Since lurk collects no contact information, we cannot notify you directly — check this page if you want to stay informed.

## Contact

If you have questions about this privacy policy, open an issue at [github.com/lurk-cli/lurk](https://github.com/lurk-cli/lurk/issues).
