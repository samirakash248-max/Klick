"""FastAPI backend client service for BrowserPilot AI Streamlit frontend.

Handles:
1. Health and connection checking.
2. Agent lifecycle controls: start, stop, resume.
3. State and observation polling.
4. Event history and action execution.
"""

import os
from typing import Any, Dict, List, Optional
import httpx


class BackendClientError(Exception):
    """Base exception for backend client errors."""


class BackendConnectionError(BackendClientError):
    """Raised when the backend server cannot be reached."""


class BackendClient:
    """Synchronous HTTP client for the BrowserPilot FastAPI backend."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 8.0) -> None:
        raw_url = base_url or os.getenv("BACKEND_URL") or "http://localhost:8000"
        self.base_url = raw_url.rstrip("/")
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def check_health(self) -> Dict[str, Any]:
        """Check server connectivity and readiness."""
        try:
            with self._client() as client:
                res = client.get("/api/health")
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "online": True,
                        "status": data.get("status", "healthy"),
                        "service": data.get("service", "BrowserPilot AI Backend"),
                        "agent_running": data.get("agent_running", False),
                    }
                # Fallback to /health
                res_fallback = client.get("/health")
                if res_fallback.status_code == 200:
                    return {
                        "online": True,
                        "status": "ok",
                        "service": "BrowserPilot AI Backend",
                        "agent_running": False,
                    }
                return {
                    "online": False,
                    "error": f"Server returned status {res.status_code}",
                }
        except httpx.ConnectError:
            return {"online": False, "error": f"Connection refused at {self.base_url}"}
        except httpx.TimeoutException:
            return {"online": False, "error": f"Health check timed out after {self.timeout}s"}
        except Exception as exc:
            return {"online": False, "error": str(exc)}

    def start_agent(
        self,
        goal: str,
        start_url: Optional[str] = None,
        max_steps: int = 15,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a task to start an autonomous agent run."""
        payload = {
            "goal": goal.strip(),
            "start_url": start_url.strip() if start_url else None,
            "max_steps": max_steps,
            "model": model.strip() if model else None,
        }
        try:
            with self._client() as client:
                res = client.post("/api/agent/start", json=payload)
                if res.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Failed to start task (HTTP {res.status_code}): {res.text}",
                    }
                data = res.json()
                if "error" in data:
                    return {"success": False, "error": data["error"]}
                return {"success": True, **data}
        except (httpx.ConnectError, httpx.RequestError) as exc:
            return {"success": False, "error": f"Connection failed: {exc}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def stop_agent(self) -> Dict[str, Any]:
        """Request the active agent run to stop and block subsequent actions."""
        results = {}
        try:
            with self._client() as client:
                # Stop autonomous loop
                res1 = client.post("/api/agent/stop")
                results["agent_stop"] = res1.json() if res1.status_code == 200 else res1.text
                # Stop execution layer
                res2 = client.post("/stop")
                results["executor_stop"] = res2.json() if res2.status_code == 200 else res2.text
                return {"success": True, "details": results}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def resume_agent(self) -> Dict[str, Any]:
        """Resume execution layer after stop."""
        try:
            with self._client() as client:
                res = client.post("/resume")
                if res.status_code == 200:
                    return {"success": True, **res.json()}
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_agent_state(self) -> Optional[Dict[str, Any]]:
        """Fetch current agent run state and history."""
        try:
            with self._client() as client:
                res = client.get("/api/agent/state")
                if res.status_code == 200:
                    return res.json()
                return None
        except Exception:
            return None

    def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch chronological activity events from the backend."""
        try:
            with self._client() as client:
                res = client.get(f"/events?limit={limit}")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def get_observation(self) -> Dict[str, Any]:
        """Fetch the latest structured browser observation."""
        try:
            with self._client() as client:
                res = client.get("/observe")
                if res.status_code == 200:
                    return res.json()
                return {"url": "", "title": "", "elements": [], "error": res.text}
        except Exception as exc:
            return {"url": "", "title": "", "elements": [], "error": str(exc)}

    def execute_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a single action to the /execute endpoint."""
        try:
            with self._client() as client:
                res = client.post("/execute", json=payload)
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": f"HTTP {res.status_code}: {res.text}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_tabs(self) -> Dict[str, Any]:
        """Fetch all currently open browser tabs and active index."""
        try:
            with self._client() as client:
                res = client.get("/api/tabs")
                if res.status_code == 200:
                    return res.json()
                return {"tabs": [], "count": 0, "active_index": 0}
        except Exception:
            return {"tabs": [], "count": 0, "active_index": 0}

    def switch_tab(self, tab_index: Optional[int] = None, target: Optional[str] = None) -> Dict[str, Any]:
        """Switch active browser tab focus by index or title."""
        try:
            with self._client() as client:
                res = client.post("/api/tabs/switch", json={"tab_index": tab_index, "target": target})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def new_tab(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Open a new browser tab with optional URL."""
        try:
            with self._client() as client:
                res = client.post("/api/tabs/new", json={"url": url})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def close_tab(self, tab_index: Optional[int] = None, target: Optional[str] = None) -> Dict[str, Any]:
        """Close an open browser tab."""
        try:
            with self._client() as client:
                res = client.post("/api/tabs/close", json={"tab_index": tab_index, "target": target})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_profile_status(self) -> Dict[str, Any]:
        """Fetch browser profile persistence configuration and cookie backup status."""
        try:
            with self._client() as client:
                res = client.get("/api/profile/status")
                if res.status_code == 200:
                    return res.json()
                return {"persistent_profile": False, "cookies_saved": False}
        except Exception:
            return {"persistent_profile": False, "cookies_saved": False}

    def clear_profile(self) -> Dict[str, Any]:
        """Clear cookies and reset persistent profile session cache."""
        try:
            with self._client() as client:
                res = client.post("/api/profile/clear")
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # DETERMINISTIC RPA MACROS
    # =========================================================================

    def get_macros(self) -> List[Dict[str, Any]]:
        """Fetch all available deterministic RPA macros."""
        try:
            with self._client() as client:
                res = client.get("/api/macros")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def get_macro(self, macro_id: str) -> Optional[Dict[str, Any]]:
        """Fetch specific macro by ID."""
        try:
            with self._client() as client:
                res = client.get(f"/api/macros/{macro_id}")
                if res.status_code == 200:
                    return res.json()
                return None
        except Exception:
            return None

    def save_macro(self, macro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Save or update a macro definition."""
        try:
            with self._client() as client:
                res = client.post("/api/macros", json=macro_data)
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def delete_macro(self, macro_id: str) -> bool:
        """Delete a macro by ID."""
        try:
            with self._client() as client:
                res = client.delete(f"/api/macros/{macro_id}")
                return res.status_code == 200
        except Exception:
            return False

    def record_macro_from_run(
        self,
        name: str,
        description: str = "",
        parameterize_query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record the current or last active session into a deterministic macro."""
        try:
            with self._client() as client:
                res = client.post(
                    "/api/macros/record-from-run",
                    json={
                        "name": name,
                        "description": description,
                        "parameterize_query": parameterize_query,
                    },
                )
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def replay_macro(
        self,
        macro_id: str,
        parameters: Optional[Dict[str, Any]] = None,
        fallback_to_agent: bool = True,
    ) -> Dict[str, Any]:
        """Deterministically replay a macro with 0ms AI inference latency."""
        try:
            with self._client() as client:
                res = client.post(
                    "/api/macros/replay",
                    json={
                        "macro_id": macro_id,
                        "parameters": parameters or {},
                        "fallback_to_agent": fallback_to_agent,
                    },
                    timeout=60.0,
                )
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # STRUCTURED DATA EXPORTS
    # =========================================================================

    def get_exports(self) -> List[Dict[str, Any]]:
        """Fetch list of all structured data exports."""
        try:
            with self._client() as client:
                res = client.get("/api/exports")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def export_data(
        self,
        target: Optional[str] = None,
        format: str = "csv",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Trigger structured table or list data export on active page."""
        try:
            with self._client() as client:
                res = client.post(
                    "/api/export",
                    json={"target": target, "format": format, "filename": filename},
                )
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # HYBRID MODEL ROUTING
    # =========================================================================

    def get_model_routes(self) -> Dict[str, Any]:
        """Fetch model routing configuration, active tier, and latency telemetry."""
        try:
            with self._client() as client:
                res = client.get("/api/models/routes")
                if res.status_code == 200:
                    return res.json()
                return {"active_tier": "auto", "tiers": []}
        except Exception:
            return {"active_tier": "auto", "tiers": []}

    def set_model_route(self, tier: str) -> Dict[str, Any]:
        """Switch active model routing tier ('local_fast', 'cloud_turbo', 'auto')."""
        try:
            with self._client() as client:
                res = client.post("/api/models/route", json={"tier": tier})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # VISUAL GROUNDING & SET-OF-MARK INSPECTION
    # =========================================================================

    def get_visual_screenshot(self) -> Dict[str, Any]:
        """Fetch latest clean viewport screenshot."""
        try:
            with self._client() as client:
                res = client.get("/api/visual/screenshot")
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def get_visual_annotated(self) -> Dict[str, Any]:
        """Fetch latest Set-of-Mark annotated viewport screenshot."""
        try:
            with self._client() as client:
                res = client.get("/api/visual/annotated")
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def inspect_visual(self) -> Dict[str, Any]:
        """Perform comprehensive visual inspection with element bounding boxes."""
        try:
            with self._client() as client:
                res = client.post("/api/visual/inspect", json={})
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    # =========================================================================
    # BROWSER WINDOW FOCUS & SYSTEM INTERACTION
    # =========================================================================

    def focus_browser(self) -> Dict[str, Any]:
        """Bring the visible Chromium browser window to front on desktop."""
        try:
            with self._client() as client:
                res = client.post("/api/browser/focus", json={})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def open_in_system_browser(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Open current tab URL in the user's default desktop browser."""
        try:
            with self._client() as client:
                res = client.post("/api/browser/open-in-system", json={"url": url} if url else {})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # PERSONA VAULT & SMART FORM AUTOFILL
    # =========================================================================

    def get_personas(self) -> List[Dict[str, Any]]:
        """Retrieve all saved personas from vault."""
        try:
            with self._client() as client:
                res = client.get("/api/personas")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single persona profile."""
        try:
            with self._client() as client:
                res = client.get(f"/api/personas/{persona_id}")
                if res.status_code == 200:
                    return res.json()
                return None
        except Exception:
            return None

    def save_persona(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Save or update persona in vault."""
        try:
            with self._client() as client:
                res = client.post("/api/personas", json=data)
                if res.status_code == 200:
                    return {"success": True, "persona": res.json()}
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def delete_persona(self, persona_id: str) -> Dict[str, Any]:
        """Delete persona from vault."""
        try:
            with self._client() as client:
                res = client.delete(f"/api/personas/{persona_id}")
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def autofill_persona(self, persona_id: Optional[str] = None, custom_values: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Trigger smart autofill on active page."""
        try:
            with self._client() as client:
                res = client.post("/api/personas/autofill", json={"persona_id": persona_id, "custom_values": custom_values or {}})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # CHROME CDP BRIDGE
    # =========================================================================

    def get_cdp_status(self) -> Dict[str, Any]:
        """Get CDP Chrome connection status."""
        try:
            with self._client() as client:
                res = client.get("/api/cdp/status")
                if res.status_code == 200:
                    return res.json()
                return {"available": False, "error": res.text}
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def connect_cdp(self, cdp_url: Optional[str] = None) -> Dict[str, Any]:
        """Connect directly to active Chrome via CDP."""
        try:
            with self._client() as client:
                res = client.post("/api/cdp/connect", json={"cdp_url": cdp_url or "http://localhost:9222"})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # "WATCH & LEARN" MACRO RECORDER
    # =========================================================================

    def get_recorder_status(self) -> Dict[str, Any]:
        """Check macro recorder status."""
        try:
            with self._client() as client:
                res = client.get("/api/recorder/status")
                if res.status_code == 200:
                    return res.json()
                return {"is_recording": False}
        except Exception:
            return {"is_recording": False}

    def start_recording(self, name: str = "Recorded Macro", description: str = "") -> Dict[str, Any]:
        """Start recording macro interactions."""
        try:
            with self._client() as client:
                res = client.post("/api/recorder/start", json={"name": name, "description": description})
                if res.status_code == 200:
                    return res.json()
                return {"status": "error", "error": res.text}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def record_step(self, step_data: Dict[str, Any]) -> Dict[str, Any]:
        """Append step to active macro recording."""
        try:
            with self._client() as client:
                res = client.post("/api/recorder/step", json=step_data)
                if res.status_code == 200:
                    return res.json()
                return {"recorded": False}
        except Exception:
            return {"recorded": False}

    def stop_recording(self) -> Dict[str, Any]:
        """Stop recording and compile into saved macro."""
        try:
            with self._client() as client:
                res = client.post("/api/recorder/stop")
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cancel_recording(self) -> Dict[str, Any]:
        """Cancel recording session."""
        try:
            with self._client() as client:
                res = client.post("/api/recorder/cancel")
                if res.status_code == 200:
                    return res.json()
                return {"status": "error"}
        except Exception:
            return {"status": "error"}

    # =========================================================================
    # 5. SCHEDULED AUTOMATIONS & WINDOWS TOAST NOTIFICATIONS
    # =========================================================================

    def list_schedules(self) -> List[Dict[str, Any]]:
        """List all recurring automation schedules."""
        try:
            with self._client() as client:
                res = client.get("/api/scheduler/tasks")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def create_schedule(self, schedule_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new recurring schedule."""
        try:
            with self._client() as client:
                res = client.post("/api/scheduler/tasks", json=schedule_data)
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def delete_schedule(self, task_id: str) -> bool:
        """Delete a scheduled task."""
        try:
            with self._client() as client:
                res = client.delete(f"/api/scheduler/tasks/{task_id}")
                return res.status_code == 200
        except Exception:
            return False

    def run_schedule_now(self, task_id: str) -> Dict[str, Any]:
        """Trigger immediate execution of a scheduled task."""
        try:
            with self._client() as client:
                res = client.post(f"/api/scheduler/tasks/{task_id}/run")
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def test_windows_toast(self, title: str, message: str) -> Dict[str, Any]:
        """Dispatch a test native Windows desktop toast alert."""
        try:
            with self._client() as client:
                res = client.post("/api/scheduler/toast/test", json={"title": title, "message": message})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "error": res.text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # =========================================================================
    # 6. VISUAL DIFF & WEBPAGE CHANGE MONITORING
    # =========================================================================

    def list_visual_baselines(self) -> List[Dict[str, Any]]:
        """List stored visual baseline snapshots."""
        try:
            with self._client() as client:
                res = client.get("/api/visual-diff/baselines")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def save_visual_baseline(self, baseline_id: str, label: Optional[str] = None) -> Dict[str, Any]:
        """Save active browser page as a visual baseline."""
        try:
            with self._client() as client:
                res = client.post("/api/visual-diff/baselines", json={"baseline_id": baseline_id, "label": label})
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def check_visual_diff(self, baseline_id: str, threshold: float = 0.05) -> Dict[str, Any]:
        """Compare active page with baseline screenshot."""
        try:
            with self._client() as client:
                res = client.post("/api/visual-diff/check", json={"baseline_id": baseline_id, "threshold": threshold})
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def compare_images(self, baseline_b64: str, current_b64: str, threshold: float = 0.02) -> Dict[str, Any]:
        """Compare any two base64 screenshots."""
        try:
            with self._client() as client:
                res = client.post("/api/visual-diff/compare", json={
                    "baseline_image_base64": baseline_b64,
                    "current_image_base64": current_b64,
                    "threshold": threshold,
                })
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    # =========================================================================
    # 7. BIDIRECTIONAL VOICE (AGENT SPEAKS BACK WITH TTS)
    # =========================================================================

    def get_tts_status(self) -> Dict[str, Any]:
        """Get voice synthesis status and voices."""
        try:
            with self._client() as client:
                res = client.get("/api/tts/status")
                if res.status_code == 200:
                    return res.json()
                return {"enabled": False}
        except Exception:
            return {"enabled": False}

    def speak_tts(self, text: str, voice_id: Optional[str] = None, wait: bool = False) -> Dict[str, Any]:
        """Trigger text-to-speech aloud."""
        try:
            with self._client() as client:
                res = client.post("/api/tts/speak", json={"text": text, "voice_id": voice_id, "wait": wait})
                if res.status_code == 200:
                    return res.json()
                return {"spoken": False, "error": res.text}
        except Exception as exc:
            return {"spoken": False, "error": str(exc)}

    def synthesize_tts_wav(self, text: str, voice_id: Optional[str] = None) -> Optional[str]:
        """Synthesize text and return base64 WAV audio string."""
        try:
            with self._client() as client:
                res = client.post("/api/tts/synthesize", json={"text": text, "voice_id": voice_id})
                if res.status_code == 200:
                    return res.json().get("audio_base64")
                return None
        except Exception:
            return None

    # =========================================================================
    # 8. AUTOMATED PDF & DOCUMENT EXTRACTION ENGINE
    # =========================================================================

    def list_downloaded_documents(self) -> List[Dict[str, Any]]:
        """List files captured by the browser download interceptor."""
        try:
            with self._client() as client:
                res = client.get("/api/documents/downloads")
                if res.status_code == 200:
                    return res.json()
                return []
        except Exception:
            return []

    def extract_document(self, file_path: Optional[str] = None, file_base64: Optional[str] = None, filename: str = "document.pdf") -> Dict[str, Any]:
        """Parse PDF or document into structured data."""
        try:
            with self._client() as client:
                payload = {"file_path": file_path, "file_base64": file_base64, "filename": filename}
                res = client.post("/api/documents/extract", json=payload)
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}

    def parse_latest_download(self) -> Dict[str, Any]:
        """Parse most recently downloaded file."""
        try:
            with self._client() as client:
                res = client.post("/api/documents/parse-last-download")
                if res.status_code == 200:
                    return res.json()
                return {"error": res.text}
        except Exception as exc:
            return {"error": str(exc)}



