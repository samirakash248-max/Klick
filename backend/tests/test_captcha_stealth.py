"""Tests for anti-detection stealth configuration and CAPTCHA detection/handling."""

import pytest
from backend.app.executor import PlaywrightExecutor, STEALTH_INIT_SCRIPT
from backend.app.schemas import ActionType, BrowserAction


@pytest.mark.asyncio
async def test_stealth_browser_properties_injected():
    """Verify anti-detection stealth scripts properly mask automation signatures."""
    async with PlaywrightExecutor(headless=True) as executor:
        page = executor.current_page
        assert page is not None

        # Verify navigator.webdriver is masked (undefined)
        is_webdriver = await page.evaluate("() => navigator.webdriver !== undefined")
        assert is_webdriver is False, "navigator.webdriver should be undefined under stealth"

        # Verify window.chrome runtime is present
        has_chrome = await page.evaluate("() => Boolean(window.chrome && window.chrome.runtime)")
        assert has_chrome is True, "window.chrome.runtime should be defined"

        # Verify navigator.plugins is non-empty
        plugin_count = await page.evaluate("() => navigator.plugins.length")
        assert plugin_count > 0, "navigator.plugins should not be empty"

        # Verify navigator.languages is populated
        languages = await page.evaluate("() => navigator.languages")
        assert len(languages) > 0, "navigator.languages should contain valid locales"


@pytest.mark.asyncio
async def test_detect_captcha_on_normal_page():
    """Verify normal pages are reported as no captcha present."""
    async with PlaywrightExecutor(headless=True) as executor:
        page = executor.current_page
        await page.set_content("<html><body><h1>Welcome to BrowserPilot</h1></body></html>")

        status = await executor.detect_captcha(page)
        assert status["present"] is False
        assert status["type"] == "none"


@pytest.mark.asyncio
async def test_detect_and_handle_google_consent():
    """Verify Google cookie consent wall is detected and automatically dismissed."""
    async with PlaywrightExecutor(headless=True) as executor:
        page = executor.current_page
        # Emulate Google consent modal HTML
        await page.set_content("""
        <html>
          <head><title>Before you continue to Google</title></head>
          <body>
            <div id="consent-dialog">
              <button id="L2AGLb" onclick="this.style.display='none'; document.getElementById('consent-dialog').remove();">Accept all</button>
            </div>
          </body>
        </html>
        """)

        # Check detection
        status = await executor.detect_captcha(page)
        assert status["present"] is True
        assert status["type"] == "google_consent"

        # Check handling
        handled = await executor.handle_captcha(page, timeout_seconds=2.0)
        assert handled is True

        # Check that consent modal button was clicked and removed
        rem = await page.locator("#L2AGLb").count()
        assert rem == 0


@pytest.mark.asyncio
async def test_detect_cloudflare_turnstile():
    """Verify Cloudflare Turnstile presence is correctly detected."""
    async with PlaywrightExecutor(headless=True) as executor:
        page = executor.current_page
        await page.set_content("""
        <html>
          <head><title>Just a moment...</title></head>
          <body>
            <div id="challenge-stage">
              <div class="cf-turnstile"></div>
            </div>
          </body>
        </html>
        """)

        status = await executor.detect_captcha(page)
        assert status["present"] is True
        assert "cloudflare" in status["type"]


@pytest.mark.asyncio
async def test_detect_google_unusual_traffic():
    """Verify Google sorry/index unusual traffic challenge is correctly detected."""
    async with PlaywrightExecutor(headless=True) as executor:
        page = executor.current_page
        await page.set_content("""
        <html>
          <head><title>Sorry...</title></head>
          <body>
            <h1>Our systems have detected unusual traffic from your computer network.</h1>
          </body>
        </html>
        """)

        status = await executor.detect_captcha(page)
        assert status["present"] is True
        assert status["type"] == "google_unusual_traffic"
