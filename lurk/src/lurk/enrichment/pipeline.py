"""Enrichment pipeline — polls DB for raw events, enriches, writes back."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..config.exclusions import ExclusionEngine
from ..config.settings import ExclusionConfig, load_config
from ..parsers.registry import ParserRegistry
from ..enrichment.agent_detector import detect_agent
from ..enrichment.classifier import classify_activity, classify_interruptibility
from ..enrichment.intent import EventRecord, IntentClassifier
from ..store.database import (
    ensure_schema,
    fetch_unenriched_events,
    get_connection,
    insert_enriched_event,
    mark_enriched,
)

logger = logging.getLogger("lurk.enrichment")


class EnrichmentPipeline:
    """Polls for raw events, enriches them with parsed context, writes enriched events."""

    def __init__(self, exclusion_config: ExclusionConfig | None = None) -> None:
        self.parser_registry = ParserRegistry()
        self.intent_classifier = IntentClassifier()
        self.last_activity_start: float = 0
        self.current_activity: str | None = None

        # Load exclusion rules
        if exclusion_config is None:
            config = load_config()
            exclusion_config = config.exclusions
        self.exclusion_engine = ExclusionEngine(exclusion_config)

    def run_once(self) -> int:
        """Process one batch of unenriched events. Returns count processed."""
        conn = get_connection()
        try:
            ensure_schema(conn)
            events = fetch_unenriched_events(conn, limit=50)
            if not events:
                return 0

            enriched_ids = []
            for event in events:
                self._enrich_event(conn, event)
                enriched_ids.append(event["id"])

            mark_enriched(conn, enriched_ids)
            conn.commit()
            return len(enriched_ids)
        finally:
            conn.close()

    def run_loop(self, poll_interval: float = 3.0) -> None:
        """Run the enrichment pipeline in a continuous loop."""
        logger.info("Enrichment pipeline started (poll every %.1fs)", poll_interval)
        while True:
            try:
                count = self.run_once()
                if count > 0:
                    logger.debug("Enriched %d events", count)
            except Exception:
                logger.exception("Error in enrichment pipeline")
            time.sleep(poll_interval)

    def _enrich_event(self, conn: Any, event: dict[str, Any]) -> None:
        """Enrich a single raw event."""
        event_type = event.get("event_type")
        app = event.get("app") or ""
        title = event.get("title") or ""
        ts = event.get("ts", 0)

        # Only parse title_change and app_switch events
        if event_type not in ("title_change", "app_switch"):
            return

        # Check exclusion rules
        if self.exclusion_engine.should_exclude(
            app=app, title=title,
            bundle_id=event.get("bundle_id"),
            timestamp=ts,
        ):
            logger.debug("Excluded event: %s / %s", app, title[:50])
            return

        # Parse the title
        ctx = self.parser_registry.parse(title, app, event.get("bundle_id"))

        # Override activity if parser didn't set one
        if ctx.activity == "unknown":
            ctx.activity = classify_activity(app, title)

        # Track activity duration
        if ctx.activity != self.current_activity:
            self.last_activity_start = ts
            self.current_activity = ctx.activity
        duration = ts - self.last_activity_start

        # Classify interruptibility
        interruptibility = classify_interruptibility(ctx.activity, duration)

        # Classify intent
        intent = self.intent_classifier.classify(EventRecord(
            ts=ts,
            app=app,
            file=ctx.file,
            activity=ctx.activity,
            sub_activity=ctx.sub_activity,
        ))

        # Detect AI agent
        agent_detection = detect_agent(app, title, event.get("bundle_id"))
        agent_tool = agent_detection.agent_tool if agent_detection else None
        agent_state = agent_detection.agent_state if agent_detection else None

        # Write enriched event
        insert_enriched_event(conn, {
            "event_id": event["id"],
            "ts": ts,
            "app": app,
            "title": title,
            "file": ctx.file,
            "project": ctx.project,
            "language": ctx.language,
            "ticket": ctx.ticket,
            "branch": ctx.branch,
            "url_domain": ctx.url_domain,
            "topic": ctx.topic,
            "channel": ctx.channel,
            "document_name": ctx.document_name,
            "activity": ctx.activity,
            "sub_activity": ctx.sub_activity,
            "intent": intent,
            "interruptibility": interruptibility,
            "agent_tool": agent_tool,
            "agent_state": agent_state,
        })
