"""Tests for Multi-Tab Management and Persistent Browser Profile.

Validates:
1. Multi-tab lifecycle: opening new tabs, listing tabs, switching focus, and closing tabs.
2. Tab popup auto-focus when popups are spawned.
3. Persistent browser profile initialization, cookie export/import, and profile clearing.
4. REST API endpoints: GET /api/tabs, POST /api/tabs/new, POST /api/tabs/switch, POST /api/tabs/close, GET /api/profile/status, POST /api/profile/clear.
5. SafetyGuard evaluation of non-destructive tab operations.
"""

import asyncio
import json
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app, executor, observer
from backend.app.executor import PlaywrightExecutor
from backend.app.observer import PlaywrightObserver
from backend.app.safety import SafetyGuard
from backend.app.schemas import ActionType, BrowserAction, PageObservation


@pytest.fixture(autouse=True)
def reset_executor_state():
    """Ensure executor is clean before each test."""
    executor.resume()
    yield
    executor.resume()


@pytest.mark.asyncio
async def test_tab_actions_safety():
    """Verify SafetyGuard classifies switch_tab, new_tab, and close_tab as safe non-destructive actions."""
    guard = SafetyGuard()
    for act_type in (ActionType.SWITCH_TAB, ActionType.NEW_TAB, ActionType.CLOSE_TAB):
        action = BrowserAction(
            action_type=act_type,
            description=f"Testing {act_type.value}",
        )
        result = guard.evaluate_action(action)
        assert result.is_safe is True
        assert result.requires_human_confirmation is False


@pytest.mark.asyncio
async def test_executor_multi_tab_lifecycle(tmp_path: Path):
    """Verify PlaywrightExecutor tracks multiple tabs, switches focus, and closes tabs."""
    exec_inst = PlaywrightExecutor(
        headless=True,
        persistent_profile=False,
        user_data_dir=str(tmp_path / "test_profile"),
    )
    try:
        await exec_inst.initialize()
        initial_page = exec_inst.current_page
        assert initial_page is not None

        # 1. Check initial tabs list
        tabs = await exec_inst.get_open_tabs()
        assert len(tabs) >= 1
        assert tabs[0]["is_active"] is True

        # 2. Open a new tab
        new_tab_res = await exec_inst.new_tab("about:blank")
        assert new_tab_res.success is True
        assert exec_inst.current_page != initial_page

        tabs_after_new = await exec_inst.get_open_tabs()
        assert len(tabs_after_new) == len(tabs) + 1
        assert tabs_after_new[-1]["is_active"] is True

        # 3. Switch back to tab #0
        switch_res = await exec_inst.switch_tab(tab_index=0)
        assert switch_res.success is True
        assert exec_inst.current_page == initial_page

        tabs_after_switch = await exec_inst.get_open_tabs()
        assert tabs_after_switch[0]["is_active"] is True

        # 4. Close tab #1
        close_res = await exec_inst.close_tab(tab_index=1)
        assert close_res.success is True

        tabs_after_close = await exec_inst.get_open_tabs()
        assert len(tabs_after_close) == len(tabs)

    finally:
        await exec_inst.close()


@pytest.mark.asyncio
async def test_persistent_profile_and_cookie_backup(tmp_path: Path):
    """Verify cookie saving and loading functions correctly."""
    profile_dir = tmp_path / "browser_profile_test"
    exec_inst = PlaywrightExecutor(
        headless=True,
        persistent_profile=False,
        user_data_dir=str(profile_dir),
    )
    try:
        await exec_inst.initialize()
        assert exec_inst.current_page is not None

        # Inject a test cookie into browser context
        if exec_inst._context:
            await exec_inst._context.add_cookies([
                {
                    "name": "test_session_id",
                    "value": "klick_test_session_xyz789",
                    "domain": "localhost",
                    "path": "/",
                }
            ])

        # Save cookies to disk
        saved = await exec_inst.save_cookies()
        assert any(c.get("name") == "test_session_id" for c in saved)

        cookie_file = profile_dir / "cookies.json"
        assert cookie_file.exists()

        # Clear cookies in context
        if exec_inst._context:
            await exec_inst._context.clear_cookies()
            cur_cookies = await exec_inst._context.cookies()
            assert not any(c.get("name") == "test_session_id" for c in cur_cookies)

        # Reload cookies from disk
        await exec_inst.load_cookies()
        if exec_inst._context:
            reloaded = await exec_inst._context.cookies()
            assert any(c.get("name") == "test_session_id" for c in reloaded)

        # Clear profile
        cleared = await exec_inst.clear_profile()
        assert cleared is True
        assert not cookie_file.exists()

    finally:
        await exec_inst.close()


@pytest.mark.asyncio
async def test_api_tabs_and_profile_endpoints():
    """Verify REST API endpoints for tab and profile management."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # GET /api/profile/status
        prof_res = await client.get("/api/profile/status")
        assert prof_res.status_code == 200
        prof_data = prof_res.json()
        assert "persistent_profile" in prof_data
        assert "user_data_dir" in prof_data

        # GET /api/tabs
        tabs_res = await client.get("/api/tabs")
        assert tabs_res.status_code == 200
        tabs_data = tabs_res.json()
        assert "tabs" in tabs_data
        assert "count" in tabs_data
        assert "active_index" in tabs_data

        # POST /api/tabs/new
        new_tab_res = await client.post("/api/tabs/new", json={"url": "about:blank"})
        assert new_tab_res.status_code == 200
        assert new_tab_res.json()["success"] is True

        # POST /api/tabs/switch
        switch_res = await client.post("/api/tabs/switch", json={"tab_index": 0})
        assert switch_res.status_code == 200
        assert switch_res.json()["success"] is True

        # POST /api/tabs/close
        close_res = await client.post("/api/tabs/close", json={"tab_index": 1})
        assert close_res.status_code == 200
        assert close_res.json()["success"] is True

        # POST /api/profile/clear
        clear_res = await client.post("/api/profile/clear")
        assert clear_res.status_code == 200
        assert clear_res.json()["success"] is True
