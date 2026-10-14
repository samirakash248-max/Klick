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
let isRecordingMacro = false;
let recordingIndicatorEl = null;

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
    sendResponse({ isRunning: isTaskRunning, isRecordingMacro: isRecordingMacro });
  } else if (message.action === "start_macro_recording") {
    startMacroRecordingListeners();
    sendResponse({ success: true, message: "Macro recording started." });
  } else if (message.action === "stop_macro_recording") {
    stopMacroRecordingListeners();
    sendResponse({ success: true, message: "Macro recording stopped." });
  } else if (message.action === "autofill_persona") {
    const filledCount = smartAutofillInTab(message.persona);
    sendResponse({ success: filledCount > 0, filled_count: filledCount });
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

      // Request tab screenshot from background service worker for visual grounding
      try {
        const shotRes = await chrome.runtime.sendMessage({ action: "capture_tab_screenshot" });
        if (shotRes && shotRes.success && shotRes.screenshot_base64) {
          observation.screenshot_base64 = shotRes.screenshot_base64;
        }
      } catch {
        // Fallback gracefully without visual screenshot
      }

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
    // Direct visual screen coordinates execution
    if (action.coordinates && typeof action.coordinates.x === "number") {
      const cx = action.coordinates.x + (action.coordinates.width || 0) / 2;
      const cy = action.coordinates.y + (action.coordinates.height || 0) / 2;
      const coordEl = document.elementFromPoint(cx, cy);
      if (coordEl) {
        highlightElement(coordEl);
        coordEl.click();
        return { success: true, message: `Clicked visual coordinates (${Math.round(cx)}, ${Math.round(cy)})` };
      }
    }

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
  } else if (actType === "new_tab") {
    try {
      chrome.runtime.sendMessage({ action: "new_tab", url: action.url || "about:blank" });
    } catch {
      window.open(action.url || "about:blank", "_blank");
    }
    return { success: true, message: `Opened new tab ${action.url || ""}` };
  } else if (actType === "switch_tab") {
    try {
      chrome.runtime.sendMessage({ action: "switch_tab", tabIndex: action.tab_index });
    } catch (e) {
      // fallback
    }
    return { success: true, message: `Switched tab to index ${action.tab_index ?? ""}` };
  } else if (actType === "close_tab") {
    try {
      chrome.runtime.sendMessage({ action: "close_tab" });
    } catch (e) {
      window.close();
    }
    return { success: true, message: "Closed current tab" };
  }

  return { success: true, message: `Action ${actType} processed` };
}

function resolveTargetElement(target) {
  if (!target) return null;
  const cleanId = target.replace(/\[data-agent-id=["']?([^"']+)["']?\]/, "$1").trim();

  // 0. Set-of-Mark visual mark resolution e.g. [0], [3], elem-0
  const somMatch = cleanId.match(/^\[?(\d+)\]?$/);
  if (somMatch) {
    const idx = somMatch[1];
    const somEl = document.querySelector(`[data-agent-id="elem-${idx}"], [data-agent-id="id-${idx}"]`);
    if (somEl) return somEl;
  }

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

// =========================================================================
// Interactive Macro Recorder Listeners & Selector Generator
// =========================================================================

function getBestSelector(el) {
  if (!el || el === document.body || el === document.documentElement) return "body";
  if (el.id && !/^\d/.test(el.id)) return `#${el.id}`;
  const agentId = el.getAttribute("data-agent-id");
  if (agentId) return `[data-agent-id="${agentId}"]`;
  if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
  const ph = el.getAttribute("placeholder");
  if (ph) return `${el.tagName.toLowerCase()}[placeholder="${ph}"]`;
  const aria = el.getAttribute("aria-label");
  if (aria) return `[aria-label="${aria}"]`;
  const tag = el.tagName.toLowerCase();
  if (el.className && typeof el.className === "string") {
    const cls = el.className.split(/\s+/).filter(c => c && !c.includes(":") && !c.startsWith("bp-")).slice(0, 2);
    if (cls.length > 0) return `${tag}.${cls.join(".")}`;
  }
  return tag;
}

function handleMacroClick(e) {
  if (!isRecordingMacro) return;
  if (e.target.closest("#bp-agent-hud, #bp-recording-indicator")) return;
  const target = e.target.closest("button, a, input, select, textarea, [role='button']") || e.target;
  const sel = getBestSelector(target);
  const text = (target.innerText || target.value || target.getAttribute("aria-label") || "").trim().slice(0, 50);

  fetch(`${BACKEND_BASE}/api/recorder/step`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action_type: "click",
      selector: sel,
      target: target.id || target.name || text.slice(0, 25),
      text: text,
      url: window.location.href
    })
  }).catch(() => {});
}

function handleMacroChange(e) {
  if (!isRecordingMacro) return;
  if (e.target.closest("#bp-agent-hud, #bp-recording-indicator")) return;
  const target = e.target;
  const sel = getBestSelector(target);

  fetch(`${BACKEND_BASE}/api/recorder/step`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action_type: "type",
      selector: sel,
      target: target.id || target.name || "",
      text: target.value || "",
      url: window.location.href
    })
  }).catch(() => {});
}

function handleMacroKeydown(e) {
  if (!isRecordingMacro) return;
  if (e.key === "Enter") {
    const target = e.target;
    if (target.closest("#bp-agent-hud, #bp-recording-indicator")) return;
    const sel = getBestSelector(target);

    fetch(`${BACKEND_BASE}/api/recorder/step`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_type: "press_key",
        key: "Enter",
        selector: sel,
        target: target.id || target.name || "",
        url: window.location.href
      })
    }).catch(() => {});
  }
}

function startMacroRecordingListeners() {
  isRecordingMacro = true;
  document.addEventListener("click", handleMacroClick, true);
  document.addEventListener("change", handleMacroChange, true);
  document.addEventListener("keydown", handleMacroKeydown, true);

  if (!recordingIndicatorEl) {
    const badge = document.createElement("div");
    badge.id = "bp-recording-indicator";
    badge.style.position = "fixed";
    badge.style.bottom = "20px";
    badge.style.left = "20px";
    badge.style.zIndex = "2147483647";
    badge.style.background = "rgba(15, 23, 42, 0.92)";
    badge.style.color = "#ffffff";
    badge.style.border = "1px solid rgba(239, 68, 68, 0.5)";
    badge.style.borderRadius = "8px";
    badge.style.padding = "8px 14px";
    badge.style.fontFamily = "-apple-system, BlinkMacSystemFont, sans-serif";
    badge.style.fontSize = "12px";
    badge.style.fontWeight = "600";
    badge.style.boxShadow = "0 8px 24px rgba(0,0,0,0.3)";
    badge.style.display = "flex";
    badge.style.alignItems = "center";
    badge.style.gap = "8px";
    badge.innerHTML = `
      <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#ef4444; animation: bp-pulse 1s infinite alternate;"></span>
      <span>Klick RPA Recording Active</span>
      <span style="font-size: 10px; color: #94a3b8; font-weight: normal;">(Interactions recorded)</span>
    `;
    const styleSheet = document.createElement("style");
    styleSheet.textContent = `@keyframes bp-pulse { 0% { opacity: 0.4; } 100% { opacity: 1; } }`;
    document.head.appendChild(styleSheet);
    document.body.appendChild(badge);
    recordingIndicatorEl = badge;
  }
}

function stopMacroRecordingListeners() {
  isRecordingMacro = false;
  document.removeEventListener("click", handleMacroClick, true);
  document.removeEventListener("change", handleMacroChange, true);
  document.removeEventListener("keydown", handleMacroKeydown, true);

  if (recordingIndicatorEl) {
    recordingIndicatorEl.remove();
    recordingIndicatorEl = null;
  }
}

// =========================================================================
// Smart Persona Autofill In-Tab Engine
// =========================================================================

function smartAutofillInTab(persona) {
  const data = persona || {
    first_name: "Samir",
    last_name: "Developer",
    full_name: "Samir Developer",
    email: "samir.dev@example.com",
    phone: "555-0199",
    company: "Acme Cloud Inc.",
    job_title: "Senior AI Engineer",
    address: "123 Tech Plaza, Suite 400",
    city: "San Francisco",
    state: "CA",
    postal_code: "94105",
    country: "United States",
    username: "samir_dev",
    password: "SecurePassword123!",
    website: "https://example.com",
    notes: "Automated test submission via Klick AI persona vault."
  };

  const fieldsMap = [
    { key: "email", re: /(email|mail|e-mail)/i },
    { key: "password", re: /(password|passwd|pwd)/i },
    { key: "phone", re: /(phone|tel|mobile|cell)/i },
    { key: "first_name", re: /(first.*name|fname|given.*name)/i },
    { key: "last_name", re: /(last.*name|lname|sur.*name|family.*name)/i },
    { key: "full_name", re: /(full.*name|name|contact.*name)/i },
    { key: "company", re: /(company|organization|org|business|employer)/i },
    { key: "job_title", re: /(title|job|role|occupation|position)/i },
    { key: "address", re: /(address|street|addr)/i },
    { key: "city", re: /(city|town|municipality)/i },
    { key: "state", re: /(state|province|region)/i },
    { key: "postal_code", re: /(zip|postal|pincode|postcode)/i },
    { key: "country", re: /(country|nation)/i },
    { key: "username", re: /(username|user|login)/i },
    { key: "website", re: /(website|url|web|domain)/i },
    { key: "notes", re: /(notes|comments|message|bio|description|about)/i }
  ];

  const inputs = Array.from(document.querySelectorAll("input:not([type='hidden']):not([type='submit']):not([type='button']), textarea, select"));
  let filledCount = 0;

  for (const el of inputs) {
    if (el.closest("#bp-agent-hud, #bp-recording-indicator")) continue;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;

    const identifier = `${el.id || ''} ${el.name || ''} ${el.placeholder || ''} ${el.getAttribute('aria-label') || ''} ${el.autocomplete || ''}`.toLowerCase();
    
    // Find closest label text if available
    let labelText = "";
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl) labelText = lbl.innerText.toLowerCase();
    }
    const fullDescriptor = `${identifier} ${labelText}`;

    for (const mapping of fieldsMap) {
      if (mapping.re.test(fullDescriptor)) {
        const val = data[mapping.key] || data.custom_fields?.[mapping.key];
        if (val) {
          el.value = val;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));

          // Visual green glow feedback
          const prevBorder = el.style.borderColor;
          el.style.borderColor = "#10b981";
          el.style.boxShadow = "0 0 0 3px rgba(16, 185, 129, 0.25)";
          setTimeout(() => {
            el.style.borderColor = prevBorder;
            el.style.boxShadow = "";
          }, 3000);

          filledCount++;
          break;
        }
      }
    }
  }

  return filledCount;
}

// =========================================================================
// In-Tab Voice Navigation Overlay & Keyboard Shortcut (Alt+Shift+V)
// =========================================================================

let voiceRecognition = null;
let voiceOverlayEl = null;

function toggleVoiceCommandOverlay() {
  if (voiceOverlayEl) {
    closeVoiceCommandOverlay();
    return;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert("Klick AI: Web Speech API is not supported in this browser tab.");
    return;
  }

  voiceOverlayEl = document.createElement("div");
  voiceOverlayEl.id = "bp-voice-overlay";
  voiceOverlayEl.innerHTML = `
    <div class="bp-voice-card">
      <div class="bp-voice-header">
        <div class="bp-voice-brand">
          <div class="bp-voice-mic-badge">🎙️</div>
          <div>
            <div class="bp-voice-title">Klick Voice Dictation</div>
            <div class="bp-voice-status" id="bp-voice-status">Listening... speak your automation goal</div>
          </div>
        </div>
        <button class="bp-voice-close-btn" id="bp-voice-close" title="Close (Esc)">✕</button>
      </div>
      <div class="bp-voice-body">
        <div class="bp-voice-transcript" id="bp-voice-transcript">Speak clearly (e.g. "search for casetoo and play first video")...</div>
      </div>
      <div class="bp-voice-actions">
        <button class="bp-voice-btn-sec" id="bp-voice-cancel">Cancel</button>
        <button class="bp-voice-btn-prim" id="bp-voice-run" disabled>⚡ Run Command</button>
      </div>
    </div>
  `;
  document.body.appendChild(voiceOverlayEl);

  const statusEl = document.getElementById("bp-voice-status");
  const transcriptEl = document.getElementById("bp-voice-transcript");
  const runBtn = document.getElementById("bp-voice-run");
  const closeBtn = document.getElementById("bp-voice-close");
  const cancelBtn = document.getElementById("bp-voice-cancel");

  let recordedGoal = "";

  function handleExecute() {
    if (!recordedGoal || !recordedGoal.trim()) return;
    const goalToRun = recordedGoal.trim();
    closeVoiceCommandOverlay();
    startInTabTask(goalToRun, 10, "llama3.2:3b");
  }

  runBtn.addEventListener("click", handleExecute);
  closeBtn.addEventListener("click", closeVoiceCommandOverlay);
  cancelBtn.addEventListener("click", closeVoiceCommandOverlay);

  try {
    voiceRecognition = new SpeechRecognition();
    voiceRecognition.continuous = false;
    voiceRecognition.interimResults = true;
    voiceRecognition.lang = "en-US";

    voiceRecognition.onstart = () => {
      if (statusEl) statusEl.textContent = "Listening... speak now";
    };

    voiceRecognition.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          final += event.results[i][0].transcript;
        } else {
          interim += event.results[i][0].transcript;
        }
      }
      recordedGoal = (final || interim).trim();
      if (recordedGoal) {
        transcriptEl.textContent = `"${recordedGoal}"`;
        transcriptEl.style.color = "#00e5ff";
        runBtn.disabled = false;
      }
    };

    voiceRecognition.onerror = (err) => {
      if (statusEl) statusEl.textContent = "Speech recognition error or paused.";
    };

    voiceRecognition.onend = () => {
      if (statusEl) {
        statusEl.textContent = recordedGoal ? "Voice captured! Executing..." : "Listening stopped";
      }
      if (recordedGoal) {
        setTimeout(() => {
          if (voiceOverlayEl && recordedGoal) {
            handleExecute();
          }
        }, 1200);
      }
    };

    voiceRecognition.start();
  } catch (err) {
    if (statusEl) statusEl.textContent = "Could not initialize mic: " + err.message;
  }
}

function closeVoiceCommandOverlay() {
  if (voiceRecognition) {
    try { voiceRecognition.stop(); } catch (e) {}
    voiceRecognition = null;
  }
  if (voiceOverlayEl && voiceOverlayEl.parentNode) {
    voiceOverlayEl.parentNode.removeChild(voiceOverlayEl);
  }
  voiceOverlayEl = null;
}

// Global hotkey: Alt + Shift + V triggers voice navigation overlay
window.addEventListener("keydown", (e) => {
  if (e.altKey && e.shiftKey && (e.code === "KeyV" || e.key === "V" || e.key === "v")) {
    e.preventDefault();
    toggleVoiceCommandOverlay();
  } else if (e.key === "Escape" && voiceOverlayEl) {
    closeVoiceCommandOverlay();
  }
});


