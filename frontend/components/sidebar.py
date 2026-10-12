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

            # Profile Clear Option
            if st.button("Clear Profile & Cookies", key="sidebar_clear_prof_btn", use_container_width=True):
                backend_client.clear_profile()
                st.toast("Browser profile session cache and cookies cleared.")
                st.rerun()

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
                # If current model in list, use selectbox
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
