"""Agent status monitor and Human-in-the-Loop approval panel."""

from typing import Any, Dict, Optional
import streamlit as st

try:
    from services.backend_client import BackendClient
    from utils.formatting import format_duration, get_status_badge
except (ImportError, ValueError):
    from frontend.services.backend_client import BackendClient
    from frontend.utils.formatting import format_duration, get_status_badge


def render_agent_status(
    state: Optional[Dict[str, Any]],
    is_running: bool,
    backend_client: BackendClient,
) -> None:
    """Render the high-level agent status bar, metrics, and HITL approval panel."""
    status_str = (state.get("status") if state else ("running" if is_running else "idle")) or "idle"
    label, color, icon = get_status_badge(status_str)

    current_step = state.get("current_step", 0) if state else 0
    max_steps = state.get("max_steps", 15) if state else 15
    start_time = state.get("start_time") if state else None
    end_time = state.get("end_time") if state else None
    elapsed = format_duration(start_time, end_time)

    # 1. Main Status Header Cards
    col1, col2, col3, col4 = st.columns([1.5, 1, 1, 1.5])

    with col1:
        st.markdown(
            f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 4px solid {color}; border-radius: 4px; padding: 0.6rem 0.8rem;">
                <div style="font-size: 0.75rem; color: #64748b; text-transform: uppercase; font-weight: 600;">Status</div>
                <div style="font-size: 1.05rem; font-weight: 700; color: {color}; margin-top: 0.15rem;">
                    {icon} {label}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0.6rem 0.8rem;">
                <div style="font-size: 0.75rem; color: #64748b; text-transform: uppercase; font-weight: 600;">Step Progress</div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #0f172a; margin-top: 0.15rem;">
                    {current_step} <span style="font-size: 0.8rem; color: #64748b;">/ {max_steps}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0.6rem 0.8rem;">
                <div style="font-size: 0.75rem; color: #64748b; text-transform: uppercase; font-weight: 600;">Elapsed Time</div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #0f172a; margin-top: 0.15rem;">
                    {elapsed}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col4:
        run_id = state.get("run_id", "idle") if state else "idle"
        short_id = run_id[:8] if run_id and run_id != "idle" else "None"
        st.markdown(
            f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0.6rem 0.8rem;">
                <div style="font-size: 0.75rem; color: #64748b; text-transform: uppercase; font-weight: 600;">Run Reference</div>
                <div style="font-size: 0.95rem; font-weight: 600; color: #334155; margin-top: 0.15rem;">
                    <code>{short_id}</code>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. Progress Bar
    if max_steps > 0:
        progress_val = min(max(current_step / max_steps, 0.0), 1.0)
        st.progress(progress_val)

    # 3. Human-in-the-Loop (HITL) Approval Panel
    # Triggered when status is awaiting_confirmation or safety flagged an action
    if status_str.lower() in ("awaiting_confirmation", "awaiting_approval"):
        render_hitl_panel(state)


def render_hitl_panel(state: Optional[Dict[str, Any]]) -> None:
    """Render Human-in-the-Loop confirmation interface or a clear 'Not Configured' status."""
    st.markdown(
        """
        <div style="background: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #d97706; border-radius: 4px; padding: 0.85rem 1rem; margin-top: 0.75rem; margin-bottom: 0.75rem;">
            <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
                <strong style="color: #92400e; font-size: 0.95rem;">Action Intercepted by SafetyGuard — Human Approval Required</strong>
            </div>
            <div style="font-size: 0.85rem; color: #78350f; line-height: 1.4;">
                The proposed action targets a potentially destructive operation (e.g., delete, purge, drop).
                The agent has safely paused execution before touching the DOM.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Inspect the last step record for action details
    history = state.get("history", []) if state else []
    last_record = history[-1] if history else {}
    action_info = last_record.get("action", {})
    safety_info = last_record.get("safety_check", {})

    with st.expander("Intercepted Action Details", expanded=True):
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"**Action Type:** `{action_info.get('action_type', 'UNKNOWN')}`")
            st.markdown(f"**Target Element:** `{action_info.get('selector') or action_info.get('target', 'None')}`")
            st.markdown(f"**Description:** {action_info.get('description', '-')}")
        with col_b:
            st.markdown(f"**Risk Level:** `{safety_info.get('risk_level', 'HIGH')}`")
            st.markdown(f"**Policy Trigger:** `{safety_info.get('flagged_pattern', safety_info.get('reason', 'Destructive keyword detected'))}`")
            st.markdown(f"**Requires Confirmation:** `{safety_info.get('requires_human_confirmation', True)}`")

        st.info(
            "**HITL Interface Status**: Interactive backend approval endpoint is **Not Configured**. "
            "The execution layer safely halted progression to guarantee system integrity."
        )

        col_act1, col_act2, _ = st.columns([1, 1, 3])
        with col_act1:
            st.button("Approve (Not Configured)", disabled=True, use_container_width=True, help="Backend approval endpoint not implemented")
        with col_act2:
            st.button("Reject (Not Configured)", disabled=True, use_container_width=True, help="Backend approval endpoint not implemented")
