"""
Playwright browser lifecycle and WhatsApp Web session management.
"""

import asyncio
import random
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from config import (
    BAN_KEYWORDS,
    HEALTH_CHECK_TIMEOUT,
    QR_TIMEOUT,
    SELECTORS,
    USER_AGENTS,
    USER_DATA_DIR,
    VIEWPORT_RANGE,
    WHATSAPP_URL,
)


class SessionManager:
    """Manages a persistent Chromium context for WhatsApp Web."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._connected: bool = False

    # ── Public API ──────────────────────────────────────────────────────────

    async def start(self, headless: bool = False) -> bool:
        """
        Launch (or reattach to) the persistent browser context.
        Returns True when a valid WhatsApp session is detected.
        """
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        viewport = {
            "width": random.randint(*VIEWPORT_RANGE["width"]),
            "height": random.randint(*VIEWPORT_RANGE["height"]),
        }
        user_agent = random.choice(USER_AGENTS)

        self._context = await self._playwright.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport=viewport,
            user_agent=user_agent,
        )

        # Mask navigator.webdriver on every new document
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(WHATSAPP_URL, wait_until="domcontentloaded")

        self._connected = await self._wait_for_session()
        return self._connected

    async def get_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Session not started. Call start() first.")
        return self._page

    async def health_check(self) -> bool:
        """Return True if WhatsApp Web is still connected and usable."""
        if self._page is None:
            return False
        try:
            await self._page.wait_for_selector(
                SELECTORS["chat_list"],
                timeout=HEALTH_CHECK_TIMEOUT * 1000,
                state="visible",
            )
            # Also check for ban overlays
            if await self._detect_ban():
                return False
            self._connected = True
            return True
        except Exception:
            self._connected = False
            return False

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal ────────────────────────────────────────────────────────────

    async def _wait_for_session(self) -> bool:
        """
        Wait for either:
        - The chat list (already logged in), or
        - The QR code (first login / session expired).
        Returns True once the chat list is visible.
        """
        try:
            # Race: already-logged-in vs QR prompt
            result = await self._page.wait_for_selector(
                f'{SELECTORS["chat_list"]}, {SELECTORS["qr_code"]}',
                timeout=QR_TIMEOUT * 1000,
            )
            tag = await result.evaluate("el => el.tagName")
            aria = await result.evaluate("el => el.getAttribute('aria-label') || ''")

            if "Scan" in aria or tag.lower() == "canvas":
                # QR displayed — wait for user to scan (up to QR_TIMEOUT more seconds)
                await self._page.wait_for_selector(
                    SELECTORS["chat_list"],
                    timeout=QR_TIMEOUT * 1000,
                )

            return True
        except Exception:
            return False

    async def _detect_ban(self) -> bool:
        """Return True if a ban/restriction overlay is present on the page."""
        try:
            content = await self._page.content()
            return any(kw.lower() in content.lower() for kw in BAN_KEYWORDS)
        except Exception:
            return False
