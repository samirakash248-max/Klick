# Klick - Custom Task Assistant (Chrome Extension)

A Manifest V3 browser extension empowering users to trigger autonomous agent tasks, custom web macros, and AI-driven page operations directly from the browser toolbar on any website without opening extra windows.

---

## Features

- **Direct Active Tab Automation**: Inspects the active webpage (`url`, `title`, and context) and executes autonomous Klick agent tasks directly in the current tab without copying/pasting URLs.
- **Custom Task Builder**: Create, configure, save, and delete reusable custom tasks with prompt templates, dynamic tokens (`{{current_url}}`, `{{current_title}}`), and step bounds.
- **1-Click Built-in Task Recipes**:
  - 📝 **Summarize Article**: Synthesizes key points from the active page.
  - 📊 **Extract Tables & Lists**: Converts tabular data to structured records.
  - ✍️ **Fill Form Fields**: Detects form inputs and fills them with valid test data.
  - 🏷️ **Find Pricing Plans**: Locates subscription tiers and pricing tables.
  - 🔗 **Collect Resources & Links**: Gathers download links and documentation endpoints.
  - 🛡️ **Element & QA Audit**: Tests buttons and inputs across the layout.
- **Live Execution Monitor**: Real-time polling showing step-by-step progress, agent thoughts, executed actions, and an emergency Stop button.
- **Context Menu Integration**: Right-click on any page or highlighted text to start tasks via the context menu.

---

## Installation & Setup Guide

### 1. Ensure Backend is Running
The extension communicates with the local Klick FastAPI backend:
```bash
# In project directory:
.\.venv\Scripts\uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```
Verify backend readiness at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

### 2. Load Extension in Google Chrome or Microsoft Edge
1. Open your browser and navigate to `chrome://extensions/` (or `edge://extensions/`).
2. Toggle on **Developer mode** in the top-right corner.
3. Click the **Load unpacked** button.
4. Select the `extension` folder.
5. The extension **Klick - AI Autonomous Browser Assistant** will appear in your extensions list.
6. Pin the extension icon to your toolbar for quick 1-click access.

---

## Architecture Overview

```
extension/
├── manifest.json              # Manifest V3 configuration with activeTab & storage permissions
├── icons/                     # Extension icons (16px, 48px, 128px PNG)
│   ├── icon-16.png
│   ├── icon-48.png
│   └── icon-128.png
├── popup/                     # Toolbar popup user interface
│   ├── popup.html             # UI with Tabs, Task Presets, Builder & Live Monitor
│   ├── popup.css              # Cyber-slate theme matching the Klick dashboard
│   └── popup.js               # State management, Chrome storage, and REST API controller
├── background/
│   └── service-worker.js      # Context menus, background task dispatch & badge status
└── content/                   # Injected scripts for active tab metadata and feedback
    ├── content.js
    └── content.css
```
