"""Sidebar navigation, service connectivity monitor, and configuration controls."""

import streamlit as st
from typing import Optional
try:
    from services.backend_client import BackendClient
    from services.ollama_client import OllamaService
except (ImportError, ValueError):
    from frontend.services.backend_client import BackendClient
    from frontend.services.ollama_client import OllamaService


def render_sidebar(backend_client: BackendClient, ollama_service: OllamaService) -> None:
    """Render the sidebar with connection badges, settings, and stop controls."""
    with st.sidebar:
        # 1. Branding Header
        st.markdown(
            """
            <div style="margin-bottom: 1.25rem;">
                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.25rem;">
                    <h2 style="margin: 0; font-size: 1.35rem; font-weight: 700; color: #0f172a;">BrowserPilot AI</h2>
                </div>
                <div style="font-size: 0.8rem; color: #64748b; line-height: 1.4;">
                    Local-first autonomous browser navigation agent powered by Ollama and Playwright.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.divider()

        # 2. Service Connectivity Monitoring
        st.subheader("Service Status")

        # Backend Health
        backend_health = backend_client.check_health()
        if backend_health.get("online"):
            st.markdown(
                """
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid #16a34a; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: #16a34a;">Backend Online</span>
                        <span style="font-size: 0.75rem; color: #64748b;">FastAPI</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            err_msg = backend_health.get("error", "Offline")
            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid #dc2626; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: #dc2626;">Backend Offline</span>
                    </div>
                    <div style="font-size: 0.72rem; color: #b91c1c; margin-top: 0.25rem;">{err_msg}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # Ollama Health
        ollama_health = ollama_service.check_health()
        if ollama_health.get("online"):
            model_info = "Ready" if ollama_health.get("model_present") else "Model not found"
            badge_color = "#16a34a" if ollama_health.get("model_present") else "#d97706"
            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid {badge_color}; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: {badge_color};">Ollama Connected</span>
                        <span style="font-size: 0.75rem; color: #64748b;">{len(ollama_health.get('models', []))} models</span>
                    </div>
                    <div style="font-size: 0.72rem; color: #64748b; margin-top: 0.2rem;">Model: <code>{ollama_service.default_model}</code> ({model_info})</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid #d97706; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.85rem; color: #d97706;">Ollama Unreachable</span>
                    </div>
                    <div style="font-size: 0.72rem; color: #b45309; margin-top: 0.25rem;">{ollama_health.get('error', '')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # 3. Browser Profile & Multi-Tab Management
        if backend_health.get("online"):
            st.subheader("Session & Tabs")
            prof_status = backend_client.get_profile_status()
            tabs_data = backend_client.get_tabs()
            open_tabs = tabs_data.get("tabs", [])
            tab_count = len(open_tabs)
            active_idx = tabs_data.get("active_index", 0)

            # Profile Badge
            if prof_status.get("persistent_profile"):
                c_count = prof_status.get("saved_cookie_count", 0)
                st.markdown(
                    f"""
                    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid #3b82f6; border-radius: 4px; padding: 0.5rem 0.75rem; margin-bottom: 0.5rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-weight: 600; font-size: 0.85rem; color: #1e40af;">Persistent Profile</span>
                            <span style="font-size: 0.75rem; color: #64748b;">{c_count} cookies</span>
                        </div>
                        <div style="font-size: 0.72rem; color: #64748b; margin-top: 0.2rem;">Logins & session state retained</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            
            # Tabs Viewer / Controller
            if tab_count > 1:
                st.markdown(
                    f"""
                    <div style="font-size: 0.8rem; font-weight: 600; color: #475569; margin-bottom: 0.25rem;">
                        Open Tabs ({tab_count})
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                tab_options = [f"#{t['index']} - {t['title'][:28]}" for t in open_tabs]
                sel_tab_label = st.selectbox(
                    "Select Tab",
                    options=tab_options,
                    index=min(active_idx, len(tab_options) - 1),
                    key="sidebar_sel_tab",
                    label_visibility="collapsed",
                )
                sel_idx = int(sel_tab_label.split(" - ")[0].replace("#", "")) if sel_tab_label else 0

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Focus Tab", key="sidebar_focus_tab_btn", use_container_width=True):
                        backend_client.switch_tab(tab_index=sel_idx)
                        st.toast(f"Switched focus to Tab #{sel_idx}")
                        st.rerun()
                with c2:
                    if st.button("Close Tab", key="sidebar_close_tab_btn", use_container_width=True):
                        backend_client.close_tab(tab_index=sel_idx)
                        st.toast(f"Closed Tab #{sel_idx}")
                        st.rerun()

            # Browser Window Visibility & Focus Controls
            c_win1, c_win2 = st.columns(2)
            with c_win1:
                if st.button("🖥️ Show Browser", key="sidebar_show_browser_btn", use_container_width=True):
                    backend_client.focus_browser()
                    st.toast("Brought browser window to front!")
            with c_win2:
                if st.button("🌐 Open in Chrome", key="sidebar_open_sys_btn", use_container_width=True):
                    backend_client.open_in_system_browser()
                    st.toast("Opened current tab in system browser!")

            # Direct Chrome DevTools Protocol (CDP) Bridge
            cdp_info = backend_client.get_cdp_status()
            cdp_connected = cdp_info.get("connected", False)
            cdp_avail = cdp_info.get("available", False)
            cdp_color = "#16a34a" if cdp_connected else ("#3b82f6" if cdp_avail else "#64748b")
            cdp_label = "CDP Connected" if cdp_connected else ("Chrome CDP Ready" if cdp_avail else "CDP Bridge (:9222)")

            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 3px solid {cdp_color}; border-radius: 4px; padding: 0.4rem 0.6rem; margin-top: 0.5rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.8rem; color: {cdp_color};">{cdp_label}</span>
                        <span style="font-size: 0.72rem; color: #64748b;">Port 9222</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if not cdp_connected:
                if st.button("🔗 Connect Chrome via CDP", key="sidebar_connect_cdp_btn", use_container_width=True):
                    res = backend_client.connect_cdp()
                    if res.get("success"):
                        st.toast("Connected to existing Chrome instance via CDP!")
                    else:
                        st.warning(f"Could not connect: {res.get('error', 'Is Chrome running with --remote-debugging-port=9222?')}")
                    st.rerun()

            # Profile Clear Option
            if st.button("Clear Profile & Cookies", key="sidebar_clear_prof_btn", use_container_width=True):
                backend_client.clear_profile()
                st.toast("Browser profile session cache and cookies cleared.")
                st.rerun()

        # 4. Deterministic RPA & Macros Panel
        with st.expander("Deterministic Macros & RPA", expanded=False):
            st.markdown(
                """
                <div style="font-size: 0.8rem; color: #64748b; margin-bottom: 0.5rem;">
                    Replay workflows deterministically with 0ms AI inference latency.
                </div>
                """,
                unsafe_allow_html=True,
            )

            # "Watch & Learn" Interactive Macro Recorder
            rec_status = backend_client.get_recorder_status()
            is_rec = rec_status.get("is_recording", False)
            step_cnt = rec_status.get("step_count", 0)

            st.markdown(
                f"""
                <div style="background: {'#fef2f2' if is_rec else '#f8fafc'}; border: 1px solid {'#f87171' if is_rec else '#e2e8f0'}; border-radius: 4px; padding: 0.45rem 0.6rem; margin-bottom: 0.5rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 0.8rem; color: {'#dc2626' if is_rec else '#475569'};">
                            {'🔴 Recording Macro...' if is_rec else 'Interactive RPA Recorder'}
                        </span>
                        <span style="font-size: 0.72rem; color: #64748b;">{step_cnt} steps</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if is_rec:
                if st.button("⏹ Stop & Save Macro", key="sidebar_stop_rec_btn", use_container_width=True, type="primary"):
                    saved_m = backend_client.stop_recording()
                    if saved_m.get("id"):
                        st.toast(f"Saved recorded macro '{saved_m.get('name')}' ({saved_m.get('step_count')} steps)!")
                    st.rerun()
            else:
                with st.popover("⏺ Start Interactive Recording"):
                    live_rec_name = st.text_input("Macro Name", placeholder="e.g. Fill Checkout Form", key="pop_live_rec_name")
                    live_rec_desc = st.text_input("Description", placeholder="User recorded workflow", key="pop_live_rec_desc")
                    if st.button("Begin Recording", key="pop_begin_rec_btn", use_container_width=True):
                        backend_client.start_recording(name=live_rec_name or "Interactive Macro", description=live_rec_desc or "")
                        st.toast("Recording started! Interact with browser or extension.")
                        st.rerun()

            macros = backend_client.get_macros()
            if macros:
                macro_names = {m.get("id"): f"{m.get('name', 'Macro')} ({len(m.get('steps', []))} steps)" for m in macros}
                sel_macro_id = st.selectbox(
                    "Select Macro",
                    options=list(macro_names.keys()),
                    format_func=lambda x: macro_names.get(x, x),
                    key="sidebar_macro_select",
                )
                selected_macro = next((m for m in macros if m.get("id") == sel_macro_id), None)
                if selected_macro:
                    st.caption(selected_macro.get("description", ""))
                    params = dict(selected_macro.get("default_params", {}))
                    param_keys = selected_macro.get("parameters", [])
                    user_params = {}
                    for p in param_keys:
                        user_params[p] = st.text_input(
                            f"Parameter: {p}",
                            value=str(params.get(p, "")),
                            key=f"macro_param_{sel_macro_id}_{p}",
                        )

                    fallback_ai = st.checkbox("AI Fallback if UI shifted", value=True, key="macro_fallback_cb")

                    if st.button("⚡ Replay Deterministic Macro", key="sidebar_replay_macro_btn", use_container_width=True, type="primary"):
                        with st.spinner("Replaying macro actions..."):
                            replay_res = backend_client.replay_macro(
                                macro_id=sel_macro_id,
                                parameters=user_params,
                                fallback_to_agent=fallback_ai,
                            )
                        if replay_res.get("success"):
                            st.success(f"Macro completed ({replay_res.get('steps_executed')}/{replay_res.get('total_steps')} steps in {replay_res.get('duration_ms')}ms)")
                        else:
                            st.warning(f"Replay paused: {replay_res.get('message')}")
            else:
                st.caption("No macros found.")

            with st.popover("Save Last Run as Macro"):
                rec_name = st.text_input("Macro Name", placeholder="e.g. Daily Search", key="pop_macro_name")
                rec_desc = st.text_input("Description", placeholder="e.g. Search and play video", key="pop_macro_desc")
                rec_query = st.text_input("Parameterize String (Optional)", placeholder="e.g. casetoo", key="pop_macro_query")
                if st.button("Save Macro", key="pop_macro_save_btn", use_container_width=True):
                    if rec_name:
                        rec_res = backend_client.record_macro_from_run(
                            name=rec_name,
                            description=rec_desc,
                            parameterize_query=rec_query,
                        )
                        if rec_res.get("id"):
                            st.toast(f"Saved macro '{rec_name}' successfully!")
                            st.rerun()
                        else:
                            st.error(rec_res.get("error", "Failed to save macro"))

        # 4. Active Run Flow Control
        is_running = st.session_state.get("is_running", False)
        if is_running:
            st.markdown(
                """
                <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 0.6rem; margin-bottom: 0.75rem; text-align: center;">
                    <div style="font-size: 0.85rem; font-weight: 600; color: #0f172a;">Agent Task Active</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Stop Agent Run", key="sidebar_stop_btn", use_container_width=True, type="primary"):
                backend_client.stop_agent()
                st.session_state["is_running"] = False
                st.session_state["stop_requested"] = True
                st.toast("Stop signal dispatched to backend.")
                st.rerun()

        st.divider()

        # 5. Settings & Endpoint Overrides
        with st.expander("Configuration & Endpoints", expanded=False):

            new_backend_url = st.text_input(
                "Backend URL",
                value=st.session_state.get("backend_url", backend_client.base_url),
                help="Base URL for the FastAPI backend server.",
            )
            if new_backend_url != st.session_state.get("backend_url"):
                st.session_state["backend_url"] = new_backend_url
                backend_client.base_url = new_backend_url.rstrip("/")

            new_ollama_url = st.text_input(
                "Ollama Base URL",
                value=st.session_state.get("ollama_url", ollama_service.base_url),
                help="Base URL for the local Ollama instance.",
            )
            if new_ollama_url != st.session_state.get("ollama_url"):
                st.session_state["ollama_url"] = new_ollama_url
                ollama_service.base_url = new_ollama_url.rstrip("/")

            available_models = ollama_health.get("models", [])
            current_model = st.session_state.get("model_name", ollama_service.default_model)
            if available_models:
                default_idx = available_models.index(current_model) if current_model in available_models else 0
                selected_model = st.selectbox(
                    "Model Name",
                    options=available_models,
                    index=default_idx,
                    help="Configured model loaded in local Ollama.",
                )
            else:
                selected_model = st.text_input(
                    "Model Name",
                    value=current_model,
                    help="Model tag to request from Ollama.",
                )

            if selected_model != st.session_state.get("model_name"):
                st.session_state["model_name"] = selected_model
                ollama_service.default_model = selected_model

            # Hybrid Model Routing Tier Selector
            routing_data = backend_client.get_model_routes()
            active_tier = routing_data.get("active_tier", "auto")
            tier_options = ["auto", "local_fast", "cloud_turbo"]
            tier_labels = {
                "auto": "Auto Hybrid (Local -> Cloud Fallback)",
                "local_fast": "Local Fast (Ollama On-Device)",
                "cloud_turbo": "Cloud Turbo (Groq / OpenAI / Gemini)",
            }
            sel_tier = st.selectbox(
                "Model Routing Tier",
                options=tier_options,
                index=tier_options.index(active_tier) if active_tier in tier_options else 0,
                format_func=lambda x: tier_labels.get(x, x),
                help="Switch between local inference, cloud turbo reasoning, or auto-fallback hybrid.",
            )
            if sel_tier != active_tier:
                backend_client.set_model_route(sel_tier)
                st.toast(f"Model route switched to {sel_tier}")
                st.rerun()

            st.session_state["max_steps"] = st.slider(
                "Max Steps per Task",
                min_value=3,
                max_value=30,
                value=st.session_state.get("max_steps", 15),
                help="Upper safety limit on cognitive steps before halting.",
            )

            st.session_state["start_url"] = st.text_input(
                "Start URL (Optional)",
                value=st.session_state.get("start_url", ""),
                placeholder="Auto-detected from goal (or enter starting URL)",
                help="Initial browser destination for the task.",
            )

        # 5. Quick Refresh Button
        if st.button("Refresh Status", key="sidebar_refresh_btn", use_container_width=True):
            st.rerun()
