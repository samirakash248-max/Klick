/**
 * Klick AI - Background Service Worker (Manifest V3)
 * Handles context menus, backend relay, and extension badge telemetry.
 */

const BACKEND_BASE = "http://127.0.0.1:8000";

// 1. Setup Context Menus on Installation
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "klick-run-page",
    title: "🤖 Klick: Automate task on this page",
    contexts: ["page"]
  });

  chrome.contextMenus.create({
    id: "klick-run-selection",
    title: "🤖 Klick: Ask agent about '%s'",
    contexts: ["selection"]
  });
});

// 2. Handle Context Menu Clicks
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab || !tab.id) return;

  let goal = "";
  if (info.menuItemId === "klick-run-selection" && info.selectionText) {
    goal = `Analyze and execute actions based on the selected text: "${info.selectionText.slice(0, 150)}"`;
  } else if (info.menuItemId === "klick-run-page") {
    goal = `Analyze the current page (${tab.title || tab.url}) and summarize key interactive elements and data`;
  }

  if (goal) {
    try {
      // First try executing directly inside active tab without opening new window
      const tabRes = await chrome.tabs.sendMessage(tab.id, {
        action: "run_in_tab",
        goal: goal,
        maxSteps: 10
      });
      if (tabRes && tabRes.success) {
        await chrome.action.setBadgeText({ text: "RUN" });
        await chrome.action.setBadgeBackgroundColor({ color: "#00e5ff" });
        return;
      }
    } catch {
      // Content script may not be ready, fall back to API runner
    }

    await startAgentTask({
      goal: goal,
      start_url: tab.url,
      max_steps: 10
    });
  }
});

// 3. Helper to trigger agent on FastAPI backend
async function startAgentTask(payload) {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/agent/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    await chrome.action.setBadgeText({ text: "RUN" });
    await chrome.action.setBadgeBackgroundColor({ color: "#00e5ff" });
    return data;
  } catch (err) {
    console.error("Failed to start agent task:", err);
    await chrome.action.setBadgeText({ text: "ERR" });
    await chrome.action.setBadgeBackgroundColor({ color: "#ff3366" });
    throw err;
  }
}

// 4. Runtime Message Listener
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message.action === "start_task") {
      try {
        const result = await startAgentTask(message.payload);
        sendResponse({ success: true, result });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    } else if (message.action === "get_status") {
      try {
        const res = await fetch(`${BACKEND_BASE}/api/agent/state`);
        const state = await res.json();
        sendResponse({ success: true, state });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    } else if (message.action === "clear_badge") {
      await chrome.action.setBadgeText({ text: "" });
      sendResponse({ success: true });
    }
  })();
  return true; // Keep channel open for async response
});
