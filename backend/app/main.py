"""FastAPI application entrypoint for BrowserPilot AI.

Provides REST and WebSocket endpoints for:
1. Health verification: GET /health
2. Browser observation: GET /observe
3. Browser action execution: POST /execute
4. Execution event telemetry: GET /events
5. Session flow control: POST /stop and POST /resume
6. Dashboard communication and static mock-site serving

Owned by: Browser Automation Engineer (Member B)
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import uuid

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
    try:
        import uvicorn.loops.asyncio
        uvicorn.loops.asyncio.asyncio_loop_factory = lambda use_subprocess=False: asyncio.ProactorEventLoop
    except Exception:
        pass
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hdesk = u32.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            u32.SetThreadDesktop(hdesk)
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from datetime import datetime, timezone

from .agent_loop import AgentLoop
from .events import EventType, event_manager
from .executor import EXPORTS_DIR, MOCK_SITE_DEFAULT_URL, PlaywrightExecutor
from .model_client import HybridModelRouter, ModelTier
from .observer import PlaywrightObserver
from .safety import safety_guard
from .schemas import (
    ActionType,
    AgentRunRequest,
    AgentRunState,
    AgentThought,
    BrowserAction,
    ExecuteActionRequest,
    ExecutionResult,
    ExtensionStepRequest,
    ExtensionStepResponse,
    MacroDefinition,
    MacroReplayRequest,
    MacroReplayResult,
    MacroStep,
    ObservationResponse,
    PageObservation,
    RunStatus,
    StepRecord,
    StopResponse,
)
from .macros import macro_manager

# Concurrency lock to serialize all active browser operations
_browser_lock: Optional[asyncio.Lock] = None
_lock_loop: Optional[asyncio.AbstractEventLoop] = None


def get_browser_lock() -> asyncio.Lock:
    """Return an asyncio.Lock bound to the current running event loop."""
    global _browser_lock, _lock_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.Lock()
    if _browser_lock is None or _lock_loop != current_loop:
        _browser_lock = asyncio.Lock()
        _lock_loop = current_loop
    return _browser_lock


# Headless mode: checks HEADLESS_BROWSER and BROWSERPILOT_HEADLESS (defaulting to False so browser is visible for human solving)
_hl_env = os.getenv("HEADLESS_BROWSER") or os.getenv("BROWSERPILOT_HEADLESS") or "false"
HEADLESS_MODE = _hl_env.strip().lower() in ("true", "1", "yes")
ALLOW_EXTERNAL = os.getenv("BROWSERPILOT_ALLOW_EXTERNAL", "true").strip().lower() in ("true", "1", "yes")
try:
    SLOW_MO_MS = int(os.getenv("BROWSER_SLOW_MO_MS", "0"))
except ValueError:
    SLOW_MO_MS = 0

# Core browser observer and executor singletons
executor = PlaywrightExecutor(headless=HEADLESS_MODE, slow_mo_ms=SLOW_MO_MS, allow_external=ALLOW_EXTERNAL)
observer = PlaywrightObserver()

# Hybrid intelligent model router singleton
model_router = HybridModelRouter()

# Global agent loop instance (Member A coordinator)
agent_loop = AgentLoop(model_client=model_router, observer=observer, executor=executor)
_active_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and graceful shutdown."""
    yield
    # Cleanup browser resources on shutdown
    async with get_browser_lock():
        if executor:
            await executor.close()
        if observer:
            await observer.close()


app = FastAPI(
    title="Klick AI",
    description="Local-first autonomous browser agent backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# STEP 4 REQUIRED ENDPOINTS
# =====================================================================

@app.get("/", include_in_schema=False)
async def root():
    """Redirect root path to interactive Swagger API documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health_check():
    """Confirm the API server is running."""
    return {"status": "ok"}


@app.get("/observe", response_model=ObservationResponse)
async def observe_browser():
    """Return the current page URL, title, and visible interactive elements using observer.py."""
    async with get_browser_lock():
        try:
            # Ensure browser page is open and active
            default_home = "https://www.google.com" if ALLOW_EXTERNAL else MOCK_SITE_DEFAULT_URL
            if not executor.current_page or executor.current_page.is_closed():
                await executor.initialize()
                await executor.execute({"action": "navigate", "url": default_home})
            elif executor.current_page.url in ("", "about:blank"):
                await executor.execute({"action": "navigate", "url": default_home})

            page = executor.current_page
            obs = await observer.observe_structured(page)

            return ObservationResponse(
                url=obs.get("url", ""),
                title=obs.get("title", ""),
                elements=obs.get("elements", []),
                screenshot_base64=obs.get("screenshot_base64"),
                annotated_screenshot_base64=obs.get("annotated_screenshot_base64"),
                visual_elements_count=obs.get("visual_elements_count", len(obs.get("elements", []))),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Observation failed: {str(exc)}")


@app.post("/execute", response_model=ExecutionResult)
async def execute_action(request: ExecuteActionRequest):
    """Accept one validated browser action, execute it through executor.py, and return actual result."""
    events_to_emit: List[Dict[str, Any]] = []

    async with get_browser_lock():
        # Check stop state before executing
        if executor.is_stopped:
            result = ExecutionResult(
                success=False,
                action_type=ActionType.FAIL,
                action=request.action,
                target=request.target,
                message="Execution is stopped. Action blocked.",
                error="Execution stopped",
            )
        else:
            # Action-level safety guard policy check at shared execution boundary
            act_type = request.action
            if isinstance(act_type, str):
                try:
                    act_type = ActionType(act_type.lower())
                except ValueError:
                    act_type = ActionType.FAIL

            browser_act = BrowserAction(
                action_type=act_type,
                selector=f'[data-agent-id="{request.target}"]' if request.target else None,
                text=request.text,
                url=request.url,
                description=f"Direct action {act_type.value if hasattr(act_type, 'value') else act_type}" + (f" on {request.target}" if request.target else ""),
            )
            safety_check = safety_guard.evaluate_action(browser_act)
            if not safety_check.is_safe:
                events_to_emit.append({
                    "type": EventType.SAFETY_ALERT,
                    "message": f"Action blocked by SafetyGuard: {safety_check.reason}",
                    "data": {
                        "action": act_type.value if hasattr(act_type, "value") else str(act_type),
                        "target": request.target,
                        "reason": safety_check.reason,
                    },
                })
                result = ExecutionResult(
                    success=False,
                    action_type=ActionType.FAIL,
                    action=act_type.value if hasattr(act_type, "value") else str(act_type),
                    target=request.target,
                    message=f"Action blocked by SafetyGuard: {safety_check.reason}",
                    error=safety_check.reason,
                )
            else:
                result = await executor.execute(request)
                # Check for prompt injections on navigation / page changes
                is_nav = act_type in (ActionType.NAVIGATE, "navigate") or bool(request.url)
                if result.success and is_nav and executor.current_page and not executor.current_page.is_closed():
                    try:
                        current_obs = await asyncio.wait_for(observer.observe(executor.current_page), timeout=2.0)
                        injections = safety_guard.inspect_observation(current_obs)
                        if injections:
                            for inj in injections:
                                events_to_emit.append({
                                    "type": EventType.SAFETY_ALERT,
                                    "message": f"Prompt injection detected on page: {inj.reason}",
                                    "data": {
                                        "reason": inj.reason,
                                        "pattern": inj.flagged_pattern,
                                        "url": executor.current_page.url,
                                    },
                                })
                            result.message = f"{result.message} [SAFETY ALERT: {injections[0].reason}]"
                    except Exception as exc:
                        logger.warning(f"Safety inspection on page observation failed or timed out: {exc}")

    # Outside browser lock: emit collected events
    for ev in events_to_emit:
        await event_manager.emit(
            ev["type"],
            message=ev["message"],
            data=ev["data"],
        )

    # Record event telemetry in event manager
    await event_manager.emit(
        EventType.ACTION_EXECUTED,
        message=result.message,
        data={
            "action": result.action or (result.action_type.value if hasattr(result.action_type, "value") else str(result.action_type)),
            "target": result.target,
            "success": result.success,
            "status": "success" if result.success else "failure",
            "error": result.error,
            "duration_ms": result.duration_ms,
        },
    )

    return result


@app.get("/events")
async def get_action_events(limit: int = 50):
    """Return recent browser action events, including timestamps, action names, and status."""
    raw_events = event_manager.get_history(limit=limit)
    formatted: List[Dict[str, Any]] = []
    for ev in raw_events:
        item = {
            "event_id": ev.event_id,
            "timestamp": ev.timestamp.isoformat(),
            "type": ev.type.value if hasattr(ev.type, "value") else str(ev.type),
            "message": ev.message,
            "action": ev.data.get("action", ""),
            "target": ev.data.get("target"),
            "status": ev.data.get("status", "success" if ev.data.get("success") else "failure"),
            "success": ev.data.get("success", False),
            "error": ev.data.get("error"),
            "duration_ms": ev.data.get("duration_ms", 0.0),
            "data": ev.data,
        }
        formatted.append(item)
    return formatted


@app.post("/stop", response_model=StopResponse)
async def stop_execution():
    """Set stop flag to prevent subsequent actions in execution layer."""
    global _active_task
    executor.stop()
    agent_loop.request_stop()
    if _active_task and not _active_task.done():
        _active_task.cancel()
    return StopResponse(status="stopped", message="Execution stopped")


@app.post("/resume")
async def resume_execution():
    """Reset stop flag to resume allowing actions."""
    executor.resume()
    agent_loop._stop_requested = False
    if agent_loop.current_state and agent_loop.current_state.status == RunStatus.STOPPED:
        agent_loop.current_state.status = RunStatus.IDLE
    return {"status": "resumed", "message": "Execution resumed"}


@app.post("/reload")
@app.post("/reset")
async def reload_browser():
    """Cleanly reload browser session and reset all stop flags."""
    global _active_task
    agent_loop.request_stop()
    executor.resume()
    if _active_task and not _active_task.done():
        _active_task.cancel()
    async with get_browser_lock():
        try:
            await executor.close()
            await observer.close()
        except Exception:
            pass
        await executor.initialize()
    return {"status": "reloaded", "message": "Browser session reloaded cleanly"}


# =====================================================================
# AGENT LIFECYCLE & LEGACY BACKWARD-COMPATIBLE ENDPOINTS
# =====================================================================

@app.get("/api/health")
async def api_health_check():
    """Health status and service readiness check."""
    return {
        "status": "healthy",
        "service": "Klick AI Backend",
        "version": "0.1.0",
        "agent_running": agent_loop._is_running,
    }


@app.post("/api/agent/start")
async def start_agent(request: AgentRunRequest):
    """Start an autonomous agent run in a background task."""
    global _active_task
    if agent_loop._is_running:
        return {"error": "Agent is already running. Please stop the current run first."}

    # Automatically ensure executor is unpaused for new run
    executor.resume()
    _active_task = asyncio.create_task(agent_loop.run(request))
    return {
        "message": "Agent execution initiated",
        "goal": request.goal,
        "max_steps": request.max_steps,
    }


@app.post("/api/agent/stop")
async def stop_agent():
    """Request the active agent run to halt."""
    global _active_task
    agent_loop.request_stop()
    executor.stop()
    if _active_task and not _active_task.done():
        _active_task.cancel()
    return {"message": "Stop signal sent to agent"}


@app.get("/api/agent/state", response_model=Optional[AgentRunState])
async def get_agent_state():
    """Retrieve the current state snapshot of the agent."""
    return agent_loop.current_state or AgentRunState(
        run_id="idle",
        goal="No active goal",
        status=RunStatus.IDLE,
    )


@app.get("/api/events")
async def get_recent_api_events(limit: int = 50):
    """Retrieve recent event history (raw AgentEvent models)."""
    return [event.model_dump() for event in event_manager.get_history(limit=limit)]


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket stream for real-time telemetry and timeline events."""
    await event_manager.connect(websocket)
    try:
        while True:
            # Keep-alive receive loop
            await websocket.receive_text()
    except WebSocketDisconnect:
        await event_manager.disconnect(websocket)


# =====================================================================
# CUSTOM TASKS API (FOR EXTENSION & REUSABLE AUTOMATION RECIPES)
# =====================================================================

_custom_tasks_db: List[Dict[str, Any]] = [
    {
        "id": "recipe-summarize",
        "name": "Summarize Page & Key Insights",
        "goal": "Extract the core article and summarize key takeaways from the page.",
        "max_steps": 6,
        "is_builtin": True,
    },
    {
        "id": "recipe-extract-data",
        "name": "Extract Table & List Data to JSON",
        "goal": "Scan this page for data tables, lists, or product cards and extract structured data.",
        "max_steps": 8,
        "is_builtin": True,
    },
    {
        "id": "recipe-fill-forms",
        "name": "Fill Form Fields with Sample Data",
        "goal": "Inspect all visible form input fields on this page and enter valid sample data.",
        "max_steps": 8,
        "is_builtin": True,
    },
    {
        "id": "recipe-pricing",
        "name": "Find Pricing Plans & Discounts",
        "goal": "Locate pricing information, subscription plans, tiers, or product costs on this website.",
        "max_steps": 7,
        "is_builtin": True,
    },
    {
        "id": "recipe-collect-links",
        "name": "Collect Navigation Links & Resources",
        "goal": "Scan this page and compile all relevant outbound navigation links, documentation, and files.",
        "max_steps": 6,
        "is_builtin": True,
    },
    {
        "id": "recipe-qa-audit",
        "name": "Element & Functional QA Audit",
        "goal": "Perform a functional inspection on this page: check buttons, search bars, and links for errors.",
        "max_steps": 8,
        "is_builtin": True,
    },
]


@app.get("/api/tasks/custom")
async def list_custom_tasks():
    """List available built-in and user-defined custom tasks."""
    return _custom_tasks_db


@app.post("/api/tasks/custom")
async def create_custom_task(task: Dict[str, Any]):
    """Register or update a custom task recipe."""
    task_id = task.get("id") or f"custom-{uuid.uuid4().hex[:8]}"
    item = {
        "id": task_id,
        "name": task.get("name", "Untitled Custom Task"),
        "goal": task.get("goal", ""),
        "start_url": task.get("start_url"),
        "max_steps": task.get("max_steps", 10),
        "is_builtin": False,
    }
    for i, existing in enumerate(_custom_tasks_db):
        if existing["id"] == task_id:
            _custom_tasks_db[i] = item
            return item
    _custom_tasks_db.append(item)
    return item


@app.delete("/api/tasks/custom/{task_id}")
async def delete_custom_task(task_id: str):
    """Delete a user-defined custom task."""
    global _custom_tasks_db
    _custom_tasks_db = [t for t in _custom_tasks_db if t["id"] != task_id or t.get("is_builtin")]
    return {"message": "Custom task deleted", "task_id": task_id}


@app.post("/api/tasks/custom/{task_id}/run")
async def run_custom_task(task_id: str, overrides: Optional[Dict[str, Any]] = None):
    """Execute a custom task by ID with optional parameter overrides."""
    global _active_task
    task = next((t for t in _custom_tasks_db if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Custom task not found")

    overrides = overrides or {}
    req = AgentRunRequest(
        goal=overrides.get("goal") or task["goal"],
        start_url=overrides.get("start_url") or task.get("start_url"),
        max_steps=overrides.get("max_steps") or task.get("max_steps", 10),
    )
    if agent_loop._is_running:
        return {"error": "Agent is already running. Please stop current run first."}

    executor.resume()
    _active_task = asyncio.create_task(agent_loop.run(req))
    return {
        "message": f"Custom task '{task['name']}' started",
        "task_id": task_id,
        "goal": req.goal,
    }


# =====================================================================
# MULTI-TAB & BROWSER PROFILE MANAGEMENT ENDPOINTS
# =====================================================================

@app.get("/api/tabs")
async def get_browser_tabs():
    """Return all currently open browser tabs and active tab index."""
    async with get_browser_lock():
        tabs = await executor.get_open_tabs()
        active_idx = next((t["index"] for t in tabs if t.get("is_active")), 0) if tabs else 0
        return {
            "tabs": tabs,
            "count": len(tabs),
            "active_index": active_idx,
        }


@app.post("/api/tabs/switch")
async def switch_browser_tab(payload: Dict[str, Any] = Body(...)):
    """Switch active tab focus by 0-based index or matching title/URL."""
    async with get_browser_lock():
        tab_index = payload.get("tab_index") or payload.get("index")
        target = payload.get("target")
        res = await executor.switch_tab(tab_index=tab_index, target=target)
        return res


@app.post("/api/tabs/new")
async def open_new_browser_tab(payload: Dict[str, Any] = Body(default={})):
    """Open a new browser tab with optional destination URL."""
    async with get_browser_lock():
        url = payload.get("url")
        res = await executor.new_tab(url=url)
        return res


@app.post("/api/tabs/close")
async def close_browser_tab(payload: Dict[str, Any] = Body(default={})):
    """Close an open browser tab by index, refocusing on remaining tabs."""
    async with get_browser_lock():
        tab_index = payload.get("tab_index") or payload.get("index")
        target = payload.get("target")
        res = await executor.close_tab(tab_index=tab_index, target=target)
        return res


@app.get("/api/profile/status")
async def get_profile_status():
    """Return persistent user profile configuration, cookie backup status, and storage directory."""
    profile_dir = Path(executor.user_data_dir)
    cookie_file = profile_dir / "cookies.json"
    cookie_count = 0
    if cookie_file.exists():
        try:
            import json
            with open(cookie_file, "r", encoding="utf-8") as f:
                c_data = json.load(f)
                cookie_count = len(c_data) if isinstance(c_data, list) else 0
        except Exception:
            pass
    return {
        "persistent_profile": getattr(executor, "persistent_profile", True),
        "user_data_dir": str(profile_dir),
        "cookies_saved": cookie_file.exists(),
        "saved_cookie_count": cookie_count,
    }


@app.post("/api/profile/clear")
async def clear_profile_data():
    """Clear cookies and reset persistent profile session cache."""
    async with get_browser_lock():
        success = await executor.clear_profile()
        return {"success": success, "message": "Browser profile cookies and session cache cleared"}


# =====================================================================
# DETERMINISTIC RPA MACROS ENDPOINTS
# =====================================================================

@app.get("/api/macros")
async def get_all_macros():
    """List all available deterministic RPA macros."""
    return macro_manager.list_macros()


@app.get("/api/macros/{macro_id}")
async def get_macro_by_id(macro_id: str):
    """Retrieve details for a specific saved macro."""
    macro = macro_manager.get_macro(macro_id)
    if not macro:
        raise HTTPException(status_code=404, detail=f"Macro '{macro_id}' not found")
    return macro


@app.post("/api/macros")
async def save_custom_macro(macro: MacroDefinition):
    """Create or update a deterministic macro definition."""
    saved = macro_manager.save_macro(macro)
    return saved


@app.delete("/api/macros/{macro_id}")
async def remove_macro(macro_id: str):
    """Delete a macro definition from disk."""
    success = macro_manager.delete_macro(macro_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Macro '{macro_id}' not found")
    return {"success": True, "message": f"Macro '{macro_id}' deleted"}


@app.post("/api/macros/record-from-run")
async def record_macro_from_session(payload: Dict[str, Any] = Body(...)):
    """Convert the last completed or active agent session history into a deterministic macro."""
    name = payload.get("name") or "New Recorded Macro"
    description = payload.get("description") or ""
    query_param = payload.get("parameterize_query")

    state = agent_loop.current_state
    if not state or not state.history:
        raise HTTPException(status_code=400, detail="No active or completed agent run history available to record as macro")

    macro = macro_manager.record_from_run(
        run_state=state,
        name=name,
        description=description,
        parameterize_query=query_param,
    )
    return macro


@app.post("/api/macros/replay", response_model=MacroReplayResult)
async def execute_macro_replay(request: MacroReplayRequest):
    """Deterministically replay a saved macro with 0ms AI inference latency, with adaptive agent fallback."""
    async with get_browser_lock():
        res = await macro_manager.replay_macro(
            macro_id=request.macro_id,
            parameters=request.parameters,
            executor=executor,
            agent_runner=agent_loop,
            fallback_to_agent=request.fallback_to_agent,
            event_manager=event_manager,
        )
        return res


# =====================================================================
# STRUCTURED DATA EXPORTS ENDPOINTS
# =====================================================================

@app.get("/api/exports")
async def list_exported_files():
    """List all extracted datasets available in data/exports."""
    exports = []
    if EXPORTS_DIR.exists():
        for f in sorted(EXPORTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in (".csv", ".json"):
                st = f.stat()
                exports.append({
                    "filename": f.name,
                    "format": f.suffix.lower().lstrip("."),
                    "size_bytes": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "download_url": f"/api/exports/{f.name}",
                })
    return exports


@app.get("/api/exports/{filename}")
async def download_exported_file(filename: str):
    """Download or stream an exported CSV or JSON dataset."""
    safe_name = Path(filename).name
    file_path = EXPORTS_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Export '{filename}' not found")
    media_type = "text/csv" if file_path.suffix.lower() == ".csv" else "application/json"
    return FileResponse(path=str(file_path), media_type=media_type, filename=safe_name)


@app.post("/api/export")
async def trigger_data_export(payload: Dict[str, Any] = Body(default={})):
    """Trigger on-demand structured data extraction and export on active page."""
    async with get_browser_lock():
        target = payload.get("target") or payload.get("selector")
        fmt = payload.get("format") or payload.get("export_format") or "csv"
        filename = payload.get("filename") or payload.get("export_filename")
        res = await executor.export_data(target=target, format=fmt, filename=filename)
        return res


# =====================================================================
# HYBRID MODEL ROUTING ENDPOINTS
# =====================================================================

@app.get("/api/models/routes")
async def get_model_routing_status():
    """Fetch active LLM tier, available providers, stats, and fallback configuration."""
    return model_router.get_routes()


@app.post("/api/models/route")
async def set_model_routing_tier(payload: Dict[str, Any] = Body(...)):
    """Set the active LLM routing tier ('local_fast', 'cloud_turbo', 'auto')."""
    tier_name = payload.get("tier") or payload.get("mode") or "auto"
    try:
        model_router.set_tier(tier_name)
        return {"success": True, "active_tier": model_router.active_tier.value, "message": f"Switched model route to {model_router.active_tier.value}"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid model tier '{tier_name}': {exc}")


# =====================================================================
# VISUAL GROUNDING & SET-OF-MARK SCREENSHOT ENDPOINTS
# =====================================================================

@app.get("/api/visual/screenshot")
async def get_visual_screenshot(format: str = "json"):
    """Return current browser viewport screenshot (clean)."""
    async with get_browser_lock():
        if not executor.current_page or executor.current_page.is_closed():
            raise HTTPException(status_code=400, detail="No active browser page")
        try:
            obs = await observer.observe_structured(executor.current_page)
            b64 = obs.get("screenshot_base64")
            if not b64:
                raise HTTPException(status_code=500, detail="Failed to capture viewport screenshot")
            if format.lower() in ("image", "jpeg", "jpg"):
                import base64
                return Response(content=base64.b64decode(b64), media_type="image/jpeg")
            return {
                "url": obs.get("url"),
                "title": obs.get("title"),
                "screenshot_base64": b64,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/visual/annotated")
async def get_visual_annotated_screenshot(format: str = "json"):
    """Return current browser viewport screenshot annotated with Set-of-Mark visual bounding boxes and badges."""
    async with get_browser_lock():
        if not executor.current_page or executor.current_page.is_closed():
            raise HTTPException(status_code=400, detail="No active browser page")
        try:
            obs = await observer.observe_structured(executor.current_page)
            b64 = obs.get("annotated_screenshot_base64") or obs.get("screenshot_base64")
            if not b64:
                raise HTTPException(status_code=500, detail="Failed to capture annotated screenshot")
            if format.lower() in ("image", "jpeg", "jpg"):
                import base64
                return Response(content=base64.b64decode(b64), media_type="image/jpeg")
            return {
                "url": obs.get("url"),
                "title": obs.get("title"),
                "visual_elements_count": obs.get("visual_elements_count", 0),
                "annotated_screenshot_base64": b64,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/visual/inspect")
async def inspect_visual_page(payload: Dict[str, Any] = Body(default_factory=dict)):
    """Perform a comprehensive visual inspection returning clean & Set-of-Mark screenshots and element bboxes."""
    async with get_browser_lock():
        if not executor.current_page or executor.current_page.is_closed():
            await executor.initialize()
        try:
            obs = await observer.observe(executor.current_page)
            return {
                "url": obs.url,
                "title": obs.title,
                "visual_elements_count": obs.visual_elements_count,
                "screenshot_base64": obs.screenshot_base64,
                "annotated_screenshot_base64": obs.annotated_screenshot_base64,
                "interactive_elements": [el.model_dump() for el in obs.interactive_elements],
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Visual inspection failed: {exc}")


# =====================================================================
# EXTENSION IN-TAB EXECUTION ENDPOINTS (NO EXTRA CHROMIUM WINDOW)
# =====================================================================


@app.post("/api/extension/start")
async def start_extension_session(request: AgentRunRequest):
    """Initialize an extension in-tab run without launching a separate Playwright browser."""
    global _active_task
    run_id = str(uuid.uuid4())
    state = AgentRunState(
        run_id=run_id,
        goal=request.goal,
        status=RunStatus.RUNNING,
        max_steps=request.max_steps or 10,
        start_time=datetime.now(timezone.utc),
        current_url=request.start_url,
    )
    agent_loop._current_state = state
    agent_loop._is_running = True
    agent_loop._stop_requested = False

    await event_manager.emit(
        EventType.STATUS_CHANGE,
        f"Extension in-tab session initialized: '{request.goal}'",
        {"status": RunStatus.RUNNING, "run_id": run_id, "mode": "extension_in_tab"},
        run_id=run_id,
    )
    return {"run_id": run_id, "status": "running", "goal": request.goal}


@app.post("/api/extension/step", response_model=ExtensionStepResponse)
async def process_extension_step(request: ExtensionStepRequest):
    """Cognitive step handler for Chrome Extension executing directly inside active tab."""
    run_id = request.run_id
    step_num = request.step
    goal = request.goal

    # Ensure state exists or update it
    if not agent_loop.current_state or agent_loop.current_state.run_id != run_id:
        agent_loop._current_state = AgentRunState(
            run_id=run_id,
            goal=goal,
            status=RunStatus.RUNNING,
            max_steps=request.max_steps,
            start_time=datetime.now(timezone.utc),
            current_url=request.observation.url,
        )
    state = agent_loop._current_state
    state.current_step = step_num
    state.current_url = request.observation.url

    # Check if stop was requested
    if agent_loop._stop_requested:
        state.status = RunStatus.STOPPED
        return ExtensionStepResponse(
            run_id=run_id,
            step=step_num,
            thought=AgentThought(reasoning="Stop requested by user", reflection="Session halted"),
            action=BrowserAction(action_type=ActionType.FAIL, description="Execution stopped by user"),
            status=RunStatus.STOPPED,
            is_terminal=True,
            safety_passed=True,
        )

    # 1. Observation event
    await event_manager.emit(
        EventType.OBSERVATION_CAPTURED,
        f"Observed in active tab: {request.observation.title} ({request.observation.url})",
        {"step": step_num, "url": request.observation.url, "title": request.observation.title, "mode": "extension"},
        run_id=run_id,
    )

    # 2. Check for adversarial injection in active tab observation
    injections = safety_guard.inspect_observation(request.observation)
    if injections:
        for inj in injections:
            await event_manager.emit(
                EventType.SAFETY_ALERT,
                inj.reason or "Adversarial injection detected in page DOM",
                {"risk": inj.risk_level, "pattern": inj.flagged_pattern},
                run_id=run_id,
            )

    # 3. Ask Model for next action
    await event_manager.emit(
        EventType.AGENT_THINKING,
        f"Agent reasoning for step {step_num} on active tab...",
        {"step": step_num},
        run_id=run_id,
    )

    # Configure target model for cognitive planning
    target_model = getattr(request, "model", None) or os.getenv("BROWSERPILOT_MODEL") or os.getenv("OLLAMA_MODEL") or "llama3.2:3b"
    if hasattr(agent_loop.model_client, "model"):
        agent_loop.model_client.model = target_model

    try:
        response = await agent_loop.model_client.get_next_action(
            goal=goal,
            observation=request.observation,
            step_number=step_num,
            max_steps=request.max_steps,
        )
    except Exception as exc:
        logger.warning(
            "[EXTENSION STEP] Model client error: %s. Using deterministic fallback.",
            exc,
        )
        from .model_client import DeterministicDemoModelClient
        adapter = DeterministicDemoModelClient()
        response = await adapter.get_next_action(
            goal=goal,
            observation=request.observation,
            step_number=step_num,
            max_steps=request.max_steps,
        )


    # Apply smart domain heuristics (YouTube search, video title targeting, and completion)
    curr_p_url = (request.observation.url or "").lower()
    goal_lower = goal.lower()
    import re
    query_match = re.search(r"(?:search|look\s+for|find|query)\s+(?:for\s+)?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|\s+on\b|\s+in\b|$)", goal, re.IGNORECASE)
    if not query_match:
        query_match = re.search(r"search (?:for )?['\"]?([^'\"\n]+?)['\"]?(?:\s+and\b|$)", goal, re.IGNORECASE)
    query_term = query_match.group(1).strip() if query_match else ""
    if query_term.lower().endswith(" on youtube"):
        query_term = query_term[:-11].strip()
    elif query_term.lower().endswith(" in youtube"):
        query_term = query_term[:-11].strip()

    if "youtube.com" in curr_p_url and "results" not in curr_p_url and "watch" not in curr_p_url and query_term:
        tgt = "id-search"
        sel = "[data-agent-id='id-search']"
        for el in request.observation.interactive_elements:
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
    elif response.action.action_type == ActionType.CLICK and (response.action.target in ("elem-3", "search", "search-input", "id-search") or "search" in (response.action.selector or "").lower()) and query_term:
        response.action.action_type = ActionType.TYPE
        response.action.target = response.action.target or "id-search"
        response.action.selector = f"[data-agent-id='{response.action.target}']"
        response.action.text = query_term
        response.action.description = f"Type '{query_term}' into search input"
    elif "results" in curr_p_url:
        if any(w in goal_lower for w in ("video", "watch", "play", "first")):
            response.action.action_type = ActionType.CLICK
            response.action.target = "id-video-title"
            response.action.selector = "[data-agent-id='id-video-title']"
            response.action.description = "Click first video title link to open and play video"
        else:
            response.action.action_type = ActionType.FINISH
            response.action.description = f"Search for '{query_term or 'query'}' completed and results are displayed"

    # 4. Evaluate proposed action safety
    safety_check = safety_guard.evaluate_action(response.action, request.observation)
    if not safety_check.is_safe:
        await event_manager.emit(
            EventType.SAFETY_ALERT,
            f"Blocked unsafe action: {safety_check.reason}",
            {"action": response.action.model_dump(), "safety": safety_check.model_dump()},
            run_id=run_id,
        )
        state.status = RunStatus.AWAITING_CONFIRMATION
        return ExtensionStepResponse(
            run_id=run_id,
            step=step_num,
            thought=response.thought,
            action=response.action,
            status=RunStatus.AWAITING_CONFIRMATION,
            is_terminal=True,
            safety_passed=False,
            safety_reason=safety_check.reason,
        )

    # 5. Broadcast action proposal
    await event_manager.emit(
        EventType.ACTION_PROPOSED,
        f"Proposed {response.action.action_type.value}: {response.action.description}",
        {"action": response.action.model_dump(), "thought": response.thought.model_dump()},
        run_id=run_id,
    )

    is_terminal = response.action.action_type in (ActionType.FINISH, ActionType.FAIL) or step_num >= request.max_steps
    final_output = None
    if response.action.action_type == ActionType.FINISH:
        state.status = RunStatus.COMPLETED
        final_output = response.action.description
        state.final_output = final_output
    elif response.action.action_type == ActionType.FAIL:
        state.status = RunStatus.FAILED
        state.error = response.action.description
    elif step_num >= request.max_steps:
        state.status = RunStatus.COMPLETED
        final_output = "Reached maximum step limit."
        state.final_output = final_output

    # Record history
    record = StepRecord(
        step_number=step_num,
        observation=request.observation,
        thought=response.thought,
        action=response.action,
        safety_check=safety_check,
        execution_result=ExecutionResult(
            success=True,
            action_type=response.action.action_type,
            action=response.action.action_type.value if hasattr(response.action.action_type, "value") else str(response.action.action_type),
            message=response.action.description,
        ),
    )
    state.history.append(record)

    return ExtensionStepResponse(
        run_id=run_id,
        step=step_num,
        thought=response.thought,
        action=response.action,
        status=state.status,
        is_terminal=is_terminal,
        final_output=final_output,
        safety_passed=True,
    )


@app.post("/api/extension/stop")
async def stop_extension_session():
    """Halt the active extension session."""
    agent_loop.request_stop()
    if agent_loop.current_state:
        agent_loop.current_state.status = RunStatus.STOPPED
    return {"status": "stopped", "message": "Extension run stopped"}
BASE_DIR = Path(__file__).resolve().parent.parent.parent
mock_site_path = BASE_DIR / "mock-site"
dashboard_path = BASE_DIR / "dashboard"

if mock_site_path.exists():
    app.mount("/mock", StaticFiles(directory=str(mock_site_path), html=True), name="mock-site")

if dashboard_path.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
