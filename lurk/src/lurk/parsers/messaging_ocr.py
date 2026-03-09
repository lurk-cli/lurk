"""Chat screen OCR analyzer — extracts conversation context from messaging app screenshots.

Uses geometric heuristics on OCR text blocks (with bounding boxes from Vision)
to identify message alignment, sender names, and conversation topics.
No ML required — just spatial reasoning about chat UI layouts.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .spatial import TextBlock


@dataclass
class ChatContext:
    """Structured context extracted from a chat screen."""
    contact_name: str | None = None
    topic_keywords: list[str] = field(default_factory=list)
    contacts_mentioned: list[str] = field(default_factory=list)
    is_group: bool = False
    breadcrumb: str = ""


_MESSAGING_APPS = {
    "wechat", "微信", "whatsapp", "telegram", "line", "signal",
    "messages", "imessage",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}")

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "at", "by", "with", "from", "about", "into", "through",
    "and", "but", "or", "not", "no", "so", "if", "then", "than",
    "this", "that", "these", "those", "it", "its", "i", "me", "my",
    "you", "your", "we", "our", "they", "them", "their", "he", "she",
    "him", "her", "his", "what", "which", "who", "how", "when", "where",
    "just", "also", "very", "too", "more", "most", "some", "any", "all",
    "new", "one", "two", "get", "got", "like", "know", "think", "see",
    "use", "used", "using", "make", "made", "here", "there", "now",
    "google", "docs", "sheets", "chrome", "stackoverflow", "github",
    "sent", "delivered", "read", "typing", "online", "offline",
    "today", "yesterday", "message", "messages",
})

# Timestamp patterns common in chat UIs
_TIMESTAMP_RE = re.compile(
    r"^\d{1,2}:\d{2}(?:\s*[APap][Mm])?$|"
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday)",
    re.IGNORECASE,
)

# CJK Unicode ranges
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFF]")
_CJK_WORD_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFF]{2,}")


def is_messaging_app(app: str) -> bool:
    """Check if an app name matches a known messaging app."""
    return any(name in app.lower() for name in _MESSAGING_APPS)


def _is_cjk_name(text: str) -> bool:
    """Check if text looks like a CJK name (2-4 CJK characters)."""
    stripped = text.strip()
    if not stripped:
        return False
    cjk_chars = _CJK_RE.findall(stripped)
    non_cjk = _CJK_RE.sub("", stripped).strip()
    return 2 <= len(cjk_chars) <= 4 and len(non_cjk) == 0


def _is_timestamp(text: str) -> bool:
    """Check if a text block looks like a timestamp or system message."""
    stripped = text.strip()
    if _TIMESTAMP_RE.match(stripped):
        return True
    # Very short text with digits — likely time
    if len(stripped) < 8 and any(c.isdigit() for c in stripped):
        return True
    return False


def _is_sender_label(block: TextBlock, msg_block: TextBlock) -> bool:
    """Check if a block looks like a sender name label above a message.

    Sender labels in group chats are:
    - Short text (<25 chars)
    - Positioned slightly above a left-aligned message
    - Similar x position to the message below
    """
    if len(block.text.strip()) > 25:
        return False
    if len(block.text.strip()) < 1:
        return False
    # Must be above the message (higher y in bottom-left-origin coords)
    y_diff = block.y - msg_block.y
    if y_diff < 0.005 or y_diff > 0.06:
        return False
    # Similar x position
    if abs(block.x - msg_block.x) > 0.1:
        return False
    return True


def _extract_keywords(texts: list[str], max_keywords: int = 10) -> list[str]:
    """Extract topic keywords from message texts."""
    combined = " ".join(texts).lower()

    # English words
    words = _WORD_RE.findall(combined)
    filtered = [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    # CJK words (2+ character sequences)
    cjk_words = _CJK_WORD_RE.findall(combined)
    filtered.extend(cjk_words)

    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(max_keywords)]


def analyze_chat_screen(
    blocks: list[TextBlock], app: str, title: str,
) -> ChatContext | None:
    """Analyze OCR text blocks from a chat screen to extract conversation context.

    Returns None if the screen doesn't look like an active chat conversation
    (e.g., contacts list, settings page).
    """
    if not blocks or len(blocks) < 3:
        return None

    # Classify blocks by horizontal position
    incoming: list[TextBlock] = []   # left-aligned (x < 0.35)
    outgoing: list[TextBlock] = []   # right-aligned (x > 0.55)
    middle: list[TextBlock] = []     # timestamps, system messages
    header: list[TextBlock] = []     # top 10% of screen

    for block in blocks:
        # Vision uses bottom-left origin, so top of screen = high y
        if block.y + block.h > 0.90:
            header.append(block)
            continue

        if _is_timestamp(block.text):
            middle.append(block)
            continue

        if block.x < 0.35:
            incoming.append(block)
        elif block.x > 0.55:
            outgoing.append(block)
        else:
            middle.append(block)

    # Need at least some messages to confirm this is a chat screen
    total_messages = len(incoming) + len(outgoing)
    if total_messages < 2:
        return None

    ctx = ChatContext()

    # --- Contact/group name from header ---
    # Look for centered text in top 10% — this is the chat header
    contact_from_header = None
    for block in header:
        text = block.text.strip()
        # Skip very short UI elements and navigation buttons
        if len(text) < 2 or text in ("<", ">", "...", "⋮"):
            continue
        # Centered-ish text is likely the contact name
        center_x = block.x + block.w / 2
        if 0.25 < center_x < 0.75:
            contact_from_header = text
            break

    # Cross-reference with window title
    if title:
        # Strip app suffix from title
        cleaned_title = re.sub(
            r"\s*[-–—]\s*(?:WeChat|微信|WhatsApp|Telegram|LINE|Signal)\s*$",
            "", title, flags=re.IGNORECASE,
        ).strip()
        # Strip group count
        cleaned_title = re.sub(r"\s*\(\d+\)\s*$", "", cleaned_title).strip()
        if cleaned_title:
            ctx.contact_name = cleaned_title
        elif contact_from_header:
            ctx.contact_name = contact_from_header
    elif contact_from_header:
        ctx.contact_name = contact_from_header

    # --- Sender name extraction (group chats) ---
    # Sort incoming messages by y position (top to bottom = descending y)
    sorted_incoming = sorted(incoming, key=lambda b: -b.y)
    sender_names: list[str] = []

    for msg_block in sorted_incoming:
        for block in blocks:
            if block is msg_block:
                continue
            if _is_sender_label(block, msg_block):
                name = block.text.strip()
                if name and name not in sender_names:
                    sender_names.append(name)
                break  # one label per message

    ctx.contacts_mentioned = sender_names[:5]
    ctx.is_group = len(sender_names) >= 2

    # --- Topic extraction from recent messages ---
    # Focus on bottom of screen (most recent messages)
    # In bottom-left origin, bottom = low y values
    all_messages = incoming + outgoing
    all_messages.sort(key=lambda b: b.y)  # ascending y = bottom first = most recent
    recent_texts = [b.text for b in all_messages[:8]]

    ctx.topic_keywords = _extract_keywords(recent_texts)

    # --- Build breadcrumb ---
    parts = ["chatting"]
    if ctx.contact_name:
        parts.append(f"with {ctx.contact_name}")
    if ctx.topic_keywords:
        parts.append(f"about {', '.join(ctx.topic_keywords[:3])}")
    ctx.breadcrumb = " ".join(parts)

    return ctx
