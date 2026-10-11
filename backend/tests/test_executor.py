"""Comprehensive unit and integration tests for Browser Executor (Member B, Step 3).

Verifies the Step 3 Definition of Done:
1. Click works using a stable agent ID
2. Typing works in inputs and textareas (and rejects non-editable elements)
3. Scroll and wait are bounded
4. Navigation rejects external URLs
5. Invalid/missing/ambiguous targets return structured errors
6. A complete hardcoded invoice workflow passes
"""

from pathlib import Path
import pytest

from backend.app.executor import PlaywrightExecutor, MAX_SCROLL_PIXELS, MAX_WAIT_SECONDS
from backend.app.schemas import ActionType, BrowserAction, ExecutionResult

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MOCK_SITE_URL = f"file:///{(ROOT_DIR / 'mock-site' / 'index.html').as_posix()}"


@pytest.mark.asyncio
async def test_click_stable_agent_id():
    """Verify click works using a stable data-agent-id and updates modal state."""
    async with PlaywrightExecutor(headless=True) as executor:
        # Navigate to Invoices tab
        nav_res = await executor.execute({"action": "navigate", "url": f"{MOCK_SITE_URL}#invoices"})
        assert nav_res.success, f"Navigation failed: {nav_res.message}"

        # Click view button for invoice 1001
        click_res = await executor.execute({"action": "click", "target": "view-invoice-1001"})
        assert click_res.success is True
        assert click_res.action == "click"
        assert click_res.target == "view-invoice-1001"
        assert click_res.message == "Invoice details opened"

        # Verify modal is actually visible in DOM
        page = executor.current_page
        assert page is not None
        is_modal_open = await page.locator("#invoice-modal-overlay.open").count() > 0
        assert is_modal_open is True

        modal_id = await page.locator("#modal-invoice-id").text_content()
        assert "INV-1001" in (modal_id or "")


@pytest.mark.asyncio
async def test_typing_in_inputs_and_textareas():
    """Verify typing works in inputs and textareas, but is rejected on non-editable elements."""
    async with PlaywrightExecutor(headless=True) as executor:
        # 1. Typing in text input
        await executor.execute({"action": "navigate", "url": f"{MOCK_SITE_URL}#invoices"})
        type_res = await executor.execute({
            "action": "type",
            "target": "invoice-search",
            "text": "INV-1001",
        })
        assert type_res.success is True
        assert type_res.target == "invoice-search"
        assert "Typed 'INV-1001'" in type_res.message

        page = executor.current_page
        assert page is not None
        val = await page.locator('[data-agent-id="invoice-search"]').input_value()
        assert val == "INV-1001"

        # 2. Typing in textarea
        await executor.execute({"action": "navigate", "url": f"{MOCK_SITE_URL}#settings"})
        textarea_res = await executor.execute({
            "action": "type",
            "target": "settings-billing-notes",
            "text": "Standard Net-30 payment terms apply.",
        })
        assert textarea_res.success is True
        assert textarea_res.target == "settings-billing-notes"

        ta_val = await page.locator('[data-agent-id="settings-billing-notes"]').input_value()
        assert ta_val == "Standard Net-30 payment terms apply."

        # 3. Reject typing into non-editable element (a button)
        btn_type_res = await executor.execute({
            "action": "type",
            "target": "settings-save-btn",
            "text": "Should fail",
        })
        assert btn_type_res.success is False
        assert btn_type_res.error == "Non-editable element"
        assert "Cannot type into <button>" in btn_type_res.message


@pytest.mark.asyncio
async def test_scroll_and_wait_are_bounded():
    """Verify scroll delta and wait duration are clamped to sensible safeguard limits."""
    async with PlaywrightExecutor(headless=True) as executor:
        await executor.execute({"action": "navigate", "url": MOCK_SITE_URL})

        # 1. Normal scroll
        scroll_res1 = await executor.execute({"action": "scroll", "delta_y": 300})
        assert scroll_res1.success is True
        assert "300px" in scroll_res1.message

        # 2. Huge scroll is bounded to MAX_SCROLL_PIXELS
        scroll_res2 = await executor.execute({"action": "scroll", "delta_y": 99999})
        assert scroll_res2.success is True
        assert f"by {MAX_SCROLL_PIXELS}px" in scroll_res2.message
        assert "bounded" in scroll_res2.message

        # 3. Wait duration
        wait_res = await executor.execute({"action": "wait", "seconds": 0.1})
        assert wait_res.success is True
        assert "Waited for 0.1s" in wait_res.message

        # 4. Wait for element appearance
        await executor.execute({"action": "click", "target": "nav-invoices"})
        await executor.execute({"action": "click", "target": "view-invoice-1001"})
        wait_elem_res = await executor.execute({
            "action": "wait",
            "target": "close-invoice-modal-btn",
            "wait_seconds": 2.0,
        })
        assert wait_elem_res.success is True
        assert "appeared and is visible" in wait_elem_res.message

        # 5. Wait for nonexistent element times out cleanly with structured error
        wait_missing = await executor.execute({
            "action": "wait",
            "target": "missing-ghost-element",
            "wait_seconds": 0.5,
        })
        assert wait_missing.success is False
        assert "Timed out" in wait_missing.message


@pytest.mark.asyncio
async def test_navigation_rejects_external_urls():
    """Verify navigation to untrusted external URLs is strictly blocked."""
    async with PlaywrightExecutor(headless=True) as executor:
        # 1. Block external HTTPS
        ext_res1 = await executor.execute({"action": "navigate", "url": "https://google.com"})
        assert ext_res1.success is False
        assert ext_res1.error == "Restricted navigation origin"
        assert "External URL" in ext_res1.message

        # 2. Block external HTTP
        ext_res2 = await executor.execute({"action": "navigate", "url": "http://evil-attacker-site.com"})
        assert ext_res2.success is False
        assert ext_res2.error == "Restricted navigation origin"

        # 3. Block javascript pseudo-protocol
        js_res = await executor.execute({"action": "navigate", "url": "javascript:alert(1)"})
        assert js_res.success is False
        assert js_res.error == "Restricted navigation origin"

        # 4. Allow local mock-site origin
        local_res = await executor.execute({"action": "navigate", "url": MOCK_SITE_URL})
        assert local_res.success is True


@pytest.mark.asyncio
async def test_invalid_targets_return_structured_errors():
    """Verify missing, invalid, or ambiguous targets return structured errors without raising."""
    async with PlaywrightExecutor(headless=True) as executor:
        await executor.execute({"action": "navigate", "url": MOCK_SITE_URL})

        # 1. Missing target on click
        res_no_target = await executor.execute({"action": "click"})
        assert res_no_target.success is False
        assert res_no_target.error == "Missing target"
        assert "Missing target" in res_no_target.message

        # 2. Nonexistent target on click
        res_not_found = await executor.execute({"action": "click", "target": "nonexistent-button-9999"})
        assert res_not_found.success is False
        assert res_not_found.error == "Target not found"
        assert "not found" in res_not_found.message

        # 3. Missing target on type
        res_type_no_target = await executor.execute({"action": "type", "text": "hello"})
        assert res_type_no_target.success is False
        assert res_type_no_target.error == "Missing target"

        # 4. Nonexistent target on type
        res_type_not_found = await executor.execute({
            "action": "type",
            "target": "ghost-input-field",
            "text": "data",
        })
        assert res_type_not_found.success is False
        assert res_type_not_found.error == "Target not found"


@pytest.mark.asyncio
async def test_complete_hardcoded_invoice_workflow():
    """Execute complete Step 5 hardcoded invoice workflow:
    
    1. Open the mock dashboard.
    2. Navigate to the invoices page.
    3. Fill in the invoice search field.
    4. Click the appropriate View button.
    5. Verify that the invoice details are visible.
    6. Verify structured result returned for each action.
    """
    async with PlaywrightExecutor(headless=True) as executor:
        # 1. Open mock dashboard
        res1 = await executor.execute({
            "action": "navigate",
            "url": MOCK_SITE_URL,
        })
        assert res1.success is True
        assert "Navigated to" in res1.message

        # 2. Navigate to the invoices page
        res2 = await executor.execute({
            "action": "click",
            "target": "nav-invoices",
        })
        assert res2.success is True
        assert res2.message == "Navigated to Invoices page"

        # 3. Fill in the invoice search field
        res3 = await executor.execute({
            "action": "type",
            "target": "invoice-search",
            "text": "INV-1001",
        })
        assert res3.success is True
        assert res3.message == "Typed 'INV-1001' into 'invoice-search'"

        # 4. Click appropriate View button
        res4 = await executor.execute({
            "action": "click",
            "target": "view-invoice-1001",
        })
        assert res4.success is True
        assert res4.message == "Invoice details opened"

        # 5. Verify that the invoice details are visible
        res5 = await executor.execute({
            "action": "wait",
            "target": "close-invoice-modal-btn",
            "wait_seconds": 2.0,
        })
        assert res5.success is True
        assert "appeared and is visible" in res5.message

        # Deep verification of modal content
        page = executor.current_page
        assert page is not None
        modal_title = await page.locator("#modal-invoice-id").text_content()
        client_name = await page.locator("#modal-client-name").text_content()
        grand_total = await page.locator("#modal-grand-total").text_content()

        assert "INV-1001" in (modal_title or "")
        assert "Apex Systems" in (client_name or "")
        assert "$2,350.00" in (grand_total or "")
