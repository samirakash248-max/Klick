"""Evaluation and metric computation module for BrowserPilot AI (Member A, Step 5).

Calculates standardized benchmarks and behavioral metrics from recorded agent trajectories:
- Task success rate (verified outcomes only, never blindly trusting model 'finish')
- Average actions per successful task
- Action execution failure rate
- Invalid / ungrounded action rate
- Stuck-run rate
- Average completion duration
- Trajectory classification: 'successful', 'failed', 'recovery'

Owned by: AI Engineer (Member A)
"""

from __future__ import annotations

from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from pydantic import BaseModel, Field

from .schemas import ActionType, RunStatus
from .trajectory_logger import TrajectoryRunSummary, TrajectoryStepRecord

logger = logging.getLogger("browserpilot.evaluation")


class TrajectoryClassification(str, Enum):
    """Classification taxonomy for reviewed agent runs."""
    SUCCESSFUL = "successful"
    FAILED = "failed"
    RECOVERY = "recovery"


class EvaluationMetrics(BaseModel):
    """Benchmark results computed across a collection of evaluated runs."""
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    unverified_tasks: int = 0
    recovery_tasks: int = 0

    task_success_rate: float = 0.0
    average_actions_per_successful_task: float = 0.0
    action_execution_failure_rate: float = 0.0
    invalid_action_rate: float = 0.0
    stuck_run_rate: float = 0.0
    average_completion_time_seconds: float = 0.0

    # Diagnostic counters
    total_decisions: int = 0
    total_execution_attempts: int = 0
    failed_execution_attempts: int = 0
    invalid_actions_count: int = 0
    stuck_runs_count: int = 0
    malformed_records_skipped: int = 0


def classify_trajectory(
    status: Union[RunStatus, str],
    stop_reason: Optional[str],
    completion_verified: bool,
    has_recovery_step: bool = False,
    steps: Optional[List[Any]] = None,
) -> TrajectoryClassification:
    """Classify an agent trajectory with strict precedence rules:

    1. If final completion was NOT independently verified: FAILED (even if recovery was attempted).
    2. If completion was verified AND a prior problem was overcome: RECOVERY.
    3. If completion was verified cleanly without problems: SUCCESSFUL.
    """
    status_str = status.value if hasattr(status, "value") else str(status)

    # Detect recovery from step records if not explicitly flagged
    detected_recovery = has_recovery_step
    if not detected_recovery and steps:
        has_failure_before = False
        for step in steps:
            # Check for failed execution or blocked action in step
            exec_res = getattr(step, "execution_result", None) or (step.get("execution_result") if isinstance(step, dict) else None)
            grounded = getattr(step, "grounding_check", True) if not isinstance(step, dict) else step.get("grounding_check", True)

            if exec_res and isinstance(exec_res, dict) and not exec_res.get("success", True):
                has_failure_before = True
            elif exec_res and hasattr(exec_res, "success") and not exec_res.success:
                has_failure_before = True
            elif not grounded:
                has_failure_before = True
            elif has_failure_before:
                # Step occurred after an earlier failure
                detected_recovery = True
                break

    # 1. Independent verification check
    is_completed = (status_str == RunStatus.COMPLETED.value or status_str == "completed") and bool(completion_verified)

    if not is_completed:
        return TrajectoryClassification.FAILED

    if detected_recovery:
        return TrajectoryClassification.RECOVERY

    return TrajectoryClassification.SUCCESSFUL


def compute_metrics(
    summaries: List[TrajectoryRunSummary],
    malformed_count: int = 0,
) -> EvaluationMetrics:
    """Compute aggregate evaluation metrics from a collection of run summaries.

    Deduplicates runs by run_id (retaining the first occurrence).
    Handles empty collections and missing durations safely without division-by-zero.
    """
    if not summaries:
        return EvaluationMetrics(malformed_records_skipped=malformed_count)

    # Deduplicate by run_id
    seen_ids: Set[str] = set()
    unique_runs: List[TrajectoryRunSummary] = []
    for s in summaries:
        if s.run_id not in seen_ids:
            seen_ids.add(s.run_id)
            unique_runs.append(s)

    total_tasks = len(unique_runs)
    if total_tasks == 0:
        return EvaluationMetrics(malformed_records_skipped=malformed_count)

    successful_tasks = 0
    failed_tasks = 0
    unverified_tasks = 0
    recovery_tasks = 0
    stuck_runs_count = 0

    total_decisions = 0
    total_execution_attempts = 0
    failed_execution_attempts = 0
    invalid_actions_count = 0

    successful_actions_total = 0
    valid_durations: List[float] = []

    for run in unique_runs:
        total_decisions += run.total_decisions

        # Verify classification
        classification = classify_trajectory(
            status=run.status,
            stop_reason=run.stop_reason,
            completion_verified=run.completion_verified,
            has_recovery_step=run.has_recovery_step,
            steps=run.steps,
        )

        is_verified_success = (run.completion_verified and run.status in (RunStatus.COMPLETED.value, "completed"))

        stop_reason_val = run.stop_reason.value if hasattr(run.stop_reason, "value") else str(run.stop_reason or "")

        if is_verified_success:
            successful_tasks += 1
            successful_actions_total += run.total_executed_actions
            if run.duration_seconds is not None and run.duration_seconds >= 0:
                valid_durations.append(run.duration_seconds)

            if classification == TrajectoryClassification.RECOVERY or run.has_recovery_step:
                recovery_tasks += 1
        else:
            failed_tasks += 1
            if stop_reason_val in ("unverified_completion", "StopReason.UNVERIFIED_COMPLETION"):
                unverified_tasks += 1

        if stop_reason_val in ("stuck_repeated_action", "StopReason.STUCK_REPEATED_ACTION"):
            stuck_runs_count += 1

        # Count per-step executions and invalid actions (counted at most once per step)
        for step in run.steps:
            step_stop = step.stop_reason.value if hasattr(step.stop_reason, "value") else str(step.stop_reason or "")
            is_invalid_action = False
            if not step.grounding_check:
                is_invalid_action = True
            elif step_stop in ("ungrounded_selector", "model_error"):
                is_invalid_action = True
            elif step.safety_check:
                if isinstance(step.safety_check, dict) and not step.safety_check.get("is_safe", True):
                    is_invalid_action = True
                elif hasattr(step.safety_check, "is_safe") and not step.safety_check.is_safe:
                    is_invalid_action = True

            if is_invalid_action:
                invalid_actions_count += 1

            if step.execution_result:
                total_execution_attempts += 1
                if isinstance(step.execution_result, dict):
                    if not step.execution_result.get("success", True):
                        failed_execution_attempts += 1
                elif hasattr(step.execution_result, "success"):
                    if not step.execution_result.success:
                        failed_execution_attempts += 1

    # Safe denominator calculations
    task_success_rate = successful_tasks / total_tasks if total_tasks > 0 else 0.0
    average_actions = successful_actions_total / successful_tasks if successful_tasks > 0 else 0.0
    exec_failure_rate = failed_execution_attempts / total_execution_attempts if total_execution_attempts > 0 else 0.0
    invalid_action_rate = invalid_actions_count / total_decisions if total_decisions > 0 else 0.0
    stuck_rate = stuck_runs_count / total_tasks if total_tasks > 0 else 0.0
    avg_duration = sum(valid_durations) / len(valid_durations) if valid_durations else 0.0

    return EvaluationMetrics(
        total_tasks=total_tasks,
        successful_tasks=successful_tasks,
        failed_tasks=failed_tasks,
        unverified_tasks=unverified_tasks,
        recovery_tasks=recovery_tasks,
        task_success_rate=round(task_success_rate, 4),
        average_actions_per_successful_task=round(average_actions, 2),
        action_execution_failure_rate=round(exec_failure_rate, 4),
        invalid_action_rate=round(invalid_action_rate, 4),
        stuck_run_rate=round(stuck_rate, 4),
        average_completion_time_seconds=round(avg_duration, 2),
        total_decisions=total_decisions,
        total_execution_attempts=total_execution_attempts,
        failed_execution_attempts=failed_execution_attempts,
        invalid_actions_count=invalid_actions_count,
        stuck_runs_count=stuck_runs_count,
        malformed_records_skipped=malformed_count,
    )


def load_trajectories_from_file(file_path: Union[str, Path]) -> tuple[List[TrajectoryRunSummary], int]:
    """Parse a .jsonl file containing step or run summary records.

    Returns (summaries_list, malformed_line_count).
    """
    path = Path(file_path)
    if not path.is_file():
        return [], 0

    summaries: Dict[str, TrajectoryRunSummary] = {}
    steps_by_run: Dict[str, List[TrajectoryStepRecord]] = {}
    malformed_count = 0

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                if not isinstance(data, dict):
                    malformed_count += 1
                    continue

                rec_type = data.get("record_type")
                run_id = data.get("run_id")
                if not run_id:
                    malformed_count += 1
                    continue

                if rec_type == "run_summary":
                    summary = TrajectoryRunSummary.model_validate(data)
                    summaries[run_id] = summary
                elif rec_type == "step":
                    step = TrajectoryStepRecord.model_validate(data)
                    steps_by_run.setdefault(run_id, []).append(step)
                else:
                    # Attempt inferring record
                    if "status" in data and "completion_verified" in data:
                        summary = TrajectoryRunSummary.model_validate(data)
                        summaries[run_id] = summary
                    else:
                        malformed_count += 1
            except Exception as exc:
                logger.debug("Skipping malformed JSON line %d: %s", line_num, exc)
                malformed_count += 1

    # Attach any step records to their corresponding summaries
    for run_id, steps in steps_by_run.items():
        if run_id in summaries:
            if not summaries[run_id].steps:
                summaries[run_id].steps = steps
        else:
            # Construct synthetic summary from steps if only steps were logged
            last_step = steps[-1]
            has_rec = any(s.is_recovery_step for s in steps)
            executed_count = sum(1 for s in steps if s.execution_result and s.execution_result.get("success", False))
            syn_summary = TrajectoryRunSummary(
                run_id=run_id,
                task_id=last_step.task_id,
                goal=last_step.goal,
                status=last_step.run_status,
                stop_reason=last_step.stop_reason,
                completion_verified=last_step.progress_verified,
                total_decisions=len(steps),
                total_executed_actions=executed_count,
                has_recovery_step=has_rec,
                steps=steps,
            )
            summaries[run_id] = syn_summary

    return list(summaries.values()), malformed_count


def load_trajectories_from_dir(dir_path: Union[str, Path]) -> tuple[List[TrajectoryRunSummary], int]:
    """Parse all .jsonl files in a directory."""
    path = Path(dir_path)
    if not path.is_dir():
        return [], 0

    all_summaries: List[TrajectoryRunSummary] = []
    total_malformed = 0

    for jsonl_file in path.glob("*.jsonl"):
        sums, malformed = load_trajectories_from_file(jsonl_file)
        all_summaries.extend(sums)
        total_malformed += malformed

    return all_summaries, total_malformed
