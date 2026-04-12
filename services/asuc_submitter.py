"""
ASUC Portal Browser Automation — Playwright tool wrappers.

This module provides a thin, stateful wrapper around a Playwright browser
instance.  Each public method maps directly to a tool that the Finance
Submission Agent (Claude) can call to navigate the ASUC reimbursement portal.

Design
──────
• One `ASUCSubmitter` instance owns a single browser context for the entire
  submission session.
• Every action returns a plain dict that is safe to pass straight back to
  Claude as a tool result.
• Screenshots are returned as base64-encoded PNG strings so Claude (vision)
  can see the current page state.

CalNet SSO
──────────
The ASUC portal is protected by Berkeley's Central Authentication Service.
`login_calnet()` handles the standard CAS flow:
  1. Navigate to the portal — CAS redirects to login.berkeley.edu
  2. Fill in username / password and submit
  3. Handle the optional Duo 2FA push (waits up to 60 s for approval)
  4. Follow the service-ticket redirect back to the portal

Configure ASUC_CALNET_USERNAME, ASUC_CALNET_PASSWORD, and optionally
ASUC_HEADLESS=false in .env.
"""

import base64
import logging
import os
from typing import Any

import config

logger = logging.getLogger(__name__)


class ASUCSubmitter:
    """
    Stateful Playwright session for interacting with the ASUC finance portal.

    Usage:
        async with ASUCSubmitter() as sub:
            result = await sub.navigate(config.ASUC_PORTAL_URL)
            result = await sub.login_calnet()
            ...
    """

    def __init__(self) -> None:
        self._browser = None
        self._context = None
        self._page = None

    # ── Context manager ───────────────────────────────────────

    async def __aenter__(self) -> "ASUCSubmitter":
        from playwright.async_api import async_playwright  # lazy import

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=config.ASUC_HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            accept_downloads=True,
        )
        self._page = await self._context.new_page()
        logger.info("Playwright browser started (headless=%s).", config.ASUC_HEADLESS)
        return self

    async def __aexit__(self, *_) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        logger.info("Playwright browser closed.")

    # ── Internal helpers ──────────────────────────────────────

    async def _screenshot_b64(self) -> str:
        png = await self._page.screenshot(full_page=False)
        return base64.b64encode(png).decode()

    def _ok(self, **kwargs) -> dict[str, Any]:
        return {"success": True, **kwargs}

    def _err(self, message: str, **kwargs) -> dict[str, Any]:
        return {"success": False, "error": message, **kwargs}

    # ── Tool methods (called by the Claude agent) ─────────────

    async def screenshot(self) -> dict[str, Any]:
        """
        Capture the current page as a base64 PNG.
        Claude uses this to understand what is on the screen.
        """
        try:
            b64 = await self._screenshot_b64()
            url = self._page.url
            return self._ok(screenshot_b64=b64, current_url=url)
        except Exception as exc:
            return self._err(f"Screenshot failed: {exc}")

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to *url* and wait for the network to be idle."""
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=30_000)
            b64 = await self._screenshot_b64()
            return self._ok(current_url=self._page.url, screenshot_b64=b64)
        except Exception as exc:
            return self._err(f"Navigation to {url} failed: {exc}")

    async def login_calnet(self) -> dict[str, Any]:
        """
        Handle the Berkeley CalNet CAS login flow.

        Steps:
          1. Fill in username and password on login.berkeley.edu
          2. Submit the form
          3. Wait up to 60 s for a Duo push approval (if prompted)
          4. Confirm we're back on the ASUC portal

        Prerequisites: the current page must already be on the CAS login page
        (i.e., navigate to the portal URL first so CAS redirects us here).
        """
        try:
            page = self._page

            # ── Step 1: fill credentials ───────────────────────
            await page.fill("#username", config.ASUC_CALNET_USERNAME)
            await page.fill("#password", config.ASUC_CALNET_PASSWORD)
            await page.click('input[type="submit"], button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # ── Step 2: handle Duo 2FA if present ────────────
            # Duo renders an iframe; if it appears we wait for the push approval.
            current_url = page.url
            if "duosecurity.com" in current_url or "duo" in current_url.lower():
                logger.info("Duo 2FA detected — waiting for push approval (up to 60s)…")
                # Wait for the page to leave the Duo domain
                await page.wait_for_function(
                    "() => !window.location.href.includes('duo')",
                    timeout=60_000,
                )
                await page.wait_for_load_state("networkidle", timeout=15_000)

            b64 = await self._screenshot_b64()
            final_url = page.url
            logger.info("CalNet login complete. Now at: %s", final_url)
            return self._ok(
                current_url=final_url,
                screenshot_b64=b64,
                note=(
                    "Login succeeded. If Duo was required, the push was approved."
                    if "duosecurity.com" not in final_url
                    else "Still on Duo page — push may not have been approved yet."
                ),
            )
        except Exception as exc:
            return self._err(f"CalNet login failed: {exc}")

    async def click(
        self,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int = 10_000,
    ) -> dict[str, Any]:
        """
        Click an element identified by a CSS *selector* or by visible *text*.
        Provide exactly one of the two parameters.
        """
        try:
            page = self._page
            if selector:
                await page.click(selector, timeout=timeout_ms)
            elif text:
                await page.get_by_text(text, exact=False).first.click(timeout=timeout_ms)
            else:
                return self._err("Provide either 'selector' or 'text'.")
            await page.wait_for_load_state("networkidle", timeout=10_000)
            b64 = await self._screenshot_b64()
            return self._ok(current_url=page.url, screenshot_b64=b64)
        except Exception as exc:
            return self._err(f"Click failed: {exc}", selector=selector, text=text)

    async def fill(
        self,
        selector: str,
        value: str,
        clear_first: bool = True,
    ) -> dict[str, Any]:
        """Fill an input or textarea identified by *selector* with *value*."""
        try:
            page = self._page
            if clear_first:
                await page.fill(selector, "")
            await page.fill(selector, value)
            return self._ok(selector=selector, value_length=len(value))
        except Exception as exc:
            return self._err(f"Fill failed on '{selector}': {exc}")

    async def select_option(self, selector: str, value: str) -> dict[str, Any]:
        """Select an <option> from a <select> element by value or label."""
        try:
            await self._page.select_option(selector, value)
            return self._ok(selector=selector, selected=value)
        except Exception as exc:
            return self._err(f"select_option failed on '{selector}': {exc}")

    async def upload_file(self, selector: str, file_path: str) -> dict[str, Any]:
        """
        Set the file path on a file input element.

        *file_path* must be an absolute path to an existing file on disk.
        Receipts should be downloaded / stored in the ASUC_RECEIPTS_DIR
        directory before calling this tool.
        """
        if not os.path.isfile(file_path):
            return self._err(f"File not found: {file_path}")
        try:
            await self._page.set_input_files(selector, file_path)
            logger.info("Uploaded file '%s' to selector '%s'.", file_path, selector)
            b64 = await self._screenshot_b64()
            return self._ok(
                selector=selector,
                file=os.path.basename(file_path),
                screenshot_b64=b64,
            )
        except Exception as exc:
            return self._err(f"File upload failed: {exc}")

    async def get_page_text(self) -> dict[str, Any]:
        """Return all visible text on the current page (for Claude to read)."""
        try:
            text = await self._page.inner_text("body")
            return self._ok(text=text[:8000], current_url=self._page.url)
        except Exception as exc:
            return self._err(f"get_page_text failed: {exc}")

    async def wait_for_text(self, text: str, timeout_ms: int = 15_000) -> dict[str, Any]:
        """
        Wait until *text* appears somewhere on the page.
        Useful for confirming a submission success message.
        """
        try:
            await self._page.wait_for_function(
                f"() => document.body.innerText.includes({text!r})",
                timeout=timeout_ms,
            )
            b64 = await self._screenshot_b64()
            return self._ok(found_text=text, screenshot_b64=b64)
        except Exception as exc:
            return self._err(f"Text '{text}' not found within {timeout_ms}ms: {exc}")

    async def submit_form(self, selector: str = 'button[type="submit"]') -> dict[str, Any]:
        """Click the form submit button and wait for the result."""
        return await self.click(selector=selector)
