"""Deterministic Macro Recording & Hybrid Replay (RPA Mode) Module.

Allows users to record browser execution sequences, parameterize template fields
(e.g. {{query}}, {{username}}), and replay them deterministically with 0ms LLM latency,
falling back gracefully to the cognitive agent if UI drift is detected.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from .events import EventType, EventManager
from .executor import PlaywrightExecutor
from .schemas import (
    ActionType,
    AgentRunRequest,
    AgentRunState,
    ExecutionResult,
    MacroDefinition,
    MacroReplayRequest,
    MacroReplayResult,
    MacroStep,
)

logger = logging.getLogger("browserpilot.macros")

DEFAULT_MACROS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "macros"


def interpolate_template(value: Optional[str], params: Dict[str, Any]) -> Optional[str]:
    """Substitute {{param_name}} tokens in string with actual parameter values."""
    if not value or "{{" not in value:
        return value
    result = value
    for k, v in params.items():
        placeholder = f"{{{{{k}}}}}"
        if placeholder in result:
            result = result.replace(placeholder, str(v))
    return result


def extract_template_parameters(text: Optional[str]) -> List[str]:
    """Extract list of variable names inside {{...}} delimiters."""
    if not text:
        return []
    return re.findall(r"\{\{([a-zA-Z0-9_\-]+)\}\}", text)


class MacroManager:
    """Manages storage, compilation, parameterization, and replay of RPA macros."""

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = storage_dir or DEFAULT_MACROS_DIR
        self._ensure_storage_dir()
        self._seed_default_macros()

    def _ensure_storage_dir(self) -> None:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"Could not create macro storage directory: {exc}")

    def _seed_default_macros(self) -> None:
        """Seed built-in standard automation recipes if not already present."""
        defaults = [
            MacroDefinition(
                id="youtube_search_play",
                name="YouTube Search & Play",
                description="Navigate to YouTube, search for query, and start the first video result.",
                parameters=["query"],
                default_params={"query": "casetoo"},
                steps=[
                    MacroStep(
                        action_type=ActionType.NAVIGATE,
                        url="https://www.youtube.com",
                        description="Navigate to YouTube homepage",
                    ),
                    MacroStep(
                        action_type=ActionType.WAIT,
                        seconds=1.2,
                        description="Wait for YouTube homepage to render",
                    ),
                    MacroStep(
                        action_type=ActionType.TYPE,
                        target="id-search",
                        selector="input#search, input[name='search_query']",
                        text="{{query}}",
                        description="Type query into YouTube search bar",
                    ),
                    MacroStep(
                        action_type=ActionType.WAIT,
                        seconds=1.0,
                        description="Wait for search results to load",
                    ),
                    MacroStep(
                        action_type=ActionType.CLICK,
                        target="id-video-title",
                        selector="ytd-video-renderer a#video-title, a[href*='/watch?v=']",
                        description="Click first video result",
                    ),
                ],
            ),
            MacroDefinition(
                id="google_search",
                name="Google Search",
                description="Navigate to Google and perform search for query.",
                parameters=["query"],
                default_params={"query": "top AI developments"},
                steps=[
                    MacroStep(
                        action_type=ActionType.NAVIGATE,
                        url="https://www.google.com",
                        description="Open Google search",
                    ),
                    MacroStep(
                        action_type=ActionType.WAIT,
                        seconds=0.8,
                        description="Wait for Google homepage",
                    ),
                    MacroStep(
                        action_type=ActionType.TYPE,
                        target="q",
                        selector="textarea[name='q'], input[name='q']",
                        text="{{query}}",
                        description="Type search query",
                    ),
                    MacroStep(
                        action_type=ActionType.PRESS_KEY,
                        key="Enter",
                        description="Submit search with Enter key",
                    ),
                ],
            ),
            MacroDefinition(
                id="mock_site_login",
                name="Mock Site Demo Login",
                description="Open local testbed site and submit sample credentials.",
                parameters=["username"],
                default_params={"username": "admin"},
                steps=[
                    MacroStep(
                        action_type=ActionType.NAVIGATE,
                        url="http://127.0.0.1:8000/mock/index.html",
                        description="Open local mock test site",
                    ),
                    MacroStep(
                        action_type=ActionType.WAIT,
                        seconds=0.5,
                        description="Wait for mock elements",
                    ),
                    MacroStep(
                        action_type=ActionType.TYPE,
                        target="id-username",
                        selector="[data-agent-id='id-username']",
                        text="{{username}}",
                        description="Fill username input",
                    ),
                ],
            ),
        ]

        for m in defaults:
            target_path = self.storage_dir / f"{m.id}.json"
            if not target_path.exists():
                try:
                    with open(target_path, "w", encoding="utf-8") as f:
                        json.dump(m.model_dump(), f, indent=2)
                except Exception as exc:
                    logger.warning(f"Failed to seed macro {m.id}: {exc}")

    def list_macros(self) -> List[MacroDefinition]:
        """Return all persisted macro definitions sorted by creation date."""
        macros: List[MacroDefinition] = []
        if not self.storage_dir.exists():
            return macros

        for p in self.storage_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    macros.append(MacroDefinition.model_validate(data))
            except Exception as exc:
                logger.warning(f"Failed to load macro file {p.name}: {exc}")

        return sorted(macros, key=lambda m: m.name.lower())

    def get_macro(self, macro_id: str) -> Optional[MacroDefinition]:
        """Fetch a specific macro by its unique ID."""
        target_path = self.storage_dir / f"{macro_id}.json"
        if not target_path.exists():
            return None
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return MacroDefinition.model_validate(data)
        except Exception as exc:
            logger.warning(f"Failed to load macro {macro_id}: {exc}")
            return None

    def save_macro(self, macro: MacroDefinition) -> MacroDefinition:
        """Persist or update a macro definition to disk."""
        self._ensure_storage_dir()
        # Detect parameters if not populated
        detected_params = set(macro.parameters)
        for s in macro.steps:
            for p in extract_template_parameters(s.text):
                detected_params.add(p)
            for p in extract_template_parameters(s.url):
                detected_params.add(p)
        macro.parameters = sorted(list(detected_params))

        target_path = self.storage_dir / f"{macro.id}.json"
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(macro.model_dump(), f, indent=2)
        return macro

    def delete_macro(self, macro_id: str) -> bool:
        """Delete a saved macro from storage."""
        target_path = self.storage_dir / f"{macro_id}.json"
        if target_path.exists():
            try:
                target_path.unlink()
                return True
            except Exception:
                return False
        return False

    def record_from_run(
        self,
        run_state: AgentRunState,
        name: str,
        description: str = "",
        parameterize_query: Optional[str] = None,
    ) -> MacroDefinition:
        """Convert a successful AgentRunState history into a deterministic MacroDefinition."""
        steps: List[MacroStep] = []
        detected_params: List[str] = []
        query_to_replace = (parameterize_query or "").strip()

        for rec in run_state.history:
            act = rec.action
            # Skip finish / fail / terminal pseudo-actions
            if act.action_type in (ActionType.FINISH, ActionType.FAIL):
                continue

            text_val = act.text
            url_val = act.url

            # Parameterize query if detected
            if query_to_replace and text_val and query_to_replace.lower() in text_val.lower():
                pattern = re.compile(re.escape(query_to_replace), re.IGNORECASE)
                text_val = pattern.sub("{{query}}", text_val)
                if "query" not in detected_params:
                    detected_params.append("query")

            step = MacroStep(
                action_type=act.action_type,
                target=act.target or act.selector,
                selector=act.selector,
                text=text_val,
                url=url_val,
                key=act.key,
                seconds=act.wait_seconds,
                scroll_delta_y=act.scroll_delta_y,
                tab_index=act.tab_index,
                description=act.description,
            )
            steps.append(step)

        macro_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.lower().strip())
        if not macro_id:
            macro_id = f"macro_{uuid.uuid4().hex[:8]}"

        macro = MacroDefinition(
            id=macro_id,
            name=name,
            description=description or f"Recorded from run {run_state.run_id} ({run_state.goal})",
            parameters=detected_params,
            default_params={"query": query_to_replace} if query_to_replace else {},
            steps=steps,
        )
        return self.save_macro(macro)

    async def replay_macro(
        self,
        macro_id: str,
        parameters: Dict[str, Any],
        executor: PlaywrightExecutor,
        agent_runner: Optional[Any] = None,
        fallback_to_agent: bool = True,
        event_manager: Optional[EventManager] = None,
    ) -> MacroReplayResult:
        """Deterministically replay a macro, executing steps in sequence with 0ms AI overhead.

        If a step fails (due to element changes or UI drift), optionally triggers
        the ControlledAgentRunner to complete the remaining task seamlessly.
        """
        macro = self.get_macro(macro_id)
        if not macro:
            return MacroReplayResult(
                macro_id=macro_id,
                macro_name=macro_id,
                success=False,
                steps_executed=0,
                total_steps=0,
                message=f"Macro '{macro_id}' not found.",
                error="Macro not found",
            )

        # Merge defaults with provided params
        merged_params = dict(macro.default_params)
        merged_params.update(parameters)

        start_time = time.perf_counter()
        step_results: List[Dict[str, Any]] = []
        executed_count = 0
        total_steps = len(macro.steps)

        if event_manager:
            await event_manager.emit(
                EventType.MACRO_EXECUTION,
                f"Starting deterministic replay of macro '{macro.name}' ({total_steps} steps)",
                {"macro_id": macro.id, "parameters": merged_params},
            )

        failed_step_idx = -1
        failed_step_error = None

        for idx, step in enumerate(macro.steps):
            # Substitute template variables
            bound_text = interpolate_template(step.text, merged_params)
            bound_url = interpolate_template(step.url, merged_params)
            bound_target = interpolate_template(step.target or step.selector, merged_params)
            bound_desc = interpolate_template(step.description, merged_params)

            action_payload: Dict[str, Any] = {
                "action": step.action_type.value if hasattr(step.action_type, "value") else str(step.action_type),
                "target": bound_target,
                "selector": step.selector,
                "text": bound_text,
                "url": bound_url,
                "key": step.key,
                "seconds": step.seconds,
                "scroll_delta_y": step.scroll_delta_y,
                "tab_index": step.tab_index,
                "description": bound_desc,
            }

            try:
                res: ExecutionResult = await executor.execute(action_payload)
                step_record = {
                    "step": idx + 1,
                    "action": action_payload["action"],
                    "target": bound_target,
                    "success": res.success,
                    "message": res.message,
                    "error": res.error,
                    "duration_ms": res.duration_ms,
                }
                step_results.append(step_record)

                if res.success:
                    executed_count += 1
                    if event_manager:
                        await event_manager.emit(
                            EventType.ACTION_EXECUTED,
                            f"[Macro {idx + 1}/{total_steps}] {res.message}",
                            step_record,
                        )
                else:
                    failed_step_idx = idx + 1
                    failed_step_error = res.error or res.message
                    break

            except Exception as exc:
                failed_step_idx = idx + 1
                failed_step_error = str(exc)
                step_results.append({
                    "step": idx + 1,
                    "action": action_payload["action"],
                    "success": False,
                    "error": str(exc),
                })
                break

        total_duration = round((time.perf_counter() - start_time) * 1000, 2)

        # Handle successful completion
        if failed_step_idx == -1:
            msg = f"Macro '{macro.name}' completed all {total_steps} steps successfully in {total_duration}ms."
            if event_manager:
                await event_manager.emit(
                    EventType.MACRO_EXECUTION,
                    msg,
                    {"macro_id": macro.id, "success": True, "duration_ms": total_duration},
                )
            return MacroReplayResult(
                macro_id=macro.id,
                macro_name=macro.name,
                success=True,
                steps_executed=executed_count,
                total_steps=total_steps,
                duration_ms=total_duration,
                message=msg,
                step_results=step_results,
            )

        # Handle Step Failure -> Check Hybrid Agent Fallback
        fallback_triggered = False
        fallback_run_id = None
        if fallback_to_agent and agent_runner is not None:
            fallback_triggered = True
            fallback_goal = (
                f"Complete remaining objective for macro '{macro.name}': {macro.description}. "
                f"Deterministic replay completed {executed_count}/{total_steps} steps before pausing at step {failed_step_idx}: {failed_step_error}."
            )
            if event_manager:
                await event_manager.emit(
                    EventType.STATUS_CHANGE,
                    f"Macro step {failed_step_idx} deviated. Handing off to AI Agent for adaptive recovery...",
                    {"macro_id": macro.id, "failed_step": failed_step_idx, "error": failed_step_error},
                )
            try:
                req = AgentRunRequest(
                    goal=fallback_goal,
                    max_steps=max(5, total_steps - executed_count + 4),
                )
                agent_state: AgentRunState = await agent_runner.run(req, page=executor.current_page)
                fallback_run_id = agent_state.run_id
                is_recovered = agent_state.status.value in ("completed", "finished")
                return MacroReplayResult(
                    macro_id=macro.id,
                    macro_name=macro.name,
                    success=is_recovered,
                    steps_executed=executed_count,
                    total_steps=total_steps,
                    duration_ms=total_duration,
                    message=f"Macro paused at step {failed_step_idx}; AI fallback executed (Status: {agent_state.status.value}).",
                    error=failed_step_error if not is_recovered else None,
                    step_results=step_results,
                    fallback_triggered=True,
                    agent_run_id=fallback_run_id,
                )
            except Exception as agent_exc:
                logger.warning(f"Macro AI fallback failed: {agent_exc}")

        return MacroReplayResult(
            macro_id=macro.id,
            macro_name=macro.name,
            success=False,
            steps_executed=executed_count,
            total_steps=total_steps,
            duration_ms=total_duration,
            message=f"Macro failed at step {failed_step_idx}: {failed_step_error}",
            error=failed_step_error,
            step_results=step_results,
            fallback_triggered=fallback_triggered,
            agent_run_id=fallback_run_id,
        )


# Global singleton instance
macro_manager = MacroManager()
