"""Page observer module using Playwright.

Responsible for inspecting the DOM, identifying interactive elements,
and distilling the page state into structured PageObservations and element lists.

Owned by: Browser Automation Engineer (Member B)
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from .schemas import ElementCoordinates, ElementDescriptor, PageObservation


class PlaywrightObserver:
    """Extracts structured observations from an active Playwright page.
    
    Supports launching visible Chromium, navigating to target web pages,
    inspecting visible interactive elements, extracting data-agent-id attributes,
    and generating LLM-ready DOM summaries.
    """

    def __init__(
        self,
        browser: Optional[Browser] = None,
        context: Optional[BrowserContext] = None,
        page: Optional[Page] = None,
        max_snippet_length: int = 1500,
    ) -> None:
        self.browser = browser
        self.context = context
        self.page = page
        self.max_snippet_length = max_snippet_length
        self._playwright_instance: Optional[Playwright] = None
        self._owns_browser: bool = False

    async def __aenter__(self) -> "PlaywrightObserver":
        if not self.page:
            await self.launch(headless=False)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def launch(
        self,
        headless: bool = False,
        slow_mo: int = 0,
        viewport: Optional[Dict[str, int]] = None,
    ) -> Page:
        """Open Chromium in visible (or headless) mode and return a new Page."""
        if self._playwright_instance is None:
            self._playwright_instance = await async_playwright().start()

        viewport_size = viewport or {"width": 1440, "height": 900}

        try:
            # First attempt: standard Playwright bundled Chromium
            self.browser = await self._playwright_instance.chromium.launch(
                headless=headless,
                slow_mo=slow_mo,
            )
        except Exception:
            # Fallback: system-installed Chrome or Edge on Windows
            try:
                self.browser = await self._playwright_instance.chromium.launch(
                    channel="chrome",
                    headless=headless,
                    slow_mo=slow_mo,
                )
            except Exception:
                self.browser = await self._playwright_instance.chromium.launch(
                    channel="msedge",
                    headless=headless,
                    slow_mo=slow_mo,
                )

        self.context = await self.browser.new_context(viewport=viewport_size)
        self.page = await self.context.new_page()
        self._owns_browser = True
        return self.page

    async def navigate(self, url: str) -> None:
        """Navigate the active browser page to the specified URL."""
        target_page = self._resolve_page(None)
        await target_page.goto(url, wait_until="domcontentloaded")
        if "#" in url:
            hash_part = url.split("#", 1)[1]
            try:
                await target_page.evaluate(f"if (typeof window.navigateTo === 'function') window.navigateTo('{hash_part}')")
            except Exception:
                pass

    def _resolve_page(self, page: Optional[Page]) -> Page:
        active_page = page or self.page
        if not active_page:
            raise RuntimeError(
                "No active page available. Call `launch()` first or provide a `page` argument."
            )
        return active_page

    async def observe_structured(self, page: Optional[Page] = None) -> Dict[str, Any]:
        """Return a structured observation matching the required format:
        
        {
          "url": str,
          "title": str,
          "elements": [
            {
              "agent_id": str | None,
              "tag": str,
              "role": str | None,
              "text": str,
              "type": str | None
            }
          ]
        }
        """
        active_page = self._resolve_page(page)
        url = active_page.url
        title = await active_page.title()
        raw_elements = await self._query_visible_dom_elements(active_page)

        elements_list = [
            {
                "agent_id": el.get("agent_id"),
                "tag": el.get("tag"),
                "role": el.get("role"),
                "text": el.get("text", ""),
                "type": el.get("type"),
            }
            for el in raw_elements
        ]

        open_tabs = []
        active_idx = 0
        if active_page and active_page.context:
            for i, p in enumerate(active_page.context.pages):
                if not p.is_closed():
                    is_active = (p == active_page)
                    if is_active:
                        active_idx = i
                    open_tabs.append({
                        "index": i,
                        "url": p.url or "",
                        "is_active": is_active,
                    })

        return {
            "url": url,
            "title": title,
            "elements": elements_list,
            "open_tabs": open_tabs,
            "active_tab_index": active_idx,
        }

    async def observe(self, page: Optional[Page] = None) -> PageObservation:
        """Capture the current state of the page into a PageObservation.
        
        Extracts title, URL, interactive elements, text snippet, and DOM summary.
        Maintains backward compatibility with agent_loop and schemas.
        """
        try:
            active_page = self._resolve_page(page)
            if active_page.is_closed():
                return PageObservation(url="about:blank", title="Closed", interactive_elements=[])
            url = active_page.url
            title = await active_page.title()

            # Extract open tabs
            open_tabs = []
            active_idx = 0
            if active_page and active_page.context:
                for i, p in enumerate(active_page.context.pages):
                    if not p.is_closed():
                        is_active = (p == active_page)
                        if is_active:
                            active_idx = i
                        open_tabs.append({
                            "index": i,
                            "url": p.url or "",
                            "title": p.url or f"Tab {i + 1}",
                            "is_active": is_active,
                        })

            # Extract visible interactive elements
            raw_elements = await self._query_visible_dom_elements(active_page)
            interactive_elements = self._build_element_descriptors(raw_elements)

            # Build token-efficient DOM summary for LLM
            dom_summary = self._build_dom_summary(interactive_elements)

            # Extract visible text snippet
            snippet = await self._extract_text_snippet(active_page)

            return PageObservation(
                url=url,
                title=title,
                dom_summary=dom_summary,
                interactive_elements=interactive_elements,
                page_text_snippet=snippet,
                open_tabs=open_tabs,
                active_tab_index=active_idx,
            )
        except Exception as exc:
            if "Target page, context or browser has been closed" in str(exc) or "TargetClosedError" in str(type(exc)) or (page and getattr(page, "is_closed", lambda: False)()):
                logger.warning("Browser page was closed during observation.")
                return PageObservation(url="about:blank", title="Browser Closed", interactive_elements=[])
            raise


    async def _query_visible_dom_elements(self, page: Page) -> List[Dict[str, Any]]:
        """Execute in-page JavaScript to extract visible interactive elements and data-agent-ids."""
        js_script = """
        () => {
            const selectorQuery = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [data-agent-id]';
            const candidates = document.querySelectorAll(selectorQuery);
            const results = [];
            const seenIds = new Set();
            
            for (let i = 0; i < candidates.length; i++) {
                const el = candidates[i];
                
                // 1. Check direct & parent visibility natively
                if (typeof el.checkVisibility === 'function') {
                    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
                        continue;
                    }
                } else {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        continue;
                    }
                    if (el.offsetParent === null && style.position !== 'fixed') {
                        continue;
                    }
                }
                
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) {
                    continue;
                }
                
                // 3. Extract attributes
                const tag = el.tagName.toLowerCase();
                let agentId = el.getAttribute('data-agent-id');
                const domId = el.id || null;
                if (!agentId) {
                    if (domId && !seenIds.has('id-' + domId)) {
                        agentId = 'id-' + domId;
                    } else {
                        agentId = 'elem-' + results.length;
                    }
                    try {
                        el.setAttribute('data-agent-id', agentId);
                    } catch (e) {}
                }
                seenIds.add(agentId);
                const rawType = el.getAttribute('type');
                const inputType = tag === 'input' ? (rawType || 'text').toLowerCase() : null;
                const href = (tag === 'a' ? (el.getAttribute('href') || '') : '') || null;
                
                // 4. Compute role
                let role = el.getAttribute('role') || null;
                if (!role) {
                    if (tag === 'button') role = 'button';
                    else if (tag === 'a') role = 'link';
                    else if (tag === 'select') role = 'combobox';
                    else if (tag === 'textarea') role = 'textbox';
                    else if (tag === 'input') {
                        if (['text', 'search', 'email', 'url', 'tel', 'password', 'number'].includes(inputType)) {
                            role = 'textbox';
                        } else if (inputType === 'checkbox') {
                            role = 'checkbox';
                        } else if (inputType === 'radio') {
                            role = 'radio';
                        } else if (['submit', 'button', 'reset'].includes(inputType)) {
                            role = 'button';
                        } else {
                            role = 'textbox';
                        }
                    }
                }
                
                // 5. Compute readable text or placeholder
                let text = '';
                if (tag === 'input' || tag === 'textarea') {
                    text = el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || '';
                } else if (tag === 'select') {
                    const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    text = selected ? selected.text : '';
                } else {
                    text = (el.innerText || el.textContent || '').trim();
                }
                
                if (text.length > 80) {
                    text = text.substring(0, 80) + '...';
                }
                
                // 6. Build recommended selector
                let selector = '';
                if (agentId) {
                    selector = `[data-agent-id='${agentId}']`;
                } else if (domId) {
                    selector = `#${domId}`;
                } else {
                    selector = `${tag}:nth-of-type(${i + 1})`;
                }
                
                results.push({
                    agent_id: agentId,
                    tag: tag,
                    role: role,
                    text: text,
                    type: inputType,
                    href: href,
                    id: domId,
                    selector: selector,
                    aria_label: el.getAttribute('aria-label') || null,
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                });
            }
            return results;
        }
        """
        try:
            return await page.evaluate(js_script)
        except Exception:
            return []

    def _build_element_descriptors(self, raw_elements: List[Dict[str, Any]]) -> List[ElementDescriptor]:
        """Convert raw JS element dictionaries into Pydantic ElementDescriptors."""
        descriptors = []
        for el in raw_elements:
            coords = None
            if el.get("width", 0) > 0 and el.get("height", 0) > 0:
                coords = ElementCoordinates(
                    x=float(el.get("x", 0)),
                    y=float(el.get("y", 0)),
                    width=float(el.get("width", 0)),
                    height=float(el.get("height", 0)),
                )

            # Store agent_id in id if standard dom id is not available
            effective_id = el.get("agent_id") or el.get("id")

            descriptors.append(
                ElementDescriptor(
                    id=effective_id,
                    tag_name=el.get("tag", "div"),
                    selector=el.get("selector", ""),
                    text=el.get("text") or None,
                    role=el.get("role"),
                    aria_label=el.get("aria_label"),
                    is_interactive=True,
                    coordinates=coords,
                    href=el.get("href"),
                )
            )
        return descriptors

    def _build_dom_summary(self, elements: List[ElementDescriptor]) -> str:
        """Format interactive elements into a concise markdown list for the model."""
        lines = []
        if len(elements) > 25:
            def sort_key(el: ElementDescriptor):
                tag = el.tag_name.lower()
                label = (el.text or el.aria_label or el.id or "").lower()
                if tag in ("input", "textarea", "select") or "search" in label:
                    return 0
                if tag == "button" or "btn" in label or "play" in label or "video" in label:
                    return 1
                if tag == "a" and el.text:
                    return 2
                return 3

            filtered = sorted(elements, key=sort_key)[:25]
        else:
            filtered = elements

        for i, el in enumerate(filtered):
            label = el.text or el.aria_label or el.id or "unlabeled"
            href_part = f" [href='{el.href}']" if getattr(el, "href", None) else ""
            lines.append(f"[{i}] <{el.tag_name}> '{label}'{href_part} -> selector: `{el.selector}`")
        return "\n".join(lines)

    async def _extract_text_snippet(self, page: Page) -> str:
        """Extract main body text truncated for token limits."""
        try:
            body_text = await page.evaluate("() => (document.body ? document.body.innerText.slice(0, 500) : '')")
            return (body_text or "").strip()
        except Exception:
            return ""

    async def close(self) -> None:
        """Close browser, context, and Playwright session cleanly without errors."""
        try:
            if self.page and not self.page.is_closed():
                await self.page.close()
        except Exception:
            pass

        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

        try:
            if self.browser and self.browser.is_connected():
                await self.browser.close()
        except Exception:
            pass

        try:
            if self._playwright_instance:
                await self._playwright_instance.stop()
                self._playwright_instance = None
        except Exception:
            pass

        self.page = None
        self.context = None
        self.browser = None
        self._owns_browser = False


# Standalone runner for testing observer independently
if __name__ == "__main__":
    import asyncio
    import sys

    async def run_observer_demo() -> None:
        mock_site_file = Path(__file__).resolve().parent.parent.parent / "mock-site" / "index.html"
        test_url = sys.argv[1] if len(sys.argv) > 1 else f"file:///{mock_site_file.as_posix()}#invoices"

        print(f"==================================================")
        print(f"Launching visible Chromium observer...")
        print(f"Navigating to: {test_url}")
        print(f"==================================================")

        observer = PlaywrightObserver()
        try:
            # 1. Launch visible Chromium
            await observer.launch(headless=False)

            # 2. Navigate to mock website
            await observer.navigate(test_url)
            if observer.page:
                await observer.page.wait_for_timeout(300)

            # 3. Read page title, URL, and interactive elements
            obs = await observer.observe_structured()

            print(f"\nPage URL:   {obs['url']}")
            print(f"Page Title: {obs['title']}")
            print(f"Detected {len(obs['elements'])} visible interactive elements.\n")

            # Pretty print elements
            print("Detected Elements Sample:")
            print(json.dumps(obs["elements"][:10], indent=2))

            # Confirm critical elements
            agent_ids = [el["agent_id"] for el in obs["elements"] if el["agent_id"]]
            print(f"\nDetected agent IDs: {agent_ids}")

            has_search = any("invoice-search" in (el["agent_id"] or "") for el in obs["elements"])
            has_view = any("view-invoice" in (el["agent_id"] or "") for el in obs["elements"])

            print(f"\nInvoice search detected: {has_search}")
            print(f"View buttons detected:   {has_view}")

            # If opened without hash and search was not found, check invoices tab automatically
            if not has_search and "#" not in test_url:
                print("\nNavigating to #invoices to observe invoice elements...")
                await observer.navigate(f"{test_url}#invoices")
                if observer.page:
                    await observer.page.wait_for_timeout(300)
                obs_inv = await observer.observe_structured()
                has_search = any("invoice-search" in (el["agent_id"] or "") for el in obs_inv["elements"])
                has_view = any("view-invoice" in (el["agent_id"] or "") for el in obs_inv["elements"])
                print(f"Invoice search detected on #invoices: {has_search}")
                print(f"View buttons detected on #invoices:   {has_view}")

        finally:
            print("\nClosing browser cleanly...")
            await observer.close()
            print("Browser closed successfully!")

    asyncio.run(run_observer_demo())
