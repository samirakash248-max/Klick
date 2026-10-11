"""Unit tests for Member A's ControlledAgentRunner.

Tests cognitive loop coordination, action limits, grounding validation,
repeated-action loop detection, completion verification, safety guardrails,
and structured event emission using mock dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from backend.app.agent_runner import (
    ControlledAgentRunner,
    StopReason,
    is_action_grounded,
    verify_task_completion,
)
from backend.app.model_client import (
    ModelResponseParseError,
    OllamaModelClient,
)
from backend.app.schemas import (
    ActionType,
    AgentResponse,
    AgentRunRequest,
    AgentThought,
    BrowserAction,
    ElementDescriptor,
    ExecutionResult,
    PageObservation,
    RiskLevel,
    RunStatus,
    SafetyCheckResult,
)


def make_sample_thought(reasoning: str = "Test reasoning") -> AgentThought:
    return AgentThought(
        reflection="Observing page",
        reasoning=reasoning,
        plan=["Step 1"],
    )


def make_sample_observation(
    url: str = "http://localhost:8080/test.html",
    title: str = "Test Page",
    dom_summary: str = "button#submit-btn 'Submit'",
    interactive_elements: list[ElementDescriptor] | None = None,
    snippet: str = "Test page text content",
) -> PageObservation:
    elements = interactive_elements or [
        ElementDescriptor(
            tag_name="button",
            selector="#submit-btn",
            id="submit-btn",
            text="Submit",
            role="button",
        )
    ]
    return PageObservation(
        url=url,
        title=title,
        dom_summary=dom_summary,
        interactive_elements=elements,
        page_text_snippet=snippet,
    )


@pytest.mark.asyncio
async def test_successful_multi_action_verified_completion():
    """Verify multi-action run succeeds when page evidence corroborates completion."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs_step1 = make_sample_observation(title="Form Page", snippet="Please click submit")
    obs_step2 = make_sample_observation(
        title="Confirmation Page",
        dom_summary="div 'Submission Confirmed'",
        snippet="Thank you! Your submission was confirmed and completed.",
    )
    mock_observer.observe = AsyncMock(side_effect=[obs_step1, obs_step2])

    resp1 = AgentResponse(
        thought=make_sample_thought("Click submit"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Click submit button",
        ),
    )
    resp2 = AgentResponse(
        thought=make_sample_thought("Task complete"),
        action=BrowserAction(
            action_type=ActionType.FINISH,
            description="Form submitted and confirmed",
        ),
    )
    mock_model.get_next_action = AsyncMock(side_effect=[resp1, resp2])

    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            action_type=ActionType.CLICK,
            message="Clicked #submit-btn",
        )
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(
        return_value=SafetyCheckResult(is_safe=True)
    )

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    req = AgentRunRequest(goal="Submit the form", max_steps=5)
    state = await runner.run(req)

    assert state.status == RunStatus.COMPLETED
    assert "Verified" in (state.final_output or "")
    assert len(state.history) == 1
    assert mock_executor.execute.call_count == 1
    assert mock_observer.observe.call_count == 2


@pytest.mark.asyncio
async def test_repeated_action_stuck_detection():
    """Verify runner stops when same action is proposed 3 times on unchanged page."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs = make_sample_observation(url="http://localhost:8080/stuck", title="Stuck Page")
    mock_observer.observe = AsyncMock(return_value=obs)

    stuck_resp = AgentResponse(
        thought=make_sample_thought("Clicking again"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Repeated click",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=stuck_resp)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(success=True, action_type=ActionType.CLICK, message="Clicked")
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
        stuck_threshold=3,
    )

    state = await runner.run(AgentRunRequest(goal="Click button", max_steps=10))

    assert state.status == RunStatus.FAILED
    assert "Stuck loop detected" in (state.error or "")
    # Should stop at step 3 before indefinite loop
    assert state.current_step == 3


@pytest.mark.asyncio
async def test_malformed_model_output_error_handling():
    """Verify runner handles model parse failures gracefully without crashing."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    mock_observer.observe = AsyncMock(return_value=make_sample_observation())
    mock_model.get_next_action = AsyncMock(
        side_effect=ModelResponseParseError("Model produced invalid JSON syntax")
    )
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_safety.inspect_observation = MagicMock(return_value=[])

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Any goal"))

    assert state.status == RunStatus.FAILED
    assert "Model client failure" in (state.error or "")


@pytest.mark.asyncio
async def test_executor_failure_handling():
    """Verify execution failures halt the run safely and record the error."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    mock_observer.observe = AsyncMock(return_value=make_sample_observation())
    mock_model.get_next_action = AsyncMock(
        return_value=AgentResponse(
            thought=make_sample_thought("Click missing"),
            action=BrowserAction(
                action_type=ActionType.CLICK,
                selector="#submit-btn",
                description="Click button",
            ),
        )
    )
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(
            success=False,
            action_type=ActionType.CLICK,
            message="Click failed",
            error="Node detached from document tree",
        )
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Click button"))

    assert state.status == RunStatus.FAILED
    assert "Execution failed" in (state.error or "")


@pytest.mark.asyncio
async def test_max_steps_action_limit():
    """Verify runner enforces the configured maximum step limit."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    # Changing observation URL so stuck detection doesn't trigger
    obs1 = make_sample_observation(url="http://localhost:8080/page1")
    obs2 = make_sample_observation(url="http://localhost:8080/page2")
    obs3 = make_sample_observation(url="http://localhost:8080/page3")
    mock_observer.observe = AsyncMock(side_effect=[obs1, obs2, obs3])

    click_action = AgentResponse(
        thought=make_sample_thought("Browse"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Navigate page",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=click_action)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(success=True, action_type=ActionType.CLICK, message="OK")
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
        default_max_steps=3,
    )

    state = await runner.run(AgentRunRequest(goal="Browse pages", max_steps=3))

    assert state.status == RunStatus.FAILED
    assert "limit of 3 actions" in (state.final_output or "")
    assert state.current_step == 3


@pytest.mark.asyncio
async def test_unverified_model_finish_rejected():
    """Verify uncorroborated finish claim is marked unverified/failed."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    # Page has no completion indicator
    unverified_obs = make_sample_observation(
        title="Error 404",
        dom_summary="h1 'Not Found'",
        snippet="The requested page could not be located on the server.",
    )
    mock_observer.observe = AsyncMock(return_value=unverified_obs)

    finish_resp = AgentResponse(
        thought=make_sample_thought("Done"),
        action=BrowserAction(
            action_type=ActionType.FINISH,
            description="I finished buying the item",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=finish_resp)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Purchase product"))

    assert state.status == RunStatus.FAILED
    assert "does not corroborate completion" in (state.error or "")


@pytest.mark.asyncio
async def test_ungrounded_selector_rejected():
    """Verify actions referencing non-existent selectors are rejected before execution."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    mock_observer.observe = AsyncMock(return_value=make_sample_observation())

    ungrounded_resp = AgentResponse(
        thought=make_sample_thought("Click ghost"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#phantom-element-never-observed",
            description="Click ghost button",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=ungrounded_resp)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_safety.inspect_observation = MagicMock(return_value=[])

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Click button"))

    assert state.status == RunStatus.FAILED
    assert "not grounded" in (state.error or "")
    assert mock_executor.execute.call_count == 0


@pytest.mark.asyncio
async def test_observer_failure_handling():
    """Verify observer capture exception is caught safely."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    mock_observer.observe = AsyncMock(side_effect=RuntimeError("Browser page disconnected"))
    mock_executor.initialize = AsyncMock(return_value=MagicMock())

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Observe"))

    assert state.status == RunStatus.FAILED
    assert "Observation capture failed" in (state.error or "")


@pytest.mark.asyncio
async def test_safety_guard_blocks_action():
    """Verify unsafe action transitions run to AWAITING_CONFIRMATION."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs = make_sample_observation(
        dom_summary="button#purge-btn 'Purge Database'",
        interactive_elements=[
            ElementDescriptor(tag_name="button", selector="#purge-btn", text="Purge Database")
        ],
    )
    mock_observer.observe = AsyncMock(return_value=obs)

    destructive_resp = AgentResponse(
        thought=make_sample_thought("Delete all"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#purge-btn",
            description="Purge all user database records",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=destructive_resp)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(
        return_value=SafetyCheckResult(
            is_safe=False,
            risk_level=RiskLevel.CRITICAL,
            reason="Destructive keyword 'purge' detected",
            requires_human_confirmation=True,
        )
    )

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Manage data"))

    assert state.status == RunStatus.AWAITING_CONFIRMATION
    assert "Blocked unsafe action" in (state.error or "")
    assert mock_executor.execute.call_count == 0


@pytest.mark.asyncio
async def test_cancellation_request_stop():
    """Verify request_stop halts the loop at the next step."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    mock_observer.observe = AsyncMock(return_value=make_sample_observation())
    mock_executor.initialize = AsyncMock(return_value=MagicMock())

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    # Trigger stop immediately
    runner.request_stop()
    state = await runner.run(AgentRunRequest(goal="Cancelled goal"))

    assert state.status == RunStatus.STOPPED
    assert "stopped by user request" in (state.final_output or "")
    assert mock_model.get_next_action.call_count == 0


# ---------------------------------------------------------------------------
# Focused regression tests for Hardening Member A's Step 3
# ---------------------------------------------------------------------------


def test_goal_keyword_without_completion_remains_unverified():
    """Verify that a goal keyword appearing on the page does not falsely verify completion."""
    obs = make_sample_observation(
        url="http://localhost:8080/products",
        title="Electronics Store - Shopping Cart (0)",
        dom_summary="[0] <a href='/cart'> 'Cart (0)' -> selector: `#cart-link`\n[1] <h3> 'Pro Laptop $1299' -> selector: `#item-1`",
        snippet="Browse our collection of laptops. Your cart is currently empty (0 items).",
    )
    assert not verify_task_completion(
        goal="Add laptop to the cart",
        finish_description="I added the laptop to the cart",
        observation=obs,
        history=[],
    )


def test_generic_completion_words_alone_do_not_verify():
    """Verify that words like 'complete', 'saved', or 'success' in unrelated copy do not verify completion."""
    obs = make_sample_observation(
        url="http://localhost:8080/about",
        title="Company Information",
        dom_summary="[0] <div> 'Footer' -> selector: `#footer`",
        snippet="View our complete product catalog. Saved items can be accessed via profile. Success stories of our happy clients.",
    )
    assert not verify_task_completion(
        goal="Submit contact inquiry form",
        finish_description="Inquiry submitted",
        observation=obs,
        history=[],
    )


def test_plausible_explicit_completion_accepted():
    """Verify that explicit completion evidence in observation and execution history is accepted."""
    obs = make_sample_observation(
        url="http://localhost:8080/contact/thank-you",
        title="Thank You - Submission Received",
        dom_summary="[0] <h2> 'Message Sent Successfully' -> selector: `#confirm-msg`",
        snippet="Thank you for contacting us! Your submission was confirmed and our team will respond shortly.",
    )
    mock_step = MagicMock()
    mock_step.execution_result.success = True

    assert verify_task_completion(
        goal="Submit contact inquiry form",
        finish_description="Contact form submitted and confirmed",
        observation=obs,
        history=[mock_step],
    )


def test_search_box_alone_does_not_prove_search_completed():
    """Verify that the mere presence of a search input or 'search' word does not verify search completion."""
    obs_unsearched = make_sample_observation(
        url="http://localhost:8080/",
        title="Store Home Page",
        dom_summary="[0] <input> 'Search products...' -> selector: `#search-box`",
        snippet="Search through hundreds of electronics. Enter your search query above.",
    )
    assert not verify_task_completion(
        goal="Search for headphones",
        finish_description="I searched for headphones",
        observation=obs_unsearched,
        history=[],
    )

    obs_searched = make_sample_observation(
        url="http://localhost:8080/search?q=headphones",
        title="Search Results for 'headphones'",
        dom_summary="[0] <div> 'Showing results for headphones' -> selector: `#results`",
        snippet="Showing 8 products found matching 'headphones'. Bluetooth Over-Ear Headphones $99.",
    )
    assert verify_task_completion(
        goal="Search for headphones",
        finish_description="Found 8 headphone products",
        observation=obs_searched,
        history=[],
    )


@pytest.mark.asyncio
async def test_max_steps_exhaustion_never_returns_completed_or_success():
    """Verify maximum-step limit exhaustion returns RunStatus.FAILED and never RunStatus.COMPLETED."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs = make_sample_observation(url="http://localhost:8080/catalog")
    mock_observer.observe = AsyncMock(return_value=obs)

    step_action = AgentResponse(
        thought=make_sample_thought("Browse page"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Click next page",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=step_action)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(success=True, action_type=ActionType.CLICK, message="OK")
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
        default_max_steps=2,
    )

    state = await runner.run(AgentRunRequest(goal="Exhaust all steps", max_steps=2))

    assert state.status == RunStatus.FAILED
    assert state.status != RunStatus.COMPLETED
    assert "limit of 2 actions" in (state.final_output or "")
    assert state.current_step == 2

    run_finished_calls = [
        call for call in mock_events.emit.call_args_list
        if getattr(call[0][0], "value", call[0][0]) == "run_finished"
    ]
    assert len(run_finished_calls) == 1
    event_payload = run_finished_calls[0][0][2]
    assert event_payload["status"] == RunStatus.FAILED.value
    assert event_payload["stop_reason"] == StopReason.MAX_STEPS_REACHED.value
    assert event_payload["completion_verified"] is False


@pytest.mark.asyncio
async def test_genuinely_verified_success_reports_completed_status():
    """Verify genuinely verified task completes with RunStatus.COMPLETED and completion_verified=True."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs_step1 = make_sample_observation(title="Checkout Form")
    obs_step2 = make_sample_observation(
        title="Order Confirmation",
        snippet="Thank you for your order! Order confirmed: #98765.",
    )
    mock_observer.observe = AsyncMock(side_effect=[obs_step1, obs_step2])

    resp1 = AgentResponse(
        thought=make_sample_thought("Place order"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Click place order",
        ),
    )
    resp2 = AgentResponse(
        thought=make_sample_thought("Order placed"),
        action=BrowserAction(
            action_type=ActionType.FINISH,
            description="Order confirmed and placed",
        ),
    )
    mock_model.get_next_action = AsyncMock(side_effect=[resp1, resp2])
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_executor.execute = AsyncMock(
        return_value=ExecutionResult(success=True, action_type=ActionType.CLICK, message="OK")
    )
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(return_value=SafetyCheckResult(is_safe=True))

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
        auto_approve=True,
    )

    state = await runner.run(AgentRunRequest(goal="Place order", max_steps=5))

    assert state.status == RunStatus.COMPLETED
    assert "Verified" in (state.final_output or "")
    assert len(state.history) == 1

    run_finished_calls = [
        call for call in mock_events.emit.call_args_list
        if getattr(call[0][0], "value", call[0][0]) == "run_finished"
    ]
    assert len(run_finished_calls) == 1
    event_payload = run_finished_calls[0][0][2]
    assert event_payload["status"] == RunStatus.COMPLETED.value
    assert event_payload["stop_reason"] == StopReason.COMPLETED.value
    assert event_payload["completion_verified"] is True


def test_selector_grounding_accepts_structured_interactive_elements():
    """Verify that valid observed selectors matching structured interactive elements are accepted."""
    obs = make_sample_observation(
        interactive_elements=[
            ElementDescriptor(
                tag_name="button",
                selector="#btn-submit",
                id="btn-submit",
                text="Submit",
            ),
            ElementDescriptor(
                tag_name="button",
                selector="button:nth-of-type(2)",
                id=None,
                text="Cancel",
            ),
            ElementDescriptor(
                tag_name="input",
                selector="[data-agent-id='agent-input-1']",
                id="agent-input-1",
                text="",
            ),
        ]
    )

    assert is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="#btn-submit", description="test"),
        obs,
    )
    assert is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="button:nth-of-type(2)", description="test"),
        obs,
    )
    assert is_action_grounded(
        BrowserAction(action_type=ActionType.TYPE, selector="[data-agent-id='agent-input-1']", description="test"),
        obs,
    )
    assert is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="btn-submit", description="test"),
        obs,
    )


def test_selector_grounding_rejects_substring_only_matches():
    """Verify that common selectors appearing only as substrings in dom_summary/text are strictly rejected."""
    obs = make_sample_observation(
        dom_summary="[0] <button> 'Submit All' -> selector: `#real-btn`\n[1] <input> 'User Input' -> selector: `#username`",
        snippet="Please click the button to input details. Check element #e1 before leaving.",
        interactive_elements=[
            ElementDescriptor(
                tag_name="button",
                selector="#real-btn",
                id="real-btn",
                text="Submit All",
            ),
            ElementDescriptor(
                tag_name="input",
                selector="#username",
                id="username",
                text="User Input",
            ),
        ],
    )

    assert not is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="button", description="test"),
        obs,
    )
    assert not is_action_grounded(
        BrowserAction(action_type=ActionType.TYPE, selector="input", description="test"),
        obs,
    )
    assert not is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="#e1", description="test"),
        obs,
    )
    assert not is_action_grounded(
        BrowserAction(action_type=ActionType.CLICK, selector="e1", description="test"),
        obs,
    )


@pytest.mark.asyncio
async def test_step_counter_semantics_distinguishes_decisions_from_executed():
    """Document that state.current_step counts decision iterations while len(history) counts executed actions."""
    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs = make_sample_observation()
    mock_observer.observe = AsyncMock(return_value=obs)

    # Action blocked by safety at step 1
    action_resp = AgentResponse(
        thought=make_sample_thought("Unsafe action"),
        action=BrowserAction(
            action_type=ActionType.CLICK,
            selector="#submit-btn",
            description="Dangerous click",
        ),
    )
    mock_model.get_next_action = AsyncMock(return_value=action_resp)
    mock_executor.initialize = AsyncMock(return_value=MagicMock())
    mock_safety.inspect_observation = MagicMock(return_value=[])
    mock_safety.evaluate_action = MagicMock(
        return_value=SafetyCheckResult(is_safe=False, reason="Blocked")
    )

    runner = ControlledAgentRunner(
        model_client=mock_model,
        observer=mock_observer,
        executor=mock_executor,
        safety=mock_safety,
        events=mock_events,
    )

    state = await runner.run(AgentRunRequest(goal="Safety test", max_steps=5))

    # 1 decision step was attempted
    assert state.current_step == 1
    # 0 browser actions were executed
    assert len(state.history) == 0
    assert mock_executor.execute.call_count == 0
