"""Formatting and display utilities for the Streamlit frontend.

Includes:
1. Sensitive text redaction (passwords, tokens, credentials).
2. Timestamp and execution duration formatting.
3. Status badge colors and visual indicator mappings.
4. Action type labels and safe JSON dumping.
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, Optional, Tuple, Union


# Patterns for sensitive data that should be redacted in UI logs/timelines
SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{15,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)(api[_\-]?key\s*[:=]\s*)[a-zA-Z0-9_\-]{15,}"), r"\1[REDACTED_KEY]"),
    (re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;&]{3,}"), r"\1[REDACTED_PASSWORD]"),
    (re.compile(r"(?i)(secret\s*[:=]\s*)[^\s,;&]{3,}"), r"\1[REDACTED_SECRET]"),
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[REDACTED_CARD_NUMBER]"),
]


def redact_sensitive_text(text: Optional[str]) -> str:
    """Mask credentials, passwords, tokens, and payment numbers."""
    if not text:
        return ""
    result = str(text)
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def parse_datetime(dt_val: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Parse string or datetime to a datetime object safely."""
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val
    try:
        # ISO format parser
        return datetime.fromisoformat(str(dt_val).replace("Z", "+00:00"))
    except Exception:
        return None


def format_timestamp(dt_val: Optional[Union[str, datetime]]) -> str:
    """Format datetime into a readable HH:MM:SS string."""
    dt = parse_datetime(dt_val)
    if not dt:
        return "-"
    try:
        return dt.strftime("%H:%M:%S")
    except Exception:
        return str(dt_val)


def format_duration(
    start_val: Optional[Union[str, datetime]],
    end_val: Optional[Union[str, datetime]] = None,
) -> str:
    """Compute and format duration between start and end (or current time if still running)."""
    start_dt = parse_datetime(start_val)
    if not start_dt:
        return "-"

    end_dt = parse_datetime(end_val) if end_val else datetime.now(timezone.utc)
    if not end_dt:
        return "-"

    try:
        delta = (end_dt - start_dt).total_seconds()
        if delta < 0:
            delta = 0.0
        if delta < 60:
            return f"{delta:.1f}s"
        minutes = int(delta // 60)
        seconds = int(delta % 60)
        return f"{minutes}m {seconds}s"
    except Exception:
        return "-"


def get_status_badge(status_str: Optional[str]) -> Tuple[str, str, str]:
    """Map a RunStatus to (Display Label, Hex Color, Status Indicator)."""
    raw = (status_str or "idle").lower().strip()
    status_map = {
        "idle": ("Ready (Idle)", "#64748b", "○"),
        "initializing": ("Initializing", "#0284c7", "◐"),
        "running": ("Running", "#2563eb", "●"),
        "awaiting_confirmation": ("Approval Required", "#d97706", "▲"),
        "completed": ("Completed", "#16a34a", "✓"),
        "failed": ("Failed", "#dc2626", "✕"),
        "stopped": ("Stopped", "#64748b", "■"),
    }
    return status_map.get(raw, (raw.title(), "#64748b", "•"))


def format_action_type(action_type: Optional[str]) -> str:
    """Format action type as clean uppercase label."""
    if not action_type:
        return "UNKNOWN"
    return str(action_type).upper().replace("ACTIONTYPE.", "").strip()


def safe_json_dumps(data: Any, indent: int = 2) -> str:
    """Serialize data to formatted JSON safely handling non-serializable objects."""
    def default_serializer(o):
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "dict"):
            return o.dict()
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    try:
        return json.dumps(data, indent=indent, default=default_serializer)
    except Exception as exc:
        return json.dumps({"error": f"Serialization error: {exc}"}, indent=indent)
