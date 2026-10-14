"""Interactive 'Watch & Learn' Macro Recorder for Klick Browser Agent.

Captures real-time user browser interactions (clicks, typing, navigation, keys)
and compiles them into deterministic, reusable, parameterized RPA MacroDefinitions.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from .macros import MacroDefinition, MacroStep, macro_manager
from .schemas import ActionType, ElementCoordinates


class MacroRecorder:
    """Session coordinator that records user interactions and compiles RPA macros."""

    def __init__(self):
        self.is_recording: bool = False
        self.macro_id: Optional[str] = None
        self.macro_name: str = ""
        self.macro_description: str = ""
        self.steps: List[MacroStep] = []
        self.default_params: Dict[str, Any] = {}
        self.start_time: float = 0.0
        self._last_input_step: Optional[MacroStep] = None

    def start_recording(
        self,
        name: str = "Recorded Macro",
        description: str = "Automated macro recorded from user interactions",
        macro_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Begin a new macro recording session."""
        import uuid
        self.is_recording = True
        self.macro_id = macro_id or f"macro-rec-{uuid.uuid4().hex[:8]}"
        self.macro_name = name
        self.macro_description = description
        self.steps = []
        self.default_params = {}
        self.start_time = time.perf_counter()
        self._last_input_step = None

        return {
            "status": "recording_started",
            "is_recording": True,
            "macro_id": self.macro_id,
            "name": self.macro_name,
            "timestamp": time.time(),
        }

    def record_step(
        self,
        action_type: str,
        target: Optional[str] = None,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        url: Optional[str] = None,
        key: Optional[str] = None,
        coordinates: Optional[Dict[str, float]] = None,
        parameter_key: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[MacroStep]:
        """Record an interaction event, deduplicating input events into clean macro steps."""
        if not self.is_recording:
            return None

        act_lower = action_type.lower().strip()
        step_num = len(self.steps) + 1

        # Coordinates parsing
        coords = None
        if coordinates and isinstance(coordinates, dict):
            coords = ElementCoordinates(
                x=coordinates.get("x", 0.0),
                y=coordinates.get("y", 0.0),
                width=coordinates.get("width", 0.0),
                height=coordinates.get("height", 0.0),
            )

        # 1. Navigation Event
        if act_lower in ("navigate", "goto", "open_url"):
            step = MacroStep(
                step_number=step_num,
                action_type=ActionType.NAVIGATE,
                url=url or target,
                description=description or f"Navigate to {url or target}",
            )
            self.steps.append(step)
            self._last_input_step = None
            return step

        # 2. Text Input Event
        elif act_lower in ("type", "input", "change"):
            input_target = target or selector or "active-input"
            input_text = text or ""

            # Parameter extraction heuristic: if user specifies param key or parameter pattern
            step_text = input_text
            if parameter_key:
                clean_param = re.sub(r"[^a-zA-Z0-9_]", "", parameter_key)
                self.default_params[clean_param] = input_text
                step_text = f"{{{{{clean_param}}}}}"

            # Deduplicate rapid typing on the same target
            if (
                self._last_input_step
                and self._last_input_step.action_type == ActionType.TYPE
                and self._last_input_step.target == input_target
            ):
                prev_text = self._last_input_step.text or ""
                if step_text.startswith(prev_text):
                    self._last_input_step.text = step_text
                else:
                    self._last_input_step.text = prev_text + step_text
                self._last_input_step.description = f"Type into '{input_target}'"
                return self._last_input_step

            step = MacroStep(
                step_number=step_num,
                action_type=ActionType.TYPE,
                target=input_target,
                selector=selector,
                text=step_text,
                description=f"Type into '{input_target}'",
            )
            self.steps.append(step)
            self._last_input_step = step
            return step

        # 3. Click Event
        elif act_lower in ("click", "tap"):
            click_target = target or selector or (f"coords({coords.x:.0f},{coords.y:.0f})" if coords else None)
            step = MacroStep(
                step_number=step_num,
                action_type=ActionType.CLICK,
                target=click_target,
                selector=selector,
                coordinates=coords,
                description=f"Click on '{click_target}'",
            )
            self.steps.append(step)
            self._last_input_step = None
            return step

        # 4. Key Press Event
        elif act_lower in ("press_key", "keydown", "keypress", "key"):
            pressed_key = key or text or "Enter"
            step = MacroStep(
                step_number=step_num,
                action_type=ActionType.PRESS_KEY,
                key=pressed_key,
                target=target,
                description=f"Press key '{pressed_key}'",
            )
            self.steps.append(step)
            self._last_input_step = None
            return step

        # 5. Wait / Sleep Event
        elif act_lower in ("wait", "sleep"):
            step = MacroStep(
                step_number=step_num,
                action_type=ActionType.WAIT,
                target=target,
                wait_seconds=1.0,
                description="Wait for page transition",
            )
            self.steps.append(step)
            self._last_input_step = None
            return step

        return None

    def stop_recording(self) -> Optional[MacroDefinition]:
        """Stop recording and register the compiled MacroDefinition into the macro manager."""
        if not self.is_recording or not self.macro_id:
            return None

        self.is_recording = False
        duration = time.perf_counter() - self.start_time

        # Renumber steps sequentially
        for idx, s in enumerate(self.steps, start=1):
            s.step_number = idx

        # Auto-extract parameters from {{param}} syntax inside steps
        detected_params: List[str] = list(self.default_params.keys())
        for s in self.steps:
            for text_field in (s.text, s.url, s.target):
                if text_field:
                    matches = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", text_field)
                    for m in matches:
                        if m not in detected_params:
                            detected_params.append(m)
                            self.default_params.setdefault(m, "")

        macro = MacroDefinition(
            id=self.macro_id,
            name=self.macro_name or "Recorded Macro",
            description=f"{self.macro_description} (Captured in {duration:.1f}s)",
            steps=self.steps,
            parameters=detected_params,
            default_params=self.default_params,
        )

        # Persist into global macro manager
        macro_manager.save_macro(macro)
        return macro

    def cancel_recording(self) -> Dict[str, Any]:
        """Cancel the active recording session without saving."""
        was_recording = self.is_recording
        self.is_recording = False
        self.steps = []
        self.macro_id = None
        return {"status": "recording_cancelled", "was_recording": was_recording}

    def get_status(self) -> Dict[str, Any]:
        """Return current recorder state."""
        return {
            "is_recording": self.is_recording,
            "macro_id": self.macro_id,
            "name": self.macro_name,
            "steps_count": len(self.steps),
            "elapsed_seconds": (time.perf_counter() - self.start_time) if self.is_recording else 0.0,
            "current_steps": [s.model_dump() for s in self.steps],
        }


# Global macro recorder singleton
macro_recorder = MacroRecorder()
