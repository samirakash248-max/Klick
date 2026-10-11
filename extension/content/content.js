/**
 * Klick AI - Content Script (In-Tab Execution Engine)
 * Runs autonomous tasks directly on the active browser tab without opening extra Chrome windows.
 * Optimized with fast token-pruned DOM extraction, cookie banner auto-clear,
 * and robust keyboard / search / media link interaction.
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
    startInTabTask(message.goal, message.maxSteps || 10, message.model || "llama3.2:3b");
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
// Cookie / Consent Banner Auto-Dismissal
// =========================================================================

function dismissConsentBanners() {
  try {
    const buttons = Array.from(document.querySelectorAll("button, [role='button'], a"));
    for (const btn of buttons) {
      const text = (btn.innerText || btn.getAttribute("aria-label") || "").trim().toLowerCase();
      if (
        text === "accept all" ||
        text === "i agree" ||
        text === "accept" ||
        text === "agree to all" ||
        text.includes("before you continue") ||
        text.includes("reject all")
      ) {
        btn.click();
        return true;
      }
    }
  } catch (e) {}
  return false;
}

// =========================================================================
// In-Tab Autonomous Execution Loop
// =========================================================================

async function startInTabTask(goal, maxSteps, model = "llama3.2:3b") {
  isTaskRunning = true;
  stopRequested = false;
  const runId = "ext-" + Date.now();

  createHud(goal, maxSteps, model);

  try {
    // Notify backend to initialize state
    await fetch(`${BACKEND_BASE}/api/extension/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        goal: goal,
        start_url: window.location.href,
        max_steps: maxSteps,
        model: model
      })
    });

    let currentStep = 0;
    let lastResult = null;

    while (currentStep < maxSteps && !stopRequested) {
      currentStep++;
      updateHud(currentStep, maxSteps, "Observing tab state & reasoning next action...", null);

      // Dismiss cookie banners before observation
      dismissConsentBanners();

      // 1. Capture live page observation (token-optimized)
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
          last_result: lastResult,
          model: model
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
      await sleep(250);

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
// Token-Optimized DOM Observation Engine
// =========================================================================

function observeDOM() {
  const isYouTube = window.location.hostname.includes("youtube.com");
  const isGoogle = window.location.hostname.includes("google.com");

  // Tag YouTube search box & primary video titles if on YouTube
  if (isYouTube) {
    const searchInput = document.querySelector("input#search, input[name='search_query'], ytd-searchbox input");
    if (searchInput && !searchInput.getAttribute("data-agent-id")) {
      searchInput.setAttribute("data-agent-id", "id-search");
    }
    const searchBtn = document.querySelector("button#search-icon-legacy, ytd-searchbox button");
    if (searchBtn && !searchBtn.getAttribute("data-agent-id")) {
      searchBtn.setAttribute("data-agent-id", "id-search-button");
    }
    const firstVideo = document.querySelector("a#video-title, ytd-video-renderer a#video-title, a[href*='/watch?v=']");
    if (firstVideo && !firstVideo.getAttribute("data-agent-id")) {
      firstVideo.setAttribute("data-agent-id", "id-video-title");
    }
  } else if (isGoogle) {
    const searchBox = document.querySelector("textarea[name='q'], input[name='q']");
    if (searchBox && !searchBox.getAttribute("data-agent-id")) {
      searchBox.setAttribute("data-agent-id", "id-search");
    }
  }

  const selectorQuery = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [data-agent-id]';
  const rawCandidates = Array.from(document.querySelectorAll(selectorQuery));
  const candidateElements = [];

  for (let i = 0; i < rawCandidates.length; i++) {
    const el = rawCandidates[i];

    // Check visibility
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") continue;

    const tag = el.tagName.toLowerCase();
    let agentId = el.getAttribute("data-agent-id");
    if (!agentId) {
      agentId = el.id ? `id-${el.id}` : `elem-${candidateElements.length}`;
      try {
        el.setAttribute("data-agent-id", agentId);
      } catch (e) {}
    }

    const text = (el.innerText || el.value || el.placeholder || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().slice(0, 70);
    const role = el.getAttribute("role") || tag;
    const href = el.getAttribute("href") || null;

    candidateElements.push({
      tag_name: tag,
      selector: `[data-agent-id="${agentId}"]`,
      id: el.id || null,
      text: text,
      role: role,
      href: href,
      data_agent_id: agentId,
      is_visible: true
    });
  }

  // Prioritize controls: inputs/search first, then buttons, then video titles, then other links
  candidateElements.sort((a, b) => {
    const rank = el => {
      const label = `${el.data_agent_id} ${el.text} ${el.tag_name}`.toLowerCase();
      if (el.tag_name === "input" || el.tag_name === "textarea" || label.includes("search")) return 0;
      if (label.includes("video-title") || (el.href && el.href.includes("/watch?v="))) return 1;
      if (el.tag_name === "button" || el.role === "button") return 2;
      if (el.tag_name === "a" && el.text) return 3;
      return 4;
    };
    return rank(a) - rank(b);
  });

  // Limit to top 25 actionable candidates to match backend observer token optimization
  const prunedElements = candidateElements.slice(0, 25);

  const domSummary = prunedElements
    .map((e, idx) => `[${idx}] <${e.tag_name}> '${e.text || e.data_agent_id}' -> selector: \`${e.selector}\``)
    .join("\n");

  const snippet = (document.body ? document.body.innerText.slice(0, 500) : "").trim();

  return {
    url: window.location.href,
    title: document.title,
    dom_summary: domSummary,
    interactive_elements: prunedElements,
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

    highlightElement(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(100);

    // Trigger click with full pointer sequence
    try {
      el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
      el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      el.click();
    } catch {
      el.click();
    }

    // If target is video link on YouTube and hasn't navigated after 300ms, navigate directly
    if (el.tagName.toLowerCase() === "a" && el.href && (el.href.includes("/watch?v=") || el.href.includes("/shorts/"))) {
      setTimeout(() => {
        if (!window.location.href.includes("/watch") && !window.location.href.includes("/shorts")) {
          window.location.href = el.href;
        }
      }, 350);
    }

    return { success: true, message: `Clicked element '${target}'` };
  } else if (actType === "type") {
    const el = resolveTargetElement(target) || document.querySelector("input#search, input[name='search_query'], textarea[name='q'], input[type='text'], input");
    if (!el) {
      return { success: false, error: `Input element '${target}' not found on page` };
    }

    highlightElement(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(80);

    el.focus();

    // Use native prototype value setter for React/Vue/Polymer input tracking
    try {
      const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) {
        setter.call(el, action.text || "");
      } else {
        el.value = action.text || "";
      }
    } catch (e) {
      el.value = action.text || "";
    }

    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));

    // Auto-submit search box with Enter
    const isSearch = target.includes("search") || (el.id && el.id.includes("search")) || el.name === "q" || el.name === "search_query";
    if (isSearch && action.text) {
      await sleep(50);
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));

      if (el.form) {
        el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
      }

      // If YouTube search button exists, also click it
      const ytdSearchBtn = document.querySelector("button#search-icon-legacy, ytd-searchbox button");
      if (ytdSearchBtn) {
        ytdSearchBtn.click();
      }
    }

    return { success: true, message: `Typed '${action.text}' into '${target}'` };
  } else if (actType === "press_key") {
    const key = action.key || "Enter";
    const activeEl = document.activeElement || document.body;
    activeEl.dispatchEvent(new KeyboardEvent("keydown", { key: key, code: key, bubbles: true }));
    activeEl.dispatchEvent(new KeyboardEvent("keyup", { key: key, code: key, bubbles: true }));

    if (key.toLowerCase() === "enter" && activeEl.form) {
      activeEl.form.requestSubmit ? activeEl.form.requestSubmit() : activeEl.form.submit();
    }
    return { success: true, message: `Pressed key '${key}'` };
  } else if (actType === "scroll") {
    const delta = action.scroll_delta_y || action.amount || action.delta_y || 400;
    window.scrollBy({ top: delta, behavior: "smooth" });
    await sleep(200);
    return { success: true, message: `Scrolled page by ${delta}px` };
  } else if (actType === "wait") {
    const sec = Math.min(action.wait_seconds || action.seconds || 1.0, 5.0);
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

  // 1. YouTube video title link resolution
  if (cleanId === "id-video-title" || cleanId.includes("video-title") || cleanId.includes("watch")) {
    const videoLink = document.querySelector("a#video-title, ytd-video-renderer a#video-title, a[href*='/watch?v=']");
    if (videoLink) return videoLink;
  }

  // 2. Search input resolution
  if (cleanId === "id-search" || cleanId === "search" || cleanId === "elem-3" || cleanId.includes("search-input")) {
    const searchInput = document.querySelector("input#search, input[name='search_query'], textarea[name='q'], input[type='search']");
    if (searchInput) return searchInput;
  }

  // 3. By data-agent-id
  let el = document.querySelector(`[data-agent-id="${cleanId}"]`);
  if (el) return el;

  // 4. By ID or standard selector
  try {
    el = document.querySelector(target) || document.querySelector(`#${cleanId}`);
    if (el) return el;
  } catch (e) {}

  // 5. Fallback by button or link text
  const buttons = Array.from(document.querySelectorAll("button, a, input[type='submit']"));
  const match = buttons.find(b => (b.innerText || b.value || "").trim().toLowerCase().includes(cleanId.toLowerCase()));
  if (match) return match;

  return null;
}

function highlightElement(el) {
  if (!el) return;
  el.classList.add("bp-target-highlight");
  setTimeout(() => {
    el.classList.remove("bp-target-highlight");
  }, 800);
}

// =========================================================================
// In-Tab Floating HUD
// =========================================================================

function createHud(goal, maxSteps, model = "llama3.2:3b") {
  if (currentHud) currentHud.remove();

  const hud = document.createElement("div");
  hud.className = "bp-hud-box";
  hud.innerHTML = `
    <div class="bp-hud-header">
      <div class="bp-hud-brand">
        <span class="bp-hud-dot"></span>
        <span>Klick AI</span>
        <span class="bp-hud-model-pill">${model}</span>
      </div>
      <span class="bp-hud-step" id="bp-hud-step-text">Step 1 / ${maxSteps}</span>
    </div>
    <div class="bp-hud-thought" id="bp-hud-thought-text">Starting autonomous task...</div>
    <div class="bp-hud-action" id="bp-hud-action-text" style="display: none;"></div>
    <div class="bp-hud-footer">
      <span style="font-size: 10px; color: #94a3b8;">Direct In-Tab Mode</span>
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
        currentHud.style.transition = "opacity 0.5s ease, transform 0.5s ease";
        currentHud.style.opacity = "0";
        currentHud.style.transform = "translateY(12px)";
        setTimeout(() => {
          if (currentHud) currentHud.remove();
          currentHud = null;
        }, 500);
      }
    }, 4500);
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
