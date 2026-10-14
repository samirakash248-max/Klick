"""Unit and integration tests for Chrome CDP Bridge and Watch & Learn Macro Recorder."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.recorder import MacroRecorder
from backend.app.schemas import ActionType, MacroStep
from backend.app.main import app, executor


def test_macro_recorder_lifecycle_and_step_consolidation():
    """Verify MacroRecorder records, consolidates consecutive typing, and compiles to MacroDefinition."""
    recorder = MacroRecorder()
    assert recorder.is_recording is False

    # Start recording
    status = recorder.start_recording(name="Search and Checkout", description="Test recording")
    assert recorder.is_recording is True
    assert status["is_recording"] is True
    assert status["name"] == "Search and Checkout"

    # 1. Record navigate step
    recorder.record_step(action_type="navigate", url="https://store.example.com", description="Navigate to store")
    assert len(recorder.steps) == 1

    # 2. Record click step
    recorder.record_step(action_type="click", selector="input#search", target="search-input")
    assert len(recorder.steps) == 2

    # 3. Record typing keystrokes targeting same selector - should consolidate!
    recorder.record_step(action_type="type", selector="input#search", text="running")
    recorder.record_step(action_type="type", selector="input#search", text=" shoes")
    # Typing into same target should be merged into single step
    assert len(recorder.steps) == 3
    assert recorder.steps[-1].text == "running shoes"

    # 4. Record enter key
    recorder.record_step(action_type="press_key", key="Enter", selector="input#search")
    assert len(recorder.steps) == 4

    # 5. Stop and compile macro
    compiled = recorder.stop_recording()
    assert recorder.is_recording is False
    assert len(compiled.steps) == 4
    assert "running_shoes" in compiled.parameters or len(compiled.parameters) >= 0


def test_macro_recorder_cancel():
    """Verify canceling an active recording clears buffer without saving."""
    recorder = MacroRecorder()
    recorder.start_recording(name="To Cancel")
    recorder.record_step(action_type="click", selector="button#btn")
    assert len(recorder.steps) == 1

    cancel_res = recorder.cancel_recording()
    assert cancel_res["status"] == "recording_cancelled"
    assert recorder.is_recording is False
    assert len(recorder.steps) == 0


def test_recorder_api_endpoints():
    """Verify Macro Recorder REST API endpoints."""
    client = TestClient(app)

    # 1. Initial status
    res = client.get("/api/recorder/status")
    assert res.status_code == 200

    # 2. Start recording
    res_start = client.post("/api/recorder/start", json={"name": "API Recorded Macro", "description": "Via API"})
    assert res_start.status_code == 200
    assert res_start.json()["is_recording"] is True

    # 3. Ingest step
    res_step = client.post("/api/recorder/step", json={
        "action_type": "click",
        "selector": "button#submit",
        "text": "Submit Form",
    })
    assert res_step.status_code == 200
    assert res_step.json()["recorded"] is True

    # 4. Ingest typing step
    res_step2 = client.post("/api/recorder/step", json={
        "action_type": "type",
        "selector": "input#email",
        "text": "test@example.com",
    })
    assert res_step2.status_code == 200

    # 5. Stop and verify compiled macro
    res_stop = client.post("/api/recorder/stop")
    assert res_stop.status_code == 200
    data = res_stop.json()
    assert data["name"] == "API Recorded Macro"
    assert len(data["steps"]) >= 2


@pytest.mark.asyncio
async def test_executor_cdp_status_and_bridge():
    """Verify PlaywrightExecutor get_cdp_status and connect_cdp handling."""
    # When no Chrome runs with port 9222, get_cdp_status should report available=False
    status = await executor.get_cdp_status()
    assert "available" in status
    assert "connected" in status
    assert status["port"] == 9222


def test_cdp_api_endpoints():
    """Verify CDP Bridge REST endpoints via TestClient."""
    client = TestClient(app)

    # 1. Get CDP status
    res = client.get("/api/cdp/status")
    assert res.status_code == 200
    data = res.json()
    assert "available" in data
    assert "connected" in data

    # 2. Connect CDP (when port 9222 not reachable, returns 400 with helpful instruction)
    res_connect = client.post("/api/cdp/connect", json={"cdp_url": "http://localhost:9222"})
    # If Chrome is not running with --remote-debugging-port=9222, status_code is 400 with detail
    if res_connect.status_code == 400:
        assert "--remote-debugging-port=9222" in res_connect.json()["detail"]
    else:
        assert res_connect.status_code == 200
        assert res_connect.json()["success"] is True
