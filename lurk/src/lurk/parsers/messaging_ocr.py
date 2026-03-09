"""Chat screen OCR analyzer — extracts conversation context from messaging app screenshots.

Uses geometric heuristics on OCR text blocks (with bounding boxes from Vision)
to identify message alignment, sender names, and conversation topics.
No ML required — just spatial reasoning about chat UI layouts.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from .spatial import TextBlock


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ChatContext:
    """Structured context extracted from a chat screen."""
    contact_name: str | None = None
    topic_keywords: list[str] = field(default_factory=list)
    contacts_mentioned: list[str] = field(default_factory=list)
    is_group: bool = False
    breadcrumb: str = ""


@dataclass
class ConversationExtract:
    """Rich structured data extracted from a messaging screenshot."""
    app: str                         # Slack, WhatsApp, etc.
    channel_or_contact: str
    speakers: list[str]              # extracted names
    messages: list[dict]             # {"speaker": str, "text": str}
    decisions: list[str]             # "Agreed to ship by Friday"
    dates_mentioned: list[str]       # "March 15", "next Tuesday"
    names_mentioned: list[str]       # people referenced
    numbers_mentioned: list[str]     # "$50k", "3 sprints"
    topic_summary: str               # brief summary
    dedupe_hash: str                 # for cross-capture deduplication


# ---------------------------------------------------------------------------
# Constants and patterns
# ---------------------------------------------------------------------------

_MESSAGING_APPS = {
    "wechat", "微信", "whatsapp", "telegram", "line", "signal",
    "messages", "imessage", "slack",
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

# Decision language patterns
_DECISION_RE = re.compile(
    r"(?i)\b(?:"
    r"let'?s\s+go\s+with|"
    r"agreed\s+(?:to|on|that)|"
    r"decided\s+(?:to|on|that)|"
    r"approved|"
    r"confirmed|"
    r"we'?ll\s+\w+|"
    r"plan\s+is\s+to|"
    r"going\s+with|"
    r"settled\s+on|"
    r"let'?s\s+do|"
    r"sounds\s+good|"
    r"ship\s+(?:it|by|on)"
    r")\b"
)

# Date/time reference patterns
_DATE_RE = re.compile(
    r"(?i)\b(?:"
    # Named months with optional day: "March 15", "Jan 3rd"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?|"
    # Numeric dates: "3/15", "03/15/2026", "2026-03-15"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"\d{4}-\d{2}-\d{2}|"
    # Relative dates
    r"next\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun|week|month|quarter)|"
    r"this\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun|week|month|quarter)|"
    r"(?:by|before|after|until)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun|EOD|end\s+of\s+(?:day|week|month|quarter))|"
    r"tomorrow|"
    r"end\s+of\s+(?:day|week|month|quarter|year)|"
    r"EOD|EOW|EOM|EOQ|"
    r"Q[1-4]\b(?:\s+\d{4})?|"
    r"(?:by|before|on)\s+Friday|"
    r"(?:by|before|on)\s+Monday"
    r")\b"
)

# Significant numbers: monetary, percentages, versions, quantities with units
_NUMBER_RE = re.compile(
    r"(?i)(?:"
    # Monetary: "$50k", "$1.5M", "$100", "100k"
    r"\$[\d,.]+[KkMmBb]?|"
    r"\d+(?:\.\d+)?[KkMmBb]\b|"
    # Percentages: "100%", "50.5%"
    r"\d+(?:\.\d+)?%|"
    # Versions: "v2.0", "v1.2.3"
    r"v\d+(?:\.\d+)+|"
    # Quantities with units: "3 sprints", "5 days", "2 weeks"
    r"\d+\s+(?:sprint|sprints|day|days|week|weeks|month|months|hour|hours|"
    r"point|points|story\s+points?|ticket|tickets|bug|bugs|PR|PRs|"
    r"engineer|engineers|people|team|teams|instance|instances|"
    r"node|nodes|server|servers|cluster|clusters|"
    r"user|users|customer|customers|request|requests)"
    r")"
)

# Common stop words for name extraction (beyond _STOP_WORDS)
_NAME_STOP_WORDS = frozenset({
    "The", "This", "That", "These", "Those", "Here", "There",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "OK", "LGTM", "TODO", "FYI", "ASAP", "WIP", "TBD", "TBA",
    "PM", "AM", "EST", "PST", "UTC", "GMT",
    "True", "False", "None", "Null",
})

# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------

_seen_hashes: dict[str, float] = {}
_SEEN_MAX_SIZE = 200
_SEEN_TTL = 300.0  # 5 minutes


def _check_and_store_hash(dedupe_hash: str) -> bool:
    """Check if a hash was recently seen. Returns True if duplicate.

    Also cleans old entries when cache exceeds max size.
    """
    now = time.time()

    # Clean old entries if cache is too large
    if len(_seen_hashes) >= _SEEN_MAX_SIZE:
        expired = [h for h, ts in _seen_hashes.items() if now - ts > _SEEN_TTL]
        for h in expired:
            del _seen_hashes[h]
        # If still too large after cleaning expired, remove oldest
        if len(_seen_hashes) >= _SEEN_MAX_SIZE:
            oldest = sorted(_seen_hashes.items(), key=lambda x: x[1])
            for h, _ in oldest[:len(_seen_hashes) - _SEEN_MAX_SIZE + 1]:
                del _seen_hashes[h]

    # Check for recent duplicate
    if dedupe_hash in _seen_hashes:
        if now - _seen_hashes[dedupe_hash] < _SEEN_TTL:
            return True  # duplicate

    _seen_hashes[dedupe_hash] = now
    return False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

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


def _extract_decisions(texts: list[str]) -> list[str]:
    """Extract decision-like statements from message texts."""
    decisions: list[str] = []
    for text in texts:
        for sent in re.split(r'[.!?\n]', text):
            sent = sent.strip()
            if not sent or len(sent) < 10:
                continue
            if _DECISION_RE.search(sent):
                # Clean up and cap length
                decision = sent[:150].strip()
                if decision and decision not in decisions:
                    decisions.append(decision)
    return decisions[:10]


def _extract_dates(texts: list[str]) -> list[str]:
    """Extract date/time references from message texts."""
    dates: list[str] = []
    combined = " ".join(texts)
    for m in _DATE_RE.finditer(combined):
        date = m.group(0).strip()
        if date and date not in dates:
            dates.append(date)
    return dates[:10]


def _extract_numbers(texts: list[str]) -> list[str]:
    """Extract significant numbers from message texts."""
    numbers: list[str] = []
    combined = " ".join(texts)
    for m in _NUMBER_RE.finditer(combined):
        num = m.group(0).strip()
        if num and num not in numbers:
            numbers.append(num)
    return numbers[:10]


def _extract_names(speakers: list[str], texts: list[str]) -> list[str]:
    """Extract people's names from speakers list and message text.

    Looks for capitalized proper nouns that aren't common stop words.
    """
    names: list[str] = list(speakers)

    # Find capitalized words that look like names in message text
    # Pattern: capitalized word not at sentence start (heuristic)
    name_re = re.compile(r'\b([A-Z][a-z]{1,15})\b')
    combined = " ".join(texts)

    for m in name_re.finditer(combined):
        candidate = m.group(1)
        if candidate in _NAME_STOP_WORDS:
            continue
        if candidate.lower() in _STOP_WORDS:
            continue
        # Skip if it's a very common English word that happens to be capitalized
        if len(candidate) <= 2:
            continue
        if candidate not in names:
            names.append(candidate)

    # Also look for CJK names
    for text in texts:
        if _is_cjk_name(text.strip()):
            name = text.strip()
            if name not in names:
                names.append(name)

    return names[:15]


def _compute_dedupe_hash(message_texts: list[str]) -> str:
    """Compute a deduplication hash from message texts.

    Uses the sorted set of first 50 chars of each message.
    """
    snippets = sorted({text[:50] for text in message_texts if text.strip()})
    raw = "|".join(snippets)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main analysis functions
# ---------------------------------------------------------------------------

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


def extract_conversation(
    blocks: list[TextBlock], app: str, title: str,
) -> ConversationExtract | None:
    """Extract rich structured conversation data from a chat screenshot.

    Returns None if the screen doesn't look like a chat, or if the
    content was recently seen (deduplication).

    This is the enhanced version of analyze_chat_screen() that extracts
    speakers, messages, decisions, dates, numbers, and names.
    """
    if not blocks or len(blocks) < 3:
        return None

    # Classify blocks by horizontal position
    incoming: list[TextBlock] = []   # left-aligned (x < 0.35)
    outgoing: list[TextBlock] = []   # right-aligned (x > 0.55)
    middle: list[TextBlock] = []     # timestamps, system messages
    header: list[TextBlock] = []     # top 10% of screen

    for block in blocks:
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

    total_messages = len(incoming) + len(outgoing)
    if total_messages < 2:
        return None

    # --- Contact/channel from header ---
    channel_or_contact = ""
    for block in header:
        text = block.text.strip()
        if len(text) < 2 or text in ("<", ">", "...", "⋮"):
            continue
        center_x = block.x + block.w / 2
        if 0.25 < center_x < 0.75:
            channel_or_contact = text
            break

    if title:
        cleaned_title = re.sub(
            r"\s*[-–—]\s*(?:WeChat|微信|WhatsApp|Telegram|LINE|Signal|Slack)\s*$",
            "", title, flags=re.IGNORECASE,
        ).strip()
        cleaned_title = re.sub(r"\s*\(\d+\)\s*$", "", cleaned_title).strip()
        if cleaned_title:
            channel_or_contact = cleaned_title

    # --- Pair speakers with messages ---
    # Sort all message blocks by y position (descending = top to bottom on screen)
    sorted_incoming = sorted(incoming, key=lambda b: -b.y)

    # Build speaker->message pairs for incoming messages
    speaker_labels: dict[int, str] = {}  # msg_block id -> speaker name
    sender_names: list[str] = []

    for msg_block in sorted_incoming:
        for block in blocks:
            if block is msg_block:
                continue
            if _is_sender_label(block, msg_block):
                name = block.text.strip()
                if name:
                    speaker_labels[id(msg_block)] = name
                    if name not in sender_names:
                        sender_names.append(name)
                break

    # Build ordered message list (by y position, ascending = most recent first)
    all_msg_blocks = incoming + outgoing
    all_msg_blocks.sort(key=lambda b: b.y)  # ascending y = bottom first

    messages: list[dict] = []
    for block in all_msg_blocks:
        text = block.text.strip()
        if not text:
            continue

        if block in outgoing:
            speaker = "You"
        elif id(block) in speaker_labels:
            speaker = speaker_labels[id(block)]
        elif sender_names:
            # Default to last known sender for ungrouped incoming
            speaker = sender_names[-1] if sender_names else "Unknown"
        else:
            speaker = "Contact"

        messages.append({"speaker": speaker, "text": text})

    if not messages:
        return None

    # Collect all message texts for extraction
    all_texts = [m["text"] for m in messages]

    # Build speakers list: named senders + "You" if outgoing messages exist
    speakers = list(sender_names)
    if outgoing and "You" not in speakers:
        speakers.append("You")

    # --- Deduplication ---
    dedupe_hash = _compute_dedupe_hash(all_texts)
    if _check_and_store_hash(dedupe_hash):
        return None  # recently seen

    # --- Extract structured data ---
    decisions = _extract_decisions(all_texts)
    dates = _extract_dates(all_texts)
    numbers = _extract_numbers(all_texts)
    names = _extract_names(speakers, all_texts)

    # --- Topic summary ---
    keywords = _extract_keywords(all_texts[:8], max_keywords=5)
    topic_parts = []
    if channel_or_contact:
        topic_parts.append(channel_or_contact)
    if keywords:
        topic_parts.append(", ".join(keywords[:3]))
    topic_summary = " — ".join(topic_parts) if topic_parts else "chat conversation"

    return ConversationExtract(
        app=app,
        channel_or_contact=channel_or_contact,
        speakers=speakers,
        messages=messages[-20:],  # cap at 20 most recent
        decisions=decisions,
        dates_mentioned=dates,
        names_mentioned=names,
        numbers_mentioned=numbers,
        topic_summary=topic_summary,
        dedupe_hash=dedupe_hash,
    )
