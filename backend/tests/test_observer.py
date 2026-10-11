"""Unit and integration tests for Browser Observer (Member B, Step 2)."""

import pytest
from pathlib import Path
from backend.app.observer import PlaywrightObserver

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MOCK_SITE_URL = f"file:///{(ROOT_DIR / 'mock-site' / 'index.html').as_posix()}"


@pytest.mark.asyncio
async def test_observer_visible_launch_and_navigate():
    """Verify Playwright launches Chromium, navigates to mock-site, and retrieves title & URL."""
    observer = PlaywrightObserver()
    try:
        # Launch visible browser as required by Step 2
        page = await observer.launch(headless=False)
        assert page is not None

        await observer.navigate(MOCK_SITE_URL)
        obs = await observer.observe_structured()

        assert "index.html" in obs["url"]
        assert "ApexFlow" in obs["title"] or "Business Dashboard" in obs["title"]
        assert len(obs["elements"]) > 0
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_observer_extracts_search_and_view_buttons():
    """Verify that invoice-search and view-invoice buttons are detected with valid attributes."""
    observer = PlaywrightObserver()
    try:
        page = await observer.launch(headless=False)
        # Navigate directly to Invoices tab
        await observer.navigate(f"{MOCK_SITE_URL}#invoices")
        await page.wait_for_timeout(300)

        obs = await observer.observe_structured()

        # Find invoice search
        search_elem = next((e for e in obs["elements"] if e.get("agent_id") == "invoice-search"), None)
        assert search_elem is not None, "invoice-search element must be detected"
        assert search_elem["tag"] == "input"
        assert search_elem["role"] == "textbox"
        assert search_elem["type"] == "text"

        # Find view buttons
        view_buttons = [e for e in obs["elements"] if (e.get("agent_id") or "").startswith("view-invoice")]
        assert len(view_buttons) >= 5, "At least 5 view buttons must be detected on Invoices page"

        target_1001 = next((e for e in view_buttons if e.get("agent_id") == "view-invoice-1001"), None)
        assert target_1001 is not None, "view-invoice-1001 button must be detected"
        assert target_1001["tag"] == "button"
        assert target_1001["role"] == "button"
        assert "View" in target_1001["text"]
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_observer_ignores_hidden_elements():
    """Verify that elements inside hidden tabs/modals are ignored."""
    observer = PlaywrightObserver()
    try:
        page = await observer.launch(headless=False)
        # On dashboard view, the invoices table and closed modal elements should be hidden
        await observer.navigate(f"{MOCK_SITE_URL}#dashboard")
        await page.wait_for_timeout(200)

        obs = await observer.observe_structured()

        # Modal close button should be hidden while modal is closed
        modal_close_btn = next((e for e in obs["elements"] if e.get("agent_id") == "close-invoice-modal-btn"), None)
        assert modal_close_btn is None, "Hidden modal buttons must be ignored"
    finally:
        await observer.close()
