"""Tests for Cyclic Stagnation Detection & Self-Healing Backtracking and Deterministic RPA Macros.

Verifies:
1. Multi-tiered self-healing recovery ladder in ControlledAgentRunner when identical actions or DOM stagnation occur.
2. Ungrounded selector self-healing reprompting.
3. Macro template parameter interpolation and extraction.
4. Deterministic macro execution (0ms AI latency) with step-by-step telemetry.
5. Hybrid RPA-to-Agent fallback when a macro step fails due to UI drift.
6. FastAPI REST endpoints for /api/macros CRUD and replay.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.app.agent_runner import ControlledAgentRunner, StopReason
from backend.app.events import EventManager, EventType
from backend.app.executor import PlaywrightExecutor
from backend.app.macros import (
    MacroManager,
    extract_template_parameters,
    interpolate_template,
)
from backend.app.model_client import ModelClientProtocol
from backend.app.safety import SafetyGuard
from backend.app.schemas import (
    ActionType,
    AgentResponse,
    AgentRunRequest,
    AgentRunState,
    AgentThought,
    BrowserAction,
    ElementCoordinates,
    ElementDescriptor,
    ExecutionResult,
    MacroDefinition,
    MacroReplayRequest,
    MacroStep,
    PageObservation,
    RiskLevel,
    RunStatus,
    StepRecord,
)


# =========================================================================
# MOCK FIXTURES & HELPERS
# =========================================================================

class MockStagnantModelClient:
    """Model client that always proposes the same clicking action."""

    def __init__(self, action: Optional[BrowserAction] = None) -> None:
        self.action = action or BrowserAction(
            action_type=ActionType.CLICK,
            target="elem-dead-button",
            selector="[data-agent-id='elem-dead-button']",
            description="Click dead button",
        )
        self.call_count = 0
        self.received_goals: List[str] = []

    async def get_next_action(
        self,
        goal: str,
        observation: PageObservation,
        step_number: int,
        max_steps: int,
    ) -> AgentResponse:
        self.call_count += 1
        self.received_goals.append(goal)
        return AgentResponse(
            thought=AgentThought(reflection="Observed state.", reasoning="Clicking dead button again."),
            action=self.action,
        )


def make_sample_observation(url: str = "http://example.com/test", title: str = "Test Page") -> PageObservation:
    return PageObservation(
        url=url,
        title=title,
        dom_summary="<div><button data-agent-id='elem-dead-button'>Click Me</button></div>",
        interactive_elements=[
            ElementDescriptor(
                id="elem-dead-button",
                tag_name="button",
                selector="[data-agent-id='elem-dead-button']",
                text="Click Me",
            ),
            ElementDescriptor(
                id="elem-alt-link",
                tag_name="a",
                selector="[data-agent-id='elem-alt-link']",
                text="Alternate Path",
            ),
        ],
    )


# =========================================================================
# UNIT TESTS: IMPROVEMENT 4 - STAGNATION DETECTION & SELF-HEALING
# =========================================================================

@pytest.mark.asyncio
async def test_stagnation_self_healing_ladder_escalation():
    """Verify runner detects repeated actions on unchanged pages and climbs the self-healing ladder."""
    obs = make_sample_observation()
    mock_observer = MagicMock()
    mock_observer.observe = AsyncMock(return_value=obs)

    mock_executor = MagicMock()
    mock_executor.initialize = AsyncMock(return_value=MagicMock(url="http://example.com/test", is_closed=lambda: False))
    mock_executor.current_page = MagicMock(url="http://example.com/test", is_closed=lambda: False)
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        success=True,
        action_type=ActionType.CLICK,
        message="Clicked dead button (no state change)",
    ))
    mock_executor.dismiss_overlays = AsyncMock(return_value=ExecutionResult(success=True, action_type=ActionType.PRESS_KEY, message="Overlays dismissed"))
    mock_executor.go_back = AsyncMock(return_value=ExecutionResult(success=True, action_type=ActionType.GO_BACK, message="Backtracked"))
    mock_executor.scroll = AsyncMock(return_value=ExecutionResult(success=True, action_type=ActionType.SCROLL, message="Scrolled 600px"))

    model_client = MockStagnantModelClient()
    events = EventManager()

    runner = ControlledAgentRunner(
        model_client=model_client,
        observer=mock_observer,
        executor=mock_executor,
        events=events,
        auto_approve=True,
    )

    req = AgentRunRequest(goal="Reach checkout page", max_steps=10)
    state = await runner.run(req)

    # Verify self-healing recovery events were emitted
    history = events.get_history(limit=100)
    recovery_events = [e for e in history if e.type == EventType.SELF_HEALING_RECOVERY]
    assert len(recovery_events) >= 3

    # Verify self-healing ladder was climbed
    assert state.self_healing_count >= 3
    assert len(state.recovery_notes) >= 3
    assert mock_executor.dismiss_overlays.called
    assert mock_executor.go_back.called
    assert mock_executor.scroll.called

    # Verify model client was provided negative feedback in the goal context
    assert any("[RECOVERY GUIDANCE]" in g for g in model_client.received_goals)

    # After climbing all 3 tiers with no progress, runner should stop cleanly
    assert state.status == RunStatus.FAILED
    assert "Stuck loop detected" in (state.error or "")


@pytest.mark.asyncio
async def test_ungrounded_selector_self_healing():
    """Verify an ungrounded selector triggers self-healing recovery before crashing."""
    obs = make_sample_observation()
    mock_observer = MagicMock()
    mock_observer.observe = AsyncMock(return_value=obs)

    mock_executor = MagicMock()
    mock_executor.initialize = AsyncMock(return_value=MagicMock(url="http://example.com/test", is_closed=lambda: False))
    mock_executor.current_page = MagicMock(url="http://example.com/test", is_closed=lambda: False)
    mock_executor.dismiss_overlays = AsyncMock(return_value=ExecutionResult(success=True, action_type=ActionType.PRESS_KEY, message="Dismissed"))
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(success=True, action_type=ActionType.CLICK, message="Clicked valid link"))

    class AdaptiveModelClient:
        def __init__(self):
            self.step = 0

        async def get_next_action(self, goal, observation, step_number, max_steps):
            self.step += 1
            if self.step == 1:
                # First step: propose hallucinated ungrounded selector
                return AgentResponse(
                    thought=AgentThought(reflection="", reasoning="Picking imaginary element"),
                    action=BrowserAction(
                        action_type=ActionType.CLICK,
                        target="elem-imaginary-button-1234",
                        selector="[data-agent-id='elem-imaginary-button-1234']",
                        description="Click non-existent button",
                    ),
                )
            else:
                # Step 2: Model adapts to feedback and chooses valid grounded element
                return AgentResponse(
                    thought=AgentThought(reflection="Recovered", reasoning="Picking valid button"),
                    action=BrowserAction(
                        action_type=ActionType.CLICK,
                        target="elem-alt-link",
                        selector="[data-agent-id='elem-alt-link']",
                        description="Click alternate link",
                    ),
                )

    runner = ControlledAgentRunner(
        model_client=AdaptiveModelClient(),
        observer=mock_observer,
        executor=mock_executor,
        auto_approve=True,
    )

    req = AgentRunRequest(goal="Click alternate link", max_steps=4)
    state = await runner.run(req)

    # Verify self-healing prevented immediate step 1 abort
    assert state.self_healing_count >= 1
    assert any("ungrounded" in note.lower() for note in state.recovery_notes)


# =========================================================================
# UNIT TESTS: IMPROVEMENT 5 - DETERMINISTIC RPA MACROS
# =========================================================================

def test_macro_interpolation_and_parameter_extraction():
    """Verify template variables like {{query}} are properly extracted and interpolated."""
    template = "https://www.youtube.com/results?search_query={{query}}&page={{page}}"
    params = extract_template_parameters(template)
    assert set(params) == {"query", "page"}

    interpolated = interpolate_template(template, {"query": "casetoo", "page": 1})
    assert interpolated == "https://www.youtube.com/results?search_query=casetoo&page=1"

    # Edge cases
    assert interpolate_template(None, {}) is None
    assert interpolate_template("static text", {"query": "x"}) == "static text"


def test_macro_manager_lifecycle(tmp_path: Path):
    """Verify saving, loading, listing, and deleting macros in MacroManager."""
    mgr = MacroManager(storage_dir=tmp_path)

    macro = MacroDefinition(
        id="test_quick_search",
        name="Test Quick Search",
        description="Search something fast",
        parameters=["query"],
        steps=[
            MacroStep(action_type=ActionType.NAVIGATE, url="https://example.com/search?q={{query}}"),
            MacroStep(action_type=ActionType.WAIT, seconds=0.5),
        ],
    )

    saved = mgr.save_macro(macro)
    assert saved.id == "test_quick_search"
    assert (tmp_path / "test_quick_search.json").exists()

    fetched = mgr.get_macro("test_quick_search")
    assert fetched is not None
    assert fetched.name == "Test Quick Search"
    assert len(fetched.steps) == 2

    macros_list = mgr.list_macros()
    assert any(m.id == "test_quick_search" for m in macros_list)

    assert mgr.delete_macro("test_quick_search") is True
    assert mgr.get_macro("test_quick_search") is None


@pytest.mark.asyncio
async def test_macro_deterministic_replay(tmp_path: Path):
    """Verify a macro executes deterministically with 0ms LLM latency."""
    mgr = MacroManager(storage_dir=tmp_path)
    macro = MacroDefinition(
        id="demo_nav_macro",
        name="Demo Navigation Macro",
        parameters=["url"],
        steps=[
            MacroStep(action_type=ActionType.NAVIGATE, url="{{url}}", description="Navigate to URL"),
            MacroStep(action_type=ActionType.WAIT, seconds=0.1, description="Wait a tick"),
        ],
    )
    mgr.save_macro(macro)

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(return_value=ExecutionResult(
        success=True,
        action_type=ActionType.NAVIGATE,
        message="Action succeeded",
        duration_ms=15.0,
    ))

    result = await mgr.replay_macro(
        macro_id="demo_nav_macro",
        parameters={"url": "https://example.com/target"},
        executor=mock_executor,
        fallback_to_agent=False,
    )

    assert result.success is True
    assert result.steps_executed == 2
    assert result.total_steps == 2
    assert result.fallback_triggered is False
    assert mock_executor.execute.call_count == 2


@pytest.mark.asyncio
async def test_macro_hybrid_agent_fallback(tmp_path: Path):
    """Verify that when a macro step fails due to UI deviation, hybrid AI fallback engages."""
    mgr = MacroManager(storage_dir=tmp_path)
    macro = MacroDefinition(
        id="flaky_macro",
        name="Flaky Macro",
        steps=[
            MacroStep(action_type=ActionType.NAVIGATE, url="https://example.com"),
            MacroStep(action_type=ActionType.CLICK, target="elem-missing-id", description="Click element"),
        ],
    )
    mgr.save_macro(macro)

    call_index = 0

    async def mock_exec_side_effect(act):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            return ExecutionResult(success=True, action_type=ActionType.NAVIGATE, message="Nav ok")
        return ExecutionResult(success=False, action_type=ActionType.CLICK, message="Element not found", error="Not found")

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(side_effect=mock_exec_side_effect)
    mock_executor.current_page = MagicMock()

    mock_agent_runner = MagicMock()
    mock_agent_runner.run = AsyncMock(return_value=AgentRunState(
        run_id="fallback-run-123",
        goal="Recover macro",
        status=RunStatus.COMPLETED,
        max_steps=5,
    ))

    result = await mgr.replay_macro(
        macro_id="flaky_macro",
        parameters={},
        executor=mock_executor,
        agent_runner=mock_agent_runner,
        fallback_to_agent=True,
    )

    assert result.success is True
    assert result.fallback_triggered is True
    assert result.agent_run_id == "fallback-run-123"
    assert mock_agent_runner.run.called


# =========================================================================
# INTEGRATION TESTS: REST API ENDPOINTS
# =========================================================================

@pytest.mark.asyncio
async def test_api_macros_endpoints():
    """Verify /api/macros REST API endpoints."""
    from httpx import ASGITransport, AsyncClient
    from backend.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. List macros (seed macros should exist)
        res = await client.get("/api/macros")
        assert res.status_code == 200
        macros = res.json()
        assert len(macros) >= 1
        assert any(m["id"] == "youtube_search_play" for m in macros)

        # 2. Get specific macro
        res_yt = await client.get("/api/macros/youtube_search_play")
        assert res_yt.status_code == 200
        assert res_yt.json()["name"] == "YouTube Search & Play"

        # 3. Create custom macro
        custom_macro = {
            "id": "api_test_macro",
            "name": "API Test Macro",
            "description": "Created via API test",
            "parameters": ["query"],
            "steps": [
                {"action_type": "wait", "seconds": 0.1, "description": "Quick wait"}
            ],
            "default_params": {"query": "sample"},
        }
        res_create = await client.post("/api/macros", json=custom_macro)
        assert res_create.status_code == 200
        assert res_create.json()["id"] == "api_test_macro"

        # 4. Delete custom macro
        res_del = await client.delete("/api/macros/api_test_macro")
        assert res_del.status_code == 200
        assert res_del.json()["success"] is True
