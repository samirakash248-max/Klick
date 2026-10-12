"""Pydantic v2 schemas for BrowserPilot AI.

Defines the shared contracts for:
- Page observations
- Browser actions
- Agent thoughts & responses
- Execution results
- Run lifecycle states
- Safety check results
"""

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class ActionType(str, Enum):
    """Supported browser action types."""
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"
    WAIT = "wait"
    EXTRACT = "extract"
    SWITCH_TAB = "switch_tab"
    NEW_TAB = "new_tab"
    CLOSE_TAB = "close_tab"
    FINISH = "finish"
    FAIL = "fail"



class RunStatus(str, Enum):
    """Lifecycle status of an agent run."""
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class RiskLevel(str, Enum):
    """Risk severity classification for safety guardrails."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ElementCoordinates(BaseModel):
    """Bounding box coordinates of an element."""
    x: float = Field(..., description="X coordinate of top-left corner")
    y: float = Field(..., description="Y coordinate of top-left corner")
    width: float = Field(..., description="Width of the element")
    height: float = Field(..., description="Height of the element")


class ElementDescriptor(BaseModel):
    """Descriptor for an interactive DOM element observed on the page."""
    id: Optional[str] = Field(default=None, description="DOM ID attribute")
    tag_name: str = Field(..., description="HTML tag name (e.g. button, input, a)")
    selector: str = Field(..., description="Recommended CSS or XPath selector")
    text: Optional[str] = Field(default=None, description="Visible inner text or placeholder")
    role: Optional[str] = Field(default=None, description="ARIA role or computed role")
    aria_label: Optional[str] = Field(default=None, description="ARIA label attribute")
    is_interactive: bool = Field(default=True, description="Whether element is clickable/typeable")
    coordinates: Optional[ElementCoordinates] = Field(default=None, description="Screen coordinates")
    href: Optional[str] = Field(default=None, description="Target link URL for anchor elements")


class PageObservation(BaseModel):
    """Structured observation of the active browser page."""
    url: str = Field(..., description="Current page URL")
    title: str = Field(default="", description="Current page title")
    dom_summary: str = Field(
        default="",
        description="Distilled, token-efficient DOM representation formatted for LLM consumption"
    )
    interactive_elements: List[ElementDescriptor] = Field(
        default_factory=list,
        description="List of detected interactive elements"
    )
    page_text_snippet: Optional[str] = Field(
        default=None,
        description="Truncated text content of the visible viewport"
    )
    screenshot_base64: Optional[str] = Field(
        default=None,
        description="Optional base64-encoded screenshot image for multimodal validation"
    )
    error: Optional[str] = Field(
        default=None,
        description="Observation error description if capture partially failed"
    )
    open_tabs: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of open browser tabs with index, title, url, is_active"
    )
    active_tab_index: int = Field(
        default=0,
        description="Index of the currently active tab"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of observation"
    )


class BrowserAction(BaseModel):
    """Next atomic action to execute on the browser."""
    action_type: ActionType = Field(..., description="Action to perform")
    target: Optional[str] = Field(
        default=None,
        description="Target element data-agent-id or identifier (for CLICK, TYPE, WAIT)"
    )
    selector: Optional[str] = Field(
        default=None,
        description="CSS selector or XPath targeting the element (for CLICK, TYPE, EXTRACT)"
    )
    text: Optional[str] = Field(
        default=None,
        description="Text content to type (for TYPE action)"
    )
    url: Optional[str] = Field(
        default=None,
        description="Target URL to open (for NAVIGATE action)"
    )
    key: Optional[str] = Field(
        default=None,
        description="Keyboard key to press (e.g. 'Enter', 'Tab', 'Escape')"
    )
    tab_index: Optional[int] = Field(
        default=None,
        description="Target tab index (0-based) for switch_tab or close_tab actions"
    )
    scroll_delta_y: Optional[int] = Field(
        default=None,
        description="Vertical scroll amount in pixels (positive down, negative up)"
    )
    wait_seconds: Optional[float] = Field(
        default=1.0,
        description="Duration in seconds to wait (for WAIT action)"
    )
    description: str = Field(
        ...,
        description="Human-readable explanation of why this action was chosen"
    )
    target_element_description: Optional[str] = Field(
        default=None,
        description="Plain text description of the target element (for verification)"
    )


    @model_validator(mode="before")
    @classmethod
    def normalize_action_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize action name alias
            raw_act = data.get("action_type") or data.get("action") or data.get("type") or data.get("act") or data.get("name")
            if not raw_act:
                if data.get("url"):
                    raw_act = "navigate"
                elif data.get("text"):
                    raw_act = "type"
                elif data.get("target") or data.get("selector"):
                    raw_act = "click"
                elif data.get("key"):
                    raw_act = "press_key"
            if raw_act:
                act_str = (raw_act.value if hasattr(raw_act, "value") else str(raw_act)).strip().lower()
                # Normalize common aliases
                if act_str in ("goto", "open", "visit") or act_str.startswith("nav") or act_str.startswith("go_to"):
                    act_str = "navigate"
                elif act_str in ("done", "complete", "stop", "exit") or act_str.startswith("finish"):
                    act_str = "finish"
                elif act_str in ("press", "key"):
                    act_str = "press_key"
                elif act_str in ("input", "write", "fill", "enter") or act_str.startswith("type") or act_str.startswith("fill"):
                    act_str = "type"
                elif act_str in ("play", "watch") or act_str.startswith("click") or act_str.startswith("select") or act_str.startswith("skip"):
                    act_str = "click"
                elif act_str in ("switch_tab", "switch-tab", "switch_to_tab", "focus_tab", "select_tab"):
                    act_str = "switch_tab"
                elif act_str in ("new_tab", "new-tab", "open_tab", "create_tab"):
                    act_str = "new_tab"
                elif act_str in ("close_tab", "close-tab", "close_current_tab"):
                    act_str = "close_tab"
                data["action_type"] = act_str

            # Support tab_index extraction from common aliases
            if "tab_index" not in data or data.get("tab_index") is None:
                for k in ("index", "tab", "tab_number", "tab_idx"):
                    if k in data and data.get(k) is not None:
                        try:
                            data["tab_index"] = int(data[k])
                            break
                        except (ValueError, TypeError):
                            pass


            # Support value alias (e.g. {"type": "navigate", "value": "..."})
            if "value" in data and data.get("value"):
                val = str(data["value"]).strip()
                if data.get("action_type") == "navigate" and not data.get("url"):
                    data["url"] = val
                elif data.get("action_type") == "type" and not data.get("text"):
                    data["text"] = val
                elif data.get("action_type") == "press_key" and not data.get("key"):
                    data["key"] = val

            if data.get("action_type") == "type" and not data.get("text"):
                for k in ("query", "input", "search", "content"):
                    if data.get(k):
                        data["text"] = str(data[k]).strip()
                        break

            # If action is navigate and url is not set, extract from target, selector, or value
            if data.get("action_type") == "navigate" and not data.get("url"):
                tgt = str(data.get("target") or data.get("selector") or "").strip()
                if tgt:
                    data["url"] = tgt

            # Normalize key aliases for press_key
            if "key" in data and data.get("key"):
                raw_key = str(data["key"]).strip()
                key_map = {
                    "search": "Enter",
                    "submit": "Enter",
                    "return": "Enter",
                    "\n": "Enter",
                    "\r": "Enter",
                    "\r\n": "Enter",
                    "esc": "Escape",
                    "space": " ",
                    "up": "ArrowUp",
                    "down": "ArrowDown",
                    "left": "ArrowLeft",
                    "right": "ArrowRight",
                }
                data["key"] = key_map.get(raw_key.lower(), raw_key)

            # Support target_id alias (e.g. from starter schema or raw model)
            if "target_id" in data and data.get("target_id"):
                tid = str(data["target_id"]).strip()
                if not data.get("target"):
                    data["target"] = tid
                if not data.get("selector"):
                    data["selector"] = f"[data-agent-id='{tid}']"

            # Cross-populate target and selector
            if data.get("selector") and not data.get("target"):
                sel = str(data["selector"]).strip()
                match = re.search(r'data-agent-id=["\']?([^"\'\]]+)["\']?', sel)
                if match:
                    data["target"] = match.group(1)
                elif sel.startswith("#"):
                    data["target"] = sel[1:]
                else:
                    data["target"] = sel
            elif data.get("target") and not data.get("selector"):
                tgt = str(data["target"]).strip()
                if tgt.startswith("#") or tgt.startswith("[") or " " in tgt or "." in tgt:
                    data["selector"] = tgt
                else:
                    data["selector"] = f"[data-agent-id='{tgt}']"

            if "delta_y" in data and "scroll_delta_y" not in data:
                data["scroll_delta_y"] = data["delta_y"]
            if "amount" in data and "scroll_delta_y" not in data:
                data["scroll_delta_y"] = data["amount"]
            if "duration" in data and "wait_seconds" not in data:
                data["wait_seconds"] = data["duration"]
            if "seconds" in data and "wait_seconds" not in data:
                data["wait_seconds"] = data["seconds"]
        return data


class AgentThought(BaseModel):
    """Reasoning traces generated by the AI agent prior to acting."""
    reflection: str = Field(
        default="Observing current page state.",
        description="Assessment of previous action result and current situation"
    )
    reasoning: str = Field(
        default="Deciding best action to advance the goal.",
        description="Analytical deduction and decision logic for the next step"
    )
    plan: List[str] = Field(
        default_factory=list,
        description="Anticipated remaining high-level steps to achieve the goal"
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_thought(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"reflection": "Observation captured.", "reasoning": data.strip(), "plan": []}
        if isinstance(data, dict):
            reasoning = data.get("reasoning") or data.get("thought") or data.get("reflection") or "Deciding best action."
            reflection = data.get("reflection") or "Observing current page state."
            plan = data.get("plan")
            if not isinstance(plan, list):
                plan = []
            return {
                "reflection": str(reflection).strip(),
                "reasoning": str(reasoning).strip(),
                "plan": plan,
            }
        return {"reflection": "Observation captured.", "reasoning": "Deciding best action.", "plan": []}


class AgentResponse(BaseModel):
    """Structured response contract produced by Ollama / Llama 3.2."""
    thought: AgentThought = Field(default_factory=AgentThought, description="Cognitive thought process")
    action: BrowserAction = Field(..., description="Structured browser action")
    raw_model_response: Optional[str] = Field(
        default=None,
        description="Raw unparsed model text response for telemetry and auditing"
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_agent_response(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        raw_action = data.get("action")
        raw_action_type = data.get("action_type")

        # Flat action shape detection:
        # e.g. {"action": "click", "selector": "[data-agent-id='elem-112']"}
        # or {"thought": "...", "action": "click", "target_id": "..."}
        # where data["action"] is a string or data has top-level action_type string
        if isinstance(raw_action, str) or (isinstance(raw_action_type, str) and not isinstance(raw_action, dict)):
            action_name = raw_action if isinstance(raw_action, str) else raw_action_type
            action_dict = dict(data)
            action_dict["action_type"] = action_name

            # Synthesize description for flat model outputs if not supplied
            if not action_dict.get("description"):
                tgt = action_dict.get("target") or action_dict.get("selector") or action_dict.get("url") or action_dict.get("target_id") or ""
                action_dict["description"] = f"{str(action_name).capitalize()} {tgt}".strip()

            raw_thought = data.get("thought")
            if isinstance(raw_thought, dict):
                thought_dict = raw_thought
            elif isinstance(raw_thought, str):
                thought_dict = {
                    "reflection": "Observation captured.",
                    "reasoning": raw_thought.strip(),
                    "plan": [],
                }
            else:
                thought_dict = {
                    "reflection": "Observation captured.",
                    "reasoning": f"Taking action: {action_name}",
                    "plan": [],
                }
            action_dict.pop("thought", None)

            return {
                "thought": thought_dict,
                "action": action_dict,
                "raw_model_response": data.get("raw_model_response"),
            }

        # Nested format where thought might be missing or a string
        if "thought" not in data or isinstance(data.get("thought"), str) or (isinstance(data.get("thought"), dict) and not data.get("thought", {}).get("reasoning")):
            raw_thought = data.get("thought")
            if isinstance(raw_thought, str):
                data["thought"] = {
                    "reflection": "Observation captured.",
                    "reasoning": raw_thought.strip(),
                    "plan": [],
                }
            elif isinstance(raw_thought, dict):
                data["thought"]["reasoning"] = data["thought"].get("reasoning") or data["thought"].get("thought") or "Deciding best action."
                data["thought"]["reflection"] = data["thought"].get("reflection") or "Observing current page state."
            else:
                data["thought"] = {
                    "reflection": "Observation captured.",
                    "reasoning": "Determined next action.",
                    "plan": [],
                }

        return data


class ExecutionResult(BaseModel):
    """Result returned by PlaywrightExecutor after attempting an action."""
    success: bool = Field(..., description="Whether action execution succeeded")
    action_type: ActionType = Field(..., description="Action type attempted")
    action: Optional[str] = Field(default=None, description="Action name string alias")
    target: Optional[str] = Field(default=None, description="Target element identifier")
    message: str = Field(..., description="Execution outcome or extracted data summary")
    duration_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Execution timestamp"
    )
    error: Optional[str] = Field(default=None, description="Detailed error message if failed")
    extracted_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Data extracted from page if action was EXTRACT"
    )

    @model_validator(mode="before")
    @classmethod
    def sync_action_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "action" in data and "action_type" not in data:
                data["action_type"] = data["action"]
            elif "action_type" in data and "action" not in data:
                act = data["action_type"]
                data["action"] = act.value if hasattr(act, "value") else str(act)
        return data


class SafetyCheckResult(BaseModel):
    """Outcome of safety and prompt-injection guardrails evaluation."""
    is_safe: bool = Field(..., description="Whether the action is deemed safe to execute")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="Assessed risk level")
    reason: Optional[str] = Field(default=None, description="Explanation for safety classification")
    flagged_pattern: Optional[str] = Field(
        default=None,
        description="Triggered regex, keyword, or adversarial marker"
    )
    requires_human_confirmation: bool = Field(
        default=False,
        description="Whether agent must pause for human confirmation before continuing"
    )


class AgentRunRequest(BaseModel):
    """Payload to initiate an autonomous browser session."""
    goal: str = Field(..., min_length=3, description="Natural language goal for the agent")
    start_url: Optional[str] = Field(
        default=None,
        description="Initial URL to navigate to (defaults to MOCK_SITE_URL or configured home)"
    )
    max_steps: Optional[int] = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum execution steps before automatic timeout"
    )
    model: Optional[str] = Field(
        default=None,
        description="Optional Ollama model name override (e.g. llama3.2:3b)"
    )


class StepRecord(BaseModel):
    """Historical trace record for a single step in an agent run."""
    step_number: int
    observation: PageObservation
    thought: AgentThought
    action: BrowserAction
    safety_check: SafetyCheckResult
    execution_result: Optional[ExecutionResult] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRunState(BaseModel):
    """Complete state snapshot of an active or finished agent session."""
    run_id: str = Field(..., description="Unique UUID for this run")
    goal: str = Field(..., description="Goal provided by user")
    status: RunStatus = Field(default=RunStatus.IDLE, description="Current run state")
    current_step: int = Field(default=0, description="Current step index")
    max_steps: int = Field(default=15, description="Configured step threshold")
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    current_url: Optional[str] = None
    history: List[StepRecord] = Field(default_factory=list, description="Step execution history")
    error: Optional[str] = None
    final_output: Optional[str] = None


class ExecuteActionRequest(BaseModel):
    """Payload to execute an atomic browser action via HTTP API."""
    action: str = Field(..., description="Action name: click, type, scroll, navigate, wait, press_key")
    target: Optional[str] = Field(default=None, description="Target data-agent-id or selector")
    text: Optional[str] = Field(default=None, description="Text to type")
    url: Optional[str] = Field(default=None, description="Target URL")
    key: Optional[str] = Field(default=None, description="Keyboard key to press")
    scroll_delta_y: Optional[int] = Field(default=None, description="Vertical scroll delta")
    delta_y: Optional[int] = Field(default=None, description="Alias for scroll_delta_y")
    wait_seconds: Optional[float] = Field(default=None, description="Duration in seconds to wait")
    seconds: Optional[float] = Field(default=None, description="Alias for wait_seconds")
    description: Optional[str] = Field(default="", description="Optional human-readable description")

    @model_validator(mode="before")
    @classmethod
    def normalize_execute_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "delta_y" in data and "scroll_delta_y" not in data:
                data["scroll_delta_y"] = data["delta_y"]
            if "seconds" in data and "wait_seconds" not in data:
                data["wait_seconds"] = data["seconds"]
            if "duration" in data and "wait_seconds" not in data:
                data["wait_seconds"] = data["duration"]
        return data


class ObservationResponse(BaseModel):
    """Structured response for GET /observe endpoint."""
    url: str = Field(..., description="Current page URL")
    title: str = Field(default="", description="Current page title")
    elements: List[Dict[str, Any]] = Field(default_factory=list, description="Visible interactive elements")


class StopResponse(BaseModel):
    """Response returned by POST /stop endpoint."""
    status: str = Field(default="stopped", description="Status confirmation")
    message: str = Field(default="Execution stopped", description="Outcome description")


class ExtensionStepRequest(BaseModel):
    """Payload sent by Chrome Extension to request next cognitive action for in-tab execution."""
    run_id: str = Field(..., description="Unique run ID for extension session")
    goal: str = Field(..., description="User's goal for active tab")
    step: int = Field(default=1, description="Current step number")
    max_steps: int = Field(default=10, description="Max step bounds")
    observation: PageObservation = Field(..., description="Fresh page observation extracted by content script")
    last_result: Optional[Dict[str, Any]] = Field(default=None, description="Execution result from prior step")
    model: Optional[str] = Field(default=None, description="Optional model override (e.g. llama3.2:3b)")


class ExtensionStepResponse(BaseModel):
    """Next atomic action and thought produced by LLM for extension execution."""
    run_id: str
    step: int
    thought: AgentThought
    action: BrowserAction
    status: RunStatus
    is_terminal: bool = False
    final_output: Optional[str] = None
    safety_passed: bool = True
    safety_reason: Optional[str] = None


