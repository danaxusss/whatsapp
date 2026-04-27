"""
Playwright browser lifecycle and WhatsApp Web session management (sync API).
"""

import random
from typing import Optional

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

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
    """Manages a persistent Chromium context for WhatsApp Web (sync Playwright)."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._connected: bool = False

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self, headless: bool = False) -> bool:
        """
        Launch (or reattach to) the persistent browser context.
        Returns True when a valid WhatsApp session is detected.
        """
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        viewport = {
            "width": random.randint(*VIEWPORT_RANGE["width"]),
            "height": random.randint(*VIEWPORT_RANGE["height"]),
        }
        user_agent = random.choice(USER_AGENTS)

        self._context = self._playwright.chromium.launch_persistent_context(
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
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto(WHATSAPP_URL, wait_until="domcontentloaded")

        self._connected = self._wait_for_session()
        return self._connected

    def get_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Session not started. Call start() first.")
        return self._page

    def health_check(self) -> bool:
        """Return True if WhatsApp Web is still connected and usable."""
        if self._page is None:
            return False
        try:
            self._page.wait_for_selector(
                SELECTORS["chat_list"],
                timeout=HEALTH_CHECK_TIMEOUT * 1000,
                state="visible",
            )
            if self._detect_ban():
                return False
            self._connected = True
            return True
        except Exception:
            self._connected = False
            return False

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._playwright:
            self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal ────────────────────────────────────────────────────────────

    def _wait_for_session(self) -> bool:
        """
        Wait for either the chat list (already logged in) or the QR code.
        Returns True once the chat list is visible.
        """
        try:
            result = self._page.wait_for_selector(
                f'{SELECTORS["chat_list"]}, {SELECTORS["qr_code"]}',
                timeout=QR_TIMEOUT * 1000,
            )
            aria = result.evaluate("el => el.getAttribute('aria-label') || ''")

            if "Scan" in aria:
                # QR displayed — wait for the user to scan
                self._page.wait_for_selector(
                    SELECTORS["chat_list"],
                    timeout=QR_TIMEOUT * 1000,
                )
            return True
        except Exception:
            return False

    def _detect_ban(self) -> bool:
        try:
            content = self._page.content()
            return any(kw.lower() in content.lower() for kw in BAN_KEYWORDS)
        except Exception:
            return False
