"""Intent classifier — rules-based sequence analysis for user intent."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class EventRecord:
    """Lightweight event record for the sliding window."""
    ts: float
    app: str
    file: str | None
    activity: str
    sub_activity: str | None


class IntentClassifier:
    """Classifies user intent from event sequences."""

    def __init__(self, window_size: int = 20) -> None:
        self.window: deque[EventRecord] = deque(maxlen=window_size)
        self.current_intent: str | None = None

    def add_event(self, record: EventRecord) -> None:
        self.window.append(record)

    def classify(self, current: EventRecord) -> str | None:
        """Classify intent based on current event and recent history."""
        self.add_event(current)

        # Single-event rules
        intent = self._single_event_intent(current)
        if intent:
            self.current_intent = intent
            return intent

        # Sequence rules (need at least 3 events)
        if len(self.window) >= 3:
            intent = self._sequence_intent()
            if intent:
                self.current_intent = intent
                return intent

        # Temporal rules
        intent = self._temporal_intent(current)
        if intent:
            self.current_intent = intent
            return intent

        return self.current_intent

    def _single_event_intent(self, event: EventRecord) -> str | None:
        """Classify from a single event."""
        if event.file:
            file_lower = event.file.lower()
            if any(p in file_lower for p in ["test", "spec", "_test.", ".test.", ".spec."]):
                return "testing"
            if file_lower.startswith("readme") or file_lower.endswith(".md"):
                return "documentation"

        if event.sub_activity:
            sub = event.sub_activity
            if sub == "code_review":
                return "code_review"
            if sub == "project_management":
                return "project_management"
            if sub == "email_composing":
                return "communicating"
            if sub in ("stack_overflow", "documentation", "learning"):
                return "researching"

        if event.activity == "meeting":
            return "meeting"

        return None

    def _sequence_intent(self) -> str | None:
        """Classify from event sequences."""
        recent = list(self.window)[-10:]

        # Rapid file switching → debugging
        if len(recent) >= 5:
            coding_events = [e for e in recent if e.activity == "coding" and e.file]
            if len(coding_events) >= 4:
                unique_files = set(e.file for e in coding_events)
                time_span = recent[-1].ts - recent[-4].ts
                if len(unique_files) >= 3 and time_span < 120:
                    return "debugging"

        # Code ↔ browser loop → researching
        transitions = []
        for i in range(1, len(recent)):
            prev_is_code = recent[i - 1].activity == "coding"
            curr_is_browse = recent[i].activity in ("browsing", "researching")
            curr_is_code = recent[i].activity == "coding"
            prev_is_browse = recent[i - 1].activity in ("browsing", "researching")
            if (prev_is_code and curr_is_browse) or (prev_is_browse and curr_is_code):
                transitions.append(i)

        if len(transitions) >= 3:
            return "researching"

        # Test file ↔ implementation file → TDD
        if len(coding_events := [e for e in recent if e.file]) >= 4:
            test_files = [e for e in coding_events if e.file and "test" in e.file.lower()]
            impl_files = [e for e in coding_events if e.file and "test" not in e.file.lower()]
            if test_files and impl_files:
                # Check if alternating
                alternations = 0
                for i in range(1, len(coding_events)):
                    prev_is_test = coding_events[i - 1].file and "test" in coding_events[i - 1].file.lower()
                    curr_is_test = coding_events[i].file and "test" in coding_events[i].file.lower()
                    if prev_is_test != curr_is_test:
                        alternations += 1
                if alternations >= 3:
                    return "tdd"

        return None

    def _temporal_intent(self, event: EventRecord) -> str | None:
        """Classify from temporal patterns."""
        if not self.window:
            return None

        # Long focus on same file → deep work / feature development
        if event.activity == "coding" and event.file:
            same_file_events = [
                e for e in self.window
                if e.file == event.file and e.activity == "coding"
            ]
            if same_file_events:
                duration = event.ts - same_file_events[0].ts
                if duration > 900:  # >15 minutes
                    return "feature_development"

        # Rapid context switching → context_switching
        recent_5min = [
            e for e in self.window
            if event.ts - e.ts < 300
        ]
        if len(recent_5min) >= 5:
            unique_apps = set(e.app for e in recent_5min)
            if len(unique_apps) >= 4:
                return "context_switching"

        return None
