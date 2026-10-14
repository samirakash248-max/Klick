"""Page observation inspector and post-run summary components."""

from typing import Any, Dict, List, Optional
import streamlit as st

try:
    from utils.formatting import format_duration, get_status_badge, safe_json_dumps
except (ImportError, ValueError):
    from frontend.utils.formatting import format_duration, get_status_badge, safe_json_dumps


def render_page_observation(obs: Optional[Dict[str, Any]]) -> None:
    """Render the latest structured browser observation from PlaywrightObserver."""
    st.subheader("Current Page Observation")

    if not obs:
        st.info("No active page observation available. Ensure the backend browser is running.")
        return

    url = obs.get("url", "about:blank")
    title = obs.get("title", "(No Page Title)")
    elements = obs.get("elements", [])
    snippet = obs.get("page_text_snippet") or obs.get("snippet") or ""

    # 1. URL & Title Card
    st.markdown(
        f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.75rem;">
            <div style="font-size: 0.75rem; color: #64748b; font-weight: 600; text-transform: uppercase;">Active Browser Target</div>
            <div style="font-weight: 700; font-size: 1rem; color: #0f172a; margin-top: 0.15rem;">{title}</div>
            <div style="font-size: 0.8rem; color: #2563eb; font-family: monospace; word-break: break-all; margin-top: 0.2rem;">{url}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 2. Live Visual Viewport with Set-of-Mark Grounding
    clean_b64 = obs.get("screenshot_base64")
    som_b64 = obs.get("annotated_screenshot_base64")
    if clean_b64 or som_b64:
        st.markdown("**Visual Viewport & Set-of-Mark Grounding**")
        view_mode = st.radio(
            "Visual Mode",
            ["Set-of-Mark Overlay [0..N]", "Clean Viewport"],
            horizontal=True,
            label_visibility="collapsed",
            key="som_view_mode",
        )
        display_b64 = som_b64 if (view_mode.startswith("Set-of-Mark") and som_b64) else (clean_b64 or som_b64)
        if display_b64:
            st.image(
                f"data:image/jpeg;base64,{display_b64}",
                use_column_width=True,
                caption=f"Live Viewport ({obs.get('visual_elements_count', len(elements))} visually grounded elements)",
            )

    # 3. Interactive Elements Table / View
    st.markdown(f"**Interactive Elements ({len(elements)} detected)**")
    if elements:
        # Format elements into clean records
        formatted_elements: List[Dict[str, Any]] = []
        for el in elements:
            agent_id = el.get("agent_id") or el.get("id") or "-"
            tag = el.get("tag") or el.get("tag_name") or "-"
            role = el.get("role") or "-"
            text = (el.get("text") or el.get("aria_label") or "").strip()
            if len(text) > 40:
                text = text[:37] + "..."
            el_type = el.get("type") or "-"

            formatted_elements.append({
                "Target ID": agent_id,
                "Tag": tag,
                "Role": role,
                "Text": text or "-",
                "Input Type": el_type,
            })

        st.dataframe(
            formatted_elements,
            use_container_width=True,
            height=240,
            column_config={
                "Target ID": st.column_config.TextColumn(width="medium"),
                "Text": st.column_config.TextColumn(width="medium"),
            },
        )
    else:
        st.caption("No interactive elements currently exposed on this view.")

    # 3. Relevant Page Text Snippet (Never render raw HTML)
    if snippet:
        with st.expander("Page Text Snippet", expanded=False):
            st.text(snippet)

    # 4. Expandable Raw JSON View
    with st.expander("Observation Payload (JSON)", expanded=False):
        st.code(safe_json_dumps(obs), language="json")


def render_run_summary(state: Optional[Dict[str, Any]]) -> None:
    """Render comprehensive post-run analysis after task completion or termination."""
    if not state:
        return

    status = state.get("status", "idle").lower()
    if status in ("idle", "initializing", "running"):
        return  # Only render summary when run has finished or paused

    st.divider()
    st.subheader("Run Execution Summary")

    label, color, icon = get_status_badge(status)
    history = state.get("history", [])
    total_steps = len(history)

    success_count = sum(
        1 for h in history
        if h.get("execution_result", {}).get("success") is True
    )
    fail_count = sum(
        1 for h in history
        if h.get("execution_result", {}).get("success") is False
    )

    elapsed = format_duration(state.get("start_time"), state.get("end_time"))

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Final Status", f"{icon} {label}")
    with col2:
        st.metric("Steps Executed", f"{total_steps} / {state.get('max_steps', 15)}")
    with col3:
        st.metric("Success / Fail", f"{success_count} / {fail_count}")
    with col4:
        st.metric("Total Duration", elapsed)

    # Final Outcome / Output / Error
    final_output = state.get("final_output")
    error = state.get("error")

    if status == "completed":
        st.success(f"**Task Completed:** {final_output or 'Goal achieved successfully.'}")
    elif status == "failed":
        st.error(f"**Task Failed:** {error or 'Execution encountered unrecoverable failure.'}")
    elif status == "stopped":
        st.warning(f"**Task Stopped:** Halting requested by user or safety policy.")
    elif status == "awaiting_confirmation":
        st.warning(f"**Task Paused:** Awaiting human confirmation due to destructive operation.")
