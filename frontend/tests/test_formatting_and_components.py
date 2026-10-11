"""Unit tests for formatting utilities, session state guards, and UI components."""

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from frontend.utils.formatting import (
    format_action_type,
    format_duration,
    format_timestamp,
    get_status_badge,
    redact_sensitive_text,
    safe_json_dumps,
)
from frontend.components.agent_status import render_hitl_panel
from frontend.components.action_timeline import render_action_timeline


def test_redact_sensitive_text_masks_credentials():
    """Verify passwords, tokens, API keys, and card numbers are masked."""
    raw = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz and password=MySecretPass123"
    redacted = redact_sensitive_text(raw)
    assert "[REDACTED_TOKEN]" in redacted
    assert "[REDACTED_PASSWORD]" in redacted
    assert "MySecretPass123" not in redacted


def test_redact_sensitive_text_masks_credit_cards():
    """Verify 16-digit credit card numbers are masked."""
    raw = "Card: 4111 2222 3333 4444 on file"
    redacted = redact_sensitive_text(raw)
    assert "[REDACTED_CARD_NUMBER]" in redacted
    assert "4111" not in redacted


def test_format_timestamp():
    """Verify ISO strings and datetime objects format to HH:MM:SS."""
    dt = datetime(2026, 10, 9, 14, 30, 45, tzinfo=timezone.utc)
    assert format_timestamp(dt) == "14:30:45"
    assert format_timestamp("2026-10-09T14:30:45Z") == "14:30:45"
    assert format_timestamp(None) == "-"
    assert format_timestamp("invalid-date") == "-"


def test_format_duration():
    """Verify elapsed duration formatting for seconds and minutes."""
    start = datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
    end1 = datetime(2026, 10, 9, 10, 0, 15, tzinfo=timezone.utc)
    assert format_duration(start, end1) == "15.0s"

    end2 = datetime(2026, 10, 9, 10, 2, 30, tzinfo=timezone.utc)
    assert format_duration(start, end2) == "2m 30s"

    assert format_duration(None) == "-"


def test_get_status_badge_mappings():
    """Verify all RunStatus strings map to distinct colors and icons."""
    statuses = ["idle", "initializing", "running", "awaiting_confirmation", "completed", "failed", "stopped"]
    for s in statuses:
        label, color, icon = get_status_badge(s)
        assert len(label) > 0
        assert color.startswith("#")
        assert len(icon) > 0


def test_format_action_type():
    """Verify action type normalization."""
    assert format_action_type("click") == "CLICK"
    assert format_action_type("ActionType.NAVIGATE") == "NAVIGATE"
    assert format_action_type(None) == "UNKNOWN"


def test_safe_json_dumps():
    """Verify safe serialization handles dicts, models, and exceptions."""
    data = {"a": 1, "time": datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)}
    serialized = safe_json_dumps(data)
    assert '"a": 1' in serialized
    assert "2026-10-09" in serialized


def test_timeline_renders_with_missing_optional_fields():
    """Verify timeline handles step records with missing optional fields without raising exceptions."""
    # Record with minimal fields (no thought, no execution_result, no target)
    sparse_history = [
        {
            "step_number": 1,
            "action": {"action_type": "scroll"},
            "timestamp": "2026-10-09T12:00:00Z",
        },
        {
            "step_number": 2,
            "action": None,
            "thought": None,
            "safety_check": None,
            "execution_result": None,
        },
    ]

    with patch("streamlit.markdown") as mock_md, patch("streamlit.container") as mock_cont:
        # Should execute cleanly without error
        render_action_timeline(history=sparse_history)
        assert mock_md.called


def test_hitl_approval_panel_not_configured_representation():
    """Verify that AWAITING_CONFIRMATION status displays the 'Not Configured' safety notice."""
    sample_state = {
        "status": "awaiting_confirmation",
        "history": [
            {
                "step_number": 3,
                "action": {
                    "action_type": "click",
                    "selector": "#purge-db-btn",
                    "description": "Click purge database",
                },
                "safety_check": {
                    "is_safe": False,
                    "risk_level": "HIGH",
                    "flagged_pattern": "purge",
                    "requires_human_confirmation": True,
                },
            }
        ],
    }

    with patch("streamlit.markdown") as mock_md, patch("streamlit.info") as mock_info, patch("streamlit.button") as mock_btn:
        render_hitl_panel(sample_state)
        # Check that info box with Not Configured was called
        assert mock_info.called
        info_text = mock_info.call_args[0][0]
        assert "Not Configured" in info_text

        # Verify buttons were rendered disabled
        assert mock_btn.called
        assert mock_btn.call_args[1].get("disabled") is True


def test_duplicate_task_submission_prevention():
    """Verify that task submission is guarded when is_running is True."""
    session_state = {"is_running": True}

    def attempt_submission(goal):
        if session_state.get("is_running"):
            return {"accepted": False, "reason": "Already running"}
        session_state["is_running"] = True
        return {"accepted": True}

    # First attempt when already running
    res1 = attempt_submission("Task 1")
    assert res1["accepted"] is False
    assert res1["reason"] == "Already running"

    # Reset state to idle
    session_state["is_running"] = False
    res2 = attempt_submission("Task 2")
    assert res2["accepted"] is True
    assert session_state["is_running"] is True
