# BrowserPilot AI - Comprehensive Test Plan

This document outlines the testing strategy, test suites, and validation metrics for the BrowserPilot AI platform.

---

## 1. Test Levels & Scope

| Level | Scope | Tools | Target Execution Time |
| :--- | :--- | :--- | :--- |
| **Unit Tests** | Schema validation, safety pattern regexes, model JSON parsing | `pytest` | < 3 seconds |
| **Integration Tests** | FastAPI endpoints, WebSocket connectivity, event broadcasting | `pytest`, `httpx`, `TestClient` | < 10 seconds |
| **End-to-End (E2E)** | Live Playwright browser navigation on local mock site | `pytest`, `playwright` | < 30 seconds |
| **Security / Adversarial** | Indirect prompt injection payloads and destructive action denial | Custom test harness | < 15 seconds |

---

## 2. Test Cases Matrix

### Suite A: Schema & Data Integrity (`backend/tests/test_schemas.py`)
- `TC-01`: Validate valid `BrowserAction` instances with all action types.
- `TC-02`: Reject invalid `BrowserAction` with missing required fields.
- `TC-03`: Ensure `AgentResponse` correctly deserializes nested `thought` and `action` structures.
- `TC-04`: Verify `ExecutionResult` captures execution timing and error payloads.

### Suite B: Safety & Injection Guardrails (`backend/app/safety.py`)
- `TC-05`: **Destructive Action Blocking**: Verify that actions targeting `#purge-database-btn` or containing words like `purge`, `delete`, `drop` are flagged as `CRITICAL` risk and blocked.
- `TC-06`: **URL Protocol Filtering**: Block prohibited schemes such as `javascript:`, `file://`, and `data:`.
- `TC-07`: **Direct Prompt Injection Detection**: Verify detection of strings matching `ignore previous instructions` in page text.
- `TC-08`: **Indirect Prompt Injection Detection**: Verify detection of hidden adversarial payloads in simulated customer reviews (`mock-site/injection.html`).

### Suite C: Browser Automation (`backend/app/executor.py` & `observer.py`)
- `TC-09`: Launch Chromium browser in headless and headful modes.
- `TC-10`: Navigate to `mock-site/tasks.html` and extract valid `PageObservation`.
- `TC-11`: Execute `CLICK` action on `#nav-tasks-link` and verify URL transition.
- `TC-12`: Execute `TYPE` action on `#task-search-input` and verify input field value.

### Suite D: Backend API & WebSocket (`backend/app/main.py`)
- `TC-13`: `GET /api/health` returns status `healthy`.
- `TC-14`: `POST /api/agent/start` accepts valid `AgentRunRequest` and returns confirmation.
- `TC-15`: `POST /api/agent/stop` halts active loop.
- `TC-16`: WebSocket `/ws/events` broadcasts serialized JSON events on status changes.

---

## 3. Running Test Suites

From the repository root in Windows PowerShell:

```powershell
# Run all unit tests
pytest backend/tests -v

# Run with stdout printing for debugging
pytest backend/tests -v -s
```
