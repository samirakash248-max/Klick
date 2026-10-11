"""Action timeline component displaying chronological execution steps with reasoning and telemetry."""

from typing import Any, Dict, List, Optional
import streamlit as st

try:
    from utils.formatting import (
        format_action_type,
        format_timestamp,
        redact_sensitive_text,
    )
except (ImportError, ValueError):
    from frontend.utils.formatting import (
        format_action_type,
        format_timestamp,
        redact_sensitive_text,
    )


ACTION_COLORS = {
    "NAVIGATE": "#2563eb",  # Blue
    "CLICK": "#0284c7",     # Sky
    "TYPE": "#0d9488",      # Teal
    "SCROLL": "#475569",    # Slate
    "WAIT": "#64748b",      # Slate
    "EXTRACT": "#0891b2",   # Cyan
    "FINISH": "#16a34a",    # Green
    "FAIL": "#dc2626",      # Red
}


def render_action_timeline(
    history: Optional[List[Dict[str, Any]]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Render the chronological timeline of executed actions and model reasoning traces."""
    st.subheader("Action Timeline & Cognitive Traces")

    steps = history or []

    # If state history is empty, check if we have events from /events to display
    if not steps and events:
        render_event_fallback_timeline(events)
        return

    if not steps:
        st.markdown(
            """
            <div style="background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 4px; padding: 1.5rem; text-align: center; color: #64748b;">
                <div style="font-weight: 600; font-size: 0.95rem; color: #334155;">No actions recorded yet</div>
                <div style="font-size: 0.8rem; margin-top: 0.25rem;">Enter a task above and start the agent to observe live execution steps.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # Render each recorded step chronologically
    for record in steps:
        step_num = record.get("step_number", "?")
        action = record.get("action", {}) or {}
        thought = record.get("thought", {}) or {}
        safety = record.get("safety_check", {}) or {}
        exec_res = record.get("execution_result", {}) or {}
        timestamp = record.get("timestamp")

        action_type = format_action_type(action.get("action_type", "ACTION"))
        badge_color = ACTION_COLORS.get(action_type, "#3b82f6")

        is_success = exec_res.get("success", False) if exec_res else True
        status_label = "Success" if is_success else "Failed"
        status_color = "#10b981" if is_success else "#ef4444"
        if not safety.get("is_safe", True):
            status_label = "Blocked"
            status_color = "#f59e0b"

        time_str = format_timestamp(timestamp)
        target = action.get("selector") or action.get("target") or action.get("target_element_description") or ""
        input_text = redact_sensitive_text(action.get("text"))

        with st.container():
            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 4px solid {badge_color}; border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.75rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                            <span style="font-weight: 600; color: #1e293b; font-size: 0.85rem;">Step {step_num}</span>
                            <span style="background: #f8fafc; color: {badge_color}; border: 1px solid #cbd5e1; padding: 0.15rem 0.5rem; border-radius: 4px; font-weight: 600; font-size: 0.75rem;">{action_type}</span>
                            <span style="background: #f8fafc; color: {status_color}; border: 1px solid #cbd5e1; padding: 0.1rem 0.45rem; border-radius: 4px; font-weight: 600; font-size: 0.7rem;">{status_label}</span>
                        </div>
                        <span style="color: #64748b; font-size: 0.75rem; font-family: monospace;">{time_str}</span>
                    </div>
                """,
                unsafe_allow_html=True,
            )

            # Details: Action target & input
            details = []
            if target:
                details.append(f"**Target:** `{target}`")
            if input_text:
                details.append(f"**Input:** `{input_text}`")
            if action.get("url"):
                details.append(f"**URL:** `{action.get('url')}`")
            if action.get("description"):
                details.append(f"_{action.get('description')}_")

            if details:
                st.markdown(" • ".join(details))

            # Thought & Reflection collapsible
            reflection = thought.get("reflection")
            reasoning = thought.get("reasoning")
            if reflection or reasoning:
                with st.expander("Cognitive Reflection & Reasoning", expanded=False):
                    if reflection:
                        st.markdown(f"**Reflection:** {reflection}")
                    if reasoning:
                        st.markdown(f"**Reasoning:** {reasoning}")
                    if thought.get("plan"):
                        st.markdown(f"**Plan:** {', '.join(thought.get('plan'))}")

            # Execution Result / Error details
            if exec_res:
                outcome_msg = exec_res.get("message") or ""
                err_msg = exec_res.get("error")
                dur_ms = exec_res.get("duration_ms", 0)

                dur_str = f" ({dur_ms:.0f}ms)" if dur_ms else ""
                if not is_success and err_msg:
                    st.error(f"Error{dur_str}: {err_msg}")
                elif outcome_msg:
                    st.caption(f"Outcome{dur_str}: {outcome_msg}")

            st.markdown("</div>", unsafe_allow_html=True)


def render_event_fallback_timeline(events: List[Dict[str, Any]]) -> None:
    """Render events from /events if full StepRecord history is absent."""
    action_events = [ev for ev in events if ev.get("type") in ("action_executed", "safety_alert", "status_change")]
    if not action_events:
        st.info("No recent action telemetry captured.")
        return

    for idx, ev in enumerate(action_events, 1):
        action_name = format_action_type(ev.get("action") or ev.get("type"))
        is_success = ev.get("success", False)
        status_color = "#16a34a" if is_success else "#dc2626"
        if ev.get("type") == "safety_alert":
            status_color = "#d97706"

        badge_color = ACTION_COLORS.get(action_name, "#2563eb")
        ts = format_timestamp(ev.get("timestamp"))

        st.markdown(
            f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid {badge_color}; border-radius: 4px; padding: 0.6rem 0.8rem; margin-bottom: 0.5rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="font-weight: 600; color: {badge_color}; font-size: 0.8rem;">{action_name}</span>
                        <span style="color: #475569; font-size: 0.75rem; margin-left: 0.5rem;">{ev.get('message', '')}</span>
                    </div>
                    <span style="color: #64748b; font-size: 0.7rem; font-family: monospace;">{ts}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
