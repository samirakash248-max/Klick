/**
 * Klick AI - Popup Controller
 * Manages active tab detection, custom tasks, preset recipes, and real-time execution monitoring.
 */

const BACKEND_BASE = "http://127.0.0.1:8000";

let currentTab = null;
let pollInterval = null;

// Built-in Task Recipes
const PRESETS = {
  summarize: {
    title: "Summarize Page",
    prompt: "Extract the core article and summarize the key insights and takeaways from this page.",
    steps: 6
  },
  scrape: {
    title: "Extract Table / List Data",
    prompt: "Scan this page for data tables, lists, or product cards and extract the structured data.",
    steps: 8
  },
  forms: {
    title: "Fill Form Fields",
    prompt: "Inspect all visible form input fields on this page and fill them with valid sample data.",
    steps: 8
  },
  pricing: {
    title: "Find Pricing Plans",
    prompt: "Locate pricing information, subscription plans, tiers, or product costs on this website.",
    steps: 7
  },
  links: {
    title: "Collect Resources & Links",
    prompt: "Scan this page and compile all relevant outbound navigation links, documentation, and files.",
    steps: 6
  },
  audit: {
    title: "Element & QA Audit",
    prompt: "Perform a functional inspection on this page: check buttons, search bars, and links for errors.",
    steps: 8
  }
};

// =========================================================================
// Initialization
// =========================================================================

document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  setupBuilder();
  setupQuickRun();
  setupMonitorActions();

  await detectActiveTab();
  await checkBackendStatus();
  await loadCustomTasks();

  // Start polling agent state
  pollAgentState();
  pollInterval = setInterval(pollAgentState, 1500);
});

window.addEventListener("unload", () => {
  if (pollInterval) clearInterval(pollInterval);
});

// =========================================================================
// Tab Navigation
// =========================================================================

function setupTabs() {
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      tab.classList.add("active");
      const targetId = tab.getAttribute("data-tab");
      document.getElementById(targetId)?.classList.add("active");
    });
  });
}

function switchToTab(tabId) {
  const tabBtn = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
  if (tabBtn) tabBtn.click();
}

// =========================================================================
// Active Tab Detection
// =========================================================================

async function detectActiveTab() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs[0]) {
      currentTab = tabs[0];
      const titleElem = document.getElementById("tab-title");
      const urlElem = document.getElementById("tab-url");

      if (titleElem) titleElem.textContent = currentTab.title || "Untitled Tab";
      if (urlElem) urlElem.textContent = currentTab.url || "";
    }
  } catch (err) {
    console.warn("Failed to query active tab:", err);
  }
}

// =========================================================================
// Backend Connectivity Check
// =========================================================================

async function checkBackendStatus() {
  const dot = document.getElementById("backend-dot");
  const text = document.getElementById("backend-text");

  try {
    const res = await fetch(`${BACKEND_BASE}/health`, { signal: AbortSignal.timeout(2000) });
    if (res.ok) {
      if (dot) {
        dot.className = "status-dot online";
      }
      if (text) text.textContent = "ONLINE";
      return true;
    }
  } catch {
    // Offline
  }

  if (dot) {
    dot.className = "status-dot offline";
  }
  if (text) text.textContent = "OFFLINE";
  return false;
}

// =========================================================================
// Custom Tasks Management (Storage & Execution)
// =========================================================================

async function loadCustomTasks() {
  const listElem = document.getElementById("user-custom-tasks");
  const countBadge = document.getElementById("custom-task-count");
  if (!listElem) return;

  const data = await chrome.storage.local.get("customTasks");
  const tasks = data.customTasks || [];

  if (countBadge) countBadge.textContent = String(tasks.length);

  if (tasks.length === 0) {
    listElem.innerHTML = `
      <div style="font-size: 11px; color: #64748b; text-align: center; padding: 12px; background: #151d2f; border-radius: 6px;">
        No custom tasks created yet. Click <b>Task Builder</b> above to add one.
      </div>
    `;
    return;
  }

  listElem.innerHTML = "";
  tasks.forEach((task, idx) => {
    const item = document.createElement("div");
    item.className = "task-item";
    item.innerHTML = `
      <div class="task-item-info">
        <h4>${escapeHtml(task.name)}</h4>
        <p title="${escapeHtml(task.goal)}">${escapeHtml(task.goal)}</p>
      </div>
      <div class="task-item-actions">
        <button class="btn btn-sm btn-outline btn-run-custom" data-idx="${idx}">Run</button>
        <button class="btn btn-sm btn-secondary btn-del-custom" data-idx="${idx}" title="Delete Task">✕</button>
      </div>
    `;
    listElem.appendChild(item);
  });

  // Attach button handlers
  listElem.querySelectorAll(".btn-run-custom").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      runCustomTask(tasks[idx]);
    });
  });

  listElem.querySelectorAll(".btn-del-custom").forEach(btn => {
    btn.addEventListener("click", async () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      tasks.splice(idx, 1);
      await chrome.storage.local.set({ customTasks: tasks });
      await loadCustomTasks();
    });
  });
}

function setupBuilder() {
  const urlMode = document.getElementById("task-url-mode");
  const customUrlGroup = document.getElementById("group-custom-url");
  const stepsRange = document.getElementById("task-max-steps");
  const stepsVal = document.getElementById("steps-val");
  const saveBtn = document.getElementById("btn-save-task");

  if (urlMode && customUrlGroup) {
    urlMode.addEventListener("change", () => {
      customUrlGroup.classList.toggle("hidden", urlMode.value !== "custom");
    });
  }

  if (stepsRange && stepsVal) {
    stepsRange.addEventListener("input", () => {
      stepsVal.textContent = stepsRange.value;
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const name = document.getElementById("task-name").value.trim();
      const goal = document.getElementById("task-goal").value.trim();
      const mode = document.getElementById("task-url-mode").value;
      const customUrl = document.getElementById("task-custom-url").value.trim();
      const steps = parseInt(document.getElementById("task-max-steps").value, 10);

      if (!name || !goal) {
        alert("Please provide both a task name and a goal prompt.");
        return;
      }

      const newTask = {
        id: "task-" + Date.now(),
        name,
        goal,
        urlMode: mode,
        customUrl: mode === "custom" ? customUrl : "",
        maxSteps: steps || 10
      };

      const data = await chrome.storage.local.get("customTasks");
      const list = data.customTasks || [];
      list.push(newTask);
      await chrome.storage.local.set({ customTasks: list });

      // Reset form
      document.getElementById("task-name").value = "";
      document.getElementById("task-goal").value = "";
      switchToTab("tab-tasks");
      await loadCustomTasks();
    });
  }
}

// =========================================================================
// Task Execution
// =========================================================================

function setupQuickRun() {
  const quickRunBtn = document.getElementById("btn-quick-run");
  const quickInput = document.getElementById("quick-goal");

  if (quickRunBtn && quickInput) {
    quickRunBtn.addEventListener("click", () => {
      const goal = quickInput.value.trim();
      if (!goal) return;
      executeGoal(goal, currentTab?.url, 10);
    });

    quickInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        quickRunBtn.click();
      }
    });
  }

  // Presets triggers
  document.querySelectorAll(".preset-card").forEach(card => {
    const key = card.getAttribute("data-preset");
    const runBtn = card.querySelector(".btn-run-preset");
    if (runBtn && PRESETS[key]) {
      runBtn.addEventListener("click", () => {
        const preset = PRESETS[key];
        executeGoal(preset.prompt, currentTab?.url, preset.steps);
      });
    }
  });
}

async function runCustomTask(task) {
  let targetUrl = currentTab?.url;
  if (task.urlMode === "custom" && task.customUrl) {
    targetUrl = task.customUrl;
  }

  // Interpolate dynamic tokens
  let resolvedGoal = task.goal;
  if (currentTab) {
    resolvedGoal = resolvedGoal.replace(/\{\{current_url\}\}/g, currentTab.url || "");
    resolvedGoal = resolvedGoal.replace(/\{\{current_title\}\}/g, currentTab.title || "");
  }

  await executeGoal(resolvedGoal, targetUrl, task.maxSteps || 10);
}

async function executeGoal(goal, startUrl, maxSteps = 10) {
  switchToTab("tab-monitor");

  const monitorStatus = document.getElementById("monitor-status");
  const monitorGoal = document.getElementById("monitor-goal");
  const monitorAction = document.getElementById("monitor-action");
  const stopBtn = document.getElementById("btn-stop-agent");

  if (monitorStatus) {
    monitorStatus.className = "monitor-badge running";
    monitorStatus.textContent = "RUNNING";
  }
  if (monitorGoal) monitorGoal.textContent = goal;
  if (monitorAction) monitorAction.textContent = "Executing directly in active tab...";
  if (stopBtn) stopBtn.disabled = false;

  // 1. Direct in-tab mode: send to active tab's content script (no extra Chrome window)
  if (currentTab && currentTab.id) {
    try {
      const response = await chrome.tabs.sendMessage(currentTab.id, {
        action: "run_in_tab",
        goal: goal,
        maxSteps: maxSteps
      });
      if (response && response.success) {
        if (monitorAction) monitorAction.textContent = "Task executing directly inside active tab.";
        return;
      }
    } catch (tabErr) {
      console.warn("Content script not reachable on current tab, falling back to background:", tabErr);
    }
  }

  // 2. Fallback: if tab cannot be injected directly, start via backend API
  try {
    const res = await fetch(`${BACKEND_BASE}/api/agent/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: goal,
        start_url: startUrl || undefined,
        max_steps: maxSteps
      })
    });

    const data = await res.json();
    if (data.error) {
      if (monitorStatus) {
        monitorStatus.className = "monitor-badge failed";
        monitorStatus.textContent = "ERROR";
      }
      if (monitorAction) monitorAction.textContent = data.error;
    }
  } catch (err) {
    if (monitorStatus) {
      monitorStatus.className = "monitor-badge failed";
      monitorStatus.textContent = "DISCONNECTED";
    }
    if (monitorAction) monitorAction.textContent = `Could not reach backend: ${err.message}`;
  }
}

// =========================================================================
// Real-Time Monitor Polling
// =========================================================================

async function pollAgentState() {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/agent/state`, { signal: AbortSignal.timeout(1500) });
    if (!res.ok) return;

    const state = await res.json();
    updateMonitorUI(state);
  } catch {
    // Ignore transient poll failures
  }
}

function updateMonitorUI(state) {
  const statusBadge = document.getElementById("monitor-status");
  const stepCount = document.getElementById("monitor-step");
  const goalElem = document.getElementById("monitor-goal");
  const actionElem = document.getElementById("monitor-action");
  const stopBtn = document.getElementById("btn-stop-agent");
  const footerStatus = document.getElementById("footer-agent-status");

  if (!state || state.status === "idle") {
    if (statusBadge) {
      statusBadge.className = "monitor-badge";
      statusBadge.textContent = "IDLE";
    }
    if (stopBtn) stopBtn.disabled = true;
    if (footerStatus) footerStatus.textContent = "Ready";
    return;
  }

  const status = (state.status || "").toLowerCase();
  if (statusBadge) {
    statusBadge.textContent = status.toUpperCase();
    statusBadge.className = `monitor-badge ${status}`;
  }

  if (stepCount) {
    stepCount.textContent = `Step ${state.current_step || 0} / ${state.max_steps || 10}`;
  }

  if (goalElem && state.goal) {
    goalElem.textContent = state.goal;
  }

  if (footerStatus) {
    footerStatus.textContent = `Status: ${status}`;
  }

  if (stopBtn) {
    stopBtn.disabled = status !== "running";
  }

  // Show latest history step or final output
  if (actionElem) {
    if (state.error) {
      actionElem.textContent = `⚠️ Error: ${state.error}`;
      actionElem.style.color = "#ef4444";
    } else if (state.final_output) {
      actionElem.textContent = `✓ ${state.final_output}`;
      actionElem.style.color = "#10b981";
    } else if (state.history && state.history.length > 0) {
      const latest = state.history[state.history.length - 1];
      const desc = latest.action?.description || latest.execution_result?.message || "Executing step...";
      actionElem.textContent = `⚡ [Step ${latest.step_number}] ${desc}`;
      actionElem.style.color = "#00e5ff";
    } else if (status === "running") {
      actionElem.textContent = "Observing page and reasoning next step...";
      actionElem.style.color = "#00e5ff";
    }
  }
}

function setupMonitorActions() {
  const stopBtn = document.getElementById("btn-stop-agent");
  const reloadBtn = document.getElementById("btn-reload-agent");
  const dashboardBtn = document.getElementById("btn-view-dashboard");

  if (stopBtn) {
    stopBtn.addEventListener("click", async () => {
      try {
        if (currentTab && currentTab.id) {
          chrome.tabs.sendMessage(currentTab.id, { action: "stop_task" }).catch(() => {});
        }
        await fetch(`${BACKEND_BASE}/api/extension/stop`, { method: "POST" }).catch(() => {});
        await fetch(`${BACKEND_BASE}/api/agent/stop`, { method: "POST" }).catch(() => {});
        stopBtn.disabled = true;
      } catch (err) {
        console.error("Failed to stop agent:", err);
      }
    });
  }

  if (reloadBtn) {
    reloadBtn.addEventListener("click", async () => {
      try {
        await fetch(`${BACKEND_BASE}/reset`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        alert("Browser session cleanly reset!");
      } catch (err) {
        console.error("Failed to reset session:", err);
      }
    });
  }

  if (dashboardBtn) {
    dashboardBtn.addEventListener("click", () => {
      chrome.tabs.create({ url: `${BACKEND_BASE}/dashboard/index.html` });
    });
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
