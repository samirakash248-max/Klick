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
