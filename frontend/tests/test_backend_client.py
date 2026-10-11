"""Unit tests for frontend BackendClient service with mocked HTTP interactions."""

import httpx
import pytest
from unittest.mock import MagicMock, patch

from frontend.services.backend_client import BackendClient


@pytest.fixture
def client():
    return BackendClient(base_url="http://mock-backend:8000", timeout=2.0)


def test_check_health_api_success(client):
    """Test successful health check against /api/health."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "healthy",
        "service": "BrowserPilot AI Backend",
        "agent_running": False,
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        res = client.check_health()
        assert res["online"] is True
        assert res["status"] == "healthy"
        assert res["agent_running"] is False


def test_check_health_fallback_success(client):
    """Test successful fallback health check against /health when /api/health is unavailable."""
    mock_resp_404 = MagicMock(status_code=404)
    mock_resp_200 = MagicMock(status_code=200, json=lambda: {"status": "ok"})

    def side_effect(path):
        if path == "/api/health":
            return mock_resp_404
        return mock_resp_200

    with patch.object(httpx.Client, "get", side_effect=side_effect):
        res = client.check_health()
        assert res["online"] is True
        assert res["status"] == "ok"


def test_check_health_connection_error(client):
    """Test connection failure when backend is unreachable."""
    with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("Connection refused")):
        res = client.check_health()
        assert res["online"] is False
        assert "Connection refused" in res["error"]


def test_check_health_timeout(client):
    """Test timeout when health check exceeds threshold."""
    with patch.object(httpx.Client, "get", side_effect=httpx.TimeoutException("Timed out")):
        res = client.check_health()
        assert res["online"] is False
        assert "timed out" in res["error"].lower()


def test_start_agent_success(client):
    """Test initiating an agent run returns success."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": "Agent execution initiated",
        "goal": "Search INV-1001",
        "max_steps": 10,
    }

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = client.start_agent("Search INV-1001", max_steps=10)
        assert res["success"] is True
        assert res["goal"] == "Search INV-1001"


def test_start_agent_backend_error_payload(client):
    """Test start agent receives an error payload from backend (e.g. already running)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"error": "Agent is already running."}

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = client.start_agent("Search INV-1001")
        assert res["success"] is False
        assert "already running" in res["error"]


def test_start_agent_http_error(client):
    """Test start agent encounters an HTTP error."""
    mock_resp = MagicMock(status_code=500, text="Internal Server Error")
    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = client.start_agent("Search INV-1001")
        assert res["success"] is False
        assert "HTTP 500" in res["error"]


def test_stop_agent_success(client):
    """Test stopping the agent dispatches signals cleanly."""
    mock_resp = MagicMock(status_code=200, json=lambda: {"status": "stopped"})
    with patch.object(httpx.Client, "post", return_value=mock_resp):
        res = client.stop_agent()
        assert res["success"] is True


def test_get_agent_state(client):
    """Test retrieving agent state snapshot."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "run_id": "test-run-123",
        "goal": "Test Goal",
        "status": "running",
        "current_step": 2,
        "max_steps": 15,
        "history": [],
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        state = client.get_agent_state()
        assert state is not None
        assert state["run_id"] == "test-run-123"
        assert state["status"] == "running"


def test_get_events(client):
    """Test retrieving event telemetry stream."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"type": "action_executed", "action": "CLICK", "success": True},
        {"type": "safety_alert", "message": "High risk flagged", "success": False},
    ]

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        events = client.get_events(limit=10)
        assert len(events) == 2
        assert events[0]["action"] == "CLICK"


def test_get_observation(client):
    """Test retrieving structured page observation."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "url": "http://localhost:8080/invoices",
        "title": "Invoices",
        "elements": [{"agent_id": "invoice-search", "tag": "input"}],
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        obs = client.get_observation()
        assert obs["url"] == "http://localhost:8080/invoices"
        assert len(obs["elements"]) == 1
