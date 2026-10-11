"""Integration tests for FastAPI backend (Member B, Step 4).

Validates all Step 4 Definition of Done checklist items:
1. GET /health returns 200 and {"status": "ok"}
2. GET /observe returns the current browser state
3. POST /execute performs a real browser action
4. Invalid actions return a clear error
5. GET /events records execution outcomes
6. POST /stop prevents subsequent actions
7. Concurrent browser requests do not interfere
"""

import asyncio
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app, executor, observer
from backend.app.executor import MOCK_SITE_DEFAULT_URL


@pytest.fixture(autouse=True)
def reset_executor_state():
    """Ensure executor is resumed and clean before and after each test."""
    executor.resume()
    yield
    executor.resume()


@pytest.mark.asyncio
async def test_get_health_returns_200():
    """Verify GET /health returns 200 with status ok."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_get_observe_returns_current_browser_state():
    """Verify GET /observe returns current browser page URL, title, and interactive elements."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First navigate to mock site
        nav_resp = await client.post("/execute", json={
            "action": "navigate",
            "url": f"{MOCK_SITE_DEFAULT_URL}#invoices",
        })
        assert nav_resp.status_code == 200
        assert nav_resp.json()["success"] is True

        # Now observe
        obs_resp = await client.get("/observe")
        assert obs_resp.status_code == 200
        obs_data = obs_resp.json()

        assert "url" in obs_data
        assert "title" in obs_data
        assert "elements" in obs_data
        assert "#invoices" in obs_data["url"]
        assert len(obs_data["elements"]) > 0

        # Confirm critical elements detected
        has_search = any(el.get("agent_id") == "invoice-search" for el in obs_data["elements"])
        has_view = any((el.get("agent_id") or "").startswith("view-invoice") for el in obs_data["elements"])
        assert has_search is True
        assert has_view is True


@pytest.mark.asyncio
async def test_post_execute_performs_real_browser_action():
    """Verify POST /execute performs a real browser action (click invoice view button)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Navigate to invoices
        await client.post("/execute", json={
            "action": "navigate",
            "url": f"{MOCK_SITE_DEFAULT_URL}#invoices",
        })

        # Click view button
        click_resp = await client.post("/execute", json={
            "action": "click",
            "target": "view-invoice-1001",
        })
        assert click_resp.status_code == 200
        click_data = click_resp.json()

        assert click_data["success"] is True
        assert click_data["action"] == "click"
        assert click_data["target"] == "view-invoice-1001"
        assert click_data["message"] == "Invoice details opened"

        # Verify through observation that modal is open
        obs = await client.get("/observe")
        obs_data = obs.json()
        has_close_btn = any(el.get("agent_id") == "close-invoice-modal-btn" for el in obs_data["elements"])
        assert has_close_btn is True


@pytest.mark.asyncio
async def test_invalid_actions_return_clear_error():
    """Verify unknown action names or missing targets return structured errors with clear messages."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Unknown action type
        unknown_resp = await client.post("/execute", json={
            "action": "teleport_now",
            "target": "view-invoice-1001",
        })
        assert unknown_resp.status_code == 200
        data = unknown_resp.json()
        assert data["success"] is False
        assert "unsupported" in data["message"].lower() or "unsupported" in (data.get("error") or "").lower()

        # 2. Missing target for click
        missing_target_resp = await client.post("/execute", json={
            "action": "click",
        })
        assert missing_target_resp.status_code == 200
        missing_data = missing_target_resp.json()
        assert missing_data["success"] is False
        assert "missing target" in missing_data["message"].lower()

        # 3. Nonexistent target
        nonexistent_resp = await client.post("/execute", json={
            "action": "click",
            "target": "nonexistent-button-xyz",
        })
        assert nonexistent_resp.status_code == 200
        nonexistent_data = nonexistent_resp.json()
        assert nonexistent_data["success"] is False
        assert "not found" in nonexistent_data["message"].lower()


@pytest.mark.asyncio
async def test_get_events_records_execution_outcomes():
    """Verify GET /events returns recorded actions with timestamps and status."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Execute an action
        await client.post("/execute", json={
            "action": "navigate",
            "url": f"{MOCK_SITE_DEFAULT_URL}#invoices",
        })

        await client.post("/execute", json={
            "action": "type",
            "target": "invoice-search",
            "text": "INV-1001",
        })

        # Fetch events
        events_resp = await client.get("/events")
        assert events_resp.status_code == 200
        events = events_resp.json()

        assert len(events) >= 2
        last_event = events[-1]

        assert "timestamp" in last_event
        assert "action" in last_event
        assert "status" in last_event
        assert "success" in last_event
        assert last_event["action"] == "type"
        assert last_event["success"] is True
        assert last_event["status"] == "success"


@pytest.mark.asyncio
async def test_post_stop_prevents_subsequent_actions():
    """Verify POST /stop sets stop flag and blocks subsequent actions in execution layer."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Stop execution
        stop_resp = await client.post("/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopped"

        # Attempt to execute an action
        exec_resp = await client.post("/execute", json={
            "action": "click",
            "target": "view-invoice-1001",
        })
        assert exec_resp.status_code == 200
        exec_data = exec_resp.json()

        # Action must be rejected
        assert exec_data["success"] is False
        assert "stopped" in exec_data["message"].lower()

        # Resume execution
        resume_resp = await client.post("/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "resumed"

        # Actions are allowed again
        resumed_action = await client.post("/execute", json={
            "action": "scroll",
            "delta_y": 100,
        })
        assert resumed_action.status_code == 200
        assert resumed_action.json()["success"] is True


@pytest.mark.asyncio
async def test_concurrent_browser_requests_do_not_interfere():
    """Verify concurrent requests are serialized by browser_lock without crashing or race conditions."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Launch 5 simultaneous requests (mix of observe, scroll, wait, type)
        tasks = [
            client.post("/execute", json={"action": "navigate", "url": f"{MOCK_SITE_DEFAULT_URL}#invoices"}),
            client.get("/observe"),
            client.post("/execute", json={"action": "scroll", "delta_y": 100}),
            client.get("/observe"),
            client.post("/execute", json={"action": "wait", "seconds": 0.1}),
        ]

        responses = await asyncio.gather(*tasks)

        # All concurrent requests should succeed without race condition failures
        for resp in responses:
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_safety_guard_blocks_destructive_direct_action():
    """Verify SafetyGuard intercepts destructive actions at POST /execute boundary."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Purge / delete action
        resp = await client.post("/execute", json={
            "action": "click",
            "target": "purge-all-data-button",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "SafetyGuard" in data["message"]
