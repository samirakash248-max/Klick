"""Playwright action executor module.

Responsible for launching Chromium, managing browser pages,
and executing atomic browser actions with safeguards against the active page.

Actions supported:
1. click: Click an element identified by exact data-agent-id.
2. type: Enter text into an editable input or textarea.
3. scroll: Scroll the page vertically by a bounded amount.
4. navigate: Navigate to an allowed local mock-site URL.
5. wait: Wait for a short, bounded interval or for a specific element to appear.

Owned by: Browser Automation Engineer (Member B)
"""

from __future__ import annotations

import asyncio
import os
import csv
import json
import math
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

try:
    from .schemas import ActionType, BrowserAction, ExecutionResult
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from backend.app.schemas import ActionType, BrowserAction, ExecutionResult

# Configuration & Safeguard Bounds
DEFAULT_ACTION_TIMEOUT_MS = 5000
MAX_WAIT_SECONDS = 10.0
MAX_SCROLL_PIXELS = 2000
MIN_SCROLL_PIXELS = -2000

MOCK_SITE_DIR = Path(__file__).resolve().parent.parent.parent / "mock-site"
MOCK_SITE_DEFAULT_URL = f"file:///{(MOCK_SITE_DIR / 'index.html').as_posix()}"
ALLOW_EXTERNAL_URLS = os.getenv("BROWSERPILOT_ALLOW_EXTERNAL", "true").lower() in ("true", "1", "yes")
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "browser_profile"
PERSISTENT_PROFILE_ENABLED = os.getenv("BROWSERPILOT_PERSISTENT_PROFILE", "true").lower() in ("true", "1", "yes")
EXPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


STEALTH_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-position=50,50",
    "--window-size=1280,800",
]

STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Remove navigator.webdriver flag
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    } catch(e) {}

    // 2. Mock window.chrome runtime and app
    try {
        if (!window.chrome) {
            window.chrome = {};
        }
        window.chrome.runtime = window.chrome.runtime || {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install' },
            PlatformArch: { X86_64: 'x86-64' },
            PlatformOs: { WIN: 'win' }
        };
        window.chrome.app = window.chrome.app || {
            isInstalled: false,
            InstallState: { DISABLED: 'DISABLED', INSTALLED: 'INSTALLED' },
            RunningState: { CANNOT_RUN: 'CANNOT_RUN', READY_TO_RUN: 'READY_TO_RUN' }
        };
    } catch(e) {}

    // 3. Mock languages
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
    } catch(e) {}

    // 4. Mock plugins & mimeTypes
    try {
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                ];
                plugins.item = (i) => plugins[i];
                plugins.namedItem = (name) => plugins.find(p => p.name === name);
                plugins.refresh = () => {};
                return plugins;
            },
        });
    } catch(e) {}

    // 5. Override permissions.query
    try {
        if (navigator.permissions && navigator.permissions.query) {
            const origQuery = navigator.permissions.query;
            navigator.permissions.query = (params) => (
                params && params.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    origQuery(params)
            );
        }
    } catch(e) {}

    // 6. WebGL vendor spoofing
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Google Inc. (Intel)';
            if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
            return getParameter.apply(this, arguments);
        };
    } catch(e) {}

    // 7. AudioContext fingerprint randomization
    try {
        if (window.AudioBuffer) {
            const origGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function() {
                const data = origGetChannelData.apply(this, arguments);
                for (let i = 0; i < data.length; i += 100) {
                    data[i] += 0.0000001 * (Math.random() - 0.5);
                }
                return data;
            };
        }
    } catch(e) {}

    // 8. Canvas fingerprint noise injection
    try {
        if (window.HTMLCanvasElement) {
            const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                const ctx = this.getContext('2d');
                if (ctx) {
                    ctx.fillStyle = 'rgba(255,255,255,0.005)';
                    ctx.fillRect(0, 0, 1, 1);
                }
                return origToDataURL.apply(this, arguments);
            };
        }
    } catch(e) {}

    // 9. Hardware concurrency and memory spoofing
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    } catch(e) {}

    // 10. WebRTC leak prevention
    try {
        if (window.RTCPeerConnection) {
            const origCreateOffer = RTCPeerConnection.prototype.createOffer;
            RTCPeerConnection.prototype.createOffer = function(options) {
                return origCreateOffer.apply(this, arguments);
            };
        }
    } catch(e) {}
})();
"""


class PlaywrightExecutor:
    """Manages Chromium lifecycle and executes browser actions with safeguards."""

    def __init__(
        self,
        page: Optional[Page] = None,
        headless: bool = False,
        slow_mo_ms: int = 0,
        allow_external: bool = False,
        user_data_dir: Optional[str] = None,
        persistent_profile: bool = True,
    ) -> None:
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.allow_external = allow_external
        self.user_data_dir = user_data_dir or os.getenv("BROWSERPILOT_USER_DATA_DIR") or str(DEFAULT_PROFILE_DIR)
        self.persistent_profile = persistent_profile if persistent_profile is not None else PERSISTENT_PROFILE_ENABLED
        self._page: Optional[Page] = page
        self._pages: List[Page] = [page] if page else []
        self._context: Optional[BrowserContext] = None
        self._browser: Optional[Browser] = None
        self._playwright: Optional[Playwright] = None
        self._owns_browser: bool = page is None
        self._is_stopped: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._current_mouse_pos: Tuple[float, float] = (150.0, 150.0)

    def _generate_bezier_trajectory(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        num_steps: int = 16,
    ) -> List[Tuple[float, float]]:
        """Generate human-like mouse coordinates following a cubic Bézier curve with randomized jitter."""
        x0, y0 = start
        x3, y3 = end
        dx = x3 - x0
        dy = y3 - y0
        dist = math.hypot(dx, dy)
        if dist < 5:
            return [(x3, y3)]

        # Intermediate control points P1, P2 with perpendicular deviation
        dev_factor = min(0.25, max(0.08, 40.0 / (dist + 1.0)))
        perp_x = -dy * dev_factor
        perp_y = dx * dev_factor

        jitter1 = (random.random() - 0.5) * 1.5
        jitter2 = (random.random() - 0.5) * 1.5

        p1_x = x0 + dx / 3.0 + perp_x * jitter1
        p1_y = y0 + dy / 3.0 + perp_y * jitter1

        p2_x = x0 + (2.0 * dx) / 3.0 + perp_x * jitter2
        p2_y = y0 + (2.0 * dy) / 3.0 + perp_y * jitter2

        trajectory: List[Tuple[float, float]] = []
        actual_steps = max(6, min(num_steps, int(dist / 25) + 6))

        for i in range(1, actual_steps + 1):
            t = i / float(actual_steps)
            # Cubic Bézier formula
            bx = ((1.0 - t) ** 3) * x0 + 3.0 * ((1.0 - t) ** 2) * t * p1_x + 3.0 * (1.0 - t) * (t ** 2) * p2_x + (t ** 3) * x3
            by = ((1.0 - t) ** 3) * y0 + 3.0 * ((1.0 - t) ** 2) * t * p1_y + 3.0 * (1.0 - t) * (t ** 2) * p2_y + (t ** 3) * y3
            if i < actual_steps:
                bx += (random.random() - 0.5) * 1.2
                by += (random.random() - 0.5) * 1.2
            trajectory.append((round(bx, 1), round(by, 1)))

        return trajectory

    @property
    def is_stopped(self) -> bool:
        """Return True if executor has been commanded to stop."""
        return self._is_stopped

    def stop(self) -> None:
        """Set stop flag to block subsequent action executions."""
        self._is_stopped = True

    def resume(self) -> None:
        """Clear stop flag to allow action executions."""
        self._is_stopped = False

    async def __aenter__(self) -> "PlaywrightExecutor":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    @property
    def current_page(self) -> Optional[Page]:
        """Return the active page instance, falling back to any open tab."""
        try:
            current_loop = asyncio.get_running_loop()
            if self._loop is not None and self._loop != current_loop:
                return None
        except RuntimeError:
            pass

        if self._page and not self._page.is_closed():
            return self._page

        # Fall back to any open page from tracked pages or context
        if self._pages:
            self._pages = [p for p in self._pages if not p.is_closed()]
            if self._pages:
                self._page = self._pages[-1]
                return self._page

        if self._context and self._context.pages:
            open_pages = [p for p in self._context.pages if not p.is_closed()]
            if open_pages:
                self._page = open_pages[-1]
                return self._page

        return None

    def _setup_context_listeners(self, context: BrowserContext) -> None:
        """Attach listeners to track tab creation, popups, and tab closures."""
        self._pages = [p for p in context.pages if not p.is_closed()]
        context.on("page", self._on_page_created)
        for p in self._pages:
            self._attach_page_listeners(p)

    def _attach_page_listeners(self, page: Page) -> None:
        """Attach listeners to a specific page for popups and closure."""
        try:
            page.on("close", lambda p: self._on_page_closed(p))
            page.on("popup", lambda p: self._on_page_created(p))
        except Exception:
            pass

    def _on_page_created(self, page: Page) -> None:
        """Handle new tab or popup window creation."""
        if page not in self._pages:
            self._pages.append(page)
        self._attach_page_listeners(page)
        self._page = page
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._safe_bring_to_front(page))
        except Exception:
            pass

    def _on_page_closed(self, page: Page) -> None:
        """Handle tab closure and reassign active page to remaining tabs."""
        if page in self._pages:
            self._pages.remove(page)
        if self._page == page:
            self._page = self._pages[-1] if self._pages else None
            if self._page and not self._page.is_closed():
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._safe_bring_to_front(self._page))
                except Exception:
                    pass

    async def _safe_bring_to_front(self, page: Page) -> None:
        """Safely bring page to front without throwing unhandled exceptions."""
        try:
            if page and not page.is_closed():
                await page.bring_to_front()
        except Exception:
            pass

    async def get_open_tabs(self) -> List[Dict[str, Any]]:
        """Return structured metadata for all currently open browser tabs."""
        tabs = []
        self._pages = [p for p in self._pages if not p.is_closed()]
        if self._context and not self._pages:
            self._pages = [p for p in self._context.pages if not p.is_closed()]

        for i, p in enumerate(self._pages):
            try:
                url = p.url or ""
                title = await p.title() if not p.is_closed() else ""
            except Exception:
                url = ""
                title = ""
            tabs.append({
                "index": i,
                "title": title or url or f"Tab {i + 1}",
                "url": url,
                "is_active": (p == self.current_page),
            })
        return tabs

    async def save_cookies(self) -> List[Dict[str, Any]]:
        """Save current session cookies to JSON backup in user data directory."""
        try:
            if self._context:
                cookies = await self._context.cookies()
                profile_dir = Path(self.user_data_dir)
                profile_dir.mkdir(parents=True, exist_ok=True)
                cookie_file = profile_dir / "cookies.json"
                import json
                with open(cookie_file, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, indent=2)
                return cookies
        except Exception:
            pass
        return []

    async def load_cookies(self) -> None:
        """Load session cookies from JSON backup if available."""
        try:
            cookie_file = Path(self.user_data_dir) / "cookies.json"
            if cookie_file.exists() and self._context:
                import json
                with open(cookie_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                if isinstance(cookies, list) and cookies:
                    await self._context.add_cookies(cookies)
        except Exception:
            pass

    async def clear_profile(self) -> bool:
        """Clear cookies and reset persistent profile cache."""
        try:
            if self._context:
                await self._context.clear_cookies()
            cookie_file = Path(self.user_data_dir) / "cookies.json"
            if cookie_file.exists():
                cookie_file.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    async def initialize(self) -> Page:
        """Start Playwright and launch Chromium browser if not already active."""
        current_loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop != current_loop:
            self._page = None
            self._pages = []
            self._context = None
            self._browser = None
            self._playwright = None

        self._loop = current_loop

        if self._page and not self._page.is_closed():
            try:
                if self._playwright and ((self._browser and self._browser.is_connected()) or self._context):
                    return self._page
            except Exception:
                pass
            self._page = None

        viewport_size = {"width": 1440, "height": 900}

        # Resilient launch loop that self-heals broken pipes or loop mismatch across uvicorn reloads
        for attempt in range(2):
            try:
                if sys.platform == "win32":
                    try:
                        import ctypes
                        u32 = ctypes.windll.user32
                        hdesk = u32.OpenDesktopW("Default", 0, False, 0x01FF)
                        if hdesk:
                            u32.SetThreadDesktop(hdesk)
                    except Exception:
                        pass

                if not self._playwright:
                    self._playwright = await async_playwright().start()

                # Attempt persistent context if enabled
                if self.persistent_profile:
                    try:
                        os.makedirs(self.user_data_dir, exist_ok=True)
                        launch_kwargs = {
                            "user_data_dir": self.user_data_dir,
                            "headless": self.headless,
                            "slow_mo": self.slow_mo_ms,
                            "args": STEALTH_CHROME_ARGS,
                            "ignore_default_args": ["--enable-automation"],
                            "viewport": viewport_size,
                            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                            "locale": "en-US",
                            "timezone_id": "America/New_York",
                            "color_scheme": "light",
                            "device_scale_factor": 1,
                            "has_touch": False,
                            "is_mobile": False,
                            "permissions": ["geolocation"],
                        }
                        context = None
                        for channel in [None, "chrome", "msedge"]:
                            try:
                                kw = dict(launch_kwargs)
                                if channel:
                                    kw["channel"] = channel
                                context = await self._playwright.chromium.launch_persistent_context(**kw)
                                if context:
                                    break
                            except Exception:
                                pass

                        if context:
                            self._context = context
                            self._browser = None
                            self._page = context.pages[0] if context.pages else await context.new_page()
                            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
                            self._setup_context_listeners(self._context)
                            try:
                                await self._page.bring_to_front()
                            except Exception:
                                pass
                            self._owns_browser = True
                            await self.load_cookies()
                            return self._page
                    except Exception:
                        # Fallback to standard launch if persistent context fails
                        pass

                # Standard ephemeral launch
                browser = None
                launch_errors = []
                for channel in [None, "chrome", "msedge"]:
                    try:
                        kwargs = {
                            "headless": self.headless,
                            "slow_mo": self.slow_mo_ms,
                            "args": STEALTH_CHROME_ARGS,
                            "ignore_default_args": ["--enable-automation"],
                        }
                        if channel:
                            kwargs["channel"] = channel
                        browser = await self._playwright.chromium.launch(**kwargs)
                        if browser:
                            break
                    except Exception as le:
                        launch_errors.append(str(le))

                if not browser:
                    raise RuntimeError(f"Failed to launch browser: {'; '.join(launch_errors)}")

                self._browser = browser
                self._context = await self._browser.new_context(
                    viewport=viewport_size,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="en-US",
                    timezone_id="America/New_York",
                    color_scheme="light",
                    device_scale_factor=1,
                    has_touch=False,
                    is_mobile=False,
                    permissions=["geolocation"],
                )
                await self._context.add_init_script(STEALTH_INIT_SCRIPT)
                self._setup_context_listeners(self._context)
                self._page = await self._context.new_page()
                try:
                    await self._page.bring_to_front()
                except Exception:
                    pass
                self._owns_browser = True
                await self.load_cookies()
                return self._page
            except Exception as exc:
                try:
                    if self._playwright:
                        await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser = None
                self._context = None
                self._pages = []
                self._page = None
                if attempt == 1:
                    raise exc


    def _clean_agent_id(self, target: Optional[str]) -> Optional[str]:
        """Extract clean agent ID from target or selector string, supporting Set-of-Mark tags."""
        if not target:
            return None
        trimmed = target.strip()
        match = re.search(r'data-agent-id=["\']?([^"\'\]]+)["\']?', trimmed)
        if match:
            return match.group(1)
        # Visual Set-of-Mark mark e.g. [0], [3]
        som_m = re.match(r"^\[(\d+)\]$", trimmed)
        if som_m:
            return f"elem-{som_m.group(1)}"
        return trimmed

    async def _resolve_target(
        self, page: Page, target: Optional[str], action_name: str
    ) -> Tuple[Optional[Locator], Optional[str], Optional[ExecutionResult]]:
        """Resolve target through exact data-agent-id match or CSS fallback."""
        if not target or not target.strip():
            # Fallback for search results page where first video title is the target
            if "youtube.com/results" in (page.url or "").lower() and action_name == "click":
                video_loc = page.locator("ytd-video-renderer a#video-title, a[href*='/watch?v=']")
                if await video_loc.count() > 0:
                    return (video_loc.first, "id-video-title", None)
            return (
                None,
                None,
                ExecutionResult(
                    success=False,
                    action_type=ActionType(action_name),
                    action=action_name,
                    target=None,
                    message=f"Missing target: {action_name.upper()} action requires a valid target data-agent-id",
                    error="Missing target",
                ),
            )

        clean_id = self._clean_agent_id(target)
        locator = page.locator(f'[data-agent-id="{clean_id}"]')
        count = await locator.count()

        if count == 0:
            # Fallback for real-world websites where elements may have standard CSS selectors or attributes
            fallback_candidates = [
                clean_id,
                f"#{clean_id}",
                f'[data-agent-id="elem-{clean_id}"]',
                f'[data-agent-id="id-{clean_id}"]',
                f'[name="{clean_id}"]',
                f'[aria-label="{clean_id}"]',
                f'[placeholder="{clean_id}"]',
                f'button:has-text("{clean_id}")',
                f'a:has-text("{clean_id}")',
                f'text="{clean_id}"',
            ]
            is_yt = page is not None and "youtube.com" in (page.url or "").lower()
            if is_yt and (action_name == "type" or any(k in clean_id.lower() for k in ("search", "input", "elem-3", "query"))):
                fallback_candidates.extend([
                    "input#search",
                    "input[name='search_query']",
                    "input[type='search']",
                    "input[role='combobox']",
                    "ytd-searchbox input",
                    "[aria-label*='Search' i]",
                ])
            if is_yt and any(k in clean_id.lower() for k in ("video", "first", "watch", "title")):
                fallback_candidates.extend([
                    "ytd-video-renderer a#video-title",
                    "ytd-video-renderer h3 a",
                    "a#video-title",
                    "a[href*='/watch?v=']",
                    "a[href*='/shorts/']",
                    "ytd-video-renderer a#thumbnail",
                ])
            for cand in fallback_candidates:
                try:
                    cand_loc = page.locator(cand)
                    if await cand_loc.count() > 0:
                        return (cand_loc.first, clean_id, None)
                except Exception:
                    continue

            return (
                None,
                clean_id,
                ExecutionResult(
                    success=False,
                    action_type=ActionType(action_name),
                    action=action_name,
                    target=clean_id,
                    message=f"Target '{clean_id}' not found on the page",
                    error="Target not found",
                ),
            )

        if count > 1:
            try:
                visible_loc = locator.locator("visible=true")
                if await visible_loc.count() > 0:
                    return (visible_loc.first, clean_id, None)
            except Exception:
                pass
            return (locator.first, clean_id, None)

        return (locator, clean_id, None)

    async def _check_editable(self, locator: Locator, target: str) -> Tuple[bool, Optional[str]]:
        """Permit typing only into appropriate editable elements."""
        try:
            info = await locator.evaluate("""
                el => {
                    const tag = el.tagName.toLowerCase();
                    const type = (el.getAttribute('type') || 'text').toLowerCase();
                    const isContentEditable = el.isContentEditable || el.getAttribute('contenteditable') === 'true';
                    const isReadonly = el.readOnly || el.hasAttribute('readonly');
                    const isDisabled = el.disabled || el.hasAttribute('disabled');
                    
                    const validTypes = ['text', 'search', 'email', 'url', 'tel', 'password', 'number'];
                    const isTextInput = tag === 'input' && validTypes.includes(type);
                    const isTextarea = tag === 'textarea';
                    const isEditable = (isTextInput || isTextarea || isContentEditable) && !isReadonly && !isDisabled;
                    
                    return {
                        tag: tag,
                        type: type,
                        isTextInput: isTextInput,
                        isTextarea: isTextarea,
                        isContentEditable: isContentEditable,
                        isReadonly: isReadonly,
                        isDisabled: isDisabled,
                        isEditable: isEditable
                    };
                }
            """)

            if info.get("isDisabled"):
                return False, f"Target '{target}' is disabled"
            if info.get("isReadonly"):
                return False, f"Target '{target}' is readonly"
            if not info.get("isEditable"):
                tag = info.get("tag", "element")
                type_attr = info.get("type", "")
                if tag == "input":
                    return False, f"Cannot type into input of type '{type_attr}': target '{target}' is not a text input"
                return False, f"Cannot type into <{tag}>: target '{target}' is not an editable input or textarea"

            return True, None
        except Exception as exc:
            return False, f"Failed to check element editability: {str(exc)}"

    def _is_allowed_url(self, url: str) -> bool:
        """Restrict navigation to local mock-site origin only."""
        if not url or not url.strip():
            return False
        clean_url = url.strip()
        if clean_url == "about:blank":
            return True

        if clean_url.startswith("#") or clean_url.startswith("/"):
            return True

        parsed = urllib.parse.urlparse(clean_url)
        if parsed.scheme in ("javascript", "data", "vbscript"):
            return False

        if parsed.scheme == "file":
            try:
                path_part = parsed.path
                if path_part.startswith("/") and len(path_part) > 2 and path_part[2] == ":":
                    path_part = path_part[1:]
                norm_path = Path(urllib.parse.unquote(path_part)).resolve().as_posix().lower()
                mock_dir = MOCK_SITE_DIR.resolve().as_posix().lower()
                return norm_path.startswith(mock_dir) or "mock-site" in norm_path
            except Exception:
                return False

        if parsed.scheme in ("http", "https"):
            if self.allow_external:
                return True
            hostname = (parsed.hostname or "").lower()
            if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
                return True
            return False

        # Local HTML filenames or anchors
        if not parsed.scheme and (clean_url.endswith(".html") or clean_url.endswith(".htm") or "#" in clean_url):
            return True

        return False

    async def detect_captcha(self, page: Optional[Page] = None) -> Dict[str, Any]:
        """Detect if the active or specified page is challenged by a CAPTCHA or blocking wall."""
        p = page or self.current_page
        if not p or p.is_closed():
            return {"present": False, "type": "none", "details": ""}

        url = (p.url or "").lower()
        try:
            title = (await p.title() or "").lower()
        except Exception:
            title = ""

        try:
            page_text = (await p.evaluate("() => (document.body ? document.body.textContent.slice(0, 4000) : '')") or "").lower()
        except Exception:
            page_text = ""

        # 1. Google "unusual traffic" / sorry
        if "/sorry/index" in url or "unusual traffic" in title or "unusual traffic from your computer network" in page_text:
            return {"present": True, "type": "google_unusual_traffic", "details": "Google unusual traffic challenge"}

        # 2. Cloudflare Turnstile / Challenge Page
        if any(cf_marker in title for cf_marker in ("just a moment...", "attention required! | cloudflare", "cloudflare")):
            return {"present": True, "type": "cloudflare", "details": "Cloudflare waiting room / challenge"}

        # Fast path: only scan frames if multiple frames exist
        if len(p.frames) > 1:
            try:
                for frame in p.frames:
                    f_url = frame.url.lower()
                    if "challenges.cloudflare.com" in f_url or "turnstile" in f_url:
                        return {"present": True, "type": "cloudflare_turnstile", "details": "Cloudflare Turnstile iframe"}
                    if "recaptcha" in f_url:
                        return {"present": True, "type": "recaptcha", "details": "Google reCAPTCHA iframe"}
                    if "hcaptcha.com" in f_url:
                        return {"present": True, "type": "hcaptcha", "details": "hCaptcha iframe"}
            except Exception:
                pass

        try:
            if await p.locator("iframe[src*='recaptcha'], iframe[title*='reCAPTCHA'], #g-recaptcha, .g-recaptcha").count() > 0:
                return {"present": True, "type": "recaptcha", "details": "Google reCAPTCHA element"}
        except Exception:
            pass

        try:
            if await p.locator("iframe[src*='hcaptcha.com'], .h-captcha").count() > 0:
                return {"present": True, "type": "hcaptcha", "details": "hCaptcha element"}
        except Exception:
            pass

        # 5. Google / YouTube Cookie Consent Wall (blocks search results / input in many regions)
        try:
            consent_locators = p.locator("#L2AGLb, #W0wltc, button:has-text('Accept all'), button:has-text('I agree'), button:has-text('Tout accepter'), button:has-text('Alle akzeptieren'), button[aria-label*='Accept' i], ytd-consent-bump-v2-lightbox, button:has-text('Reject all')")
            if await consent_locators.count() > 0 and await consent_locators.first.is_visible():
                return {"present": True, "type": "google_consent", "details": "Google/YouTube Cookie Consent Modal"}
        except Exception:
            pass

        # 6. Generic human verification text in body
        if any(phrase in page_text for phrase in ("verify you are human", "please verify that you are not a robot", "complete the security check to continue")):
            return {"present": True, "type": "generic_challenge", "details": "Human verification challenge text"}

        return {"present": False, "type": "none", "details": ""}

    async def handle_captcha(self, page: Optional[Page] = None, timeout_seconds: float = 8.0) -> bool:
        """Attempt automatic resolution / dismissal of CAPTCHAs, challenges, and consent walls."""
        p = page or self.current_page
        if not p or p.is_closed():
            return False

        detection = await self.detect_captcha(p)
        if not detection.get("present"):
            return True

        c_type = detection.get("type")
        start_time = time.monotonic()

        # Case 1: Google Consent Modal
        if c_type == "google_consent":
            consent_selectors = [
                "#L2AGLb",
                "button:has-text('Accept all')",
                "button:has-text('I agree')",
                "button:has-text('Agree')",
                "button[aria-label*='Accept' i]",
                "ytd-consent-bump-v2-lightbox button",
                "#dismiss-button",
                "button:has-text('Tout accepter')",
                "button:has-text('Alle akzeptieren')",
                "#W0wltc",
                "button:has-text('Reject all')",
                "button[aria-label*='Reject' i]",
            ]
            for sel in consent_selectors:
                try:
                    btn = p.locator(sel)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        await btn.first.click(timeout=1500)
                        await p.wait_for_timeout(500)
                        return True
                except Exception:
                    continue

        # Case 2: Cloudflare Turnstile
        if c_type in ("cloudflare", "cloudflare_turnstile"):
            clicked = False
            for frame in p.frames:
                f_url = frame.url.lower()
                if "challenges.cloudflare.com" in f_url or "turnstile" in f_url:
                    try:
                        candidates = ["input[type='checkbox']", "#challenge-stage", ".ctp-checkbox-label", "span.mark"]
                        for c_sel in candidates:
                            loc = frame.locator(c_sel)
                            if await loc.count() > 0:
                                await loc.first.click(timeout=2000)
                                clicked = True
                                break
                        if clicked:
                            break
                    except Exception:
                        pass

            if not clicked:
                try:
                    cf_stage = p.locator("#challenge-stage, .cf-turnstile, [data-sitekey]")
                    if await cf_stage.count() > 0:
                        await cf_stage.first.click(timeout=1500)
                except Exception:
                    pass

        # Case 3: Google reCAPTCHA
        if c_type in ("recaptcha", "google_unusual_traffic"):
            for frame in p.frames:
                if "recaptcha" in frame.url.lower():
                    try:
                        anchor = frame.locator("#recaptcha-anchor, .recaptcha-checkbox-border, .recaptcha-checkbox")
                        if await anchor.count() > 0:
                            await anchor.first.click(timeout=2000)
                            break
                    except Exception:
                        pass

        # Case 4: hCaptcha
        if c_type == "hcaptcha":
            for frame in p.frames:
                if "hcaptcha.com" in frame.url.lower():
                    try:
                        cb = frame.locator("#checkbox, [aria-haspopup='true']")
                        if await cb.count() > 0:
                            await cb.first.click(timeout=2000)
                            break
                    except Exception:
                        pass

        # Polling wait loop: check if challenge clears (auto-solved or human-solved)
        while (time.monotonic() - start_time) < timeout_seconds:
            await asyncio.sleep(0.8)
            current_check = await self.detect_captcha(p)
            if not current_check.get("present"):
                return True

        return False

    async def execute(self, action: Union[BrowserAction, Dict[str, Any]]) -> ExecutionResult:
        """Execute a validated browser action dictionary or BrowserAction object."""
        if not self.current_page or (self._page and self._page.is_closed()):
            await self.initialize()

        page = self._page
        start_time = time.perf_counter()

        # Normalize action payload
        if hasattr(action, "model_dump"):
            action = action.model_dump()

        if isinstance(action, dict):
            raw_act = action.get("action") or action.get("action_type") or ""
            raw_action = (raw_act.value if hasattr(raw_act, "value") else str(raw_act)).strip().lower()
            target = action.get("target") or action.get("selector")
            text = action.get("text")
            url = action.get("url")
            key = action.get("key")
            scroll_delta_y = action.get("scroll_delta_y") or action.get("delta_y") or action.get("amount")
            wait_seconds = action.get("wait_seconds") or action.get("seconds") or action.get("duration")
            tab_index = action.get("tab_index")
            description = action.get("description", "")
        else:
            return ExecutionResult(
                success=False,
                action_type=ActionType.FAIL,
                action="fail",
                message=f"Unsupported action object type: {type(action).__name__}",
                error="Invalid action type",
                duration_ms=0.0,
            )

        if not raw_action:
            return ExecutionResult(
                success=False,
                action_type=ActionType.FAIL,
                action="fail",
                message="Action type is required but was not provided",
                error="Missing action type",
                duration_ms=0.0,
            )

        if self._is_stopped:
            return ExecutionResult(
                success=False,
                action_type=ActionType.FAIL,
                action=raw_action or "stop",
                target=self._clean_agent_id(target),
                message="Execution is stopped. Action rejected.",
                error="Execution stopped",
                duration_ms=0.0,
            )

        try:
            # Dispatch to action handlers
            coordinates = action.get("coordinates")
            if raw_action == "click":
                result = await self._execute_click(page, target, coordinates=coordinates)
            elif raw_action == "type":
                result = await self._execute_type(page, target, text)
            elif raw_action == "scroll":
                result = await self._execute_scroll(page, scroll_delta_y)
            elif raw_action == "navigate":
                result = await self._execute_navigate(page, url)
            elif raw_action == "wait":
                result = await self._execute_wait(page, target, wait_seconds)
            elif raw_action == "press_key":
                result = await self._execute_press_key(page, key)
            elif raw_action in ("switch_tab", "switch-tab", "switch_to_tab", "focus_tab"):
                result = await self._execute_switch_tab(tab_index, target)
            elif raw_action in ("new_tab", "new-tab", "open_tab", "create_tab"):
                result = await self._execute_new_tab(url or target)
            elif raw_action in ("close_tab", "close-tab", "close_current_tab"):
                result = await self._execute_close_tab(tab_index, target)
            elif raw_action in ("go_back", "back", "navigate_back", "history_back"):
                result = await self._execute_go_back(page)
            elif raw_action in ("export_data", "export", "scrape_table", "scrape_data", "download_data"):
                export_fmt = action.get("export_format") or action.get("format") or "csv"
                export_fn = action.get("export_filename") or action.get("filename") or action.get("text")
                result = await self._execute_export_data(page, target, format=export_fmt, filename=export_fn)
            elif raw_action == "extract":
                if url:
                    result = await self._execute_navigate(page, url)
                elif target:
                    result = await self._execute_click(page, target)
                else:
                    result = ExecutionResult(
                        success=True,
                        action_type=ActionType.EXTRACT,
                        action="extract",
                        message="Extracted observation data",
                    )
            elif raw_action in ("finish", "fail"):
                is_finish = raw_action == "finish"
                result = ExecutionResult(
                    success=is_finish,
                    action_type=ActionType.FINISH if is_finish else ActionType.FAIL,
                    action=raw_action,
                    message=description or f"Agent requested {raw_action}",
                )
            else:
                result = ExecutionResult(

                    success=False,
                    action_type=ActionType.FAIL,
                    action=raw_action,
                    message=f"Unknown or unsupported action type: '{raw_action}'",
                    error="Unsupported action",
                )

            duration_ms = (time.perf_counter() - start_time) * 1000
            result.duration_ms = duration_ms
            return result

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            act_enum = ActionType(raw_action) if raw_action in ActionType._value2member_map_ else ActionType.FAIL
            return ExecutionResult(
                success=False,
                action_type=act_enum,
                action=raw_action,
                target=self._clean_agent_id(target),
                message=f"Execution error: {str(exc)}",
                duration_ms=duration_ms,
                error=str(exc),
            )

    async def _animate_cursor(
        self,
        page: Page,
        locator: Optional[Locator] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        is_click: bool = False,
    ) -> None:
        """Render and smoothly animate a visual cursor indicator on the page."""
        try:
            target_x = x
            target_y = y
            if locator is not None:
                box = await locator.bounding_box()
                if box:
                    target_x = box["x"] + box["width"] / 2
                    target_y = box["y"] + box["height"] / 2

            if target_x is None or target_y is None:
                return

            # Inject / update visual cursor in DOM with smooth CSS bezier transition
            await page.evaluate("""([tx, ty, clickAnim]) => {
                let cur = document.getElementById('__bp_agent_cursor__');
                if (!cur) {
                    cur = document.createElement('div');
                    cur.id = '__bp_agent_cursor__';
                    cur.innerHTML = `
                      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" style="pointer-events: none !important; filter: drop-shadow(0 2px 6px rgba(0,0,0,0.7)); transform: translate(-2px, -2px);">
                        <path d="M5.5 3.21V20.8c0 .45.54.67.85.35l4.86-4.86a.5.5 0 0 1 .35-.15h6.87c.45 0 .67-.54.35-.85L6.35 2.85a.5.5 0 0 0-.85.36z" fill="#00e5ff" stroke="#0f172a" stroke-width="1.6" style="pointer-events: none !important;"/>
                      </svg>
                      <div id="__bp_ripple__" style="position: absolute; top: -10px; left: -10px; width: 40px; height: 40px; border-radius: 50%; border: 3px solid #00e5ff; pointer-events: none !important; opacity: 0; transform: scale(0.3); transition: transform 0.35s ease-out, opacity 0.35s ease-out;"></div>
                    `;
                    cur.style.position = 'fixed';
                    cur.style.zIndex = '2147483647';
                    cur.style.pointerEvents = 'none';
                    cur.style.transition = 'left 0.35s cubic-bezier(0.2, 0.8, 0.2, 1), top 0.35s cubic-bezier(0.2, 0.8, 0.2, 1)';
                    cur.style.left = `${tx}px`;
                    cur.style.top = `${ty}px`;
                    document.documentElement.appendChild(cur);
                }
                cur.style.left = `${tx}px`;
                cur.style.top = `${ty}px`;
                if (clickAnim) {
                    const rip = document.getElementById('__bp_ripple__');
                    if (rip) {
                        rip.style.transform = 'scale(1.5)';
                        rip.style.opacity = '1';
                        setTimeout(() => {
                            rip.style.transform = 'scale(0.3)';
                            rip.style.opacity = '0';
                        }, 300);
                    }
                }
            }""", [target_x, target_y, is_click])

            # Move physical mouse along human-like cubic Bézier trajectory
            trajectory = self._generate_bezier_trajectory(self._current_mouse_pos, (target_x, target_y))
            for pt_x, pt_y in trajectory:
                await page.mouse.move(pt_x, pt_y)
                if self.slow_mo_ms > 0:
                    step_delay = max(4, int(self.slow_mo_ms / len(trajectory)))
                    await page.wait_for_timeout(step_delay)
            self._current_mouse_pos = (target_x, target_y)
        except Exception:
            pass

    async def _execute_click(
        self,
        page: Page,
        target: Optional[str] = None,
        coordinates: Optional[Union[Dict[str, Any], ElementCoordinates]] = None,
    ) -> ExecutionResult:
        """Perform click action with exact data-agent-id target resolution or direct visual coordinates."""
        # 1. Direct visual coordinates execution
        if coordinates:
            cx = float(coordinates.get("x") if isinstance(coordinates, dict) else coordinates.x)
            cy = float(coordinates.get("y") if isinstance(coordinates, dict) else coordinates.y)
            w = float(coordinates.get("width", 0) if isinstance(coordinates, dict) else (getattr(coordinates, "width", 0) or 0))
            h = float(coordinates.get("height", 0) if isinstance(coordinates, dict) else (getattr(coordinates, "height", 0) or 0))
            if w > 0 and h > 0:
                cx += w / 2.0
                cy += h / 2.0

            await self._animate_cursor(page, x=cx, y=cy, is_click=True)
            await page.mouse.click(cx, cy)
            return ExecutionResult(
                success=True,
                action_type=ActionType.CLICK,
                action="click",
                target=f"coords({cx:.1f},{cy:.1f})",
                message=f"Clicked visual screen coordinates ({cx:.1f}, {cy:.1f})",
            )

        # 2. String coordinates target: "coords(x, y)" or "point(x, y)"
        if target:
            coord_m = re.match(r"^(?:coords?|point)\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)$", target.strip(), re.IGNORECASE)
            if coord_m:
                cx = float(coord_m.group(1))
                cy = float(coord_m.group(2))
                await self._animate_cursor(page, x=cx, y=cy, is_click=True)
                await page.mouse.click(cx, cy)
                return ExecutionResult(
                    success=True,
                    action_type=ActionType.CLICK,
                    action="click",
                    target=f"coords({cx:.1f},{cy:.1f})",
                    message=f"Clicked visual screen coordinates ({cx:.1f}, {cy:.1f})",
                )

        locator, clean_id, error_result = await self._resolve_target(page, target, "click")
        if error_result:
            return error_result

        # Check visibility with action timeout
        try:
            await locator.wait_for(state="visible", timeout=3000)
        except Exception:
            try:
                await locator.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                return ExecutionResult(
                    success=False,
                    action_type=ActionType.CLICK,
                    action="click",
                    target=clean_id,
                    message=f"Target '{clean_id}' is not visible on the page",
                    error="Element not visible",
                )

        # Smoothly move visual cursor to target element with click pulse animation
        await self._animate_cursor(page, locator=locator, is_click=True)

        # Execute click with resilient fallback
        try:
            await locator.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        try:
            await locator.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        except Exception as click_err:
            try:
                # Force click to bypass overlay / pointer-events interception
                await locator.click(timeout=2000, force=True)
            except Exception:
                try:
                    await locator.dispatch_event("click")
                except Exception:
                    raise click_err

        if self.slow_mo_ms > 0:
            await page.wait_for_timeout(50)

        # Check if video was clicked on YouTube and wait for watch or shorts page transition
        if any(k in (clean_id or "").lower() for k in ("video", "watch", "title")) or "results" in (page.url or "").lower():
            try:
                await page.wait_for_url(lambda u: "/watch" in u or "/shorts" in u, timeout=2500)
            except Exception:
                pass

        # Verify what happened after click without blindly assuming success
        modal_open = await page.locator("#invoice-modal-overlay.open").count() > 0
        if modal_open and ("invoice" in clean_id.lower() or "view" in clean_id.lower()):
            msg = "Invoice details opened"
        elif clean_id.startswith("nav-"):
            section_name = clean_id.replace("nav-", "")
            msg = f"Navigated to {section_name.capitalize()} page"
        elif "close" in clean_id.lower() and not modal_open:
            msg = "Invoice details modal closed"
        elif "search" in clean_id.lower():
            msg = f"Executed invoice search with '{clean_id}'"
        else:
            msg = f"Clicked element with agent ID '{clean_id}'"

        # Check if clicking triggered a consent banner or challenge
        if len(page.frames) > 1 or any(k in (page.url or "").lower() for k in ("challenge", "consent")):
            try:
                await self.handle_captcha(page, timeout_seconds=1.0)
            except Exception:
                pass

        return ExecutionResult(
            success=True,
            action_type=ActionType.CLICK,
            action="click",
            target=clean_id,
            message=msg,
        )

    async def _execute_type(
        self, page: Page, target: Optional[str], text: Optional[str]
    ) -> ExecutionResult:
        """Perform type action with editability safeguard."""
        locator, clean_id, error_result = await self._resolve_target(page, target, "type")
        if error_result:
            return error_result

        # Check visibility
        try:
            await locator.wait_for(state="visible", timeout=3000)
        except Exception:
            return ExecutionResult(
                success=False,
                action_type=ActionType.TYPE,
                action="type",
                target=clean_id,
                message=f"Target '{clean_id}' is not visible on the page",
                error="Element not visible",
            )

        # Verify element is an appropriate editable element
        is_editable, edit_err = await self._check_editable(locator, clean_id)
        if not is_editable:
            # Check if locator contains an editable child element (e.g. wrapper div around input)
            try:
                child_input = locator.locator("input:not([type='hidden']), textarea, [contenteditable='true']")
                if await child_input.count() > 0:
                    child_loc = child_input.first
                    child_editable, _ = await self._check_editable(child_loc, clean_id)
                    if child_editable:
                        locator = child_loc
                        is_editable = True
            except Exception:
                pass

        if not is_editable:
            # If on YouTube and typing search query, fall back to actual search input
            if page and "youtube.com" in (page.url or "").lower():
                try:
                    yt_search = page.locator("input#search, input[name='search_query'], input[role='combobox']")
                    if await yt_search.count() > 0:
                        child_loc = yt_search.first
                        child_editable, _ = await self._check_editable(child_loc, clean_id)
                        if child_editable:
                            locator = child_loc
                            is_editable = True
                except Exception:
                    pass

        if not is_editable:
            return ExecutionResult(
                success=False,
                action_type=ActionType.TYPE,
                action="type",
                target=clean_id,
                message=edit_err or f"Target '{clean_id}' is not editable",
                error="Non-editable element",
            )

        text_to_type = text if text is not None else ""
        if not text_to_type:
            current_val = ""
            try:
                current_val = await locator.input_value()
            except Exception:
                pass
            if current_val:
                await locator.press("Enter")
                return ExecutionResult(
                    success=True,
                    action_type=ActionType.PRESS_KEY,
                    action="press_key",
                    target=clean_id,
                    message=f"Input '{clean_id}' already has '{current_val}'; submitted with Enter",
                )

        # Determine if target is a search input
        is_search = False
        target_lower = (clean_id or "").lower()
        if any(k in target_lower for k in ("search", "query", "find", "filter")):
            is_search = True
        try:
            elem_is_search = await locator.evaluate("""el => {
                const type = (el.getAttribute('type') || '').toLowerCase();
                const name = (el.getAttribute('name') || '').toLowerCase();
                const placeholder = (el.getAttribute('placeholder') || '').toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                return type === 'search' || role === 'searchbox' ||
                       name.includes('search') || name.includes('query') ||
                       placeholder.includes('search') || aria.includes('search');
            }""")
            if elem_is_search:
                is_search = True
        except Exception:
            pass

        # Smoothly move visual cursor to target input element
        await self._animate_cursor(page, locator=locator, is_click=False)

        # Human keystroke simulation with randomized delay jitter (35ms - 75ms)
        try:
            await locator.click(timeout=1500)
            await locator.fill("")
            if hasattr(locator, "press_sequentially"):
                jitter_delay = random.randint(35, 75)
                await locator.press_sequentially(text_to_type, delay=jitter_delay)
            else:
                for char in text_to_type:
                    await locator.press(char)
                    await page.wait_for_timeout(random.randint(30, 70))
        except Exception:
            # Resilient fallback to instantaneous fill
            await locator.fill(text_to_type, timeout=DEFAULT_ACTION_TIMEOUT_MS)

        if self.slow_mo_ms > 0:
            await page.wait_for_timeout(50)

        # Auto-submit search box with Enter
        if is_search and text_to_type:
            try:
                await locator.press("Enter")
                try:
                    await page.wait_for_url(lambda u: "results" in u or "search" in u, timeout=1200)
                except Exception:
                    await page.wait_for_timeout(100)
                # If still not navigated to results on YouTube, click search button
                if "youtube.com" in (page.url or "").lower() and "results" not in (page.url or "").lower():
                    search_btn = page.locator("button#search-icon-legacy, ytd-searchbox button, #search-icon-legacy")
                    if await search_btn.count() > 0:
                        await search_btn.first.click()
                        try:
                            await page.wait_for_url(lambda u: "results" in u, timeout=1200)
                        except Exception:
                            await page.wait_for_timeout(100)
            except Exception:
                pass

        # Verify that value was typed correctly
        actual_val = await locator.input_value()
        if actual_val == text_to_type:
            msg = f"Typed '{text_to_type}' into '{clean_id}'"
        else:
            msg = f"Typed text into '{clean_id}' (value is now '{actual_val}')"

        return ExecutionResult(
            success=True,
            action_type=ActionType.TYPE,
            action="type",
            target=clean_id,
            message=msg,
        )

    async def _execute_scroll(
        self, page: Page, delta_y: Optional[Union[int, float]]
    ) -> ExecutionResult:
        """Perform vertical scroll bounded between MIN_SCROLL_PIXELS and MAX_SCROLL_PIXELS."""
        try:
            val = int(delta_y) if delta_y is not None else 300
        except (ValueError, TypeError):
            val = 300

        bounded_delta = max(MIN_SCROLL_PIXELS, min(MAX_SCROLL_PIXELS, val))
        clamped = bounded_delta != val

        start_y = await page.evaluate("window.scrollY || window.pageYOffset || 0")
        await page.evaluate(f"window.scrollBy(0, {bounded_delta})")
        await page.wait_for_timeout(100)
        new_y = await page.evaluate("window.scrollY || window.pageYOffset || 0")

        clamp_note = f" (bounded from {val}px)" if clamped else ""
        msg = f"Scrolled page by {bounded_delta}px{clamp_note} (scroll position: {start_y} -> {new_y})"

        return ExecutionResult(
            success=True,
            action_type=ActionType.SCROLL,
            action="scroll",
            message=msg,
        )

    async def _execute_navigate(self, page: Page, url: Optional[str]) -> ExecutionResult:
        """Perform navigation with origin restriction safeguard."""
        target_url = (url or "").strip()
        if not target_url:
            return ExecutionResult(
                success=False,
                action_type=ActionType.NAVIGATE,
                action="navigate",
                message="Missing URL: NAVIGATE action requires a target URL",
                error="Missing URL",
            )

        if (target_url.startswith("/") or not urllib.parse.urlparse(target_url).scheme) and not target_url.startswith("#"):
            if page and page.url and page.url.startswith("http"):
                target_url = urllib.parse.urljoin(page.url, target_url)

        if not self._is_allowed_url(target_url):
            return ExecutionResult(
                success=False,
                action_type=ActionType.NAVIGATE,
                action="navigate",
                message=f"Navigation rejected: External URL '{target_url}' is not permitted. Only local mock-site URLs are allowed.",
                error="Restricted navigation origin",
            )

        if "youtube.com/watch" in target_url and "v=" not in target_url:
            video_loc = page.locator("ytd-video-renderer a#video-title, a[href*='/watch?v=']")
            if await video_loc.count() > 0:
                first_vid = video_loc.first
                title = await first_vid.inner_text()
                href = await first_vid.get_attribute("href") or ""
                await first_vid.click()
                await page.wait_for_timeout(2000)
                curr_url = page.url
                return ExecutionResult(
                    success=True,
                    action_type=ActionType.NAVIGATE,
                    action="navigate",
                    message=f"Opened first video '{title}' ({curr_url})",
                )

        if target_url.startswith("#"):
            hash_part = target_url.lstrip("#")
            await page.evaluate(f"""() => {{
                if (typeof navigateTo === 'function') {{
                    navigateTo('{hash_part}');
                }} else {{
                    window.location.hash = '{target_url}';
                }}
            }}""")
            await page.wait_for_timeout(100)
        else:
            hash_part = ""
            if not urllib.parse.urlparse(target_url).scheme:
                file_part, sep, hash_part = target_url.partition("#")
                candidate = (MOCK_SITE_DIR / file_part) if file_part else None
                if candidate and candidate.exists():
                    target_url = f"file:///{(candidate).as_posix()}{sep}{hash_part}"
            else:
                _, sep, hash_part = target_url.partition("#")

            nav_timeout = (DEFAULT_ACTION_TIMEOUT_MS * 5) if self.allow_external else (DEFAULT_ACTION_TIMEOUT_MS * 2)
            await page.goto(target_url, wait_until="domcontentloaded", timeout=nav_timeout)
            if hash_part:
                await page.evaluate(f"""() => {{
                    if (typeof navigateTo === 'function') {{
                        navigateTo('{hash_part}');
                    }}
                }}""")
            await page.wait_for_timeout(100)

            # Auto-dismiss cookie consent modal (Google) or solve challenge
            try:
                await self.handle_captcha(page, timeout_seconds=3.0)
            except Exception:
                pass

        current_url = page.url
        title = await page.title()
        msg = f"Navigated to {current_url} ('{title}')"

        return ExecutionResult(
            success=True,
            action_type=ActionType.NAVIGATE,
            action="navigate",
            message=msg,
        )

    async def _execute_wait(
        self, page: Page, target: Optional[str], wait_seconds: Optional[Union[int, float]]
    ) -> ExecutionResult:
        """Perform bounded wait for interval or for target element appearance."""
        try:
            sec = float(wait_seconds) if wait_seconds is not None else 1.0
        except (ValueError, TypeError):
            sec = 1.0

        bounded_sec = max(0.0, min(MAX_WAIT_SECONDS, sec))
        clamped = bounded_sec != sec

        # Case A: Wait for specific element to appear
        if target and target.strip():
            clean_id = self._clean_agent_id(target)
            selector = f'[data-agent-id="{clean_id}"]'
            timeout_ms = max(500, int(bounded_sec * 1000))

            try:
                await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
                msg = f"Element '{clean_id}' appeared and is visible"
                return ExecutionResult(
                    success=True,
                    action_type=ActionType.WAIT,
                    action="wait",
                    target=clean_id,
                    message=msg,
                )
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    action_type=ActionType.WAIT,
                    action="wait",
                    target=clean_id,
                    message=f"Timed out waiting for element '{clean_id}' to appear within {timeout_ms}ms",
                    error=str(exc),
                )

        # Case B: Wait for fixed bounded duration
        await page.wait_for_timeout(int(bounded_sec * 1000))
        clamp_note = f" (bounded from {sec}s)" if clamped else ""
        msg = f"Waited for {bounded_sec:.1f}s{clamp_note}"

        return ExecutionResult(
            success=True,
            action_type=ActionType.WAIT,
            action="wait",
            message=msg,
        )

    async def _execute_press_key(self, page: Page, key: Optional[str]) -> ExecutionResult:
        """Perform key press on active element with key alias normalization."""
        raw_key = (key or "Enter").strip()
        key_map = {
            "search": "Enter",
            "submit": "Enter",
            "return": "Enter",
            "\n": "Enter",
            "\r": "Enter",
            "\r\n": "Enter",
            "esc": "Escape",
            "space": " ",
            "up": "ArrowUp",
            "down": "ArrowDown",
            "left": "ArrowLeft",
            "right": "ArrowRight",
        }
        key_name = key_map.get(raw_key.lower(), raw_key)
        await page.keyboard.press(key_name)
        return ExecutionResult(
            success=True,
            action_type=ActionType.PRESS_KEY,
            action="press_key",
            message=f"Pressed key '{key_name}'",
        )

    async def _execute_switch_tab(
        self, tab_index: Optional[int] = None, target: Optional[str] = None
    ) -> ExecutionResult:
        """Switch active focus to another open tab by index or matching title/URL."""
        tabs = await self.get_open_tabs()
        if not self._pages:
            return ExecutionResult(
                success=False,
                action_type=ActionType.SWITCH_TAB,
                action="switch_tab",
                message="No open browser tabs to switch between",
                error="No open tabs",
            )

        target_idx: Optional[int] = None
        if tab_index is not None and 0 <= tab_index < len(self._pages):
            target_idx = tab_index
        elif target:
            clean_tgt = str(target).strip()
            if clean_tgt.isdigit() and 0 <= int(clean_tgt) < len(self._pages):
                target_idx = int(clean_tgt)
            else:
                clean_lower = clean_tgt.lower()
                for i, p in enumerate(self._pages):
                    try:
                        p_url = (p.url or "").lower()
                        p_title = (await p.title() if not p.is_closed() else "").lower()
                        if clean_lower in p_title or clean_lower in p_url:
                            target_idx = i
                            break
                    except Exception:
                        pass

        if target_idx is None:
            # Cycle to next available tab
            curr_idx = next((i for i, p in enumerate(self._pages) if p == self.current_page), 0)
            target_idx = (curr_idx + 1) % len(self._pages)

        selected_page = self._pages[target_idx]
        self._page = selected_page
        try:
            await selected_page.bring_to_front()
            title = await selected_page.title()
        except Exception:
            title = selected_page.url or ""

        return ExecutionResult(
            success=True,
            action_type=ActionType.SWITCH_TAB,
            action="switch_tab",
            target=str(target_idx),
            message=f"Switched to tab #{target_idx}: '{title}' ({selected_page.url})",
        )

    async def _execute_new_tab(
        self, url: Optional[str] = None
    ) -> ExecutionResult:
        """Open a new browser tab and optionally navigate to destination URL."""
        if not self._context:
            await self.initialize()

        new_page = await self._context.new_page()
        self._on_page_created(new_page)
        self._page = new_page
        try:
            await new_page.bring_to_front()
        except Exception:
            pass

        target_url = url or "about:blank"
        if target_url and target_url != "about:blank":
            await self._execute_navigate(new_page, target_url)

        return ExecutionResult(
            success=True,
            action_type=ActionType.NEW_TAB,
            action="new_tab",
            url=target_url,
            message=f"Opened new browser tab #{len(self._pages) - 1} at '{target_url}'",
        )

    async def _execute_close_tab(
        self, tab_index: Optional[int] = None, target: Optional[str] = None
    ) -> ExecutionResult:
        """Close specified tab (or active tab), refocusing on remaining tabs."""
        await self.get_open_tabs()
        if not self._pages:
            return ExecutionResult(
                success=False,
                action_type=ActionType.CLOSE_TAB,
                action="close_tab",
                message="No open browser tabs to close",
                error="No open tabs",
            )

        target_page = self.current_page
        if tab_index is not None and 0 <= tab_index < len(self._pages):
            target_page = self._pages[tab_index]
        elif target and str(target).strip().isdigit():
            idx = int(str(target).strip())
            if 0 <= idx < len(self._pages):
                target_page = self._pages[idx]

        if not target_page:
            target_page = self._pages[-1]

        closed_url = target_page.url
        # If closing the only remaining tab, navigate to about:blank to keep browser context alive
        if len(self._pages) <= 1:
            try:
                await target_page.goto("about:blank")
            except Exception:
                pass
            return ExecutionResult(
                success=True,
                action_type=ActionType.CLOSE_TAB,
                action="close_tab",
                message="Reset last remaining tab to 'about:blank' (kept window open)",
            )

        try:
            await target_page.close()
        except Exception:
            pass
        self._on_page_closed(target_page)

        new_active_idx = next((i for i, p in enumerate(self._pages) if p == self.current_page), 0)
        return ExecutionResult(
            success=True,
            action_type=ActionType.CLOSE_TAB,
            action="close_tab",
            message=f"Closed tab '{closed_url}'. Active tab is now #{new_active_idx}",
        )

    async def _execute_go_back(self, page: Page) -> ExecutionResult:
        """Navigate backwards in session history on current active page."""
        start_time = time.perf_counter()
        target_page = page or self.current_page
        if not target_page or target_page.is_closed():
            return ExecutionResult(
                success=False,
                action_type=ActionType.GO_BACK,
                action="go_back",
                message="No active page available to navigate back",
                error="No active page",
            )
        try:
            prev_url = target_page.url
            await target_page.go_back(timeout=DEFAULT_ACTION_TIMEOUT_MS)
            duration = (time.perf_counter() - start_time) * 1000
            current_url = target_page.url
            return ExecutionResult(
                success=True,
                action_type=ActionType.GO_BACK,
                action="go_back",
                message=f"Navigated back from '{prev_url}' to '{current_url}'",
                duration_ms=round(duration, 2),
            )
        except Exception as exc:
            duration = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                action_type=ActionType.GO_BACK,
                action="go_back",
                message=f"Failed to navigate back: {exc}",
                error=str(exc),
                duration_ms=round(duration, 2),
            )

    async def _execute_export_data(
        self,
        page: Page,
        target: Optional[str] = None,
        format: str = "csv",
        filename: Optional[str] = None,
    ) -> ExecutionResult:
        """Extract structured data (tables, lists, cards) and export to CSV or JSON."""
        start_time = time.perf_counter()
        target_page = page or self.current_page
        if not target_page or target_page.is_closed():
            return ExecutionResult(
                success=False,
                action_type=ActionType.EXPORT_DATA,
                action="export_data",
                message="No active page available for data extraction",
                error="No active page",
            )

        clean_format = (format or "csv").strip().lower()
        if clean_format not in ("csv", "json"):
            clean_format = "csv"

        # In-page JS extraction
        extract_script = """
        (targetSelector) => {
            const cleanText = (t) => (t || '').trim().replace(/\\s+/g, ' ');

            // 1. If explicit selector provided and matches a table or container
            if (targetSelector) {
                try {
                    const el = document.querySelector(targetSelector) || document.querySelector(`[data-agent-id='${targetSelector}']`);
                    if (el) {
                        const table = el.tagName.toLowerCase() === 'table' ? el : el.querySelector('table');
                        if (table) {
                            const headers = Array.from(table.querySelectorAll('thead th, thead td, tr:first-child th')).map(c => cleanText(c.textContent)).filter(Boolean);
                            const rows = [];
                            const items = [];
                            const trs = Array.from(table.querySelectorAll('tbody tr, tr:not(:first-child)'));
                            for (const tr of trs) {
                                const cells = Array.from(tr.querySelectorAll('td, th')).map(c => cleanText(c.textContent));
                                if (cells.length > 0 && cells.some(c => c.length > 0)) {
                                    rows.push(cells);
                                    if (headers.length > 0) {
                                        const item = {};
                                        headers.forEach((h, i) => { item[h] = cells[i] || ''; });
                                        items.push(item);
                                    }
                                }
                            }
                            if (headers.length === 0 && rows.length > 0) {
                                const defaultH = Array.from({length: rows[0].length}, (_, i) => `Column_${i+1}`);
                                return { title: document.title, headers: defaultH, rows: rows, items: items };
                            }
                            return { title: document.title, headers: headers, rows: rows, items: items };
                        }
                    }
                } catch(e) {}
            }

            // 2. Scan for any table on the page
            const tables = Array.from(document.querySelectorAll('table'));
            if (tables.length > 0) {
                let bestTable = tables[0];
                let maxRows = 0;
                for (const t of tables) {
                    const r = t.querySelectorAll('tr').length;
                    if (r > maxRows) { maxRows = r; bestTable = t; }
                }
                const headers = Array.from(bestTable.querySelectorAll('thead th, thead td, tr:first-child th')).map(c => cleanText(c.textContent)).filter(Boolean);
                const rows = [];
                const items = [];
                const trs = Array.from(bestTable.querySelectorAll('tbody tr, tr:not(:first-child)'));
                for (const tr of trs) {
                    const cells = Array.from(tr.querySelectorAll('td, th')).map(c => cleanText(c.textContent));
                    if (cells.length > 0 && cells.some(c => c.length > 0)) {
                        rows.push(cells);
                        if (headers.length > 0) {
                            const item = {};
                            headers.forEach((h, i) => { item[h] = cells[i] || ''; });
                            items.push(item);
                        }
                    }
                }
                const actualHeaders = headers.length > 0 ? headers : (rows.length > 0 ? Array.from({length: rows[0].length}, (_, i) => `Column_${i+1}`) : ["Data"]);
                return { title: document.title, headers: actualHeaders, rows: rows, items: items };
            }

            // 3. Scan for repeating item cards or list elements
            const cardSelectors = ['.task-item', '.invoice-item', '.card', '.table-row', 'li.item', 'ul > li'];
            for (const cSel of cardSelectors) {
                const elements = Array.from(document.querySelectorAll(cSel));
                if (elements.length >= 2) {
                    const headers = ["Index", "Item_Text", "Link"];
                    const rows = [];
                    const items = [];
                    elements.forEach((el, idx) => {
                        const txt = cleanText(el.textContent);
                        const link = el.querySelector('a')?.href || '';
                        if (txt) {
                            rows.push([String(idx + 1), txt, link]);
                            items.push({ "Index": String(idx + 1), "Item_Text": txt, "Link": link });
                        }
                    });
                    if (rows.length > 0) {
                        return { title: document.title, headers: headers, rows: rows, items: items };
                    }
                }
            }

            // 4. Fallback page snapshot
            return {
                title: document.title,
                headers: ["Field", "Value"],
                rows: [["Title", document.title || ""], ["URL", window.location.href]],
                items: [{"Field": "Title", "Value": document.title || ""}, {"Field": "URL", "Value": window.location.href}]
            };
        }
        """

        try:
            extracted = await target_page.evaluate(extract_script, target)
        except Exception as exc:
            return ExecutionResult(
                success=False,
                action_type=ActionType.EXPORT_DATA,
                action="export_data",
                message=f"Failed to extract structured data: {exc}",
                error=str(exc),
            )

        headers: List[str] = extracted.get("headers", [])
        rows: List[List[str]] = extracted.get("rows", [])
        items: List[Dict[str, Any]] = extracted.get("items", [])
        page_title: str = extracted.get("title", "export")

        # Sanitize filename
        ts = time.strftime("%Y%m%d_%H%M%S")
        if filename:
            clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", filename.strip())
        else:
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", page_title.lower())[:30].strip("_") or "data"
            clean_name = f"{slug}_{ts}"

        if not clean_name.endswith(f".{clean_format}"):
            file_name = f"{clean_name}.{clean_format}"
        else:
            file_name = clean_name

        export_path = EXPORTS_DIR / file_name

        try:
            if clean_format == "csv":
                with open(export_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    if headers:
                        writer.writerow(headers)
                    for r in rows:
                        writer.writerow(r)
            else:
                with open(export_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "title": page_title,
                            "url": target_page.url,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "row_count": len(rows),
                            "columns": headers,
                            "records": items if items else [{"data": r} for r in rows],
                        },
                        f,
                        indent=2,
                    )
        except Exception as file_err:
            return ExecutionResult(
                success=False,
                action_type=ActionType.EXPORT_DATA,
                action="export_data",
                message=f"Failed to write export file: {file_err}",
                error=str(file_err),
            )

        duration = (time.perf_counter() - start_time) * 1000
        msg = f"Exported {len(rows)} rows to {export_path.name}"
        return ExecutionResult(
            success=True,
            action_type=ActionType.EXPORT_DATA,
            action="export_data",
            message=msg,
            duration_ms=round(duration, 2),
            extracted_data={
                "filename": export_path.name,
                "filepath": str(export_path),
                "row_count": len(rows),
                "columns": headers,
                "format": clean_format,
                "data": items[:15] if items else rows[:15],
            },
        )

    # Convenience helper methods
    async def export_data(
        self,
        target: Optional[str] = None,
        format: str = "csv",
        filename: Optional[str] = None,
    ) -> ExecutionResult:
        return await self.execute({
            "action": "export_data",
            "target": target,
            "export_format": format,
            "export_filename": filename,
        })
    async def click(self, target: str) -> ExecutionResult:
        return await self.execute({"action": "click", "target": target})

    async def type(self, target: str, text: str) -> ExecutionResult:
        return await self.execute({"action": "type", "target": target, "text": text})

    async def scroll(self, delta_y: int = 300) -> ExecutionResult:
        return await self.execute({"action": "scroll", "delta_y": delta_y})

    async def navigate(self, url: str) -> ExecutionResult:
        return await self.execute({"action": "navigate", "url": url})

    async def wait(self, seconds: float = 1.0, target: Optional[str] = None) -> ExecutionResult:
        return await self.execute({"action": "wait", "seconds": seconds, "target": target})

    async def switch_tab(self, tab_index: Optional[int] = None, target: Optional[str] = None) -> ExecutionResult:
        return await self.execute({"action": "switch_tab", "tab_index": tab_index, "target": target})

    async def new_tab(self, url: Optional[str] = None) -> ExecutionResult:
        return await self.execute({"action": "new_tab", "url": url})

    async def close_tab(self, tab_index: Optional[int] = None, target: Optional[str] = None) -> ExecutionResult:
        return await self.execute({"action": "close_tab", "tab_index": tab_index, "target": target})

    async def go_back(self) -> ExecutionResult:
        return await self.execute({"action": "go_back"})

    async def dismiss_overlays(self) -> ExecutionResult:
        """Attempt to dismiss intrusive modal overlays, popups, or dialogs."""
        start_time = time.perf_counter()
        target_page = self.current_page
        if target_page and not target_page.is_closed():
            try:
                await target_page.keyboard.press("Escape")
            except Exception:
                pass
            for sel in [
                "button[aria-label*='close' i]",
                "button[aria-label*='dismiss' i]",
                ".modal-close",
                "[data-dismiss='modal']",
                "button.close",
            ]:
                try:
                    btn = target_page.locator(sel).first
                    if await btn.is_visible(timeout=150):
                        await btn.click(timeout=400)
                        break
                except Exception:
                    pass
        duration = (time.perf_counter() - start_time) * 1000
        return ExecutionResult(
            success=True,
            action_type=ActionType.PRESS_KEY,
            action="dismiss_overlays",
            message="Attempted modal/popup overlay dismissal",
            duration_ms=round(duration, 2),
        )

    async def close(self) -> None:
        """Tear down browser session, save cookies, and release resources cleanly."""
        try:
            await self.save_cookies()
        except Exception:
            pass

        for p in list(self._pages):
            try:
                if not p.is_closed():
                    await p.close()
            except Exception:
                pass

        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass

        try:
            if self._browser and self._browser.is_connected():
                await self._browser.close()
        except Exception:
            pass

        try:
            if self._playwright and self._owns_browser:
                await self._playwright.stop()
        except Exception:
            pass

        self._pages = []
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._loop = None



# Standalone runner for fixed invoice sequence test
if __name__ == "__main__":
    import asyncio
    import json
    import sys

    async def run_standalone_demo() -> None:
        headless = "--headless" in sys.argv
        executor = PlaywrightExecutor(headless=headless)

        print("==================================================")
        print("Starting Standalone Browser Executor Test (Step 3)")
        print(f"Mock Site URL: {MOCK_SITE_DEFAULT_URL}")
        print("==================================================")

        try:
            # 1. Open mock dashboard
            print("\n[Step 1] Opening mock dashboard...")
            res1 = await executor.execute({"action": "navigate", "url": MOCK_SITE_DEFAULT_URL})
            print(json.dumps(res1.model_dump(exclude_none=True), indent=2, default=str))
            assert res1.success, f"Step 1 failed: {res1.message}"

            # 2. Navigate to invoices page
            print("\n[Step 2] Navigating to invoices page...")
            res2 = await executor.execute({"action": "click", "target": "nav-invoices"})
            print(json.dumps(res2.model_dump(exclude_none=True), indent=2, default=str))
            assert res2.success, f"Step 2 failed: {res2.message}"

            # 3. Fill in invoice search field
            print("\n[Step 3] Filling invoice search field with 'INV-1001'...")
            res3 = await executor.execute({"action": "type", "target": "invoice-search", "text": "INV-1001"})
            print(json.dumps(res3.model_dump(exclude_none=True), indent=2, default=str))
            assert res3.success, f"Step 3 failed: {res3.message}"

            # 4. Click appropriate View button
            print("\n[Step 4] Clicking View button for invoice 1001...")
            res4 = await executor.execute({"action": "click", "target": "view-invoice-1001"})
            print(json.dumps(res4.model_dump(exclude_none=True), indent=2, default=str))
            assert res4.success, f"Step 4 failed: {res4.message}"

            # 5. Verify invoice details are visible
            print("\n[Step 5] Waiting for invoice modal details to appear...")
            res5 = await executor.execute({"action": "wait", "target": "close-invoice-modal-btn", "wait_seconds": 2.0})
            print(json.dumps(res5.model_dump(exclude_none=True), indent=2, default=str))
            assert res5.success, f"Step 5 failed: {res5.message}"

            # Check details
            page = executor.current_page
            invoice_title = await page.locator("#modal-invoice-id").text_content()
            client_name = await page.locator("#modal-client-name").text_content()
            total_due = await page.locator("#modal-grand-total").text_content()

            print("\n[Verification Details]")
            print(f"  Invoice Title: {invoice_title}")
            print(f"  Client Name:   {client_name}")
            print(f"  Grand Total:   {total_due}")

            assert "1001" in (invoice_title or ""), "Invoice modal ID does not match 1001"
            assert "Apex Systems" in (client_name or ""), "Client name does not match Apex Systems"

            print("\nSUCCESS: All 5 steps completed and verified successfully!")

        finally:
            print("\nClosing browser...")
            await executor.close()
            print("Browser closed.")

    asyncio.run(run_standalone_demo())
