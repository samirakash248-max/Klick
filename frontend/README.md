# BrowserPilot AI - Streamlit Frontend 🌐

Interactive, dark-themed Streamlit control center for **BrowserPilot AI**, a local-first autonomous browser agent powered by an Ollama-hosted language model (e.g. Llama 3.2 3B) and Playwright.

---

## 1. Architecture Overview

```text
Streamlit Frontend (app.py)
    │
    ├── BackendClient ───► FastAPI Backend (:8000)
    │                         │
    │                         ├── AgentLoop ───► OllamaModelClient (:11434)
    │                         └── Executor / Observer ───► Playwright Browser
    │
    └── OllamaService ───► Local Ollama API (:11434) [Direct Playground Tab]
```

- **Agent Control**: Submits goals to the FastAPI backend (`POST /api/agent/start`), monitors step-by-step progress (`GET /api/agent/state`), streams activity telemetry (`GET /events`), and halts runs safely (`POST /stop`).
- **Live Observation**: Displays live page URLs, page titles, detected interactive elements (`data-agent-id`), and relevant page text snippets.
- **Model Playground**: Direct sandbox for inspecting Ollama model health, verifying raw prompt responses, and auditing structured `BrowserAction` schema adherence without touching a browser.
- **Human-in-the-Loop (HITL) Guardrails**: Surfaces safety intercepts when actions target destructive keywords (`purge`, `delete`).

---

## 2. Directory Structure

```text
frontend/
├── app.py                     # Streamlit main application and tab routing
├── components/
│   ├── sidebar.py             # Connectivity monitor, URL/model config, stop controls
│   ├── agent_status.py        # Status KPI cards, step progress bar, HITL approval panel
│   ├── action_timeline.py     # Chronological action entries, reasoning traces, outcomes
│   └── settings.py            # Page observation table, raw JSON view, post-run summary
├── services/
│   ├── backend_client.py      # HTTP client service for FastAPI backend endpoints
│   └── ollama_client.py       # Direct HTTP client for Ollama API & playground
├── utils/
│   └── formatting.py          # Sensitive text redactor, duration, timestamp, badges
├── tests/
│   ├── test_backend_client.py # Mocked tests for backend client operations
│   ├── test_ollama_client.py  # Mocked tests for Ollama playground service
│   └── test_formatting_and_components.py # Utilities, timeline, HITL, duplicate guards
├── requirements.txt           # Python package requirements
└── README.md                  # Setup guide and documentation
```

---

## 3. Environment Setup & Startup

### Step 1: Start the Backend Services

In separate terminal windows:

```powershell
# Terminal 1: Mock Website Server
py -3.13 -m http.server 8080 --directory mock-site

# Terminal 2: FastAPI Backend Server
py -3.13 -m uvicorn backend.app.main:app --port 8000 --reload
```

Ensure Ollama is running locally:
```bash
ollama serve
# Ensure configured model is downloaded:
ollama pull llama3.2:3b
```

### Step 2: Setup & Launch the Streamlit Frontend

```bash
cd frontend

# Create virtual environment (optional if using shared environment)
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Launch the Streamlit app
streamlit run app.py
```

The Streamlit app will open automatically in your browser at:
👉 **`http://localhost:8501`**

---

## 4. Configuration Environment Variables

| Variable | Default Value | Description |
|---|---|---|
| `BACKEND_URL` | `http://localhost:8000` | FastAPI backend server URL. Can also be overridden in the sidebar. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama API server base URL. |
| `BROWSERPILOT_MODEL` | `llama3.2:3b` | Default Ollama model name. Fallbacks: `OLLAMA_MODEL` or `llama3.2:3b`. |

---

## 5. Feature Highlights

### 🎯 Agent Control & Live Dashboard
- **Natural Language Task Dispatch**: Enter goals like *"Search for invoice INV-1001 and inspect details"*.
- **Quick Preset Goals**: One-click preset triggers for Invoice workflows, Settings updates, Injection audits, and Destructive safety checks.
- **Duplicate Run Prevention**: Submission buttons are disabled while a task is running.
- **Live Status & KPI Bar**: Displays execution state (`RUNNING`, `COMPLETED`, `STOPPED`, `AWAITING_CONFIRMATION`), step counts, and elapsed time.
- **Action Timeline**: Visualizes each action (`NAVIGATE`, `CLICK`, `TYPE`, `SCROLL`, `WAIT`), target elements, redacted inputs, and model reflection/reasoning.
- **Page Observation View**: Shows current page title, URL, detected interactive elements table, and expandable raw JSON.
- **Run Summary**: Calculates total steps, duration, success/fail ratio, and final output upon task completion.

### 🧠 Ollama Model Playground
- Test your local language model in isolation without launching browser sessions.
- Run raw generation queries with customizable system prompts and JSON formatting.
- **Schema Adherence Validator**: Tests if the model outputs valid `AgentResponse` JSON matching `thought` and `action` contracts.

### 🛡️ Safety & HITL Interception
- When an action is flagged by the backend's `SafetyGuard`, the agent transitions to `AWAITING_CONFIRMATION`.
- The dashboard highlights the intercepted action, risk level, and reasons, while clearly noting the unconfigured backend approval endpoint.

---

## 6. Running the Automated Tests

All HTTP requests to Ollama and FastAPI are mocked; no active browser or running model is required:

```powershell
py -3.13 -m pytest frontend/tests/ -vv
```
