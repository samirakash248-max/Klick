"""Structured Trajectory Logger for BrowserPilot AI (Member A, Step 5).

Captures structured, audit-ready telemetry for agent decision steps and run lifecycles.
Outputs JSON Lines (.jsonl) with automatic redaction of sensitive credentials,
graceful error handling, and configurable output paths.

Owned by: AI Engineer (Member A)
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from .schemas import (
    ActionType,
    BrowserAction,
    ExecutionResult,
    PageObservation,
    RiskLevel,
    RunStatus,
    SafetyCheckResult,
)

logger = logging.getLogger("browserpilot.trajectory_logger")

DEFAULT_TRAJECTORY_DIR = Path("data/trajectories")
MAX_TEXT_SNIPPET_LENGTH = 500

# Regular expressions for detecting and redacting sensitive data
SENSITIVE_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"api[_-]?key\s*[:=]\s*(['\"])[^'\"]+\1", re.IGNORECASE), r"api_key=\1[REDACTED_API_KEY]\1"),
    (re.compile(r"api[_-]?key\s*[:=]\s*([^\s,;'\"=]+)", re.IGNORECASE), r"api_key=[REDACTED_API_KEY]"),
    (re.compile(r"password\s*[:=]\s*(['\"])[^'\"]+\1", re.IGNORECASE), r"password=\1[REDACTED_PASSWORD]\1"),
    (re.compile(r"password\s*[:=]\s*([^\s,;'\"=]+)", re.IGNORECASE), r"password=[REDACTED_PASSWORD]"),
    (re.compile(r"secret\s*[:=]\s*(['\"])[^'\"]+\1", re.IGNORECASE), r"secret=\1[REDACTED_SECRET]\1"),
    (re.compile(r"secret\s*[:=]\s*([^\s,;'\"=]+)", re.IGNORECASE), r"secret=[REDACTED_SECRET]"),
    (re.compile(r"access_token\s*[:=]\s*(['\"])[^'\"]+\1", re.IGNORECASE), r"access_token=\1[REDACTED_TOKEN]\1"),
    (re.compile(r"([?&](?:token|access_token|api[_-]?key|password|secret|session)[=:])[^\s&'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "[REDACTED_CARD_NUMBER]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED_OPENAI_KEY]"),
]


def redact_sensitive_text(text: Optional[str], max_length: int = MAX_TEXT_SNIPPET_LENGTH) -> Optional[str]:
    """Redact known secret patterns and truncate verbose content."""
    if not text:
        return text

    sanitized = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "... [TRUNCATED]"

    return sanitized


def sanitize_dict(obj: Any, max_text_len: int = MAX_TEXT_SNIPPET_LENGTH) -> Any:
    """Recursively sanitize dicts, lists, and primitives."""
    if isinstance(obj, str):
        return redact_sensitive_text(obj, max_length=max_text_len)
    elif isinstance(obj, dict):
        sanitized_dict = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if any(secret_term in key_lower for secret_term in ("password", "secret", "token", "auth_header", "cookie", "session", "authorization", "apikey", "api_key")):
                sanitized_dict[k] = "[REDACTED]"
            else:
                sanitized_dict[k] = sanitize_dict(v, max_text_len=max_text_len)
        return sanitized_dict
    elif isinstance(obj, (list, tuple)):
        return [sanitize_dict(item, max_text_len=max_text_len) for item in obj]
    elif isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    elif hasattr(obj, "model_dump"):
        return sanitize_dict(obj.model_dump(), max_text_len=max_text_len)
    elif hasattr(obj, "dict"):
        return sanitize_dict(obj.dict(), max_text_len=max_text_len)
    else:
        return str(obj)


class TrajectoryStepRecord(BaseModel):
    """Structured record for a single cognitive / execution step."""
    record_type: str = "step"
    run_id: str
    task_id: Optional[str] = None
    step_number: int = 1
    goal: str = Field(default="")
    observation_before: Dict[str, Any] = Field(default_factory=dict)
    action: Optional[Dict[str, Any]] = None
    grounding_check: bool = True
    safety_check: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None
    progress_verified: bool = False
    observation_after: Optional[Dict[str, Any]] = None
    run_status: str = Field(default="running")
    stop_reason: Optional[str] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_recovery_step: bool = False


class TrajectoryRunSummary(BaseModel):
    """Comprehensive summary record written at run termination."""
    record_type: str = "run_summary"
    run_id: str
    task_id: Optional[str] = None
    goal: str = Field(default="")
    status: str = Field(default="failed")
    stop_reason: Optional[str] = None
    completion_verified: bool = False
    total_decisions: int = 0
    total_executed_actions: int = 0
    duration_seconds: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error: Optional[str] = None
    final_output: Optional[str] = None
    trajectory_label: str = "failed"  # 'successful' | 'failed' | 'recovery'
    has_recovery_step: bool = False
    steps: List[TrajectoryStepRecord] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TrajectoryLogger:
    """Thread-safe, non-blocking-capable logger for experiment trajectories."""

    def __init__(
        self,
        output_dir: Union[str, Path] = DEFAULT_TRAJECTORY_DIR,
        enabled: bool = True,
        filename: str = "trajectories.jsonl",
        redact_secrets: bool = True,
        max_snippet_len: int = MAX_TEXT_SNIPPET_LENGTH,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.enabled = enabled
        self.filename = filename
        self.redact_secrets = redact_secrets
        self.max_snippet_len = max_snippet_len

    @property
    def log_path(self) -> Path:
        return self.output_dir / self.filename

    def _ensure_dir(self) -> bool:
        if not self.enabled:
            return False
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as exc:
            logger.warning("Failed to create trajectory directory %s: %s", self.output_dir, exc)
            return False

    def log_step(self, step: TrajectoryStepRecord) -> bool:
        """Write a single step record to JSONL."""
        if not self.enabled:
            return False

        if not self._ensure_dir():
            return False

        try:
            data = step.model_dump()
            if self.redact_secrets:
                data = sanitize_dict(data, max_text_len=self.max_snippet_len)

            line = json.dumps(data, ensure_ascii=False)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except Exception as exc:
            logger.warning("Failed to write trajectory step record for run %s: %s", step.run_id, exc)
            return False

    def log_run(self, summary: TrajectoryRunSummary) -> bool:
        """Write a comprehensive run summary record to JSONL."""
        if not self.enabled:
            return False

        if not self._ensure_dir():
            return False

        try:
            data = summary.model_dump()
            if self.redact_secrets:
                data = sanitize_dict(data, max_text_len=self.max_snippet_len)

            line = json.dumps(data, ensure_ascii=False)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except Exception as exc:
            logger.warning("Failed to write trajectory run summary for run %s: %s", summary.run_id, exc)
            return False

    @staticmethod
    def sanitize_observation(obs: Optional[PageObservation], max_len: int = MAX_TEXT_SNIPPET_LENGTH) -> Dict[str, Any]:
        """Convert PageObservation to a safe, token-efficient dictionary."""
        if not obs:
            return {}
        return {
            "url": obs.url,
            "title": obs.title,
            "dom_summary_snippet": redact_sensitive_text(obs.dom_summary, max_length=max_len),
            "element_count": len(obs.interactive_elements),
            "page_text_snippet": redact_sensitive_text(obs.page_text_snippet, max_length=max_len),
            "error": obs.error,
        }
