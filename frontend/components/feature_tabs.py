"""Feature Tab components for BrowserPilot Streamlit Dashboard.

Modularized rendering routines for:
1. Persona Vault & Autofill
2. Recurring Automations & Windows Toast Alerts
3. Visual Diff & Webpage Change Monitoring
4. Bidirectional Voice & Speech Synthesis (TTS)
5. Automated PDF & Document Extraction Engine
6. Ollama Model Playground
7. Structured Data Exports & Datasets
8. Telemetry & Raw Events Stream
"""

from __future__ import annotations

import base64
import csv
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import streamlit as st


# =====================================================================
# 1. PERSONA VAULT & SMART FORM AUTO-FILLER TAB
# =====================================================================
def render_persona_tab(backend_client: Any) -> None:
    """Render the Persona Vault and Form Autofill management tab."""
    st.subheader("Persona Vault & Smart Form Auto-Filler")
    st.caption("Manage saved user personas and securely autofill web forms, checkout flows, and test data.")

    if hasattr(backend_client, "get_personas"):
        personas = backend_client.get_personas()
    elif hasattr(backend_client, "list_personas"):
        personas = backend_client.list_personas()
    else:
        personas = []

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
    col_p1, col_p2 = st.columns([1, 1])

    with col_p1:
        st.markdown("### Active Persona Profiles")
        if personas:
            p_names = [f"{p.get('name', 'Unnamed')} ({p.get('id')})" for p in personas]
            selected_idx = st.selectbox(
                "Select Profile to Inspect / Edit",
                range(len(personas)),
                format_func=lambda i: p_names[i],
            )
            selected_p = personas[selected_idx]
        else:
            st.info("No personas in vault yet. Create your first profile below.")
            selected_p = None

    with col_p2:
        st.markdown("### Profile Details")
        if selected_p:
            st.markdown(f"**ID:** `{selected_p.get('id')}` | **Name:** `{selected_p.get('name')}`")
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.text_input("First Name", value=selected_p.get("first_name", ""), disabled=True, key=f"pv_fn_{selected_p.get('id')}")
                st.text_input("Email", value=selected_p.get("email", ""), disabled=True, key=f"pv_em_{selected_p.get('id')}")
                st.text_input("Company", value=selected_p.get("company", ""), disabled=True, key=f"pv_co_{selected_p.get('id')}")
                st.text_input("City", value=selected_p.get("city", ""), disabled=True, key=f"pv_ct_{selected_p.get('id')}")
            with col_d2:
                st.text_input("Last Name", value=selected_p.get("last_name", ""), disabled=True, key=f"pv_ln_{selected_p.get('id')}")
                st.text_input("Phone", value=selected_p.get("phone", ""), disabled=True, key=f"pv_ph_{selected_p.get('id')}")
                st.text_input("Job Title", value=selected_p.get("job_title", ""), disabled=True, key=f"pv_jt_{selected_p.get('id')}")
                st.text_input("Country", value=selected_p.get("country", ""), disabled=True, key=f"pv_cn_{selected_p.get('id')}")

            if selected_p.get("custom_fields"):
                with st.expander("Custom Fields", expanded=False):
                    st.json(selected_p.get("custom_fields"))

            if st.button("🗑️ Delete Persona", key=f"del_persona_{selected_p.get('id')}"):
                backend_client.delete_persona(selected_p.get("id"))
                st.toast(f"Deleted persona {selected_p.get('name')}")
                st.rerun()

    st.divider()

    # Create or Update Persona Profile
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
# 2. RECURRING AUTOMATIONS & DESKTOP TOAST ALERTS TAB
# =====================================================================
def render_scheduler_tab(backend_client: Any) -> None:
    """Render the Automation Scheduler and Windows Toast Alerts tab."""
    st.subheader("⏰ Recurring Background Automations & Desktop Toast Alerts")
    st.caption("Autonomous scheduled monitoring with native Windows desktop alerts upon completion or price changes.")

    col_toast_test1, col_toast_test2 = st.columns([3, 1])
    with col_toast_test1:
        toast_sample_text = st.text_input("Desktop Toast Alert Test Message", value="Klick: Price drop detected on monitor ($299.99)!")
    with col_toast_test2:
        st.write("")
        st.write("")
        if st.button("🔔 Send Test Toast", type="primary", use_container_width=True):
            with st.spinner("Dispatching toast..."):
                t_res = backend_client.test_windows_toast(
                    title="Klick Browser Agent",
                    message=toast_sample_text,
                )
                if t_res.get("success"):
                    st.success("Native Windows desktop notification dispatched!")
                else:
                    st.warning(f"Toast response: {t_res.get('message', 'Dispatched')}")

    st.markdown("---")
    st.markdown("### Configured Automation Schedules")
    schedules = backend_client.list_schedules()

    if schedules:
        for sch in schedules:
            sid = sch.get("id")
            sname = sch.get("name")
            stype = str(sch.get("schedule_type") or "interval").upper()
            interval = sch.get("interval_seconds", 3600)
            status = sch.get("last_status") or "idle"
            last_run = sch.get("last_run") or "Never"
            next_run = sch.get("next_run") or "-"
            status_color = "🟢" if status == "success" else ("🔴" if status == "failed" else "⚪")

            with st.expander(f"{status_color} **{sname}** ({stype} every {interval}s) - Status: {status.upper()}", expanded=True):
                col_s1, col_s2, col_s3 = st.columns([2, 2, 2])
                with col_s1:
                    st.markdown(f"**Task Type:** `{sch.get('task_type')}`")
                    st.markdown(f"**Interval:** `{interval} seconds`")
                    st.markdown(f"**Desktop Toast:** `{'Enabled' if sch.get('notify_on_complete') else 'Disabled'}`")
                with col_s2:
                    st.markdown(f"**Last Run:** `{last_run}`")
                    st.markdown(f"**Next Run:** `{next_run}`")
                    st.markdown(f"**Times Run:** `{sch.get('run_count', 0)}`")
                with col_s3:
                    if st.button("▶ Run Now", key=f"run_sch_{sid}", use_container_width=True):
                        with st.spinner(f"Running '{sname}'..."):
                            run_res = backend_client.run_schedule_now(sid)
                            if run_res.get("success"):
                                st.success(f"Executed: {run_res.get('message')}")
                                st.rerun()
                            else:
                                st.error(f"Failed: {run_res.get('error') or run_res.get('message')}")
                    if st.button("🗑 Delete", key=f"del_sch_{sid}", use_container_width=True):
                        backend_client.delete_schedule(sid)
                        st.rerun()

                if sch.get("last_message"):
                    st.info(f"**Last Outcome:** {sch.get('last_message')}")
    else:
        st.info("No recurring schedules configured yet.")

    with st.expander("➕ Create New Automation Schedule", expanded=False):
        col_ns1, col_ns2 = st.columns(2)
        with col_ns1:
            new_sch_name = st.text_input("Schedule Name", value="Periodic Price & Task Check")
            new_sch_type = st.selectbox("Task Action Type", options=["macro", "goal", "url_check"])
            new_sch_interval = st.number_input("Interval (seconds)", min_value=10, value=300, step=30)
        with col_ns2:
            new_sch_target = st.text_input("Target Goal / URL / Macro ID", value="Check http://localhost:8080/index.html")
            new_sch_notify = st.checkbox("Send Windows Toast Alert on Complete", value=True)
            new_sch_on_change = st.checkbox("Notify Only on Change", value=False)

        if st.button("Save & Register Schedule", type="primary"):
            payload = {
                "name": new_sch_name,
                "schedule_type": "interval",
                "interval_seconds": int(new_sch_interval),
                "task_type": new_sch_type,
                "payload": {"goal": new_sch_target, "url": new_sch_target},
                "notify_on_complete": new_sch_notify,
                "notify_on_change_only": new_sch_on_change,
            }
            res = backend_client.create_schedule(payload)
            if "id" in res:
                st.success(f"Created schedule '{new_sch_name}'!")
                st.rerun()
            else:
                st.error(f"Error: {res.get('error')}")


# =====================================================================
# 3. VISUAL DIFF & WEBPAGE CHANGE MONITORING TAB
# =====================================================================
def render_visual_diff_tab(backend_client: Any) -> None:
    """Render the Visual Diff and Webpage Monitoring tab."""
    st.subheader("🔍 Visual Diff & Webpage Change Monitoring")
    st.caption("Perceptual and pixel-level comparison engine. Detects price shifts, altered UI components, and layout changes.")

    baselines = backend_client.list_visual_baselines()

    col_vb1, col_vb2 = st.columns([2, 1])
    with col_vb1:
        new_base_id = st.text_input("Baseline ID", value="homepage_baseline")
        new_base_label = st.text_input("Label / Description", value="Mock Site Homepage Baseline")
    with col_vb2:
        st.write("")
        st.write("")
        if st.button("📸 Capture Active Page as Baseline", type="primary", use_container_width=True):
            with st.spinner("Capturing baseline..."):
                c_res = backend_client.save_visual_baseline(new_base_id, label=new_base_label)
                if "id" in c_res:
                    st.success(f"Saved baseline '{new_base_id}'!")
                    st.rerun()
                else:
                    st.error(f"Failed to capture: {c_res.get('error')}")

    st.markdown("---")
    st.markdown("### Compare Active Browser Page with Baseline")

    if baselines:
        selected_base = st.selectbox(
            "Select Baseline to Compare",
            options=[b["id"] for b in baselines],
            format_func=lambda bid: f"{bid} ({next((b.get('label') for b in baselines if b['id'] == bid), '')})",
        )
        diff_threshold = st.slider("Change Sensitivity Threshold (%)", min_value=0.5, max_value=20.0, value=2.0, step=0.5)

        if st.button("🔍 Run Visual Diff Analysis", type="primary"):
            with st.spinner("Comparing current page against baseline..."):
                diff_res = backend_client.check_visual_diff(selected_base, threshold=diff_threshold / 100.0)
                if "similarity_score" in diff_res:
                    st.session_state["last_diff_res"] = diff_res
                else:
                    st.error(f"Diff error: {diff_res.get('error')}")

        if "last_diff_res" in st.session_state:
            d_res = st.session_state["last_diff_res"]
            sim = d_res.get("similarity_score", 1.0)
            chg = d_res.get("change_percentage", 0.0)
            has_chg = d_res.get("has_changed", False)
            regions = d_res.get("changed_regions_count", 0)

            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            with col_m1:
                st.metric("Similarity Score", f"{sim * 100.0:.1f}%")
            with col_m2:
                st.metric("Change Magnitude", f"{chg:.2f}%", delta=f"{'+' if has_chg else ''}{chg:.2f}%")
            with col_m3:
                st.metric("Changed Regions", str(regions))
            with col_m4:
                st.metric("Verdict", "ALTERED" if has_chg else "UNCHANGED")

            diff_img_b64 = d_res.get("diff_image_base64")
            if diff_img_b64:
                st.markdown("#### Visual Difference Overlay (Red: Altered Pixels, Green: Bounding Regions)")
                st.image(f"data:image/jpeg;base64,{diff_img_b64}", use_container_width=True)
    else:
        st.info("No visual baselines captured yet. Click 'Capture Active Page as Baseline' above to establish your reference snapshot.")


# =====================================================================
# 4. BIDIRECTIONAL VOICE & SPEECH SYNTHESIS (TTS) TAB
# =====================================================================
def render_voice_tts_tab(backend_client: Any) -> None:
    """Render the Voice Text-to-Speech synthesis and testing tab."""
    st.subheader("🗣️ Bidirectional Voice & Speech Synthesis (TTS)")
    st.caption("Agent speaks back status updates aloud using offline Windows SAPI5 / pyttsx3 or browser Web Speech.")

    tts_stat = backend_client.get_tts_status()
    voices = tts_stat.get("available_voices", [])

    col_v1, col_v2 = st.columns(2)
    with col_v1:
        st.markdown(f"**Speech Engine:** `{tts_stat.get('engine', 'sapi5_windows')}`")
        st.markdown(f"**Synthesizer Status:** `{'Enabled' if tts_stat.get('enabled') else 'Disabled'}`")
    with col_v2:
        voice_opts = [v["id"] for v in voices] if voices else ["default"]
        selected_v = st.selectbox(
            "Selected Voice",
            options=voice_opts,
            format_func=lambda vid: next((v.get("name", vid) for v in voices if v["id"] == vid), vid),
        )

    st.markdown("---")
    st.markdown("### Interactive Voice Test")
    sample_tts_text = st.text_area(
        "Text to Speak Aloud",
        value="Task completed successfully: Scraped 15 tasks to CSV and found invoice INV-1001.",
        height=75,
    )

    col_spk1, col_spk2 = st.columns(2)
    with col_spk1:
        if st.button("📢 Speak Aloud (Desktop Speakers)", type="primary", use_container_width=True):
            with st.spinner("Speaking..."):
                backend_client.speak_tts(sample_tts_text, voice_id=selected_v)
                st.success("Dispatched to Windows speech audio device!")
    with col_spk2:
        if st.button("🎵 Synthesize & Play in Browser", use_container_width=True):
            with st.spinner("Synthesizing WAV audio..."):
                wav_b64 = backend_client.synthesize_tts_wav(sample_tts_text, voice_id=selected_v)
                if wav_b64:
                    st.audio(f"data:audio/wav;base64,{wav_b64}", format="audio/wav")
                else:
                    st.error("Failed to generate WAV audio.")


# =====================================================================
# 5. AUTOMATED PDF & DOCUMENT EXTRACTION ENGINE TAB
# =====================================================================
def render_document_extractor_tab(backend_client: Any) -> None:
    """Render the Automated PDF and Document Extraction tab."""
    st.subheader("📄 Automated PDF & Document Extraction Engine")
    st.caption("Auto-detects and extracts tabular data, totals, invoice IDs, dates, and contacts from downloaded or uploaded documents.")

    col_doc1, col_doc2 = st.columns([2, 1])
    with col_doc1:
        uploaded_doc = st.file_uploader("Upload PDF, CSV, or Invoice Document to Parse", type=["pdf", "csv", "tsv", "json", "txt"])
    with col_doc2:
        st.write("")
        st.write("")
        if st.button("⚡ Parse Last Browser Download", type="primary", use_container_width=True):
            with st.spinner("Extracting from latest download..."):
                p_res = backend_client.parse_latest_download()
                if "filename" in p_res:
                    st.session_state["last_doc_res"] = p_res
                    st.success(f"Parsed latest download '{p_res.get('filename')}'!")
                else:
                    st.info(p_res.get("error", "No downloads found."))

    if uploaded_doc is not None:
        if st.button("📑 Extract Uploaded Document", type="primary"):
            with st.spinner(f"Extracting {uploaded_doc.name}..."):
                doc_bytes = uploaded_doc.read()
                doc_b64 = base64.b64encode(doc_bytes).decode("utf-8")
                e_res = backend_client.extract_document(file_base64=doc_b64, filename=uploaded_doc.name)
                if "filename" in e_res:
                    st.session_state["last_doc_res"] = e_res
                    st.success(f"Successfully extracted {uploaded_doc.name}!")
                else:
                    st.error(f"Extraction failed: {e_res.get('error')}")

    if "last_doc_res" in st.session_state:
        doc = st.session_state["last_doc_res"]
        st.markdown("---")
        st.markdown(f"### Extraction Results: `{doc.get('filename')}`")

        c_meta1, c_meta2, c_meta3 = st.columns(3)
        with c_meta1:
            st.metric("Format", doc.get("file_type", "").upper())
        with c_meta2:
            st.metric("Pages", str(doc.get("page_count", 1)))
        with c_meta3:
            st.metric("Total Characters", f"{doc.get('total_characters', 0):,}")

        # Entities Card
        entities = doc.get("entities", {})
        if entities:
            st.markdown("#### Detected Business Entities")
            col_ent1, col_ent2, col_ent3 = st.columns(3)
            with col_ent1:
                totals = entities.get("currency_amounts", [])
                st.markdown(f"**Amounts / Totals:** {', '.join(totals) if totals else 'None'}")
            with col_ent2:
                invs = entities.get("invoice_numbers", [])
                st.markdown(f"**Invoice / Order IDs:** {', '.join(invs) if invs else 'None'}")
            with col_ent3:
                dates = entities.get("dates", [])
                st.markdown(f"**Dates:** {', '.join(dates) if dates else 'None'}")

        # Tables
        tables = doc.get("tables", [])
        if tables:
            st.markdown(f"#### Extracted Tables ({len(tables)})")
            for i, tbl in enumerate(tables):
                headers = tbl.get("headers", [])
                rows = tbl.get("rows", [])
                if rows:
                    import pandas as pd
                    df = pd.DataFrame(rows, columns=headers if headers and len(headers) == len(rows[0]) else None)
                    st.dataframe(df, use_container_width=True)
        else:
            with st.expander("Plain Text Preview", expanded=False):
                st.text(doc.get("text_content", "")[:1500])

    # Downloads directory inventory
    downloads_list = backend_client.list_downloaded_documents()
    if downloads_list:
        with st.expander(f"📁 Captured Browser Downloads ({len(downloads_list)} files)", expanded=False):
            for d in downloads_list:
                st.markdown(f"- **{d['filename']}** ({d['size_bytes'] / 1024:.1f} KB) - `{d['modified'][:19].replace('T', ' ')}`")


# =====================================================================
# 6. OLLAMA MODEL PLAYGROUND TAB
# =====================================================================
def render_playground_tab(ollama_service: Any, sample_observation: Dict[str, Any]) -> None:
    """Render the Ollama Direct Model Playground tab."""
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
            value="You are BrowserPilot AI. Output only valid JSON with 'thought' and 'action' objects.",
            height=70,
        )
        format_json_opt = st.checkbox("Enforce JSON Output (`format: json`)", value=True)

    col_run_pg1, col_run_pg2 = st.columns([1, 1.5])
    with col_run_pg1:
        run_model_btn = st.button("Query Ollama", type="primary", use_container_width=True)
    with col_run_pg2:
        test_schema_btn = st.button("Test Schema Adherence", use_container_width=True)

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

    if test_schema_btn:
        with st.spinner("Validating BrowserAction schema contract with Ollama..."):
            schema_res = ollama_service.test_structured_action(
                goal=playground_prompt,
                sample_obs=sample_observation,
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
# 7. STRUCTURED DATA EXPORTS & DATASET VIEWER TAB
# =====================================================================
def render_data_exports_tab(backend_client: Any) -> None:
    """Render the Structured Data Exports and Datasets tab."""
    st.subheader("Structured Data Exports & Datasets")
    st.caption("Inspect and download extracted table and list data saved to data/exports.")

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
                col_btn1, _ = st.columns([1, 4])
                with col_btn1:
                    st.markdown(f"[⬇ Download File]({dl_url})")

                local_path = Path(__file__).resolve().parent.parent.parent / "data" / "exports" / fn
                if local_path.exists():
                    try:
                        if fn.endswith(".csv"):
                            with open(local_path, "r", encoding="utf-8-sig") as cf:
                                reader = csv.reader(cf)
                                preview_rows = [row for i, row in enumerate(reader) if i < 10]
                                if preview_rows:
                                    data_rows = preview_rows[1:]
                                    st.caption(f"Previewing top {len(data_rows)} rows:")
                                    st.dataframe(data_rows, use_container_width=True)
                        elif fn.endswith(".json"):
                            with open(local_path, "r", encoding="utf-8") as jf:
                                json_data = json.load(jf)
                                st.json(json_data)
                    except Exception as prev_err:
                        st.caption(f"Could not load preview: {prev_err}")
    else:
        st.info("No data exports found in `data/exports`. Trigger an extraction above or ask the agent with action `export_data`.")


# =====================================================================
# 8. TELEMETRY & RAW EVENTS STREAM TAB
# =====================================================================
def render_telemetry_tab(backend_client: Any, safe_json_dumps: Callable[[Any], str]) -> None:
    """Render the Telemetry and Raw Event Stream tab."""
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
