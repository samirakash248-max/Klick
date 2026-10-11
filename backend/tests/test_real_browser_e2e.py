"""Real browser end-to-end integration tests for BrowserPilot AI (Member A, Step 4).

Connects Member A's ControlledAgentRunner and OllamaModelClient (using real local
model 'gemma4:e2b') to Member B's PlaywrightObserver and PlaywrightExecutor
against the live mock website served over HTTP.

Separated from unit tests and marked for end-to-end integration execution.
"""

from __future__ import annotations

import asyncio
from functools import partial
import http.server
from pathlib import Path
import socketserver
import threading
import time
from typing import Generator, Tuple
import pytest

from backend.app.agent_runner import ControlledAgentRunner, StopReason
from backend.app.events import EventManager
from backend.app.executor import PlaywrightExecutor
from backend.app.model_client import OllamaModelClient
from backend.app.observer import PlaywrightObserver
from backend.app.safety import SafetyGuard
from backend.app.schemas import (
    ActionType,
    AgentRunRequest,
    RunStatus,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
MOCK_SITE_DIR = ROOT_DIR / "mock-site"


class EphemeralMockServer:
    """Spins up an ephemeral HTTP server serving mock-site on an available local port."""

    def __init__(self, directory: Path = MOCK_SITE_DIR) -> None:
        self.directory = directory
        self._httpd: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0
        self.base_url: str = ""

    def start(self) -> str:
        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(self.directory))
        # Bind to 127.0.0.1 on OS-assigned free port (port 0)
        self._httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


@pytest.fixture
def mock_server() -> Generator[str, None, None]:
    """Fixture providing a running ephemeral HTTP server for the mock website."""
    server = EphemeralMockServer()
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_real_browser_e2e_search_task(mock_server: str) -> None:
    """Real end-to-end test: Launches Chromium, queries real Ollama gemma4:e2b model,

    and performs a task search on mock-site/tasks.html with full safety and verification.
    """
    tasks_url = f"{mock_server}/tasks.html"

    # Initialize real components
    model_client = OllamaModelClient(model="llama3.2:3b", timeout=90.0)
    observer = PlaywrightObserver()
    executor = PlaywrightExecutor(headless=True, slow_mo_ms=200)
    safety = SafetyGuard(strict_mode=True, block_destructive=True)
    events = EventManager()

    runner = ControlledAgentRunner(
        model_client=model_client,
        observer=observer,
        executor=executor,
        safety=safety,
        events=events,
        default_max_steps=6,
    )

    start_time = time.perf_counter()
    state = None

    try:
        req = AgentRunRequest(
            goal="Type 'budget' into the task search input to filter tasks",
            start_url=tasks_url,
            max_steps=6,
        )

        state = await runner.run(req)

        # Baseline execution assertions
        elapsed_sec = time.perf_counter() - start_time
        print(f"\n[E2E] Real run completed in {elapsed_sec:.2f}s across {state.current_step} decision steps.")
        print(f"[E2E] Final Status: {state.status.value}, Output: {state.final_output}")
        safe_err = (state.error or "").encode("ascii", errors="replace").decode("ascii")
        print(f"[E2E] Error: {safe_err}")
        print(f"[E2E] Executed Actions Count: {len(state.history)}")

        for i, step in enumerate(state.history):
            print(f"  Step {i + 1}: {step.action.action_type.value} -> selector: {step.action.selector} ({step.action.description})")

        # 1. Browser launched and page was initialized
        assert executor.current_page is not None, "Real Playwright browser page must be initialized"
        assert state.current_url is not None and "tasks.html" in state.current_url

        # 2. Real model returned grounded actions that passed safety
        assert state.current_step >= 1, "At least one decision step must have occurred"
        assert len(state.history) >= 1, "At least one browser action must have been executed"

        # 3. Verify target page state in the real DOM
        active_page = executor.current_page
        search_input_val = await active_page.input_value("#task-search-input")
        print(f"[E2E] Live DOM search input value: '{search_input_val}'")
        assert search_input_val.lower() == "budget", f"Expected input value 'budget', got '{search_input_val}'"

        # Check visible task rows in live DOM (oninput filter hides other rows)
        visible_rows_count = await active_page.evaluate("""() => {
            const rows = document.querySelectorAll('.task-row');
            let visible = 0;
            rows.forEach(r => { if (r.style.display !== 'none') visible++; });
            return visible;
        }""")
        print(f"[E2E] Live DOM visible task rows: {visible_rows_count}")
        assert visible_rows_count == 1, f"Expected 1 visible filtered row, got {visible_rows_count}"

        # The table should contain the target task row #TSK-102: Approve cloud migration budget
        budget_row_text = await active_page.inner_text("#tasks-body")
        assert "budget" in budget_row_text.lower(), "Live DOM must display budget task"

        # 4. Verified completion by ControlledAgentRunner and SafetyGuard
        assert state.status == RunStatus.COMPLETED, (
            f"Expected completed status, got {state.status.value} (Error: {state.error})"
        )

    finally:
        # Reliable cleanup
        await executor.close()
        await observer.close()


@pytest.mark.asyncio
async def test_real_browser_safety_guard_blocks_restricted_url(mock_server: str) -> None:
    """Real browser test: Verify SafetyGuard blocks attempts to navigate to restricted file:// scheme."""
    model_client = OllamaModelClient(model="llama3.2:3b", timeout=30.0)
    observer = PlaywrightObserver()
    executor = PlaywrightExecutor(headless=True)
    safety = SafetyGuard(strict_mode=True, block_destructive=True)
    events = EventManager()

    runner = ControlledAgentRunner(
        model_client=model_client,
        observer=observer,
        executor=executor,
        safety=safety,
        events=events,
        default_max_steps=3,
    )

    try:
        # Direct restricted file scheme navigation requested inside safety guard
        from backend.app.schemas import BrowserAction
        restricted_action = BrowserAction(
            action_type=ActionType.NAVIGATE,
            url="file:///C:/Windows/System32/cmd.exe",
            description="Exfiltrate system files",
        )
        check = safety.evaluate_action(restricted_action)
        assert not check.is_safe
        assert "Restricted URL scheme" in (check.reason or "")
    finally:
        await executor.close()
        await observer.close()
