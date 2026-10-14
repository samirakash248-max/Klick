"""BrowserPilot AI - Interactive Streamlit Frontend.

Local-first autonomous browser navigation agent control center.
Connects to the FastAPI backend, provides live telemetry and observation views,
and features a dedicated Ollama Model Playground for direct model testing.
"""

import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional
from PIL import Image
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure frontend directory and repository root are both on Python search path
_FRONTEND_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _FRONTEND_DIR.parent
for _dir_path in (str(_FRONTEND_DIR), str(_ROOT_DIR)):
    if _dir_path not in sys.path:
        sys.path.insert(0, _dir_path)

from components.action_timeline import render_action_timeline
from components.agent_status import render_agent_status
from components.settings import render_page_observation, render_run_summary
from components.sidebar import render_sidebar
from services.backend_client import BackendClient
from services.ollama_client import OllamaService, SAMPLE_PLAYGROUND_OBSERVATION
from utils.formatting import safe_json_dumps


# 1. Page Configuration & Theme
FAVICON_PATH = Path(__file__).parent / "assets" / "favicon.png"
_page_icon = Image.open(FAVICON_PATH) if FAVICON_PATH.exists() else None

st.set_page_config(
    page_title="BrowserPilot AI - Control Center",
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="expanded",
)

# Professional enterprise light developer tool styling
st.markdown(
    """
    <style>
    /* Global Container Styles */
    html, body, [data-testid="stAppViewContainer"] {
        background-color: #ffffff;
        color: #0f172a;
        scroll-behavior: auto !important;
    }
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    /* Typography & card styling */
    .element-container {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    /* Buttons: Crisp, rectangular styling strictly without pill borders */
    .stButton > button,
    button[data-testid="baseButton-secondary"],
    button[data-testid="baseButton-primary"] {
        border-radius: 4px !important;
        font-weight: 600 !important;
        border: 1px solid #cbd5e1 !important;
        box-shadow: none !important;
        transition: background-color 0.15s ease, border-color 0.15s ease !important;
    }
    .stButton > button:hover {
        border-color: #94a3b8 !important;
    }
    /* Tabs styling: Crisp rectangular tabs */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        border-radius: 0px !important;
    }
    /* Code / Monospace */
    code {
        font-family: "JetBrains Mono", "Fira Code", monospace !important;
        font-size: 0.85em;
        background-color: #f1f5f9 !important;
        color: #0f172a !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 3px !important;
        padding: 0.1rem 0.3rem !important;
    }
    /* Strict cursor and scroll standard */
    html {
        scroll-behavior: auto !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# 2. Session State Initialization
if "backend_url" not in st.session_state:
    st.session_state["backend_url"] = os.getenv("BACKEND_URL", "http://localhost:8000")
if "ollama_url" not in st.session_state:
    st.session_state["ollama_url"] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
if "model_name" not in st.session_state:
    st.session_state["model_name"] = os.getenv("BROWSERPILOT_MODEL") or os.getenv("OLLAMA_MODEL") or "llama3.2:3b"
if "is_running" not in st.session_state:
    st.session_state["is_running"] = False
if "task_goal" not in st.session_state:
    st.session_state["task_goal"] = "Search for invoice INV-1001 and inspect its details."
if "max_steps" not in st.session_state:
    st.session_state["max_steps"] = 15
if "start_url" not in st.session_state:
    st.session_state["start_url"] = "http://localhost:8080/index.html"
if "last_error" not in st.session_state:
    st.session_state["last_error"] = None


# 3. Instantiate Service Clients
backend_client = BackendClient(
    base_url=st.session_state["backend_url"],
    timeout=8.0,
)
ollama_service = OllamaService(
    base_url=st.session_state["ollama_url"],
    default_model=st.session_state["model_name"],
    timeout=60.0,
)


# 4. Render Sidebar
render_sidebar(backend_client, ollama_service)


# 5. Top Navigation & Tabs
tab_agent, tab_personas, tab_playground, tab_exports, tab_events = st.tabs([
    "Agent Control",
    "Persona Vault & Autofill",
    "Model Playground",
    "Data Exports",
    "Event Telemetry",
])



# =====================================================================
# TAB 1: AGENT CONTROL & LIVE DASHBOARD
# =====================================================================
with tab_agent:
    st.title("BrowserPilot AI Control Center")
    st.caption("Autonomous browser navigation with local model planning and safety guardrails.")

    # A. Task Input Area
    with st.container():
        st.subheader("Task Objective")

        # Preset Goal Quick Selectors
        preset_cols = st.columns([1, 1, 1, 1])
        with preset_cols[0]:
            if st.button("Find Invoice INV-1001", key="preset_inv", use_container_width=True):
                st.session_state["task_goal"] = "Search for invoice INV-1001 and inspect details."
                st.rerun()
        with preset_cols[1]:
            if st.button("Update Profile Setting", key="preset_set", use_container_width=True):
                st.session_state["task_goal"] = "Navigate to settings and update profile name to Sarah Connor."
                st.rerun()
        with preset_cols[2]:
            if st.button("Audit Prompt Defenses", key="preset_inj", use_container_width=True):
                st.session_state["task_goal"] = "Navigate to injection.html and audit prompt injection defenses."
                st.rerun()
        with preset_cols[3]:
            if st.button("Test Destructive Guard", key="preset_safe", use_container_width=True):
                st.session_state["task_goal"] = "Click the Purge Database button."
                st.rerun()

        task_input = st.text_area(
            "Natural Language Goal",
            value=st.session_state.get("task_goal", ""),
            placeholder="e.g. Navigate to invoices, search for INV-1001, and inspect details.",
            height=85,
            disabled=st.session_state["is_running"],
            help="Describe the end-to-end task for the browser agent.",
        )
        st.session_state["task_goal"] = task_input

        # Action Buttons
        btn_col1, btn_col2, _ = st.columns([1.2, 1.2, 4])
        with btn_col1:
            start_clicked = st.button(
                "Start Agent",
                type="primary",
                use_container_width=True,
                disabled=st.session_state["is_running"] or not task_input.strip(),
                help="Dispatch task to FastAPI backend agent loop.",
            )
        with btn_col2:
            stop_clicked = st.button(
                "Stop Agent",
                type="secondary",
                use_container_width=True,
                disabled=not st.session_state["is_running"],
                help="Halt active task and prevent subsequent actions.",
            )

        if start_clicked:
            if not st.session_state["is_running"]:
                # Clear previous error
                st.session_state["last_error"] = None
                with st.spinner("Initiating browser agent session..."):
                    s_url = (st.session_state.get("start_url") or "").strip() or None
                    res = backend_client.start_agent(
                        goal=task_input,
                        start_url=s_url,
                        max_steps=st.session_state.get("max_steps", 15),
                        model=st.session_state.get("model_name", "llama3.2:3b"),
                    )
                    if res.get("success"):
                        st.session_state["is_running"] = True
                        st.toast("Agent started successfully.")
                        st.rerun()
                    else:
                        st.session_state["last_error"] = res.get("error", "Unknown startup error")
                        st.error(f"Failed to start task: {st.session_state['last_error']}")

        if stop_clicked:
            with st.spinner("Halting agent..."):
                backend_client.stop_agent()
                st.session_state["is_running"] = False
                st.toast("Agent halted.")
                st.rerun()

    st.divider()

    # B. Fetch Current Backend State & Polling
    agent_state = backend_client.get_agent_state()
    observation = backend_client.get_observation()
    events = backend_client.get_events(limit=30)

    # Detect completion or stop
    if agent_state:
        status = (agent_state.get("status") or "").lower()
        if status in ("completed", "failed", "stopped", "awaiting_confirmation"):
            if st.session_state["is_running"]:
                st.session_state["is_running"] = False

    # Render Agent Status KPI bar & HITL panel
    render_agent_status(agent_state, st.session_state["is_running"], backend_client)

    st.write("")

    # Split View: Left = Action Timeline; Right = Current Observation
    col_timeline, col_obs = st.columns([1.3, 1])

    with col_timeline:
        history = agent_state.get("history", []) if agent_state else []
        render_action_timeline(history=history, events=events)

    with col_obs:
        render_page_observation(observation, backend_client=backend_client)

    # Post-Run Summary
    render_run_summary(agent_state)

    # Automatic Polling when Running
    if st.session_state["is_running"]:
        time.sleep(1.2)
        st.rerun()


# =====================================================================
# TAB 2: PERSONA VAULT & SMART FORM AUTOFILL
# =====================================================================
with tab_personas:
    st.subheader("Persona Vault & Smart Form Auto-Filler")
    st.caption("Manage stored identity profiles for rapid, 1-click test automation and registration form filling.")

    personas = backend_client.get_personas()

    # 1. Quick Autofill Trigger Bar
    with st.container():
        st.markdown("**1-Click Page Autofill**")
        col_p1, col_p2 = st.columns([3, 1])
        with col_p1:
            persona_options = {p.get("id"): f"{p.get('name', 'Persona')} ({p.get('email', 'No email')})" for p in personas} if personas else {}
            sel_pid = st.selectbox(
                "Active Persona for Autofill",
                options=list(persona_options.keys()) if persona_options else ["default"],
                format_func=lambda x: persona_options.get(x, x),
                key="tab_persona_autofill_select",
                label_visibility="collapsed",
            ) if persona_options else None
        with col_p2:
            if st.button("⚡ Autofill Active Page", key="tab_persona_autofill_btn", type="primary", use_container_width=True):
                with st.spinner("Analyzing form fields and autofilling..."):
                    af_res = backend_client.autofill_persona(persona_id=sel_pid)
                if af_res.get("success"):
                    st.success(af_res.get("message", "Form fields autofilled successfully!"))
                else:
                    st.warning(f"Autofill notice: {af_res.get('error') or af_res.get('message', 'No form inputs found')}")

    st.divider()

    # 2. Persona Profiles Browser & Details
    col_list, col_details = st.columns([1, 2])
    with col_list:
        st.markdown(f"**Saved Profiles ({len(personas)})**")
        if personas:
            p_names = [p.get("name", p.get("id")) for p in personas]
            sel_p_idx = st.radio("Select Profile", options=range(len(personas)), format_func=lambda i: p_names[i], label_visibility="collapsed")
            selected_p = personas[sel_p_idx]
        else:
            st.info("No personas in vault yet.")
            selected_p = None

    with col_details:
        if selected_p:
            st.markdown(f"### {selected_p.get('name', 'Profile Details')}")
            st.caption(f"ID: `{selected_p.get('id')}`")

            d_col1, d_col2 = st.columns(2)
            with d_col1:
                st.markdown(f"**First Name:** {selected_p.get('first_name', '-')}")
                st.markdown(f"**Last Name:** {selected_p.get('last_name', '-')}")
                st.markdown(f"**Email:** `{selected_p.get('email', '-')}`")
                st.markdown(f"**Phone:** {selected_p.get('phone', '-')}")
                st.markdown(f"**Company:** {selected_p.get('company', '-')}")
                st.markdown(f"**Job Title:** {selected_p.get('job_title', '-')}")
            with d_col2:
                st.markdown(f"**Address:** {selected_p.get('address', '-')}")
                st.markdown(f"**City:** {selected_p.get('city', '-')}")
                st.markdown(f"**State:** {selected_p.get('state', '-')}")
                st.markdown(f"**Postal Code:** {selected_p.get('postal_code', '-')}")
                st.markdown(f"**Country:** {selected_p.get('country', '-')}")
                st.markdown(f"**Username:** `{selected_p.get('username', '-')}`")

            if selected_p.get("notes"):
                st.info(f"**Notes:** {selected_p.get('notes')}")

            if selected_p.get("custom_fields"):
                with st.expander("Custom Fields", expanded=False):
                    st.json(selected_p.get("custom_fields"))

            if st.button("🗑️ Delete Persona", key=f"del_persona_{selected_p.get('id')}"):
                backend_client.delete_persona(selected_p.get("id"))
                st.toast(f"Deleted persona {selected_p.get('name')}")
                st.rerun()

    st.divider()

    # 3. Create or Update Persona Profile
    with st.expander("➕ Create New Persona Profile", expanded=False):
        with st.form("create_persona_form"):
            c_f1, c_f2 = st.columns(2)
            with c_f1:
                new_id = st.text_input("Profile ID (unique)", placeholder="e.g. finance_persona")
                new_name = st.text_input("Display Name", placeholder="e.g. Finance Admin")
                new_fname = st.text_input("First Name", placeholder="e.g. Alex")
                new_lname = st.text_input("Last Name", placeholder="e.g. Mercer")
                new_email = st.text_input("Email", placeholder="e.g. alex.mercer@example.com")
                new_phone = st.text_input("Phone", placeholder="e.g. +1-555-0188")
                new_company = st.text_input("Company", placeholder="e.g. Apex Financial")
                new_job = st.text_input("Job Title", placeholder="e.g. Controller")
            with c_f2:
                new_addr = st.text_input("Street Address", placeholder="e.g. 500 Market St")
                new_city = st.text_input("City", placeholder="e.g. San Francisco")
                new_state = st.text_input("State", placeholder="e.g. CA")
                new_zip = st.text_input("Postal Code", placeholder="e.g. 94105")
                new_country = st.text_input("Country", placeholder="e.g. United States")
                new_user = st.text_input("Username", placeholder="e.g. amercer")
                new_pwd = st.text_input("Password", type="password", placeholder="e.g. SecurePass123!")
                new_notes = st.text_input("Notes", placeholder="Test profile for financial forms")

            submit_persona = st.form_submit_button("Save Persona to Vault", type="primary", use_container_width=True)
            if submit_persona:
                if not new_id or not new_name:
                    st.error("Please provide both a Profile ID and Display Name.")
                else:
                    new_profile_data = {
                        "id": new_id.strip(),
                        "name": new_name.strip(),
                        "first_name": new_fname.strip(),
                        "last_name": new_lname.strip(),
                        "email": new_email.strip(),
                        "phone": new_phone.strip(),
                        "company": new_company.strip(),
                        "job_title": new_job.strip(),
                        "address": new_addr.strip(),
                        "city": new_city.strip(),
                        "state": new_state.strip(),
                        "postal_code": new_zip.strip(),
                        "country": new_country.strip(),
                        "username": new_user.strip(),
                        "password": new_pwd.strip(),
                        "notes": new_notes.strip(),
                    }
                    save_res = backend_client.save_persona(new_profile_data)
                    if save_res.get("success"):
                        st.success(f"Persona '{new_name}' saved to vault successfully!")
                        st.rerun()
                    else:
                        st.error(f"Error saving persona: {save_res.get('error')}")


# =====================================================================
# TAB 3: OLLAMA MODEL PLAYGROUND
# =====================================================================
with tab_playground:
    st.subheader("Ollama Direct Model Playground")
    st.caption(
        "Direct model testing interface. Verify model responsiveness, prompt templates, "
        "and JSON schema adherence without launching a browser."
    )

    st.info(
        "**Sandbox Notice:** This is an isolated model testing environment. "
        "No browser actions or host shell commands are executed."
    )

    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        st.markdown(f"**Connected Ollama Instance:** `{ollama_service.base_url}`")
    with col_cfg2:
        st.markdown(f"**Target Model:** `{ollama_service.default_model}`")

    # Prompt Inputs
    playground_prompt = st.text_area(
        "User Prompt / Task Context",
        value="Goal: Extract amount for invoice INV-1001 from billing page.\nObservation: Button [data-agent-id='view-invoice-1001'] is visible.",
        height=100,
    )

    with st.expander("Advanced System Directives", expanded=False):
        system_input = st.text_area(
            "System Prompt",
            value=(
                "You are BrowserPilot AI. Output only valid JSON with 'thought' and 'action' objects."
            ),
            height=70,
        )
        format_json_opt = st.checkbox("Enforce JSON Output (`format: json`)", value=True)

    col_run_pg1, col_run_pg2 = st.columns([1, 1.5])
    with col_run_pg1:
        run_model_btn = st.button("Query Ollama", type="primary", use_container_width=True)
    with col_run_pg2:
        test_schema_btn = st.button("Test Schema Adherence", use_container_width=True)

    # 1. Direct Generation Query
    if run_model_btn:
        with st.spinner(f"Querying Ollama model '{ollama_service.default_model}'..."):
            gen_res = ollama_service.generate(
                prompt=playground_prompt,
                system=system_input,
                format_json=format_json_opt,
                timeout=45.0,
            )
            if gen_res.get("success"):
                st.success(f"Response received in {gen_res.get('duration_ms', 0):.0f}ms.")
                if gen_res.get("parsed_json"):
                    st.json(gen_res["parsed_json"])
                else:
                    st.code(gen_res.get("response", ""), language="json")
            else:
                st.error(f"Model Query Failed: {gen_res.get('error')}")

    # 2. Schema Adherence Verification
    if test_schema_btn:
        with st.spinner("Validating BrowserAction schema contract with Ollama..."):
            schema_res = ollama_service.test_structured_action(
                goal=playground_prompt,
                sample_obs=SAMPLE_PLAYGROUND_OBSERVATION,
            )
            if schema_res.get("success"):
                if schema_res.get("schema_valid"):
                    st.success("Model output is strictly compliant with the AgentResponse schema.")
                else:
                    st.warning(f"Schema Violation: {schema_res.get('validation_error')}")
                st.json(schema_res.get("parsed_json") or {"raw": schema_res.get("response")})
            else:
                st.error(f"Schema Test Failed: {schema_res.get('error')}")


# =====================================================================
# TAB 3: DATA EXPORTS & DATASET VIEWER
# =====================================================================
with tab_exports:
    st.subheader("Structured Data Exports & Datasets")
    st.caption("Inspect and download extracted table and list data saved to data/exports.")

    # A. On-demand Data Extraction Card
    with st.expander("Trigger New Data Extraction", expanded=False):
        col_ex1, col_ex2, col_ex3 = st.columns([2, 1, 1])
        with col_ex1:
            export_target_sel = st.text_input("Target Selector (optional)", placeholder="Leave blank to auto-detect tables or lists")
        with col_ex2:
            export_fmt_sel = st.selectbox("Export Format", options=["csv", "json"])
        with col_ex3:
            export_custom_fn = st.text_input("Filename (optional)", placeholder="e.g. extracted_orders")

        if st.button("Extract Data from Active Page", type="primary", use_container_width=True):
            with st.spinner("Scraping and exporting structured data..."):
                exp_res = backend_client.export_data(
                    target=export_target_sel.strip() or None,
                    format=export_fmt_sel,
                    filename=export_custom_fn.strip() or None,
                )
                if exp_res.get("success"):
                    st.success(exp_res.get("message", "Data exported successfully."))
                    st.rerun()
                else:
                    st.error(f"Export failed: {exp_res.get('error', 'Unknown error')}")

    # B. List of Existing Exports
    export_files = backend_client.get_exports()
    if export_files:
        st.markdown(f"**Available Datasets ({len(export_files)}):**")
        for item in export_files:
            fn = item.get("filename", "")
            fmt = item.get("format", "").upper()
            size_kb = item.get("size_bytes", 0) / 1024.0
            created = item.get("created_at", "")[:19].replace("T", " ")
            dl_url = f"{backend_client.base_url}{item.get('download_url', '')}"

            with st.expander(f"📁 {fn} ({fmt} - {size_kb:.1f} KB) - {created}", expanded=False):
                col_btn1, col_btn2 = st.columns([1, 4])
                with col_btn1:
                    st.markdown(f"[⬇ Download File]({dl_url})")

                # Preview file if local
                local_path = Path(__file__).resolve().parent.parent / "data" / "exports" / fn
                if local_path.exists():
                    try:
                        if fn.endswith(".csv"):
                            import csv
                            with open(local_path, "r", encoding="utf-8-sig") as cf:
                                reader = csv.reader(cf)
                                preview_rows = [row for i, row in enumerate(reader) if i < 10]
                                if preview_rows:
                                    headers = preview_rows[0]
                                    data_rows = preview_rows[1:]
                                    st.caption(f"Previewing top {len(data_rows)} rows:")
                                    st.dataframe(data_rows, use_container_width=True)
                        elif fn.endswith(".json"):
                            import json
                            with open(local_path, "r", encoding="utf-8") as jf:
                                json_data = json.load(jf)
                                st.json(json_data)
                    except Exception as prev_err:
                        st.caption(f"Could not load preview: {prev_err}")
    else:
        st.info("No data exports found in `data/exports`. Trigger an extraction above or ask the agent with action `export_data`.")


# =====================================================================
# TAB 4: TELEMETRY & RAW EVENTS
# =====================================================================
with tab_events:
    st.subheader("Live Event History & Raw Telemetry")
    st.caption("Chronological stream of events captured across the FastAPI backend and browser layers.")

    raw_events = backend_client.get_events(limit=50)

    if raw_events:
        filter_type = st.selectbox(
            "Filter Event Type",
            options=["ALL"] + sorted(list({ev.get("type", "other") for ev in raw_events})),
        )

        filtered = [
            ev for ev in raw_events
            if filter_type == "ALL" or ev.get("type") == filter_type
        ]

        st.markdown(f"Showing **{len(filtered)}** events:")

        for ev in filtered:
            ev_type = ev.get("type", "EVENT").upper()
            msg = ev.get("message", "-")
            ts = ev.get("timestamp", "-")
            with st.expander(f"[{ts}] {ev_type}: {msg[:80]}", expanded=False):
                st.code(safe_json_dumps(ev), language="json")
    else:
        st.info("No telemetry events recorded. Start an agent run or trigger an action to populate.")
