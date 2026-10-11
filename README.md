# Klick

> **A local-first, privacy-respecting autonomous browser agent powered by Ollama, Playwright, FastAPI, and Streamlit.**

Klick executes end-to-end web navigation tasks autonomously on your workstation without routing sensitive browser telemetry, cookies, or credentials to third-party cloud AI providers.

---

## Key Features

- **Local-First AI Reasoning**: Powered locally by [Ollama](https://ollama.com/) with lightweight models such as `llama3.2:3b` for sub-second planning and zero cloud dependency.
- **Visible Browser Automation**: Direct Chromium control with visible window execution, smooth visual cursor tracking, and accessibility-guided element resolution.
- **Robust Real-World Navigation**: Built-in heuristics for major web applications (YouTube, Google, Wikipedia, etc.) and complex multi-step search/interaction flows.
- **Safety & Injection Guardrails**: Multi-tier safety guard (`SafetyGuard`) blocks destructive actions (`delete`, `purge`, form submissions with high risk) and filters adversarial prompt injections.
- **Streamlit Control Center**: Clean, responsive frontend featuring live action timelines, cognitive traces, model playground, and telemetry streaming.
- **Companion Chrome Extension**: Manifest V3 extension providing in-tab automation, custom recipe builders, and direct communication with the backend.
- **Offline Mock Test Arena**: Includes realistic task tables, invoice management systems, and injection test sandboxes.

---

## Architecture Overview

```
                      +-----------------------------+
                      |   Streamlit Control Center  |
                      |     (http://localhost:8501) |
                      +--------------+--------------+
                                     |  REST / WS
                                     v
+------------------+  HTTP   +-----------------------------+
|   Local Ollama   |<------->|       FastAPI Backend       |
|  (llama3.2:3b)   |         |     (http://localhost:8000) |
+------------------+         +--------------+--------------+
                                            |
                                            | Playwright API
                                            v
                             +-----------------------------+
                             |  Visible Chromium Browser   |
                             |  (YouTube, Google, Mock UI) |
                             +-----------------------------+
```

---

## Project Structure

```text
Klick/
├── backend/
│   ├── app/
│   │   ├── __init__.py           # Package version
│   │   ├── main.py               # FastAPI application & WebSocket telemetry
│   │   ├── schemas.py            # Pydantic v2 data models & action contracts
│   │   ├── model_client.py       # Ollama client, few-shot prompts & parser
│   │   ├── agent_loop.py         # Autonomous coordinator loop & goal checks
│   │   ├── observer.py           # Playwright DOM inspection & labeling
│   │   ├── executor.py           # Action execution & cursor animation
│   │   ├── safety.py             # Safety policies & injection defense
│   │   └── events.py             # Telemetry pub/sub event broadcaster
│   ├── run_server.py             # Backend launcher with visible desktop support
│   └── tests/                    # Backend unit & integration test suites
├── frontend/
│   ├── app.py                    # Streamlit AI Control Center application
│   ├── components/               # Modular UI components (timeline, status, sidebar)
│   ├── services/                 # Backend & Ollama HTTP client services
│   ├── assets/                   # UI assets and favicon
│   ├── tests/                    # Frontend test suite
│   └── requirements.txt          # Frontend dependencies
├── extension/                    # Manifest V3 Chrome Extension ("Klick")
├── dashboard/                    # Static dark-mode HTML/JS dashboard
├── mock-site/                    # Offline evaluation arena & test pages
├── docs/                         # Architecture, test plans, and demo scripts
├── .env.example                  # Environment configuration template
├── .gitignore                    # Git ignore rules
├── requirements.txt              # Core Python dependencies
├── start_backend.bat             # 1-click Windows backend launcher
└── start_frontend.bat            # 1-click Windows Streamlit launcher
```

---

## Prerequisites

1. **Operating System**: Windows 10/11, macOS, or Linux
2. **Python**: Python 3.10 or higher
3. **Ollama**: Download and install from [ollama.com](https://ollama.com/)
   - Pull the recommended lightweight model:
     ```bash
     ollama pull llama3.2:3b
     ```

---

## Quickstart Guide

### 1. Clone & Set Up Environment

```bash
# Clone the repository
git clone https://github.com/samirakash248-max/Klick.git
cd Klick

# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Windows (Command Prompt):
.\.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
# Install core and frontend requirements
pip install -r requirements.txt
pip install -r frontend/requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

### 3. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env
```

*(On Windows PowerShell: `Copy-Item .env.example .env`)*

---

## Running the Application

### Option A: Using 1-Click Launchers (Windows)

1. Double-click **`start_backend.bat`** to start the FastAPI server on port 8000.
2. Double-click **`start_frontend.bat`** to start the Streamlit UI on port 8501.

---

### Option B: Manual CLI Launch

#### Terminal 1 — Start Ollama (if not running as a system service)
```bash
ollama serve
```

#### Terminal 2 — Start the FastAPI Backend
```bash
python backend/run_server.py
```
> API available at: **http://127.0.0.1:8000** (Swagger documentation: `http://127.0.0.1:8000/docs`)

#### Terminal 3 — Start the Streamlit Control Center
```bash
python -m streamlit run frontend/app.py --server.port 8501
```
> Dashboard available at: **http://127.0.0.1:8501**

---

## Testing & Verification

Run the automated test suite across model client, executor, safety guardrails, and agent loops:

```bash
pytest backend/tests -v
```

---

## License

This project is licensed under the MIT License.
