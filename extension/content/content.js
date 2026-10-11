/**
 * Klick AI - Content Script (In-Tab Execution Engine)
 * Runs autonomous tasks directly on the active browser tab without opening extra Chrome windows.
 */

const BACKEND_BASE = "http://127.0.0.1:8000";

let isTaskRunning = false;
let stopRequested = false;
let currentHud = null;

// =========================================================================
// Message Listener
// =========================================================================

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "run_in_tab") {
    if (isTaskRunning) {
      sendResponse({ success: false, error: "Task is already executing on this tab." });
      return true;
    }
    startInTabTask(message.goal, message.maxSteps || 10);
    sendResponse({ success: true, message: "Task started directly in active tab." });
  } else if (message.action === "stop_task") {
    stopRequested = true;
    sendResponse({ success: true, message: "Stop signal received." });
  } else if (message.action === "get_status") {
    sendResponse({ isRunning: isTaskRunning });
  }
  return true;
});

// =========================================================================
// In-Tab Autonomous Execution Loop
// =========================================================================

async function startInTabTask(goal, maxSteps) {
  isTaskRunning = true;
  stopRequested = false;
  const runId = "ext-" + Date.now();

  createHud(goal, maxSteps);

  try {
    // Notify backend to initialize state
    await fetch(`${BACKEND_BASE}/api/extension/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: goal,
        start_url: window.location.href,
        max_steps: maxSteps
      })
    });

    let currentStep = 0;
    let lastResult = null;

    while (currentStep < maxSteps && !stopRequested) {
      currentStep++;
      updateHud(currentStep, maxSteps, "Observing tab state & reasoning next action...", null);

      // 1. Capture live page observation
      const observation = observeDOM();

      // 2. Query backend cognitive step
      const stepRes = await fetch(`${BACKEND_BASE}/api/extension/step`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: runId,
          goal: goal,
          step: currentStep,
          max_steps: maxSteps,
          observation: observation,
          last_result: lastResult
        })
      });

      if (!stepRes.ok) {
        throw new Error(`Backend error (${stepRes.status})`);
      }

      const stepData = await stepRes.json();
      const action = stepData.action;
      const thought = stepData.thought;

      const thoughtText = thought?.reasoning || thought?.reflection || "Executing step...";
      const actionDesc = action?.description || `${action?.action_type || "action"}`;
      updateHud(currentStep, maxSteps, thoughtText, actionDesc);

      // 3. Check for terminal condition or safety block
      if (!stepData.safety_passed) {
        updateHud(currentStep, maxSteps, `Blocked unsafe action: ${stepData.safety_reason}`, null, true);
        break;
      }

      if (action.action_type === "finish") {
        updateHud(currentStep, maxSteps, `✓ Goal Accomplished: ${action.description}`, null, true);
        break;
      } else if (action.action_type === "fail") {
        updateHud(currentStep, maxSteps, `⚠️ Task Failed: ${action.description}`, null, true);
        break;
      }

      // 4. Visually execute action on the active tab
      lastResult = await executeDomAction(action);
      await sleep(600);

      if (stepData.is_terminal) {
        updateHud(currentStep, maxSteps, `✓ Task completed: ${stepData.final_output || "Done"}`, null, true);
        break;
      }
    }

    if (stopRequested) {
      updateHud(currentStep, maxSteps, "🛑 Execution stopped by user.", null, true);
    }
  } catch (err) {
    console.error("Klick in-tab error:", err);
    updateHud(0, maxSteps, `⚠️ Execution error: ${err.message}`, null, true);
  } finally {
    isTaskRunning = false;
  }
}

// =========================================================================
// DOM Observation Engine
// =========================================================================

function observeDOM() {
  const selectorQuery = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [data-agent-id]';
  const candidates = Array.from(document.querySelectorAll(selectorQuery));
  const elements = [];

  for (let i = 0; i < candidates.length; i++) {
    const el = candidates[i];

    // Check visibility
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") continue;

    const tag = el.tagName.toLowerCase();
    let agentId = el.getAttribute("data-agent-id");
    if (!agentId) {
      agentId = el.id ? `id-${el.id}` : `elem-${elements.length}`;
      try {
        el.setAttribute("data-agent-id", agentId);
      } catch (e) {}
    }

    const text = (el.innerText || el.value || el.placeholder || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().slice(0, 80);
    const role = el.getAttribute("role") || tag;

    elements.push({
      tag_name: tag,
      selector: `[data-agent-id="${agentId}"]`,
      id: el.id || null,
      text: text,
      role: role,
      data_agent_id: agentId,
      is_visible: true
    });

    if (elements.length >= 60) break; // Limit elements for token efficiency
  }

  // Summary
  const domSummary = elements
    .map(e => `${e.tag_name}#${e.data_agent_id} "${e.text}"`)
    .join(" | ");

  const snippet = (document.body ? document.body.innerText : "").slice(0, 1500);

  return {
    url: window.location.href,
    title: document.title,
    dom_summary: domSummary,
    interactive_elements: elements,
    page_text_snippet: snippet
  };
}

// =========================================================================
// DOM Action Execution Engine
// =========================================================================

async function executeDomAction(action) {
  const actType = (action.action_type || action.action || "").toLowerCase();
  const target = action.selector || action.target || "";

  if (actType === "click") {
    const el = resolveTargetElement(target);
    if (!el) {
      return { success: false, error: `Element '${target}' not found on page` };
    }

    // Visual pulse & scroll
    highlightElement(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(350);

    // Trigger click
    try {
      el.click();
    } catch {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    }
    return { success: true, message: `Clicked element '${target}'` };
  } else if (actType === "type") {
    const el = resolveTargetElement(target);
    if (!el) {
      return { success: false, error: `Element '${target}' not found on page` };
    }

    highlightElement(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(250);

    el.focus();
    el.value = action.text || "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, message: `Typed '${action.text}' into '${target}'` };
  } else if (actType === "scroll") {
    const delta = action.scroll_delta_y || action.amount || action.delta_y || 400;
    window.scrollBy({ top: delta, behavior: "smooth" });
    await sleep(400);
    return { success: true, message: `Scrolled page by ${delta}px` };
  } else if (actType === "wait") {
    const sec = action.wait_seconds || action.seconds || 1.0;
    await sleep(sec * 1000);
    return { success: true, message: `Waited for ${sec}s` };
  } else if (actType === "navigate") {
    if (action.url) {
      window.location.href = action.url;
    }
    return { success: true, message: `Navigating to ${action.url}` };
  }

  return { success: true, message: `Action ${actType} processed` };
}

function resolveTargetElement(target) {
  if (!target) return null;
  const cleanId = target.replace(/\[data-agent-id=["']?([^"']+)["']?\]/, "$1").trim();

  // 1. By data-agent-id
  let el = document.querySelector(`[data-agent-id="${cleanId}"]`);
  if (el) return el;

  // 2. By ID or standard selector
  try {
    el = document.querySelector(target) || document.querySelector(`#${cleanId}`);
    if (el) return el;
  } catch (e) {}

  // 3. Fallback by button or link text
  const buttons = Array.from(document.querySelectorAll("button, a"));
  const match = buttons.find(b => (b.innerText || "").trim().toLowerCase().includes(cleanId.toLowerCase()));
  if (match) return match;

  return null;
}

function highlightElement(el) {
  if (!el) return;
  el.classList.add("bp-target-highlight");
  setTimeout(() => {
    el.classList.remove("bp-target-highlight");
  }, 1200);
}

// =========================================================================
// In-Tab Floating HUD
// =========================================================================

function createHud(goal, maxSteps) {
  if (currentHud) currentHud.remove();

  const hud = document.createElement("div");
  hud.className = "bp-hud-box";
  hud.innerHTML = `
    <div class="bp-hud-header">
      <div class="bp-hud-brand">
        <span class="bp-hud-dot"></span>
        <span>Klick AI</span>
      </div>
      <span class="bp-hud-step" id="bp-hud-step-text">Step 1 / ${maxSteps}</span>
    </div>
    <div class="bp-hud-thought" id="bp-hud-thought-text">Starting autonomous task...</div>
    <div class="bp-hud-action" id="bp-hud-action-text" style="display: none;"></div>
    <div class="bp-hud-footer">
      <span style="font-size: 10px; color: #64748b;">Direct In-Tab Mode</span>
      <button class="bp-hud-stop-btn" id="bp-hud-stop-trigger">Stop</button>
    </div>
  `;

  document.body.appendChild(hud);
  currentHud = hud;

  hud.querySelector("#bp-hud-stop-trigger").addEventListener("click", () => {
    stopRequested = true;
    fetch(`${BACKEND_BASE}/api/extension/stop`, { method: "POST" }).catch(() => {});
  });
}

function updateHud(step, maxSteps, thought, action, isFinished = false) {
  if (!currentHud) return;

  const stepElem = currentHud.querySelector("#bp-hud-step-text");
  const thoughtElem = currentHud.querySelector("#bp-hud-thought-text");
  const actionElem = currentHud.querySelector("#bp-hud-action-text");
  const stopBtn = currentHud.querySelector("#bp-hud-stop-trigger");

  if (stepElem) stepElem.textContent = `Step ${step} / ${maxSteps}`;
  if (thoughtElem) thoughtElem.textContent = thought;

  if (actionElem) {
    if (action) {
      actionElem.style.display = "block";
      actionElem.textContent = `⚡ ${action}`;
    } else {
      actionElem.style.display = "none";
    }
  }

  if (isFinished) {
    if (stopBtn) stopBtn.style.display = "none";
    setTimeout(() => {
      if (currentHud) {
        currentHud.style.transition = "opacity 0.6s ease, transform 0.6s ease";
        currentHud.style.opacity = "0";
        currentHud.style.transform = "translateY(12px)";
        setTimeout(() => {
          if (currentHud) currentHud.remove();
          currentHud = null;
        }, 600);
      }
    }, 6000);
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
