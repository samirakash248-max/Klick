"""Controlled Agent Runner module for BrowserPilot AI.

Implements Member A's Step 3 controlled decision loop connecting the model client,
page observer, and browser executor with strict safeguards:
1. Configurable, bounded action limit (default: 10 actions).
2. Fresh observation captured before every decision step.
3. Element grounding validation against the current observation.
4. Repeated-action / stuck detection on unchanged pages.
5. Independent completion verification (never blindly trusting model 'finish').
6. Controlled error handling for model timeouts, parse failures, observer/executor errors.
7. Structured event telemetry and explicit stop reasons.

Owned by: AI Engineer (Member A)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import hashlib
import inspect
import os
import re
from typing import Any, Dict, List, Optional, Union
import uuid

from .approval import (
    ApprovalManager,
    ApprovalRequest,
    ApprovalStatus,
    SafetyDecision,
    SafetyDecisionType,
    classify_action_safety,
    resolve_target_element,
)
from .events import EventType, event_manager as default_event_manager, EventManager
from .executor import PlaywrightExecutor
from .model_client import (
    ModelClientError,
    ModelConnectionError,
    ModelResponseParseError,
    ModelClientProtocol,
    OllamaModelClient,
)
from .observer import PlaywrightObserver
from .safety import SafetyGuard, safety_guard as default_safety_guard
from .schemas import (
    ActionType,
    AgentResponse,
    AgentRunRequest,
    AgentRunState,
    BrowserAction,
    ExecutionResult,
    PageObservation,
    RiskLevel,
    RunStatus,
    SafetyCheckResult,
    StepRecord,
)
from .trajectory_logger import (
    TrajectoryLogger,
    TrajectoryRunSummary,
    TrajectoryStepRecord,
)
from .evaluation import classify_trajectory

DEFAULT_MAX_ACTIONS = 10
MAX_ALLOWED_ACTIONS = 30
STUCK_ACTION_THRESHOLD = 3
DEFAULT_MAX_CONSECUTIVE_FAILURES = 1


class StopReason(str, Enum):
    """Explicit stop reasons for controlled agent loop termination."""
    COMPLETED = "completed"
    UNVERIFIED_COMPLETION = "unverified_completion"
    MAX_STEPS_REACHED = "max_steps_reached"
    STUCK_REPEATED_ACTION = "stuck_repeated_action"
    UNGROUNDED_SELECTOR = "ungrounded_selector"
    SAFETY_BLOCKED = "safety_blocked"
    EXECUTION_FAILED = "execution_failed"
    MODEL_ERROR = "model_error"
    OBSERVER_ERROR = "observer_error"
    STOP_REQUESTED = "stop_requested"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_TIMED_OUT = "approval_timed_out"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    CONSECUTIVE_FAILURES = "consecutive_failures"


def compute_observation_fingerprint(observation: PageObservation) -> str:
    """Generate a hash representing page state to detect identical consecutive states."""
    data = f"{observation.url}|{observation.title}|{observation.dom_summary}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


# Regex patterns for explicit transaction/submission completion
SUBMISSION_CONFIRMATION_PATTERNS = [
    r"thank\s+you.*(?:submission|order|contacting|reaching\s+out|registering|signing\s+up|feedback)",
    r"submission\s+(?:was\s+|has\s+been\s+)?(?:received|confirmed|successful)",
    r"order\s+(?:was\s+|has\s+been\s+)?(?:confirmed|placed|received|completed)",
    r"message\s+(?:was\s+|has\s+been\s+)?(?:sent|received)",
    r"form\s+(?:was\s+|has\s+been\s+)?submitted",
    r"successfully\s+submitted",
    r"successfully\s+saved",
    r"account\s+created",
    r"registration\s+(?:was\s+)?(?:complete|successful)",
    r"booking\s+confirmed",
    r"reservation\s+confirmed",
]

CART_CONFIRMATION_PATTERNS = [
    r"added\s+to\s+(?:your\s+)?cart",
    r"item\s+added",
    r"in\s+(?:your\s+)?cart",
    r"cart\s*\([1-9]\d*\)",
    r"cart:\s*[1-9]\d*",
    r"[1-9]\d*\s+items?\s+in\s+cart",
    r"proceed\s+to\s+checkout",
]

SEARCH_RESULT_PATTERNS = [
    r"results?\s+for",
    r"search\s+results?",
    r"items?\s+found",
    r"showing\s+results?",
    r"products?\s+found",
    r"matching\s+results?",
    r"(?:no|0)\s+(?:results?|products?|items?)\s+found",
]


def is_action_grounded(action: BrowserAction, observation: PageObservation) -> bool:
    """Verify that an action targeting an element has a selector grounded in the observation.

    Prefers exact matches against structured `observation.interactive_elements`.
    Rejects loose substring occurrences in DOM summary or page copy (e.g. 'button', 'input', '#e1').
    """
    if action.action_type not in (ActionType.CLICK, ActionType.TYPE, ActionType.EXTRACT, ActionType.PRESS_KEY):
        return True

    # Global keypress without target element is allowed
    if action.action_type == ActionType.PRESS_KEY and not action.selector and not action.target:
        return True

    target_val = action.selector or action.target
    if not target_val:
        return False

    sel = target_val.strip()
    norm_sel = sel.replace("'", '"')
    raw_sel_id = sel.lstrip("#")
    clean_agent_id = None
    m = re.search(r'data-agent-id=["\']?([^"\'\]]+)["\']?', sel)
    if m:
        clean_agent_id = m.group(1)

    # 1. Match against structured interactive_elements fields
    for el in observation.interactive_elements:
        if el.selector:
            el_sel = el.selector.strip()
            if el_sel == sel or el_sel.replace("'", '"') == norm_sel:
                return True
            if clean_agent_id and (f'data-agent-id="{clean_agent_id}"' in el_sel or f"data-agent-id='{clean_agent_id}'" in el_sel):
                return True
        if el.id:
            clean_id = el.id.strip()
            if sel == clean_id or sel == f"#{clean_id}" or raw_sel_id == clean_id or clean_agent_id == clean_id:
                return True
            if norm_sel == f'[data-agent-id="{clean_id}"]' or sel == f"[data-agent-id='{clean_id}']":
                return True

    # 2. Check for selector declarations formatted in dom_summary
    if observation.dom_summary:
        if f"selector: `{sel}`" in observation.dom_summary or f"selector: `{norm_sel}`" in observation.dom_summary:
            return True
        if clean_agent_id and f"data-agent-id=\"{clean_agent_id}\"" in observation.dom_summary:
            return True
        if clean_agent_id and f"data-agent-id='{clean_agent_id}'" in observation.dom_summary:
            return True

    return False


def verify_task_completion(
    goal: str,
    finish_description: str,
    observation: PageObservation,
    history: Optional[List[StepRecord]] = None,
) -> bool:
    """Verify whether a model's finish claim is corroborated by credible page evidence.

    Conservative verification rules:
    - Never trusts model finish claim alone or isolated generic words.
    - Rejects isolated words like 'complete', 'saved', 'cart', or 'success' that appear
      in standard page copy, navigation headers, or footers.
    - Distinguishes task types (state-changing, search, navigation, extraction) and requires
      credible outcome evidence appropriate to the goal.
    - Prefers unverified (False) when available evidence is ambiguous or insufficient.
    """
    if observation.error:
        return False

    goal_lower = goal.lower().strip()
    title_lower = observation.title.lower()
    url_lower = observation.url.lower()
    dom_lower = observation.dom_summary.lower()
    snippet_lower = (observation.page_text_snippet or "").lower()
    page_text = f"{title_lower} {snippet_lower} {dom_lower}"

    # 1. State-changing / Transactional goals (cart, purchase, submit, register, delete)
    state_changing_triggers = (
        "cart", "buy", "purchase", "checkout", "submit", "fill",
        "register", "sign up", "order", "book", "reserve", "delete",
        "remove", "subscribe", "save", "contact", "send"
    )
    is_state_changing = any(trigger in goal_lower for trigger in state_changing_triggers)

    if is_state_changing:
        # If execution history is provided, state-changing tasks cannot succeed with 0 executed steps
        if history is not None and len(history) == 0:
            return False

        # Check for cart/checkout specific tasks
        if any(c in goal_lower for c in ("cart", "buy", "purchase", "checkout")):
            has_cart_evidence = any(
                re.search(pat, page_text, re.IGNORECASE) for pat in CART_CONFIRMATION_PATTERNS
            )
            has_cart_route = any(r in url_lower for r in ("/cart", "/checkout/success", "/order-confirmed"))
            if has_cart_evidence or has_cart_route:
                return True
            return False

        # Check for form submission, registration, contact, booking, or saving
        has_submission_evidence = any(
            re.search(pat, page_text, re.IGNORECASE) for pat in SUBMISSION_CONFIRMATION_PATTERNS
        )
        has_submission_route = any(
            r in url_lower for r in ("/thank-you", "/confirmation", "/success", "/submitted")
        )
        if has_submission_evidence or has_submission_route:
            return True
        return False

    # 2. Search / Query / Lookup goals
    search_triggers = ("search", "find", "look up", "filter")
    is_search = any(trigger in goal_lower for trigger in search_triggers)

    if is_search:
        has_search_route = any(
            p in url_lower for p in ("?q=", "?search=", "?query=", "&q=", "&search=", "/search?", "/results?")
        )
        has_result_pattern = any(
            re.search(pat, page_text, re.IGNORECASE) for pat in SEARCH_RESULT_PATTERNS
        )

        stop_words = {"search", "for", "find", "look", "up", "the", "a", "an", "in", "on", "products", "item", "items", "table", "tasks"}
        query_words = [w for w in re.findall(r"\w+", goal_lower) if len(w) > 2 and w not in stop_words]

        # Corroborate client-side search/filter actions executed during the run
        has_executed_search_action = False
        if history:
            for step in history:
                if step.execution_result and step.execution_result.success:
                    if step.action.action_type in (ActionType.TYPE, ActionType.CLICK):
                        sel = (step.action.selector or "").lower()
                        if "search" in sel or "filter" in sel or (step.action.text and any(qw in (step.action.text or "").lower() for qw in query_words)):
                            has_executed_search_action = True
                            break

        if not (has_search_route or has_result_pattern or has_executed_search_action):
            return False

        if query_words:
            query_in_page = any(qw in page_text or qw in url_lower for qw in query_words)
            if not query_in_page:
                return False

        # If goal requires opening or watching a video on YouTube, ensure /watch is active
        if any(w in goal_lower for w in ("video", "watch", "play")) and "youtube" in goal_lower:
            if "/watch" not in url_lower:
                return False

        return True

    # 3. Direct Navigation goals
    nav_triggers = ("go to", "navigate to", "open", "visit")
    is_nav = any(goal_lower.startswith(trigger) or f" {trigger} " in f" {goal_lower} " for trigger in nav_triggers)

    if is_nav:
        stop_words = {"go", "to", "navigate", "open", "visit", "the", "page", "section", "site"}
        nav_targets = [w for w in re.findall(r"\w+", goal_lower) if len(w) > 2 and w not in stop_words]
        if nav_targets:
            target_in_url = any(f"/{t}" in url_lower or f"{t}." in url_lower for t in nav_targets)
            target_in_title = any(t in title_lower for t in nav_targets)
            if target_in_url or target_in_title:
                return True
            return False

    # 4. Information extraction / Inquiry goals
    extract_triggers = ("what is", "extract", "read", "get the", "find the price")
    is_extract = any(trigger in goal_lower for trigger in extract_triggers)

    if is_extract:
        if finish_description and len(finish_description.strip()) > 5:
            desc_words = [w for w in re.findall(r"\w+", finish_description.lower()) if len(w) > 3]
            if desc_words and any(dw in page_text for dw in desc_words):
                return True
        return False

    # 5. Fallback for unspecified task types:
    has_explicit_outcome = any(
        re.search(pat, page_text, re.IGNORECASE) for pat in SUBMISSION_CONFIRMATION_PATTERNS
    )
    if has_explicit_outcome:
        substantive_goal_words = [
            w for w in re.findall(r"\w+", goal_lower)
            if len(w) > 3 and w not in {"with", "that", "this", "from", "page"}
        ]
        if not substantive_goal_words or any(gw in page_text for gw in substantive_goal_words):
            return True

    return False


class ControlledAgentRunner:
    """Controlled agent orchestration runner with safety boundaries and verification."""

    def __init__(
        self,
        model_client: Optional[Union[ModelClientProtocol, OllamaModelClient]] = None,
        observer: Optional[PlaywrightObserver] = None,
        executor: Optional[PlaywrightExecutor] = None,
        safety: Optional[SafetyGuard] = None,
        events: Optional[EventManager] = None,
        default_max_steps: int = DEFAULT_MAX_ACTIONS,
        stuck_threshold: int = STUCK_ACTION_THRESHOLD,
        trajectory_logger: Optional[TrajectoryLogger] = None,
        approval_manager: Optional[ApprovalManager] = None,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        auto_approve: bool = False,
    ) -> None:
        self.model_client = model_client or OllamaModelClient()
        self.observer = observer or PlaywrightObserver()
        allow_ext = os.getenv("BROWSERPILOT_ALLOW_EXTERNAL", "true").strip().lower() in ("true", "1", "yes")
        self.executor = executor or PlaywrightExecutor(allow_external=allow_ext)
        self.safety = safety or default_safety_guard
        self.events = events or default_event_manager
        self.default_max_steps = default_max_steps
        self.stuck_threshold = stuck_threshold
        self.trajectory_logger = trajectory_logger
        self.approval_manager = approval_manager or (ApprovalManager() if auto_approve else None)
        self.max_consecutive_failures = max(1, max_consecutive_failures)
        self.auto_approve = auto_approve
        self._stop_requested = False
        self._current_run_id: Optional[str] = None
        self._current_state: Optional[AgentRunState] = None
        self._run_lock = asyncio.Lock()

    def _record_trajectory_step(
        self,
        run_id: str,
        task_id: Optional[str],
        step_number: int,
        goal: str,
        observation: Optional[PageObservation],
        action: Optional[BrowserAction],
        grounding_valid: bool = True,
        safety_check: Optional[SafetyCheckResult] = None,
        exec_result: Optional[ExecutionResult] = None,
        progress_verified: bool = False,
        status: RunStatus = RunStatus.RUNNING,
        stop_reason: Optional[StopReason] = None,
        error: Optional[str] = None,
        is_recovery_step: bool = False,
    ) -> Optional[TrajectoryStepRecord]:
        if not self.trajectory_logger:
            return None
        rec = TrajectoryStepRecord(
            run_id=run_id,
            task_id=task_id,
            step_number=step_number,
            goal=goal,
            observation_before=TrajectoryLogger.sanitize_observation(observation),
            action=action.model_dump() if action else None,
            grounding_check=grounding_valid,
            safety_check=safety_check.model_dump() if safety_check else None,
            execution_result=exec_result.model_dump() if exec_result else None,
            progress_verified=progress_verified,
            run_status=status.value,
            stop_reason=stop_reason.value if stop_reason else None,
            error=error,
            is_recovery_step=is_recovery_step,
        )
        self.trajectory_logger.log_step(rec)
        return rec

    @property
    def current_state(self) -> Optional[AgentRunState]:
        return self._current_state

    def request_stop(self) -> None:
        """Signal the runner to safely halt at the next iteration and invalidate pending approvals."""
        self._stop_requested = True
        if self.approval_manager and self._current_run_id:
            self.approval_manager.cancel_run(self._current_run_id)

    async def run(
        self,
        request: AgentRunRequest,
        page: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> AgentRunState:
        """Run the controlled cognitive loop until verification, limit, or failure.

        Guarded by _run_lock to prevent overlapping concurrent executions on the same runner instance.
        """
        if self._run_lock.locked():
            raise RuntimeError("ControlledAgentRunner is already executing an active run.")

        async with self._run_lock:
            return await self._run_impl(request, page=page, task_id=task_id)

    async def _run_impl(
        self,
        request: AgentRunRequest,
        page: Optional[Any] = None,
        task_id: Optional[str] = None,
    ) -> AgentRunState:
        run_id = str(uuid.uuid4())
        self._current_run_id = run_id

        requested_steps = request.max_steps if request.max_steps is not None else self.default_max_steps
        max_steps = max(1, min(requested_steps, MAX_ALLOWED_ACTIONS))

        if self._stop_requested:
            state = AgentRunState(
                run_id=run_id,
                goal=request.goal,
                status=RunStatus.STOPPED,
                max_steps=max_steps,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                final_output="Run stopped by user request.",
            )
            self._current_state = state
            await self.events.emit(
                EventType.RUN_FINISHED,
                "Run stopped by user request before start.",
                {
                    "status": RunStatus.STOPPED.value,
                    "steps": 0,
                    "stop_reason": StopReason.STOP_REQUESTED.value,
                    "completion_verified": False,
                },
                run_id=run_id,
            )
            return state

        self._stop_requested = False

        state = AgentRunState(
            run_id=run_id,
            goal=request.goal,
            status=RunStatus.INITIALIZING,
            max_steps=max_steps,
            start_time=datetime.now(timezone.utc),
        )
        self._current_state = state

        await self.events.emit(
            EventType.STATUS_CHANGE,
            f"Run initialized: '{request.goal}' with limit {max_steps} actions",
            {"status": RunStatus.INITIALIZING, "run_id": run_id, "max_steps": max_steps},
            run_id=run_id,
        )

        # Track consecutive repeated actions, stagnation, and self-healing attempts
        last_action_signature: Optional[str] = None
        last_page_fingerprint: Optional[str] = None
        consecutive_identical_count = 0
        consecutive_stagnant_steps = 0
        self_healing_attempts = 0
        max_self_healing_attempts = 3
        active_recovery_hint: Optional[str] = None
        consecutive_failures = 0
        stop_reason: Optional[StopReason] = None
        completion_verified = False
        logged_steps: List[TrajectoryStepRecord] = []

        try:
            # 1. Initialize browser environment
            active_page = page or await self.executor.initialize()
            state.status = RunStatus.RUNNING

            # Initial navigation if requested
            if request.start_url and hasattr(self.executor, "execute"):
                nav_action = BrowserAction(
                    action_type=ActionType.NAVIGATE,
                    url=request.start_url,
                    description=f"Initial navigation to {request.start_url}",
                )
                exec_ret = self.executor.execute(nav_action)
                if asyncio.iscoroutine(exec_ret):
                    nav_res = await exec_ret
                    if hasattr(nav_res, "success") and not nav_res.success:
                        stop_reason = StopReason.EXECUTION_FAILED
                        state.status = RunStatus.FAILED
                        state.error = f"Failed initial navigation: {getattr(nav_res, 'error', 'navigation error')}"
                        return state

            # 2. Controlled Execution Loop
            while state.current_step < state.max_steps and not self._stop_requested:
                state.current_step += 1
                step_idx = state.current_step

                # Refresh active_page from executor in case previous action opened a popup or new tab
                if hasattr(self.executor, "current_page") and self.executor.current_page:
                    if not self.executor.current_page.is_closed():
                        active_page = self.executor.current_page

                # A0. Pre-observation CAPTCHA / challenge handling
                if hasattr(self.executor, "detect_captcha") and hasattr(self.executor, "handle_captcha"):

                    try:
                        c_check = self.executor.detect_captcha(active_page)
                        if asyncio.iscoroutine(c_check):
                            captcha_status = await c_check
                        else:
                            captcha_status = c_check

                        if isinstance(captcha_status, dict) and captcha_status.get("present"):
                            c_type = captcha_status.get("type", "unknown")
                            await self.events.emit(
                                EventType.STATUS_CHANGE,
                                f"CAPTCHA or security challenge detected ({c_type}). Attempting resolution...",
                                {"captcha": captcha_status, "step": step_idx},
                                run_id=run_id,
                            )
                            h_res = self.executor.handle_captcha(active_page, timeout_seconds=12.0)
                            if asyncio.iscoroutine(h_res):
                                resolved = await h_res
                            else:
                                resolved = bool(h_res)

                            if resolved:
                                await self.events.emit(
                                    EventType.STATUS_CHANGE,
                                    "CAPTCHA or challenge resolved successfully.",
                                    {"captcha": captcha_status, "resolved": True, "step": step_idx},
                                    run_id=run_id,
                                )
                    except Exception:
                        pass

                # A. Fresh Observation (always re-observe after action)
                try:
                    observation = await self.observer.observe(active_page)
                except Exception as exc:
                    consecutive_failures += 1
                    if consecutive_failures >= self.max_consecutive_failures:
                        stop_reason = (
                            StopReason.CONSECUTIVE_FAILURES
                            if self.max_consecutive_failures > 1
                            else StopReason.OBSERVER_ERROR
                        )
                        state.status = RunStatus.FAILED
                        state.error = f"Observation capture failed at step {step_idx}: {exc}"
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=None,
                            action=None,
                            grounding_valid=False,
                            status=RunStatus.FAILED,
                            stop_reason=stop_reason,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"step": step_idx, "error": str(exc)},
                            run_id=run_id,
                        )
                        break
                    else:
                        await self.events.emit(
                            EventType.ERROR,
                            f"Observation capture failed at step {step_idx} ({consecutive_failures}/{self.max_consecutive_failures}): {exc}",
                            {"step": step_idx, "consecutive_failures": consecutive_failures},
                            run_id=run_id,
                        )
                        await asyncio.sleep(0.1)
                        continue

                state.current_url = observation.url
                page_fingerprint = compute_observation_fingerprint(observation)

                await self.events.emit(
                    EventType.OBSERVATION_CAPTURED,
                    f"Observed: {observation.title} ({observation.url})",
                    {
                        "step": step_idx,
                        "url": observation.url,
                        "title": observation.title,
                        "fingerprint": page_fingerprint,
                    },
                    run_id=run_id,
                )

                # B. Injection Defense Check
                injections = self.safety.inspect_observation(observation)
                if injections:
                    for inj in injections:
                        await self.events.emit(
                            EventType.SAFETY_ALERT,
                            inj.reason or "Adversarial injection detected in DOM",
                            {"risk": inj.risk_level.value, "pattern": inj.flagged_pattern},
                            run_id=run_id,
                        )

                # C. Query Model Client for Next Action
                await self.events.emit(
                    EventType.AGENT_THINKING,
                    f"Agent thinking for step {step_idx} of {state.max_steps}...",
                    {"step": step_idx},
                    run_id=run_id,
                )

                try:
                    goal_for_step = request.goal
                    if state.history:
                        recent_actions_summary = "; ".join(
                            f"Step {s.step_number}: {s.action.action_type.value}"
                            + (f" '{s.action.text}'" if s.action.text else "")
                            + (f" on {s.action.selector}" if s.action.selector else "")
                            + (f" ({s.execution_result.message})" if s.execution_result else "")
                            for s in state.history[-3:]
                        )
                        goal_for_step = (
                            f"{request.goal}\n"
                            f"[Context: Previous actions executed: {recent_actions_summary}. "
                            "If the goal has already been achieved by previous actions or current page state, select action 'finish'.]"
                        )
                    if active_recovery_hint:
                        goal_for_step = f"{goal_for_step}\n\n[RECOVERY GUIDANCE]: {active_recovery_hint}"

                    response = await self.model_client.get_next_action(
                        goal=goal_for_step,
                        observation=observation,
                        step_number=step_idx,
                        max_steps=state.max_steps,
                    )
                except (ModelConnectionError, ModelResponseParseError, ModelClientError, Exception) as exc:
                    stop_reason = StopReason.MODEL_ERROR
                    state.status = RunStatus.FAILED
                    state.error = f"Model client failure at step {step_idx}: {exc}"
                    step_rec = self._record_trajectory_step(
                        run_id=run_id,
                        task_id=task_id,
                        step_number=step_idx,
                        goal=request.goal,
                        observation=observation,
                        action=None,
                        grounding_valid=False,
                        status=RunStatus.FAILED,
                        stop_reason=StopReason.MODEL_ERROR,
                        error=state.error,
                    )
                    if step_rec:
                        logged_steps.append(step_rec)
                    await self.events.emit(
                        EventType.ERROR,
                        state.error,
                        {"step": step_idx, "error": str(exc)},
                        run_id=run_id,
                    )
                    break

                proposed_action = response.action

                # D. Grounding Validation
                if not is_action_grounded(proposed_action, observation):
                    if self_healing_attempts < max_self_healing_attempts:
                        self_healing_attempts += 1
                        state.self_healing_count += 1
                        active_recovery_hint = (
                            f"Target selector '{proposed_action.selector}' is not present on the active page. "
                            f"Choose from visible elements: {[e.id or e.selector for e in observation.interactive_elements[:8]]}"
                        )
                        state.recovery_notes.append(f"Self-healing: selector '{proposed_action.selector}' ungrounded, reprompting.")
                        await self.events.emit(
                            EventType.SELF_HEALING_RECOVERY,
                            f"Self-Healing (Attempt {self_healing_attempts}): Selector '{proposed_action.selector}' ungrounded. Reprompting agent.",
                            {"attempt": self_healing_attempts, "step": step_idx, "type": "ungrounded_selector"},
                            run_id=run_id,
                        )
                        if hasattr(self.executor, "dismiss_overlays"):
                            _res = self.executor.dismiss_overlays()
                            if inspect.isawaitable(_res):
                                await _res
                        continue
                    else:
                        stop_reason = StopReason.UNGROUNDED_SELECTOR
                        state.status = RunStatus.FAILED
                        state.error = (
                            f"Action selector '{proposed_action.selector}' is not grounded "
                            "in current page observation elements or DOM summary."
                        )
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=False,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.UNGROUNDED_SELECTOR,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"step": step_idx, "action": proposed_action.model_dump()},
                            run_id=run_id,
                        )
                        break

                # E. Repeated-Action Stuck & Stagnation Detection with Multi-Tiered Self-Healing
                action_signature = f"{proposed_action.action_type.value}|{proposed_action.selector}|{proposed_action.text}|{proposed_action.url}"
                is_identical_action = (action_signature == last_action_signature and page_fingerprint == last_page_fingerprint)

                if is_identical_action:
                    consecutive_identical_count += 1
                else:
                    consecutive_identical_count = 1
                    active_recovery_hint = None

                last_action_signature = action_signature
                last_page_fingerprint = page_fingerprint

                if consecutive_identical_count >= self.stuck_threshold:
                    if self_healing_attempts < max_self_healing_attempts:
                        self_healing_attempts += 1
                        state.self_healing_count += 1

                        if self_healing_attempts == 1:
                            # Tier 1: Modal / Overlay Dismissal (Escape + close buttons)
                            if hasattr(self.executor, "dismiss_overlays"):
                                _res = self.executor.dismiss_overlays()
                                if inspect.isawaitable(_res):
                                    await _res
                            active_recovery_hint = (
                                f"Action '{proposed_action.action_type.value}' had no effect on page state. "
                                "Possible blocking overlay or modal was dismissed (Escape pressed). "
                                "Do NOT repeat this exact action. Try an alternative button, link, or scroll."
                            )
                            state.recovery_notes.append(f"Self-healing (Attempt 1): Dismissed overlays for stagnant action '{proposed_action.action_type.value}'.")
                            await self.events.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing (Tier 1): Dismissed potential overlays, reprompting agent.",
                                {"attempt": 1, "step": step_idx, "action": proposed_action.action_type.value},
                                run_id=run_id,
                            )
                            continue

                        elif self_healing_attempts == 2:
                            # Tier 2: Backtracking Navigation
                            if hasattr(self.executor, "go_back"):
                                _res = self.executor.go_back()
                                if inspect.isawaitable(_res):
                                    await _res
                            active_recovery_hint = (
                                "Stagnant loop detected: Backtracked to previous page. "
                                f"Do NOT repeat prior action '{proposed_action.action_type.value}'. Explore an alternate path."
                            )
                            state.recovery_notes.append("Self-healing (Attempt 2): Backtracked to previous page via go_back().")
                            await self.events.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing (Tier 2): Backtracked to previous page to escape dead-end loop.",
                                {"attempt": 2, "step": step_idx, "type": "backtracking_go_back"},
                                run_id=run_id,
                            )
                            continue

                        elif self_healing_attempts == 3:
                            # Tier 3: Viewport Scrolling & Discovery
                            if hasattr(self.executor, "scroll"):
                                _res = self.executor.scroll(delta_y=600)
                                if inspect.isawaitable(_res):
                                    await _res
                            active_recovery_hint = (
                                "Page state is stagnant: Scrolled down 600px to discover new elements. "
                                "Choose a newly visible element to make progress."
                            )
                            state.recovery_notes.append("Self-healing (Attempt 3): Scrolled viewport down 600px.")
                            await self.events.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing (Tier 3): Scrolled page to discover fresh elements.",
                                {"attempt": 3, "step": step_idx, "type": "scroll_exploration"},
                                run_id=run_id,
                            )
                            continue
                    else:
                        stop_reason = StopReason.STUCK_REPEATED_ACTION
                        state.status = RunStatus.FAILED
                        state.error = (
                            f"Stuck loop detected: Action '{proposed_action.action_type.value}' "
                            f"repeated without progress across {self_healing_attempts} self-healing attempts."
                        )
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.STUCK_REPEATED_ACTION,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"step": step_idx, "signature": action_signature},
                            run_id=run_id,
                        )
                        break

                # F. Safety Guardrail Evaluation & Human-In-The-Loop Approval
                decision = classify_action_safety(
                    action=proposed_action,
                    observation=observation,
                    guard=self.safety,
                )
                safety_check = SafetyCheckResult(
                    is_safe=decision.is_safe,
                    risk_level=decision.risk_level,
                    reason=decision.reason,
                    flagged_pattern=decision.flagged_pattern,
                    requires_human_confirmation=decision.requires_human_confirmation,
                )

                if decision.decision_type == SafetyDecisionType.BLOCK:
                    stop_reason = StopReason.SAFETY_BLOCKED
                    state.status = RunStatus.AWAITING_CONFIRMATION
                    state.error = f"Blocked unsafe action: {decision.reason}"
                    step_rec = self._record_trajectory_step(
                        run_id=run_id,
                        task_id=task_id,
                        step_number=step_idx,
                        goal=request.goal,
                        observation=observation,
                        action=proposed_action,
                        grounding_valid=True,
                        safety_check=safety_check,
                        status=RunStatus.AWAITING_CONFIRMATION,
                        stop_reason=StopReason.SAFETY_BLOCKED,
                        error=state.error,
                    )
                    if step_rec:
                        logged_steps.append(step_rec)
                    await self.events.emit(
                        EventType.SAFETY_ALERT,
                        state.error,
                        {
                            "action": proposed_action.model_dump(),
                            "safety": safety_check.model_dump(),
                            "decision_type": decision.decision_type.value,
                        },
                        run_id=run_id,
                    )
                    break

                elif decision.decision_type == SafetyDecisionType.REQUIRE_APPROVAL:
                    if not self.approval_manager:
                        stop_reason = StopReason.APPROVAL_UNAVAILABLE
                        state.status = RunStatus.FAILED
                        state.error = (
                            f"Action requires human approval, but no ApprovalManager is configured: {decision.reason}"
                        )
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.APPROVAL_UNAVAILABLE,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {
                                "action": proposed_action.model_dump(),
                                "reason": decision.reason,
                                "risk_level": decision.risk_level.value,
                                "step": step_idx,
                            },
                            run_id=run_id,
                        )
                        break

                    approval_req = self.approval_manager.create_request(
                        run_id=run_id,
                        step_number=step_idx,
                        action=proposed_action,
                        reason=decision.reason,
                        risk_level=decision.risk_level,
                        context={
                            "url": observation.url,
                            "title": observation.title,
                            "goal": request.goal,
                        },
                    )

                    state.status = RunStatus.AWAITING_CONFIRMATION
                    await self.events.emit(
                        EventType.STATUS_CHANGE,
                        f"Awaiting human approval for step {step_idx}: {decision.reason}",
                        {
                            "status": RunStatus.AWAITING_CONFIRMATION.value,
                            "run_id": run_id,
                            "step": step_idx,
                            "approval_id": approval_req.approval_id,
                        },
                        run_id=run_id,
                    )

                    await self.events.emit(
                        EventType.SAFETY_ALERT,
                        f"Approval required for step {step_idx}: {decision.reason}",
                        {
                            "approval_id": approval_req.approval_id,
                            "action": proposed_action.model_dump(),
                            "reason": decision.reason,
                            "risk_level": decision.risk_level.value,
                        },
                        run_id=run_id,
                    )

                    if self.auto_approve:
                        self.approval_manager.resolve_request(
                            approval_req.approval_id,
                            approved=True,
                            reason="Auto-approved by runner configuration",
                        )

                    try:
                        approval_status = await self.approval_manager.wait_for_decision(approval_req.approval_id)
                    except Exception as exc:
                        stop_reason = StopReason.SAFETY_BLOCKED
                        state.status = RunStatus.FAILED
                        state.error = f"Approval mechanism error (failing closed): {exc}"
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.SAFETY_BLOCKED,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"approval_id": approval_req.approval_id, "step": step_idx, "error": str(exc)},
                            run_id=run_id,
                        )
                        break

                    if self._stop_requested or approval_status == ApprovalStatus.CANCELLED:
                        stop_reason = StopReason.STOP_REQUESTED
                        state.status = RunStatus.STOPPED
                        state.final_output = "Run stopped by user request during approval wait."
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.STOPPED,
                            stop_reason=StopReason.STOP_REQUESTED,
                            error=state.final_output,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        break

                    if approval_status == ApprovalStatus.REJECTED:
                        stop_reason = StopReason.APPROVAL_REJECTED
                        state.status = RunStatus.FAILED
                        state.error = f"Action rejected by human operator: {approval_req.decision_reason or 'Rejected'}"
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.APPROVAL_REJECTED,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"approval_id": approval_req.approval_id, "step": step_idx},
                            run_id=run_id,
                        )
                        break

                    elif approval_status == ApprovalStatus.TIMED_OUT:
                        stop_reason = StopReason.APPROVAL_TIMED_OUT
                        state.status = RunStatus.FAILED
                        state.error = f"Approval timed out: {approval_req.decision_reason or 'No operator response within timeout'}"
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.APPROVAL_TIMED_OUT,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        await self.events.emit(
                            EventType.ERROR,
                            state.error,
                            {"approval_id": approval_req.approval_id, "step": step_idx},
                            run_id=run_id,
                        )
                        break

                    elif approval_status == ApprovalStatus.APPROVED:
                        fresh_obs = observation
                        # Re-observe page immediately after approval wait to verify context has not drifted.
                        # (Skipped when auto_approve=True as resolution occurs synchronously without operator delay)
                        if not self.auto_approve:
                            try:
                                fresh_obs = await self.observer.observe(active_page)
                            except Exception as exc:
                                stop_reason = StopReason.OBSERVER_ERROR
                                state.status = RunStatus.FAILED
                                state.error = f"Observation capture failed after approval at step {step_idx}: {exc}"
                                step_rec = self._record_trajectory_step(
                                    run_id=run_id,
                                    task_id=task_id,
                                    step_number=step_idx,
                                    goal=request.goal,
                                    observation=observation,
                                    action=proposed_action,
                                    grounding_valid=False,
                                    safety_check=safety_check,
                                    status=RunStatus.FAILED,
                                    stop_reason=StopReason.OBSERVER_ERROR,
                                    error=state.error,
                                )
                                if step_rec:
                                    logged_steps.append(step_rec)
                                await self.events.emit(
                                    EventType.ERROR,
                                    state.error,
                                    {"step": step_idx, "error": str(exc)},
                                    run_id=run_id,
                                )
                                break

                            drift_detected = False
                            drift_detail = ""
                            if fresh_obs.url != observation.url:
                                drift_detected = True
                                drift_detail = f"Page URL navigated away during approval wait: '{observation.url}' -> '{fresh_obs.url}'."
                            elif proposed_action.selector and not is_action_grounded(proposed_action, fresh_obs):
                                drift_detected = True
                                drift_detail = f"Target selector '{proposed_action.selector}' is no longer grounded after approval wait."
                            elif proposed_action.selector:
                                orig_elem = resolve_target_element(proposed_action, observation)
                                fresh_elem = resolve_target_element(proposed_action, fresh_obs)
                                if orig_elem is not None:
                                    if fresh_elem is None:
                                        drift_detected = True
                                        drift_detail = "Target element was removed from page during approval wait."
                                    elif (
                                        (fresh_elem.text or "").strip() != (orig_elem.text or "").strip()
                                        or (fresh_elem.aria_label or "").strip() != (orig_elem.aria_label or "").strip()
                                    ):
                                        drift_detected = True
                                        drift_detail = (
                                            f"Target element text/label mutated during approval wait: "
                                            f"'{orig_elem.text}' -> '{fresh_elem.text}'."
                                        )

                            if drift_detected:
                                stop_reason = StopReason.SAFETY_BLOCKED
                                state.status = RunStatus.FAILED
                                state.error = f"Page state drifted during approval wait (failing closed): {drift_detail}"
                                step_rec = self._record_trajectory_step(
                                    run_id=run_id,
                                    task_id=task_id,
                                    step_number=step_idx,
                                    goal=request.goal,
                                    observation=fresh_obs,
                                    action=proposed_action,
                                    grounding_valid=False,
                                    safety_check=safety_check,
                                    status=RunStatus.FAILED,
                                    stop_reason=StopReason.SAFETY_BLOCKED,
                                    error=state.error,
                                )
                                if step_rec:
                                    logged_steps.append(step_rec)
                                await self.events.emit(
                                    EventType.SAFETY_ALERT,
                                    state.error,
                                    {"step": step_idx, "drift_detail": drift_detail},
                                    run_id=run_id,
                                )
                                break

                            observation = fresh_obs

                        is_valid = await self.approval_manager.validate_and_consume(
                            approval_id=approval_req.approval_id,
                            expected_run_id=run_id,
                            expected_action=proposed_action,
                            is_run_active=(not self._stop_requested),
                        )
                        if not is_valid:
                            stop_reason = StopReason.SAFETY_BLOCKED
                            state.status = RunStatus.FAILED
                            state.error = "Approval validation failed: action modified, run inactive, stale request, or already consumed."
                            step_rec = self._record_trajectory_step(
                                run_id=run_id,
                                task_id=task_id,
                                step_number=step_idx,
                                goal=request.goal,
                                observation=observation,
                                action=proposed_action,
                                grounding_valid=True,
                                safety_check=safety_check,
                                status=RunStatus.FAILED,
                                stop_reason=StopReason.SAFETY_BLOCKED,
                                error=state.error,
                            )
                            if step_rec:
                                logged_steps.append(step_rec)
                            await self.events.emit(
                                EventType.ERROR,
                                state.error,
                                {"approval_id": approval_req.approval_id, "step": step_idx},
                                run_id=run_id,
                            )
                            break

                        state.status = RunStatus.RUNNING
                        observation = fresh_obs
                        await self.events.emit(
                            EventType.STATUS_CHANGE,
                            f"Approval granted and validated for step {step_idx}: resuming execution",
                            {"status": RunStatus.RUNNING.value, "run_id": run_id, "step": step_idx},
                            run_id=run_id,
                        )
                    else:
                        stop_reason = StopReason.SAFETY_BLOCKED
                        state.status = RunStatus.FAILED
                        state.error = f"Unexpected approval status '{approval_status}': failing closed."
                        step_rec = self._record_trajectory_step(
                            run_id=run_id,
                            task_id=task_id,
                            step_number=step_idx,
                            goal=request.goal,
                            observation=observation,
                            action=proposed_action,
                            grounding_valid=True,
                            safety_check=safety_check,
                            status=RunStatus.FAILED,
                            stop_reason=StopReason.SAFETY_BLOCKED,
                            error=state.error,
                        )
                        if step_rec:
                            logged_steps.append(step_rec)
                        break

                await self.events.emit(
                    EventType.ACTION_PROPOSED,
                    f"Proposed {proposed_action.action_type.value}: {proposed_action.description}",
                    {"action": proposed_action.model_dump(), "thought": response.thought.model_dump()},
                    run_id=run_id,
                )

                # G. Completion Verification on FINISH
                if proposed_action.action_type == ActionType.FINISH:
                    is_verified = verify_task_completion(
                        request.goal,
                        proposed_action.description,
                        observation,
                        history=state.history,
                    )
                    if is_verified:
                        stop_reason = StopReason.COMPLETED
                        state.status = RunStatus.COMPLETED
                        state.final_output = f"Verified: {proposed_action.description}"
                        completion_verified = True
                    else:
                        stop_reason = StopReason.UNVERIFIED_COMPLETION
                        state.status = RunStatus.FAILED
                        state.error = (
                            "Model claimed task completion ('finish'), but page state "
                            "does not corroborate completion."
                        )
                    step_rec = self._record_trajectory_step(
                        run_id=run_id,
                        task_id=task_id,
                        step_number=step_idx,
                        goal=request.goal,
                        observation=observation,
                        action=proposed_action,
                        grounding_valid=True,
                        safety_check=safety_check,
                        progress_verified=completion_verified,
                        status=state.status,
                        stop_reason=stop_reason,
                        error=state.error,
                    )
                    if step_rec:
                        logged_steps.append(step_rec)
                    break

                # H. Controlled Failure on FAIL
                if proposed_action.action_type == ActionType.FAIL:
                    stop_reason = StopReason.EXECUTION_FAILED
                    state.status = RunStatus.FAILED
                    state.error = proposed_action.description
                    step_rec = self._record_trajectory_step(
                        run_id=run_id,
                        task_id=task_id,
                        step_number=step_idx,
                        goal=request.goal,
                        observation=observation,
                        action=proposed_action,
                        grounding_valid=True,
                        safety_check=safety_check,
                        status=RunStatus.FAILED,
                        stop_reason=StopReason.EXECUTION_FAILED,
                        error=state.error,
                    )
                    if step_rec:
                        logged_steps.append(step_rec)
                    break

                # Re-check cancellation right before execution
                if self._stop_requested:
                    stop_reason = StopReason.STOP_REQUESTED
                    state.status = RunStatus.STOPPED
                    state.final_output = "Run stopped by user request before execution."
                    break

                # I. Execute Action
                exec_result = await self.executor.execute(proposed_action)
                await self.events.emit(
                    EventType.ACTION_EXECUTED,
                    exec_result.message,
                    {"result": exec_result.model_dump(), "step": step_idx},
                    run_id=run_id,
                )

                step_record = StepRecord(
                    step_number=step_idx,
                    observation=observation,
                    thought=response.thought,
                    action=proposed_action,
                    safety_check=safety_check,
                    execution_result=exec_result,
                )
                state.history.append(step_record)

                had_prior_problem = any(
                    not s.grounding_check or (s.execution_result and not s.execution_result.get("success", True))
                    for s in logged_steps
                ) if logged_steps else False

                step_rec = self._record_trajectory_step(
                    run_id=run_id,
                    task_id=task_id,
                    step_number=step_idx,
                    goal=request.goal,
                    observation=observation,
                    action=proposed_action,
                    grounding_valid=True,
                    safety_check=safety_check,
                    exec_result=exec_result,
                    progress_verified=exec_result.success,
                    status=state.status,
                    stop_reason=None if exec_result.success else StopReason.EXECUTION_FAILED,
                    error=f"Execution failed: {exec_result.error}" if not exec_result.success else None,
                    is_recovery_step=had_prior_problem and exec_result.success,
                )
                if step_rec:
                    logged_steps.append(step_rec)

                if not exec_result.success:
                    consecutive_failures += 1
                    if consecutive_failures >= self.max_consecutive_failures:
                        stop_reason = (
                            StopReason.CONSECUTIVE_FAILURES
                            if self.max_consecutive_failures > 1
                            else StopReason.EXECUTION_FAILED
                        )
                        state.status = RunStatus.FAILED
                        state.error = f"Execution failed at step {step_idx}: {exec_result.error or exec_result.message}"
                        break
                    else:
                        await self.events.emit(
                            EventType.ERROR,
                            f"Action execution failure at step {step_idx} ({consecutive_failures}/{self.max_consecutive_failures}): {exec_result.error or exec_result.message}",
                            {"step": step_idx, "consecutive_failures": consecutive_failures},
                            run_id=run_id,
                        )
                        await asyncio.sleep(0.1)
                        continue
                else:
                    consecutive_failures = 0

                await asyncio.sleep(0.1)

            # 3. Post-loop resolution
            if self._stop_requested:
                stop_reason = StopReason.STOP_REQUESTED
                state.status = RunStatus.STOPPED
                state.final_output = "Run stopped by user request."
            elif state.status == RunStatus.RUNNING and state.current_step >= state.max_steps:
                stop_reason = StopReason.MAX_STEPS_REACHED
                state.status = RunStatus.FAILED
                state.error = f"Maximum step limit of {state.max_steps} actions reached without completing goal."
                state.final_output = f"Stopped: reached limit of {state.max_steps} actions."

        except Exception as exc:
            stop_reason = StopReason.EXECUTION_FAILED
            state.status = RunStatus.FAILED
            state.error = f"Unhandled runner exception: {exc}"
            await self.events.emit(
                EventType.ERROR,
                state.error,
                {"error": str(exc)},
                run_id=run_id,
            )

        finally:
            if self.approval_manager and self._current_run_id:
                self.approval_manager.cancel_run(self._current_run_id)
            self._current_run_id = None

            state.end_time = datetime.now(timezone.utc)
            effective_reason = stop_reason.value if stop_reason else state.status.value
            await self.events.emit(
                EventType.RUN_FINISHED,
                f"Run finished with status {state.status.value} (Reason: {effective_reason})",
                {
                    "status": state.status.value,
                    "steps": state.current_step,
                    "stop_reason": effective_reason,
                    "completion_verified": completion_verified,
                },
                run_id=run_id,
            )

            if self.trajectory_logger:
                duration_sec = None
                if state.start_time and state.end_time:
                    duration_sec = (state.end_time - state.start_time).total_seconds()

                has_rec_step = any(s.is_recovery_step for s in logged_steps)
                classification = classify_trajectory(
                    status=state.status,
                    stop_reason=effective_reason,
                    completion_verified=completion_verified,
                    has_recovery_step=has_rec_step,
                    steps=logged_steps,
                )
                summary = TrajectoryRunSummary(
                    run_id=run_id,
                    task_id=task_id,
                    goal=request.goal,
                    status=state.status.value,
                    stop_reason=effective_reason,
                    completion_verified=completion_verified,
                    total_decisions=state.current_step,
                    total_executed_actions=len(state.history),
                    duration_seconds=round(duration_sec, 3) if duration_sec is not None else None,
                    start_time=state.start_time.isoformat() if state.start_time else None,
                    end_time=state.end_time.isoformat() if state.end_time else None,
                    error=state.error,
                    final_output=state.final_output,
                    trajectory_label=classification.value,
                    has_recovery_step=has_rec_step,
                    steps=logged_steps,
                )
                self.trajectory_logger.log_run(summary)

        return state
