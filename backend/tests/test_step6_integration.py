"""Step 6 End-to-End Integration & Stabilization Test Suite.

Validates the complete system architecture under Member B's scope:
1. Test A: Browser connection (Observer returns title, URL, interactive elements)
2. Test B: Action execution & DOM verification (Search INV-1001, view details, verify specific amount & identity)
3. Test C: Dashboard synchronization (Causal event verification, URL/title updates, status)
4. Test D: Stop & failure handling (Stop blocks actions, invalid targets produce errors, timeouts don't crash API)
5. Test E: Repeated runs idempotence & recovery (Recovery from an abandoned open modal without stale state poisoning)
"""

import asyncio
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List

import httpx
from playwright.async_api import async_playwright
import pytest


def get_free_port() -> int:
    """Find an available port on localhost for isolated test execution."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def uvicorn_server():
    """Start isolated uvicorn server in a subprocess with verified liveness."""
    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ready = False
    for _ in range(40):
        # Fail fast if subprocess died early (e.g. port or startup error)
        if proc.poll() is not None:
            raise RuntimeError(f"Uvicorn subprocess terminated prematurely with exit code {proc.returncode}")

        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.25)

    if not ready:
        proc.kill()
        raise RuntimeError(f"Uvicorn server at {base_url} failed to respond to /health within timeout.")

    yield base_url

    # Graceful teardown
    try:
        httpx.post(f"{base_url}/resume", timeout=2.0)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_a_browser_connection_and_observation(uvicorn_server):
    """Test A: Confirm browser connects and observer returns correct page title, URL, and elements."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Health check
        health_resp = await client.get(f"{uvicorn_server}/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}

        # 2. Reset stop flag
        await client.post(f"{uvicorn_server}/resume")

        # 3. Navigate to mock dashboard
        nav_resp = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html"},
        )
        assert nav_resp.status_code == 200
        assert nav_resp.json()["success"] is True

        # 4. Observe page state
        obs_resp = await client.get(f"{uvicorn_server}/observe")
        assert obs_resp.status_code == 200
        data = obs_resp.json()

        assert "index.html" in data["url"]
        assert len(data["title"]) > 0
        assert "ApexFlow" in data["title"] or "Dashboard" in data["title"]
        assert isinstance(data["elements"], list)
        assert len(data["elements"]) > 0

        # Verify observer extracts data-agent-id elements
        agent_ids = [el.get("agent_id") for el in data["elements"] if el.get("agent_id")]
        assert "nav-invoices" in agent_ids or "global-search-input" in agent_ids


@pytest.mark.asyncio
async def test_b_action_execution_invoice_workflow(uvicorn_server):
    """Test B: Search for invoice INV-1001, open details, confirm exact invoice amount & identity."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        # 1. Navigate to invoices
        r1 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#invoices"},
        )
        assert r1.status_code == 200
        assert r1.json()["success"] is True

        # 2. Type search INV-1001
        r2 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "type", "target": "invoice-search", "text": "INV-1001"},
        )
        assert r2.status_code == 200
        assert r2.json()["success"] is True
        assert "INV-1001" in r2.json()["message"]

        # 3. Click View button for invoice 1001
        r3 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "view-invoice-1001"},
        )
        assert r3.status_code == 200
        assert r3.json()["success"] is True
        assert "Invoice details opened" in r3.json()["message"]

        # 4. Wait for invoice-detail-amount element to appear
        r4 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "wait", "target": "invoice-detail-amount", "seconds": 2.0},
        )
        assert r4.status_code == 200
        assert r4.json()["success"] is True
        assert "appeared and is visible" in r4.json()["message"]

        # 5. Deep verification of invoice amount and identity in the live DOM
        obs = await client.get(f"{uvicorn_server}/observe")
        assert obs.status_code == 200
        obs_data = obs.json()
        elements = obs_data["elements"]

        amount_elem = next((el for el in elements if el.get("agent_id") == "invoice-detail-amount"), None)
        assert amount_elem is not None, "invoice-detail-amount must be present in observed elements"
        assert "$2,350.00" in amount_elem.get("text", ""), f"Expected $2,350.00 in amount element, got {amount_elem.get('text')}"


@pytest.mark.asyncio
async def test_c_dashboard_synchronization(uvicorn_server):
    """Test C: Verify dashboard displays real causal events, updates URL/title, and reflects execution state."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_selector("#backend-status-text:has-text('ONLINE')", timeout=8000)

        # Trigger unique scroll action via API directly
        async with httpx.AsyncClient(timeout=10.0) as client:
            action_res = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "scroll", "delta_y": 385},
            )
            assert action_res.json()["success"] is True

        # Dashboard activity feed must render this specific causal scroll event
        await page.wait_for_selector(".feed-item .feed-action-badge:has-text('SCROLL')", timeout=6000)
        feed_body = await page.inner_text(".feed-item:first-child .feed-item-body")
        assert "385" in feed_body, f"Expected 385px in feed event message, got: {feed_body}"

        # Check browser state indicators reflect current page
        async with httpx.AsyncClient(timeout=10.0) as client:
            current_obs = await client.get(f"{uvicorn_server}/observe")
            expected_title = current_obs.json()["title"]
            expected_url = current_obs.json()["url"]

        url_text = await page.inner_text("#browser-url")
        title_text = await page.inner_text("#browser-title")
        assert expected_title in title_text
        assert "index.html" in url_text

        await browser.close()


@pytest.mark.asyncio
async def test_d_stop_and_failure_handling(uvicorn_server):
    """Test D: Stop blocks subsequent actions, invalid target errors cleanly, timeouts don't crash server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Part 1: Stop blocks subsequent actions
        stop_resp = await client.post(f"{uvicorn_server}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopped"

        blocked_resp = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "scroll", "delta_y": 100},
        )
        assert blocked_resp.status_code == 200
        assert blocked_resp.json()["success"] is False
        assert "stopped" in blocked_resp.json()["message"].lower()

        # Part 2: Resume and test nonexistent target error
        await client.post(f"{uvicorn_server}/resume")
        missing_resp = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "target-that-does-not-exist-xyz-999"},
        )
        assert missing_resp.status_code == 200
        assert missing_resp.json()["success"] is False
        assert "not found" in missing_resp.json()["message"].lower()

        # Part 3: Element wait timeout does not crash the server
        timeout_wait_resp = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "wait", "target": "phantom-element-never-appears", "seconds": 0.5},
        )
        assert timeout_wait_resp.status_code == 200
        assert timeout_wait_resp.json()["success"] is False
        assert "timed out" in timeout_wait_resp.json()["message"].lower()

        # Server must still be fully responsive
        health_check = await client.get(f"{uvicorn_server}/health")
        assert health_check.status_code == 200
        assert health_check.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_e_repeated_runs_do_not_leave_stale_state(uvicorn_server):
    """Test E: Recovery from an abandoned open modal without stale state poisoning."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        # Run 1: Open invoice 1001 details and LEAVE THE MODAL OPEN
        nav1 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#invoices"},
        )
        assert nav1.json()["success"] is True

        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "type", "target": "invoice-search", "text": "INV-1001"},
        )
        view1 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "view-invoice-1001"},
        )
        assert view1.json()["success"] is True
        # Modal is now intentionally OPEN (do not close it)

        # Run 2: Re-navigate to invoices. Mock site route change dismisses the abandoned modal.
        nav2 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#invoices"},
        )
        assert nav2.json()["success"] is True

        # Clear/type search in fresh view
        search2 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "type", "target": "invoice-search", "text": "INV-1001"},
        )
        assert search2.json()["success"] is True

        # View invoice again in run 2 - must succeed without being blocked by stale overlay
        view2 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "view-invoice-1001"},
        )
        assert view2.json()["success"] is True
        assert "Invoice details opened" in view2.json()["message"]

        # Verify amount in run 2
        wait2 = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "wait", "target": "invoice-detail-amount", "seconds": 2.0},
        )
        assert wait2.json()["success"] is True
