"""Step 7 Final Demo Readiness & Feature Freeze Verification Suite.

Validates the full demo reliability checklist under Member B's scope:
1. Scenario 1: Find invoice (Run 3x repeatedly, verify details $2,350.00).
2. Scenario 2: Update setting (Run 3x repeatedly, verify preference update is displayed).
3. Scenario 3: Stop execution (Start multi-action task, trigger Stop mid-run, confirm halt).
4. Prompt-Injection Defense: Detect adversarial prompt injection in comments and emit SAFETY_ALERT.
5. Navigation Stale Element Check: Verify no stale elements leak across page views in SPA.
6. Error & Server Resilience: Invalid targets and timeouts produce structured errors without server crash.
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
    """Find an available ephemeral port on localhost for test isolation."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def uvicorn_server():
    """Start isolated uvicorn server in a subprocess with fail-fast liveness check."""
    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ready = False
    for _ in range(40):
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
        raise RuntimeError(f"Uvicorn server at {base_url} failed to respond within timeout.")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_scenario_1_find_invoice_repeated_runs(uvicorn_server):
    """Scenario 1: Find an invoice (runs 3 times repeatedly, verifying invoice amount $2,350.00)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        for run_idx in range(1, 4):
            # 1. Start from / navigate to invoices
            nav = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "navigate", "url": "index.html#invoices"},
            )
            assert nav.json()["success"] is True, f"Run {run_idx}: Navigate failed"

            # 2. Search for INV-1001
            search = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "type", "target": "invoice-search", "text": "INV-1001"},
            )
            assert search.json()["success"] is True, f"Run {run_idx}: Search type failed"

            # 3. Open the matching invoice
            view = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "click", "target": "view-invoice-1001"},
            )
            assert view.json()["success"] is True, f"Run {run_idx}: Click view failed"

            # 4. Wait for details to be visible
            wait_amount = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "wait", "target": "invoice-detail-amount", "seconds": 2.0},
            )
            assert wait_amount.json()["success"] is True, f"Run {run_idx}: Wait amount failed"

            # 5. Verify details in observation
            obs = await client.get(f"{uvicorn_server}/observe")
            assert obs.status_code == 200
            elements = obs.json()["elements"]

            amount_elem = next((el for el in elements if el.get("agent_id") == "invoice-detail-amount"), None)
            assert amount_elem is not None, f"Run {run_idx}: invoice-detail-amount must be visible"
            assert "$2,350.00" in amount_elem.get("text", ""), f"Run {run_idx}: Expected $2,350.00 in amount element"

            # 6. Close modal
            close_btn = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "click", "target": "close-invoice-modal-btn"},
            )
            assert close_btn.json()["success"] is True


@pytest.mark.asyncio
async def test_scenario_2_update_setting_repeated_runs(uvicorn_server):
    """Scenario 2: Update a setting (runs 3 times repeatedly, verifying updated value displayed)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        test_names = ["Sarah Connor", "Elena Rostova", "Marcus Brody"]

        for run_idx, new_name in enumerate(test_names, 1):
            # 1. Navigate to settings
            nav = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "navigate", "url": "index.html#settings"},
            )
            assert nav.json()["success"] is True

            # 2. Change harmless mock preference (Profile Name)
            type_name = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "type", "target": "settings-profile-name", "text": new_name},
            )
            assert type_name.json()["success"] is True

            # 3. Save settings
            save = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "click", "target": "settings-save-btn"},
            )
            assert save.json()["success"] is True

            # 4. Wait for confirmation message
            wait_conf = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "wait", "target": "settings-save-success-msg", "seconds": 2.0},
            )
            assert wait_conf.json()["success"] is True

            # 5. Verify the new value is displayed on page and in sidebar
            obs = await client.get(f"{uvicorn_server}/observe")
            elements = obs.json()["elements"]

            user_name_elem = next((el for el in elements if el.get("agent_id") == "sidebar-user-name"), None)
            assert user_name_elem is not None, f"Run {run_idx}: sidebar-user-name must be visible"
            assert new_name in user_name_elem.get("text", ""), f"Run {run_idx}: Expected {new_name} in sidebar-user-name"


@pytest.mark.asyncio
async def test_scenario_3_stop_execution_live_control(uvicorn_server):
    """Scenario 3: Start multi-action task, press Stop while running, verify no subsequent action starts (repeated 3x)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_selector("#backend-status-text:has-text('ONLINE')", timeout=8000)

        for run_idx in range(1, 4):
            # Ensure fresh resumed state before run
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{uvicorn_server}/resume")
            await page.wait_for_timeout(200)

            # Select Scenario 3 Stop preset
            await page.click("#preset-stop")
            await page.wait_for_timeout(200)

            # Click Run Task
            await page.click("#btn-run")

            # Wait until task transitions to RUNNING
            await page.wait_for_selector("#agent-status-text:has-text('RUNNING')", timeout=5000)

            # Trigger Stop mid-flight
            await page.click("#btn-stop")

            # Confirm dashboard transitions to STOPPED
            await page.wait_for_function(
                "() => document.getElementById('agent-status-text')?.textContent.trim() === 'STOPPED'",
                timeout=8000,
            )
            final_status = await page.inner_text("#agent-status-text")
            assert final_status == "STOPPED", f"Run {run_idx}: Expected STOPPED status"

            # Wait for client loop to settle and verify no further action begins
            await page.wait_for_timeout(1000)

            # Verify backend stop flag is active and rejects subsequent direct execution
            async with httpx.AsyncClient(timeout=5.0) as client:
                subsequent = await client.post(
                    f"{uvicorn_server}/execute",
                    json={"action": "click", "target": "nav-dashboard"},
                )
                assert subsequent.json()["success"] is False, f"Run {run_idx}: Subsequent action must be blocked"
                assert "stopped" in subsequent.json()["error"].lower()

                # Cleanly resume before next cycle
                await client.post(f"{uvicorn_server}/resume")

        await browser.close()


@pytest.mark.asyncio
async def test_prompt_injection_security_defense(uvicorn_server):
    """Verify detection of adversarial prompt injection in comments emitting SAFETY_ALERT."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        # Navigate to injection sandbox page
        nav = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "injection.html"},
        )
        assert nav.json()["success"] is True

        # Check events telemetry for SAFETY_ALERT
        events_resp = await client.get(f"{uvicorn_server}/events?limit=20")
        assert events_resp.status_code == 200
        events = events_resp.json()

        safety_alerts = [ev for ev in events if ev.get("type") == "safety_alert"]
        assert len(safety_alerts) > 0, "Safety alert must be emitted upon navigating to injection page"
        assert "prompt injection" in safety_alerts[0].get("message", "").lower()


@pytest.mark.asyncio
async def test_stale_element_ids_after_navigation(uvicorn_server):
    """Check that element IDs do not leak across inactive pages and cannot be interacted with."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        # 1. On Dashboard: verify dashboard actions exist, invoices search is not visible
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#dashboard"},
        )
        obs_dash = (await client.get(f"{uvicorn_server}/observe")).json()
        dash_ids = {el.get("agent_id") for el in obs_dash["elements"] if el.get("agent_id")}
        assert "quick-create-invoice-btn" in dash_ids
        assert "invoice-search" not in dash_ids, "invoice-search must not be visible on dashboard"

        # 2. On Invoices: verify invoice-search exists, dashboard quick buttons are not visible
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#invoices"},
        )
        obs_inv = (await client.get(f"{uvicorn_server}/observe")).json()
        inv_ids = {el.get("agent_id") for el in obs_inv["elements"] if el.get("agent_id")}
        assert "invoice-search" in inv_ids
        assert "quick-create-invoice-btn" not in inv_ids, "quick-create-invoice-btn must not be visible on invoices"

        # 3. On Settings: verify settings inputs exist, invoice search is not visible
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "navigate", "url": "index.html#settings"},
        )
        obs_set = (await client.get(f"{uvicorn_server}/observe")).json()
        set_ids = {el.get("agent_id") for el in obs_set["elements"] if el.get("agent_id")}
        assert "settings-profile-name" in set_ids
        assert "invoice-search" not in set_ids, "invoice-search must not be visible on settings page"

        # 4. Attempting to execute an action against a stale element from inactive page fails cleanly
        stale_click = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "quick-create-invoice-btn"},
        )
        assert stale_click.json()["success"] is False
        assert "not visible" in stale_click.json()["error"].lower() or "not found" in stale_click.json()["error"].lower()


@pytest.mark.asyncio
async def test_error_handling_and_timeout_resilience(uvicorn_server):
    """Verify invalid targets and action timeouts produce structured errors without crashing server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{uvicorn_server}/resume")

        # 1. Missing target produces clean structured error
        err_res = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "non-existent-button-xyz-999"},
        )
        assert err_res.status_code == 200
        data = err_res.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

        # 2. Bounded wait action timeout produces clean structured error without hanging
        timeout_res = await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "wait", "target": "unmatched-element-999", "seconds": 1.0},
        )
        assert timeout_res.status_code == 200
        timeout_data = timeout_res.json()
        assert timeout_data["success"] is False
        assert "timeout" in timeout_data["error"].lower() or "not found" in timeout_data["error"].lower()

        # 3. Server remains alive and functional for subsequent health check and actions
        health = await client.get(f"{uvicorn_server}/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
