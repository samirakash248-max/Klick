"""Unit tests for Evaluation, Trajectory Logging, and Scenarios (Member A, Step 5).

Verifies:
- Evaluation metric calculations on known synthetic datasets.
- Task outcome classifications (successful, failed, recovery).
- Robust handling of edge cases (empty data, duplicates, missing durations, malformed JSONL).
- Credential and sensitive data redaction.
- Non-crashing logger behavior on file write failures.
- Scenario definitions and mock-driven evaluation execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.app.agent_runner import ControlledAgentRunner, StopReason
from backend.app.evaluation import (
    EvaluationMetrics,
    TrajectoryClassification,
    classify_trajectory,
    compute_metrics,
    load_trajectories_from_dir,
    load_trajectories_from_file,
)
from backend.app.model_client import OllamaModelClient
from backend.app.scenarios import (
    SCENARIOS,
    ScenarioDefinition,
    get_scenario,
    list_scenarios,
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
from backend.app.trajectory_logger import (
    TrajectoryLogger,
    TrajectoryRunSummary,
    TrajectoryStepRecord,
    redact_sensitive_text,
    sanitize_dict,
)


# ==============================================================================
# 1. SECRET REDACTION & LOGGING SAFETY TESTS
# ==============================================================================

def test_redact_sensitive_text_masks_credentials():
    """Verify secrets like Bearer tokens, passwords, query tokens, and API keys are redacted."""
    raw_text = (
        "Authorization: Bearer my-super-secret-token-123456789. "
        "User password: password='SuperSecretPassword123' "
        "OpenAI key: sk-abcdefghijklmnopqrstuvwxyz1234567890. "
        "Card: 4111-2222-3333-4444. "
        "Callback: https://example.com/oauth?token=secretOAuthToken123&session=userSession456."
    )
    sanitized = redact_sensitive_text(raw_text)
    assert "my-super-secret-token" not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized
    assert "SuperSecretPassword123" not in sanitized
    assert "[REDACTED_PASSWORD]" in sanitized
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in sanitized
    assert "[REDACTED_OPENAI_KEY]" in sanitized
    assert "4111-2222-3333-4444" not in sanitized
    assert "[REDACTED_CARD_NUMBER]" in sanitized
    assert "secretOAuthToken123" not in sanitized
    assert "userSession456" not in sanitized


def test_redact_truncates_long_snippets():
    """Verify verbose strings exceeding max_length are truncated."""
    long_string = "A" * 800
    truncated = redact_sensitive_text(long_string, max_length=200)
    assert len(truncated) < 300
    assert "[TRUNCATED]" in truncated


def test_sanitize_dict_recursively_redacts():
    """Verify nested dictionaries and sensitive keys are scrubbed."""
    nested = {
        "headers": {"authorization": "Bearer sec123456789"},
        "form": {"password": "mypassword", "username": "admin"},
        "cookie": "session=xyz123",
        "session": "active_user_session_abc",
        "nested_list": ["ok", "secret: 'dont_tell'"],
    }
    cleaned = sanitize_dict(nested)
    assert cleaned["form"]["password"] == "[REDACTED]"
    assert cleaned["cookie"] == "[REDACTED]"
    assert cleaned["session"] == "[REDACTED]"
    assert "[REDACTED_SECRET]" in cleaned["nested_list"][1] or "[REDACTED]" in cleaned["nested_list"][1]


def test_logger_disabled_writes_nothing(tmp_path: Path):
    """Verify disabled logger performs no disk writes."""
    logger = TrajectoryLogger(output_dir=tmp_path, enabled=False)
    step = TrajectoryStepRecord(
        run_id="run-1",
        step_number=1,
        goal="Test goal",
        run_status="running",
    )
    result = logger.log_step(step)
    assert result is False
    assert not (tmp_path / "trajectories.jsonl").exists()


def test_logger_handles_write_failures_gracefully(tmp_path: Path):
    """Verify logger catches OS / filesystem exceptions without crashing."""
    logger = TrajectoryLogger(output_dir=tmp_path / "non_writable_dir", enabled=True)
    # Simulate an un-creatable directory or disk error by mocking open
    with patch("builtins.open", side_effect=PermissionError("Permission denied")):
        step = TrajectoryStepRecord(
            run_id="run-err",
            step_number=1,
            goal="Test error handling",
            run_status="running",
        )
        assert logger.log_step(step) is False
        summary = TrajectoryRunSummary(
            run_id="run-err",
            goal="Test error handling",
            status="failed",
        )
        assert logger.log_run(summary) is False


# ==============================================================================
# 2. TRAJECTORY CLASSIFICATION TESTS
# ==============================================================================

def test_classify_trajectory_precedence():
    """Verify strict classification precedence: FAILED > RECOVERY > SUCCESSFUL."""
    # 1. Unverified completion is always FAILED, even with recovery
    assert classify_trajectory(
        status=RunStatus.FAILED,
        stop_reason=StopReason.UNVERIFIED_COMPLETION.value,
        completion_verified=False,
        has_recovery_step=True,
    ) == TrajectoryClassification.FAILED

    # 2. Clean verified completion without problems is SUCCESSFUL
    assert classify_trajectory(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.COMPLETED.value,
        completion_verified=True,
        has_recovery_step=False,
    ) == TrajectoryClassification.SUCCESSFUL

    # 3. Verified completion with prior recovered failure is RECOVERY
    assert classify_trajectory(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.COMPLETED.value,
        completion_verified=True,
        has_recovery_step=True,
    ) == TrajectoryClassification.RECOVERY


def test_classify_trajectory_detects_recovery_from_steps():
    """Verify recovery is detected from step list when an earlier step failed."""
    step1 = TrajectoryStepRecord(
        run_id="r1",
        step_number=1,
        goal="Test",
        run_status="running",
        execution_result={"success": False, "message": "Click timed out"},
    )
    step2 = TrajectoryStepRecord(
        run_id="r1",
        step_number=2,
        goal="Test",
        run_status="running",
        execution_result={"success": True, "message": "Retry clicked"},
    )
    classification = classify_trajectory(
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.COMPLETED.value,
        completion_verified=True,
        steps=[step1, step2],
    )
    assert classification == TrajectoryClassification.RECOVERY


# ==============================================================================
# 3. METRIC COMPUTATION TESTS (KNOWN SYNTHETIC DATASET)
# ==============================================================================

@pytest.fixture
def synthetic_run_dataset() -> list[TrajectoryRunSummary]:
    """Create a known, deterministic dataset for metric computation validation:

    - 5 unique runs total:
      * Run 1: Verified success, 2 actions, duration 10.0s
      * Run 2: Verified recovery success, 3 actions (1 failed step), duration 20.0s
      * Run 3: Failed (stuck loop), 3 actions, duration 15.0s
      * Run 4: Failed (unverified finish), 1 action, duration 5.0s
      * Run 5: Failed (ungrounded selector), 0 executed actions, missing duration (None)
    """
    return [
        TrajectoryRunSummary(
            run_id="run-01",
            goal="Goal 1",
            status=RunStatus.COMPLETED.value,
            stop_reason=StopReason.COMPLETED.value,
            completion_verified=True,
            total_decisions=2,
            total_executed_actions=2,
            duration_seconds=10.0,
            has_recovery_step=False,
            steps=[
                TrajectoryStepRecord(
                    run_id="run-01",
                    step_number=1,
                    goal="Goal 1",
                    run_status="running",
                    grounding_check=True,
                    execution_result={"success": True},
                ),
                TrajectoryStepRecord(
                    run_id="run-01",
                    step_number=2,
                    goal="Goal 1",
                    run_status="completed",
                    grounding_check=True,
                    progress_verified=True,
                ),
            ],
        ),
        TrajectoryRunSummary(
            run_id="run-02",
            goal="Goal 2",
            status=RunStatus.COMPLETED.value,
            stop_reason=StopReason.COMPLETED.value,
            completion_verified=True,
            total_decisions=3,
            total_executed_actions=3,
            duration_seconds=20.0,
            has_recovery_step=True,
            steps=[
                TrajectoryStepRecord(
                    run_id="run-02",
                    step_number=1,
                    goal="Goal 2",
                    run_status="running",
                    grounding_check=True,
                    execution_result={"success": False, "error": "Element not ready"},
                ),
                TrajectoryStepRecord(
                    run_id="run-02",
                    step_number=2,
                    goal="Goal 2",
                    run_status="running",
                    grounding_check=True,
                    execution_result={"success": True},
                    is_recovery_step=True,
                ),
            ],
        ),
        TrajectoryRunSummary(
            run_id="run-03",
            goal="Goal 3",
            status=RunStatus.FAILED.value,
            stop_reason=StopReason.STUCK_REPEATED_ACTION.value,
            completion_verified=False,
            total_decisions=3,
            total_executed_actions=2,
            duration_seconds=15.0,
            has_recovery_step=False,
            steps=[
                TrajectoryStepRecord(
                    run_id="run-03",
                    step_number=1,
                    goal="Goal 3",
                    run_status="running",
                    grounding_check=True,
                    execution_result={"success": True},
                ),
                TrajectoryStepRecord(
                    run_id="run-03",
                    step_number=2,
                    goal="Goal 3",
                    run_status="failed",
                    stop_reason=StopReason.STUCK_REPEATED_ACTION.value,
                    grounding_check=True,
                ),
            ],
        ),
        TrajectoryRunSummary(
            run_id="run-04",
            goal="Goal 4",
            status=RunStatus.FAILED.value,
            stop_reason=StopReason.UNVERIFIED_COMPLETION.value,
            completion_verified=False,
            total_decisions=1,
            total_executed_actions=0,
            duration_seconds=5.0,
            has_recovery_step=False,
            steps=[
                TrajectoryStepRecord(
                    run_id="run-04",
                    step_number=1,
                    goal="Goal 4",
                    run_status="failed",
                    stop_reason=StopReason.UNVERIFIED_COMPLETION.value,
                    grounding_check=True,
                ),
            ],
        ),
        TrajectoryRunSummary(
            run_id="run-05",
            goal="Goal 5",
            status=RunStatus.FAILED.value,
            stop_reason=StopReason.UNGROUNDED_SELECTOR.value,
            completion_verified=False,
            total_decisions=1,
            total_executed_actions=0,
            duration_seconds=None,  # Missing duration
            has_recovery_step=False,
            steps=[
                TrajectoryStepRecord(
                    run_id="run-05",
                    step_number=1,
                    goal="Goal 5",
                    run_status="failed",
                    stop_reason=StopReason.UNGROUNDED_SELECTOR.value,
                    grounding_check=False,
                ),
            ],
        ),
    ]


def test_compute_metrics_known_dataset(synthetic_run_dataset: list[TrajectoryRunSummary]):
    """Verify exact benchmark metric calculations against the known dataset."""
    metrics = compute_metrics(synthetic_run_dataset)

    # 1. Counts
    assert metrics.total_tasks == 5
    assert metrics.successful_tasks == 2  # run-01 and run-02
    assert metrics.failed_tasks == 3      # run-03, run-04, run-05
    assert metrics.unverified_tasks == 1  # run-04
    assert metrics.recovery_tasks == 1    # run-02

    # 2. Success rate = 2 / 5 = 0.4000
    assert metrics.task_success_rate == 0.4

    # 3. Average actions per successful task = (2 + 3) / 2 = 2.50
    assert metrics.average_actions_per_successful_task == 2.5

    # 4. Action execution failure rate = 1 failed / 4 execution attempts = 0.2500
    assert metrics.action_execution_failure_rate == 0.25

    # 5. Invalid action rate = 1 ungrounded action / 10 decisions = 0.1000
    assert metrics.invalid_action_rate == 0.1

    # 6. Stuck-run rate = 1 / 5 = 0.2000
    assert metrics.stuck_run_rate == 0.2

    # 7. Average completion time across successful runs with valid duration:
    # run-01 (10.0s) + run-02 (20.0s) = 30.0s / 2 = 15.00s
    assert metrics.average_completion_time_seconds == 15.0


def test_compute_metrics_empty_dataset():
    """Verify empty dataset returns zeroed metrics without division errors."""
    metrics = compute_metrics([])
    assert metrics.total_tasks == 0
    assert metrics.task_success_rate == 0.0
    assert metrics.average_actions_per_successful_task == 0.0
    assert metrics.average_completion_time_seconds == 0.0


def test_compute_metrics_deduplicates_run_ids(synthetic_run_dataset: list[TrajectoryRunSummary]):
    """Verify duplicate run_id entries in input are deduplicated."""
    duplicated = synthetic_run_dataset + [synthetic_run_dataset[0]]  # Add duplicate run-01
    assert len(duplicated) == 6
    metrics = compute_metrics(duplicated)
    assert metrics.total_tasks == 5  # Deduplicated down to 5


# ==============================================================================
# 4. JSONL FILE SERIALIZATION AND PARSING TESTS
# ==============================================================================

def test_jsonl_write_and_load(tmp_path: Path):
    """Verify trajectory writing to JSONL and reloading with loader."""
    logger = TrajectoryLogger(output_dir=tmp_path, filename="test_exp.jsonl")

    step1 = TrajectoryStepRecord(
        run_id="run-test",
        step_number=1,
        goal="Test jsonl",
        observation_before={"url": "http://example.com", "title": "Example"},
        run_status="running",
    )
    summary = TrajectoryRunSummary(
        run_id="run-test",
        goal="Test jsonl",
        status="completed",
        completion_verified=True,
        total_decisions=1,
        total_executed_actions=1,
        duration_seconds=3.5,
    )

    logger.log_step(step1)
    logger.log_run(summary)

    # Read back using loader
    summaries, malformed = load_trajectories_from_file(logger.log_path)
    assert len(summaries) == 1
    assert malformed == 0
    assert summaries[0].run_id == "run-test"
    assert summaries[0].completion_verified is True
    assert summaries[0].duration_seconds == 3.5


def test_load_trajectories_skips_malformed_lines(tmp_path: Path):
    """Verify malformed JSONL lines are safely counted and skipped."""
    file_path = tmp_path / "corrupted.jsonl"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write('{"record_type": "run_summary", "run_id": "r1", "status": "completed", "completion_verified": true}\n')
        f.write('NOT_VALID_JSON_LINE\n')
        f.write('{"record_type": "run_summary", "run_id": "r2", "status": "failed", "completion_verified": false}\n')
        f.write('{"truncated": true\n')

    summaries, malformed = load_trajectories_from_file(file_path)
    assert len(summaries) == 2
    assert malformed == 2

    # Verify metrics record skipped count
    metrics = compute_metrics(summaries, malformed_count=malformed)
    assert metrics.total_tasks == 2
    assert metrics.malformed_records_skipped == 2


# ==============================================================================
# 5. INTEGRATION WITH CONTROLLED AGENT RUNNER TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_runner_trajectory_logging_integration(tmp_path: Path):
    """Verify ControlledAgentRunner instruments trajectory logging without changing behavior."""
    traj_logger = TrajectoryLogger(output_dir=tmp_path, filename="runner_traj.jsonl")

    mock_model = MagicMock(spec=OllamaModelClient)
    mock_observer = MagicMock()
    mock_executor = MagicMock()
    mock_safety = MagicMock()
    mock_events = MagicMock()
    mock_events.emit = AsyncMock()

    obs1 = PageObservation(
        url="http://test.com",
        title="Page 1",
        dom_summary="button#b1 'Submit'",
        interactive_elements=[ElementDescriptor(tag_name="button", selector="#b1", id="b1", text="Submit")],
    )
    obs2 = PageObservation(
        url="http://test.com/confirmation",
        title="Page 2",
        dom_summary="div 'Done'",
        page_text_snippet="Thank you, your form was successfully submitted.",
    )
    mock_observer.observe = AsyncMock(side_effect=[obs1, obs2])

    resp1 = AgentResponse(
        thought=AgentThought(reflection="Obs", reasoning="Click", plan=[]),
        action=BrowserAction(action_type=ActionType.CLICK, selector="#b1", description="Click submit"),
    )
    resp2 = AgentResponse(
        thought=AgentThought(reflection="Done", reasoning="Finish", plan=[]),
        action=BrowserAction(action_type=ActionType.FINISH, description="Finish order"),
    )
    mock_model.get_next_action = AsyncMock(side_effect=[resp1, resp2])
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
        trajectory_logger=traj_logger,
    )

    req = AgentRunRequest(goal="Click submit", max_steps=3)
    state = await runner.run(req, task_id="test-scenario-01")

    # Verify run succeeded
    assert state.status == RunStatus.COMPLETED

    # Verify trajectories written to file
    summaries, malformed = load_trajectories_from_file(traj_logger.log_path)
    assert len(summaries) == 1
    assert malformed == 0
    run_sum = summaries[0]
    assert run_sum.task_id == "test-scenario-01"
    assert run_sum.completion_verified is True
    assert run_sum.trajectory_label == "successful"
    assert len(run_sum.steps) >= 1


# ==============================================================================
# 6. FIVE BENCHMARK SCENARIO DEFINITIONS & MOCK-DRIVEN EVALUATION
# ==============================================================================

def test_five_scenarios_registered():
    """Verify all five required evaluation scenarios are defined and discoverable."""
    scenarios = list_scenarios()
    assert len(scenarios) == 5

    scenario_ids = [s.scenario_id for s in scenarios]
    assert "eval-scenario-01-search" in scenario_ids
    assert "eval-scenario-02-item-modal" in scenario_ids
    assert "eval-scenario-03-form-fill" in scenario_ids
    assert "eval-scenario-04-missing-element" in scenario_ids
    assert "eval-scenario-05-prompt-injection" in scenario_ids


def test_scenario_search_metadata():
    """Verify scenario 1 (search) specification."""
    s = get_scenario("eval-scenario-01-search")
    assert s is not None
    assert "/tasks.html" in s.start_path
    assert "budget" in s.goal.lower()
    assert s.expected_status == RunStatus.COMPLETED


def test_scenario_missing_element_grounding_failure():
    """Verify scenario 4 (missing element) expects failure with ungrounded selector."""
    s = get_scenario("eval-scenario-04-missing-element")
    assert s is not None
    assert s.expected_status == RunStatus.FAILED
    assert s.expected_stop_reason == StopReason.UNGROUNDED_SELECTOR


def test_scenario_prompt_injection_security_flag():
    """Verify scenario 5 (prompt injection) is marked adversarial and targets injection.html."""
    s = get_scenario("eval-scenario-05-prompt-injection")
    assert s is not None
    assert s.adversarial is True
    assert "/injection.html" in s.start_path
