"""Agent execution loop coordinator.

Orchestrates the cognitive cycle:
1. Observe page state via Observer
2. Check observation for adversarial injections
3. Request next action from ModelClient
4. Evaluate action safety via SafetyGuard
5. Execute action via Executor
6. Broadcast telemetry via EventManager
7. Evaluate termination conditions

Jointly owned by: AI Engineer & Browser Automation Engineer
"""

import asyncio
from datetime import datetime, timezone
import inspect
import os
import uuid
from typing import Optional

from .events import EventType, event_manager
from .executor import PlaywrightExecutor
from .model_client import OllamaModelClient
from .observer import PlaywrightObserver
from .safety import SafetyGuard, safety_guard
from .schemas import (
    ActionType,
    AgentRunRequest,
    AgentRunState,
    BrowserAction,
    RunStatus,
    StepRecord,
)


class AgentLoop:
    """Orchestrates an autonomous browser agent session."""

    def __init__(
        self,
        model_client: Optional[OllamaModelClient] = None,
        observer: Optional[PlaywrightObserver] = None,
        executor: Optional[PlaywrightExecutor] = None,
        safety: Optional[SafetyGuard] = None,
    ) -> None:
        self.model_client = model_client or OllamaModelClient()
        self.observer = observer or PlaywrightObserver()
        allow_ext = os.getenv("BROWSERPILOT_ALLOW_EXTERNAL", "true").strip().lower() in ("true", "1", "yes")
        self.executor = executor or PlaywrightExecutor(allow_external=allow_ext)
        self.safety = safety or safety_guard
        self._is_running = False
        self._stop_requested = False
        self._current_state: Optional[AgentRunState] = None

    @property
    def current_state(self) -> Optional[AgentRunState]:
        return self._current_state

    def request_stop(self) -> None:
        """Flag the running loop to safely stop at next iteration."""
        self._stop_requested = True

    async def run(self, request: AgentRunRequest) -> AgentRunState:
        """Execute the agent loop for the provided goal."""
        run_id = str(uuid.uuid4())
        self._is_running = True
        self._stop_requested = False
        self.executor.resume()

        state = AgentRunState(
            run_id=run_id,
            goal=request.goal,
            status=RunStatus.INITIALIZING,
            max_steps=request.max_steps or 15,
            start_time=datetime.now(timezone.utc),
        )
        self._current_state = state

        # Configure target model for this run
        target_model = getattr(request, "model", None) or os.getenv("BROWSERPILOT_MODEL") or os.getenv("OLLAMA_MODEL") or "llama3.2:3b"
        if hasattr(self.model_client, "model"):
            self.model_client.model = target_model

        await event_manager.emit(
            EventType.STATUS_CHANGE,
            f"Run initialized: '{request.goal}'",
            {"status": RunStatus.INITIALIZING, "run_id": run_id},
            run_id=run_id,
        )

        try:
            # 1. Initialize browser
            page = await self.executor.initialize()
            state.status = RunStatus.RUNNING

            # Initial navigation if requested or inferred from goal
            target_url = request.start_url
            goal_lower = request.goal.lower()

            # Infer initial destination if start_url is omitted or is default mock URL but goal targets known public site
            is_mock_default = not target_url or "localhost:8080" in target_url or "127.0.0.1:8080" in target_url
            if is_mock_default:
                # 1. Check for explicit domain or URL in goal (e.g. "monkeytype.com", "typingtest.com", "github.com", "https://...")
                import re
                domain_match = re.search(
                    r'(https?://[^\s,]+|(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|co|ai|edu|gov|dev|app|gg|in|me)[^\s,]*)',
                    request.goal,
                    re.IGNORECASE
                )
                if domain_match:
                    found = domain_match.group(1).strip().rstrip(".,;\"'")
                    if not found.startswith("http://") and not found.startswith("https://"):
                        target_url = f"https://{found}"
                    else:
                        target_url = found
                elif any(w in goal_lower for w in ("map", "maps", "route", "direction")):
                    target_url = "https://www.google.com/maps"
                elif "youtube" in goal_lower:
                    target_url = "https://www.youtube.com"
                elif "wikipedia" in goal_lower:
                    target_url = "https://www.wikipedia.org"
                elif "flipkart" in goal_lower:
                    target_url = "https://www.flipkart.com"
                elif "amazon" in goal_lower:
                    target_url = "https://www.amazon.com"
                elif any(k in goal_lower for k in ("invoice", "apexflow", "task", "inv-")):
                    target_url = "http://127.0.0.1:8000/mock/tasks.html"
                elif not target_url:
                    target_url = "https://www.google.com"

            if target_url:
                await self.executor.execute(
                    BrowserAction(
                        action_type=ActionType.NAVIGATE,
                        url=target_url,
                        description=f"Initial navigation to {target_url}",
                    )
                )

            # 2. Main agent loop with stagnation detection & self-healing
            last_signature: Optional[str] = None
            last_url: Optional[str] = None
            consecutive_stagnant_count = 0
            self_healing_attempts = 0
            max_self_healing = 3
            recovery_hint: Optional[str] = None

            while state.current_step < state.max_steps and not self._stop_requested:
                state.current_step += 1
                step_idx = state.current_step

                # A0. Pre-Observation CAPTCHA / Security Challenge Resolution
                if hasattr(self.executor, "detect_captcha") and hasattr(self.executor, "handle_captcha"):
                    try:
                        if len(page.frames) > 1 or any(k in (page.url or "").lower() for k in ("challenge", "consent", "sorry")):
                            captcha_status = await self.executor.detect_captcha(page)
                            if captcha_status.get("present"):
                                c_type = captcha_status.get("type", "unknown")
                                await event_manager.emit(
                                    EventType.STATUS_CHANGE,
                                    f"CAPTCHA or security challenge detected ({c_type}). Attempting resolution...",
                                    {"captcha": captcha_status, "step": step_idx},
                                    run_id=run_id,
                                )
                                resolved = await self.executor.handle_captcha(page, timeout_seconds=12.0)
                                if resolved:
                                    await event_manager.emit(
                                        EventType.STATUS_CHANGE,
                                        "CAPTCHA or challenge resolved successfully.",
                                        {"captcha": captcha_status, "resolved": True, "step": step_idx},
                                        run_id=run_id,
                                    )
                                else:
                                    await event_manager.emit(
                                        EventType.SAFETY_ALERT,
                                        "CAPTCHA challenge detected. If browser window is open, please solve it.",
                                        {"captcha": captcha_status, "resolved": False, "step": step_idx},
                                        run_id=run_id,
                                    )
                    except Exception:
                        pass

                # A. Observe Page
                observation = await self.observer.observe(page)
                state.current_url = observation.url

                await event_manager.emit(
                    EventType.OBSERVATION_CAPTURED,
                    f"Observed: {observation.title} ({observation.url})",
                    {"step": step_idx, "url": observation.url, "title": observation.title},
                    run_id=run_id,
                )

                # B. Injection Defense Check
                injections = self.safety.inspect_observation(observation)
                if injections:
                    for inj in injections:
                        await event_manager.emit(
                            EventType.SAFETY_ALERT,
                            inj.reason or "Adversarial injection detected",
                            {"risk": inj.risk_level, "pattern": inj.flagged_pattern},
                            run_id=run_id,
                        )

                # C. Query LLM for next action
                await event_manager.emit(
                    EventType.AGENT_THINKING,
                    f"Agent thinking for step {step_idx}...",
                    {"step": step_idx},
                    run_id=run_id,
                )

                goal_text = f"{request.goal}\n[RECOVERY GUIDANCE]: {recovery_hint}" if recovery_hint else request.goal
                response = await self.model_client.get_next_action(
                    goal=goal_text,
                    observation=observation,
                    step_number=step_idx,
                    max_steps=state.max_steps,
                )

                # Extract query term if goal involves searching
                import re
                query_match = re.search(r"(?:search|look\s+for|find|query)\s+(?:for\s+)?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|\s+on\b|\s+in\b|$)", request.goal, re.IGNORECASE)
                if not query_match:
                    query_match = re.search(r"search (?:for )?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|$)", request.goal, re.IGNORECASE)
                query_term = query_match.group(1).strip() if query_match else ""
                if query_term.lower().endswith(" on youtube"):
                    query_term = query_term[:-11].strip()
                elif query_term.lower().endswith(" in youtube"):
                    query_term = query_term[:-11].strip()

                # Ensure action has text if typing search query from goal
                if response.action.action_type == ActionType.TYPE and not response.action.text and query_term:
                    response.action.text = query_term

                # Specialized Smart Workflow Handlers:
                curr_p_url = (page.url or "").lower()

                # A. YouTube Handler
                if "youtube.com" in curr_p_url and "results" not in curr_p_url and "watch" not in curr_p_url and "shorts" not in curr_p_url and query_term:
                    tgt = "id-search"
                    sel = "[data-agent-id='id-search']"
                    for el in observation.interactive_elements:
                        el_desc = f"{el.id or ''} {el.text or ''} {el.aria_label or ''} {el.selector or ''}".lower()
                        if el.tag_name in ("input", "textarea") and any(k in el_desc for k in ("search", "query")):
                            tgt = el.id or "id-search"
                            sel = el.selector or f"[data-agent-id='{tgt}']"
                            break

                    response.action.action_type = ActionType.TYPE
                    response.action.target = tgt
                    response.action.selector = sel
                    response.action.text = query_term
                    response.action.description = f"Type '{query_term}' into search input to search YouTube"

                elif "youtube.com" in curr_p_url and "results" in curr_p_url:
                    if any(w in goal_lower for w in ("video", "watch", "play", "first")):
                        response.action.action_type = ActionType.CLICK
                        response.action.target = "id-video-title"
                        response.action.selector = "[data-agent-id='id-video-title']"
                        response.action.description = "Click first video title link to open and play video"
                    else:
                        response.action.action_type = ActionType.FINISH
                        response.action.description = f"Search for '{query_term or 'query'}' completed and results are displayed"

                # B. Google / Search Engine Handler
                elif ("google.com" in curr_p_url or "bing.com" in curr_p_url) and "/search" not in curr_p_url and query_term:
                    tgt = None
                    sel = "textarea[name='q'], input[name='q']"
                    for el in observation.interactive_elements:
                        el_desc = f"{el.id or ''} {el.text or ''} {el.aria_label or ''} {el.selector or ''}".lower()
                        if el.tag_name in ("input", "textarea") and any(k in el_desc for k in ("search", "query", "q")):
                            tgt = el.id
                            sel = el.selector or f"[data-agent-id='{tgt}']"
                            break
                    response.action.action_type = ActionType.TYPE
                    response.action.target = tgt or "id-search"
                    response.action.selector = sel
                    response.action.text = query_term
                    response.action.description = f"Type '{query_term}' into search box"

                elif ("google.com" in curr_p_url or "bing.com" in curr_p_url) and "/search" in curr_p_url:
                    if any(w in goal_lower for w in ("open", "website", "test", "first", "link", "click")):
                        # Click first organic search result
                        result_tgt = None
                        for el in observation.interactive_elements:
                            if el.tag_name == "a" and el.text and len(el.text.strip()) > 3:
                                el_txt = el.text.lower()
                                if not any(skip in el_txt for skip in ("tools", "all", "images", "videos", "news", "sign in", "settings", "feedback")):
                                    result_tgt = el.id
                                    break
                        if result_tgt:
                            response.action.action_type = ActionType.CLICK
                            response.action.target = result_tgt
                            response.action.selector = f"[data-agent-id='{result_tgt}']"
                            response.action.description = f"Click search result link '{result_tgt}' to open website"
                    else:
                        response.action.action_type = ActionType.FINISH
                        response.action.description = f"Search for '{query_term}' completed successfully"

                # C. Typing Test & Monkeytype Handler
                elif any(tt in curr_p_url for tt in ("monkeytype.com", "typingtest.com", "10fastfingers.com", "typing.com")):
                    if any(w in goal_lower for w in ("type", "typing", "words", "test")):
                        snippet = observation.page_text_snippet or ""
                        extracted_words = "the quick brown fox jumps over the lazy dog and types with speed"
                        if snippet:
                            word_candidates = [w for w in re.findall(r'\b[a-z]{2,10}\b', snippet.lower()) if w not in ("monkeytype", "cookies", "privacy", "leaderboard", "settings", "about", "restart", "test")]
                            if len(word_candidates) >= 5:
                                extracted_words = " ".join(word_candidates[:12])

                        response.action.action_type = ActionType.TYPE
                        response.action.target = "words"
                        response.action.selector = "#wordsInput, #words, .word, input[type='text'], body"
                        response.action.text = extracted_words
                        response.action.description = f"Start typing test words: '{extracted_words[:35]}...'"

                # D. Convert Click on Input to Type
                elif response.action.action_type == ActionType.CLICK and query_term:
                    is_input = False
                    for el in observation.interactive_elements:
                        if el.id == response.action.target and el.tag_name in ("input", "textarea"):
                            is_input = True
                            break
                    if is_input:
                        response.action.action_type = ActionType.TYPE
                        response.action.text = query_term
                        response.action.description = f"Type '{query_term}' into '{response.action.target}'"

                # Check cyclic stagnation
                action_sig = f"{response.action.action_type.value}|{response.action.target}|{response.action.text}"
                curr_url = page.url or ""
                if action_sig == last_signature and curr_url == last_url:
                    consecutive_stagnant_count += 1
                else:
                    consecutive_stagnant_count = 0
                    recovery_hint = None

                last_signature = action_sig
                last_url = curr_url

                if consecutive_stagnant_count >= 2:
                    if self_healing_attempts < max_self_healing:
                        self_healing_attempts += 1
                        state.self_healing_count += 1
                        if self_healing_attempts == 1:
                            if hasattr(self.executor, "dismiss_overlays"):
                                _res = self.executor.dismiss_overlays()
                                if inspect.isawaitable(_res):
                                    await _res
                            recovery_hint = f"Action '{response.action.action_type.value}' had no effect. Dismissed possible modal overlays (Escape). Try an alternate button or link."
                            state.recovery_notes.append("Self-healing: dismissed blocking overlays on stagnation.")
                            await event_manager.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing: Dismissed potential overlays, reprompting agent.",
                                {"attempt": 1, "step": step_idx},
                                run_id=run_id,
                            )
                            continue
                        elif self_healing_attempts == 2:
                            if hasattr(self.executor, "go_back"):
                                _res = self.executor.go_back()
                                if inspect.isawaitable(_res):
                                    await _res
                            recovery_hint = "Stagnant loop detected. Backtracked to previous page. Choose an alternative link."
                            state.recovery_notes.append("Self-healing: backtracked via go_back().")
                            await event_manager.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing: Backtracked to previous page to escape stagnation.",
                                {"attempt": 2, "step": step_idx},
                                run_id=run_id,
                            )
                            continue
                        elif self_healing_attempts == 3:
                            if hasattr(self.executor, "scroll"):
                                _res = self.executor.scroll(delta_y=600)
                                if inspect.isawaitable(_res):
                                    await _res
                            recovery_hint = "Stagnation detected. Scrolled page down 600px to uncover fresh elements."
                            state.recovery_notes.append("Self-healing: scrolled viewport 600px.")
                            await event_manager.emit(
                                EventType.SELF_HEALING_RECOVERY,
                                "Self-Healing: Scrolled page to discover fresh elements.",
                                {"attempt": 3, "step": step_idx},
                                run_id=run_id,
                            )
                            continue

                # Target click loop protection (prevent clicking the same element 3+ times on same URL)
                if not hasattr(state, "_target_clicks"):
                    state._target_clicks = {}
                if response.action.target and response.action.action_type == ActionType.CLICK:
                    tgt_key = f"{curr_url}::{response.action.target}"
                    state._target_clicks[tgt_key] = state._target_clicks.get(tgt_key, 0) + 1
                    if state._target_clicks[tgt_key] >= 3:
                        logger.warning(f"Target '{response.action.target}' clicked {state._target_clicks[tgt_key]} times without URL advance. Diverting action.")
                        is_input = any((el.id == response.action.target or getattr(el, 'agent_id', None) == response.action.target) and el.tag_name in ("input", "textarea") for el in observation.interactive_elements)
                        if is_input and query_term:
                            response.action.action_type = ActionType.TYPE
                            response.action.text = query_term
                            response.action.description = f"Repetitive click diverted: typing '{query_term}' into input"
                        else:
                            alt_elem = None
                            for el in observation.interactive_elements:
                                if el.id != response.action.target and getattr(el, 'agent_id', None) != response.action.target and el.tag_name in ("a", "button"):
                                    alt_elem = el.id
                                    break
                            if alt_elem:
                                response.action.target = alt_elem
                                response.action.selector = f"[data-agent-id='{alt_elem}']"
                                response.action.description = f"Repetitive click diverted: clicking alternate element '{alt_elem}'"
                            else:
                                response.action.action_type = ActionType.SCROLL
                                response.action.scroll_delta_y = 500
                                response.action.description = "Repetitive click diverted: scrolling page down"

                # D. Safety check on proposed action
                safety_check = self.safety.evaluate_action(response.action, observation)
                if not safety_check.is_safe:
                    await event_manager.emit(
                        EventType.SAFETY_ALERT,
                        f"Blocked unsafe action: {safety_check.reason}",
                        {"action": response.action.model_dump(), "safety": safety_check.model_dump()},
                        run_id=run_id,
                    )
                    state.status = RunStatus.AWAITING_CONFIRMATION
                    break

                await event_manager.emit(
                    EventType.ACTION_PROPOSED,
                    f"Proposed {response.action.action_type.value}: {response.action.description}",
                    {"action": response.action.model_dump(), "thought": response.thought.model_dump()},
                    run_id=run_id,
                )

                # E. Execute action
                exec_result = await self.executor.execute(response.action)
                await event_manager.emit(
                    EventType.ACTION_EXECUTED,
                    exec_result.message,
                    {"result": exec_result.model_dump()},
                    run_id=run_id,
                )

                # Record step history
                record = StepRecord(
                    step_number=step_idx,
                    observation=observation,
                    thought=response.thought,
                    action=response.action,
                    safety_check=safety_check,
                    execution_result=exec_result,
                )
                state.history.append(record)

                # F. Check termination & goal completion verification
                curr_url = (page.url or "").lower()
                if any(w in goal_lower for w in ("video", "watch", "play")) and "youtube" in goal_lower and any(k in curr_url for k in ("watch", "shorts")):
                    await event_manager.emit(
                        EventType.STATUS_CHANGE,
                        f"Video playback verified on YouTube page: {page.url}. Task completed successfully!",
                        {"status": RunStatus.COMPLETED, "url": page.url, "step": step_idx},
                        run_id=run_id,
                    )
                    state.status = RunStatus.COMPLETED
                    state.final_output = f"Successfully navigated to YouTube, searched for '{query_term or 'query'}', and opened video for playback: {page.url}"
                    break

                if response.action.action_type == ActionType.FINISH:
                    # Guard against premature finish:
                    if any(w in goal_lower for w in ("video", "watch", "play")) and "youtube" in goal_lower:
                        if not any(k in curr_url for k in ("watch", "shorts")):
                            await event_manager.emit(
                                EventType.AGENT_THINKING,
                                f"Goal requires opening a video, but browser is currently on '{page.url}'. Continuing to open the first video...",
                                {"step": step_idx, "url": page.url},
                                run_id=run_id,
                            )
                            # If already on results page, click the first video title
                            if "results" in curr_url:
                                video_links = page.locator("ytd-video-renderer a#video-title, ytd-video-renderer h3 a, a[href*='/watch?v='], a[href*='/shorts/']")
                                if await video_links.count() > 0:
                                    await video_links.first.click()
                                    await page.wait_for_timeout(2000)
                            continue

                    state.status = RunStatus.COMPLETED
                    state.final_output = response.action.description
                    break
                elif response.action.action_type == ActionType.FAIL:
                    state.status = RunStatus.FAILED
                    state.error = response.action.description
                    break

                await asyncio.sleep(0.05)

            if self._stop_requested:
                state.status = RunStatus.STOPPED
            elif state.status == RunStatus.RUNNING and state.current_step >= state.max_steps:
                state.status = RunStatus.FAILED
                state.error = f"Reached maximum step limit ({state.max_steps} steps) without completing goal."
                state.final_output = f"Task incomplete: reached {state.max_steps} step limit."

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            err_msg = f"{type(exc).__name__}: {str(exc)}\n{tb}".strip()
            state.status = RunStatus.FAILED
            state.error = err_msg
            await event_manager.emit(
                EventType.ERROR,
                f"Agent run failed: {type(exc).__name__}: {str(exc)}",
                {"error": err_msg, "traceback": tb},
                run_id=run_id,
            )

        finally:
            self._is_running = False
            state.end_time = datetime.now(timezone.utc)
            await event_manager.emit(
                EventType.RUN_FINISHED,
                f"Run finished with status {state.status.value}",
                {"status": state.status, "steps": state.current_step},
                run_id=run_id,
            )
            try:
                from .voice_tts import voice_tts_engine
                if state.status == RunStatus.COMPLETED and voice_tts_engine.enabled:
                    voice_tts_engine.speak(f"Task completed: {state.final_output or 'Goal achieved'}")
            except Exception:
                pass

            # Browser cleanup is deferred or handled explicitly

        return state
