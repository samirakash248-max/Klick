"""Integration and E2E tests for Live Dashboard (Member B, Step 5).

Validates all 7 Definition of Done checklist items for Step 5:
1. Dashboard loads without JavaScript errors
2. Backend connection status updates correctly (ONLINE)
3. Current URL and page title are visible
4. Activity feed shows real backend events
5. Action success and failure are distinguishable
6. Stop button calls the backend and prevents subsequent actions
7. Run button executes real actions without faking execution status
"""

import asyncio
from pathlib import Path
import subprocess
import sys
import time
from typing import List

import httpx
from playwright.async_api import Page, async_playwright
import pytest


@pytest.fixture(scope="module")
def uvicorn_server():
    """Start uvicorn server in a subprocess on port 8000 for dashboard E2E tests."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    for _ in range(40):
        try:
            r = httpx.get("http://127.0.0.1:8000/health", timeout=2.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.25)

    if not ready:
        proc.kill()
        raise RuntimeError("Uvicorn server failed to start within timeout.")

    yield "http://127.0.0.1:8000"

    # Teardown
    try:
        httpx.post("http://127.0.0.1:8000/resume", timeout=2.0)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_dashboard_loads_without_javascript_errors(uvicorn_server):
    """Checklist #1: Dashboard loads cleanly with zero uncaught JavaScript errors."""
    js_errors: List[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Capture uncaught page errors
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)

        # Assert no JS errors occurred during load
        assert len(js_errors) == 0, f"Dashboard loaded with JavaScript errors: {js_errors}"
        await browser.close()


@pytest.mark.asyncio
async def test_backend_connection_status_updates_correctly(uvicorn_server):
    """Checklist #2: Backend connection indicator correctly updates to ONLINE."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")

        # Wait for health check to resolve to ONLINE
        await page.wait_for_selector("#backend-status-text:has-text('ONLINE')", timeout=8000)
        status_text = await page.inner_text("#backend-status-text")
        dot_class = await page.get_attribute("#backend-status-dot", "class")

        assert status_text == "ONLINE"
        assert "dot-online" in dot_class
        await browser.close()


@pytest.mark.asyncio
async def test_current_url_and_page_title_are_visible(uvicorn_server):
    """Checklist #3: Current URL, page title, and interactive element count are visible."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(f"{uvicorn_server}/execute", json={"action": "navigate", "url": "index.html#invoices"})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        title_text = await page.inner_text("#browser-title")
        url_text = await page.inner_text("#browser-url")
        elements_count_text = await page.inner_text("#elements-count")

        assert title_text != "Checking page..."
        assert len(title_text) > 0
        assert "invoices" in url_text
        assert "Elements" in elements_count_text

        # Verify interactive tags are rendered in tag cloud
        tag_elements = await page.locator(".tag-pill").all()
        assert len(tag_elements) > 0, "Expected interactive agent-id tag pills to be rendered"
        await browser.close()


@pytest.mark.asyncio
async def test_activity_feed_shows_real_backend_events(uvicorn_server):
    """Checklist #4: Activity feed displays real backend events with timestamp and target."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "scroll", "delta_y": 200},
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Activity feed items should be visible
        feed_items = await page.locator(".feed-item").all()
        assert len(feed_items) > 0, "Activity feed should display at least one event item"

        first_badge = await page.inner_text(".feed-item:first-child .feed-action-badge")
        assert len(first_badge) > 0
        await browser.close()


@pytest.mark.asyncio
async def test_action_success_and_failure_are_distinguishable(uvicorn_server):
    """Checklist #5: Action success and failure are clearly distinguishable with styling."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Successful action
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "scroll", "delta_y": 150},
        )
        # 2. Failing action (safeguard rejection for missing target)
        await client.post(
            f"{uvicorn_server}/execute",
            json={"action": "click", "target": "invalid-missing-target-999"},
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Must have both a success item and an error item
        success_item = page.locator(".feed-item-success").first
        error_item = page.locator(".feed-item-error").first

        assert await success_item.count() > 0, "Expected at least one .feed-item-success card"
        assert await error_item.count() > 0, "Expected at least one .feed-item-error card"

        # Check distinct error box
        error_box = error_item.locator(".feed-error-box")
        assert await error_box.count() > 0, "Error feed item must render .feed-error-box"
        error_text = await error_box.inner_text()
        assert "Error:" in error_text
        await browser.close()


@pytest.mark.asyncio
async def test_stop_button_calls_backend_and_prevents_actions(uvicorn_server):
    """Checklist #6: Stop button invokes POST /stop and prevents subsequent actions."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)

        # Click the Stop button on the dashboard
        await page.click("#btn-stop")
        await page.wait_for_timeout(800)

        # Dashboard status must show STOPPED
        status_text = await page.inner_text("#agent-status-text")
        assert status_text == "STOPPED"

        # Resume button must now be visible
        resume_visible = await page.is_visible("#btn-resume")
        assert resume_visible is True

        # Directly verify backend now rejects execution
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "scroll", "delta_y": 50},
            )
            assert res.json()["success"] is False
            assert "stopped" in res.json()["message"].lower()

        # Click Resume on dashboard
        await page.click("#btn-resume")
        await page.wait_for_timeout(800)

        status_text_after = await page.inner_text("#agent-status-text")
        assert status_text_after == "IDLE"

        # Backend should now allow execution again
        async with httpx.AsyncClient(timeout=10.0) as client:
            res2 = await client.post(
                f"{uvicorn_server}/execute",
                json={"action": "scroll", "delta_y": 50},
            )
            assert res2.json()["success"] is True

        await browser.close()


@pytest.mark.asyncio
async def test_run_button_executes_successful_workflow(uvicorn_server):
    """Checklist #7A: Run button executes real workflow and updates status to COMPLETED."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(f"{uvicorn_server}/resume")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_selector("#backend-status-text:has-text('ONLINE')", timeout=8000)

        # Select 'Open Invoices' preset
        await page.click("button:has-text('Open Invoices')")
        await page.wait_for_timeout(300)

        # Click Run Task
        await page.click("#btn-run")

        # Wait for task completion (COMPLETED)
        await page.wait_for_function(
            "() => document.getElementById('agent-status-text')?.textContent.trim() === 'COMPLETED'",
            timeout=15000,
        )
        final_status = await page.inner_text("#agent-status-text")
        assert final_status == "COMPLETED"

        # Verify browser URL reflects real navigation
        await page.wait_for_function(
            "() => document.getElementById('browser-url')?.textContent.includes('invoices')",
            timeout=8000,
        )
        url_text = await page.inner_text("#browser-url")
        assert "invoices" in url_text
        await browser.close()


@pytest.mark.asyncio
async def test_run_button_handles_real_failure(uvicorn_server):
    """Checklist #7B: Run button handles real execution failure and updates status to FAILED."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(f"{uvicorn_server}/resume")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        dashboard_url = f"{uvicorn_server}/dashboard/index.html"
        await page.goto(dashboard_url, wait_until="domcontentloaded")
        await page.wait_for_selector("#backend-status-text:has-text('ONLINE')", timeout=8000)

        # Select 'Error Demo (Invalid Target)' preset
        await page.click("button:has-text('Error Demo')")
        await page.wait_for_timeout(300)

        # Click Run Task
        await page.click("#btn-run")

        # Wait for task failure (FAILED)
        await page.wait_for_function(
            "() => document.getElementById('agent-status-text')?.textContent.trim() === 'FAILED'",
            timeout=15000,
        )
        failed_status = await page.inner_text("#agent-status-text")
        assert failed_status == "FAILED"

        # Activity feed must capture the real failure event
        await page.wait_for_selector(".feed-item-error", timeout=8000)
        error_items = await page.locator(".feed-item-error").all()
        assert len(error_items) > 0, "Activity feed must display real failed action item"
        await browser.close()
