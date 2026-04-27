"""
Rate limiter, delay engine, circuit breaker, ban detection, and session warmup.
This is the most critical module — do NOT remove or bypass any checks here.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import (
    ACCOUNT_TIERS,
    BAN_KEYWORDS,
    CIRCUIT_BREAKER,
    COFFEE_BREAK,
    DELAYS,
    MEDIA_EXTRA_DELAY,
    SELECTORS,
    WARMUP,
)


# ── Delay Engine ──────────────────────────────────────────────────────────────

def human_delay(tier: str = "normal") -> float:
    """
    Return a realistic randomized delay in seconds for the given tier.
    Uses a truncated Gaussian distribution to avoid perfectly uniform intervals.
    """
    cfg = DELAYS.get(tier, DELAYS["normal"])
    lo, hi = cfg["min"], cfg["max"]
    mid = (lo + hi) / 2
    sigma = (hi - lo) / 4  # ~95% of values fall within [lo, hi]
    delay = random.gauss(mid, sigma)
    return max(lo, min(hi, delay))


def media_extra_delay() -> float:
    """Additional wait after a media attachment."""
    lo, hi = MEDIA_EXTRA_DELAY["min"], MEDIA_EXTRA_DELAY["max"]
    return random.uniform(lo, hi)


async def async_human_delay(tier: str = "normal") -> None:
    await asyncio.sleep(human_delay(tier))


# ── Coffee Break ──────────────────────────────────────────────────────────────

class CoffeeBreakScheduler:
    """Decides when to take a coffee break and tracks the countdown."""

    def __init__(self):
        self._next_break_at: int = self._roll_next()
        self._messages_since_break: int = 0
        self.on_break: bool = False
        self.break_remaining: int = 0        # seconds remaining in current break
        self._break_end: float = 0.0

    def tick(self) -> bool:
        """
        Call after each successful send.
        Returns True if a coffee break should start now.
        """
        self._messages_since_break += 1
        if self._messages_since_break >= self._next_break_at:
            return True
        return False

    async def do_break(self, progress_callback: Optional[Callable[[int], None]] = None) -> None:
        """Perform the coffee break pause, optionally calling progress_callback(remaining_seconds)."""
        lo, hi = COFFEE_BREAK["duration_range"]
        duration = random.randint(lo, hi)
        self._break_end = time.time() + duration
        self.on_break = True
        self._messages_since_break = 0
        self._next_break_at = self._roll_next()

        for remaining in range(duration, 0, -1):
            self.break_remaining = remaining
            if progress_callback:
                progress_callback(remaining)
            await asyncio.sleep(1)

        self.on_break = False
        self.break_remaining = 0

    @staticmethod
    def _roll_next() -> int:
        lo, hi = COFFEE_BREAK["trigger_range"]
        return random.randint(lo, hi)


# ── Circuit Breaker ────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerState:
    consecutive_failures: int = 0
    aborted: bool = False
    paused: bool = False

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> str:
        """
        Increment the failure counter and return the action to take:
        "continue" | "pause" | "abort"
        """
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_BREAKER["abort_after"]:
            self.aborted = True
            return "abort"
        if self.consecutive_failures >= CIRCUIT_BREAKER["pause_after"]:
            return "pause"
        return "continue"

    async def pause(self) -> None:
        """Wait for the circuit breaker cooldown period."""
        self.paused = True
        await asyncio.sleep(CIRCUIT_BREAKER["pause_duration"])
        self.paused = False


# ── Daily Limit Tracker ────────────────────────────────────────────────────────

class DailyLimitTracker:
    def __init__(self, tier: str = "established"):
        cfg = ACCOUNT_TIERS.get(tier, ACCOUNT_TIERS["established"])
        self.recommended: int = cfg["recommended"]
        self.hard_limit: int = cfg["hard_limit"]
        self._sent_today: int = 0

    def record_sent(self) -> None:
        self._sent_today += 1

    @property
    def sent_today(self) -> int:
        return self._sent_today

    def would_exceed_recommended(self, additional: int = 0) -> bool:
        return (self._sent_today + additional) > self.recommended

    def would_exceed_hard_limit(self, additional: int = 0) -> bool:
        return (self._sent_today + additional) >= self.hard_limit

    def remaining_hard(self) -> int:
        return max(0, self.hard_limit - self._sent_today)


# ── Session Warmup ────────────────────────────────────────────────────────────

async def perform_warmup(page) -> None:
    """
    Simulate human-like browsing before the first send:
    scroll the chat list a few times, briefly open/close a random chat.
    """
    try:
        chat_list = page.locator(SELECTORS["chat_list"])
        for _ in range(WARMUP["scroll_steps"]):
            await chat_list.evaluate("el => el.scrollBy(0, 200)")
            await asyncio.sleep(random.uniform(0.5, 1.5))
        await asyncio.sleep(random.uniform(0.5, 1.5))
        for _ in range(WARMUP["scroll_steps"]):
            await chat_list.evaluate("el => el.scrollBy(0, -200)")
            await asyncio.sleep(random.uniform(0.5, 1.5))

        # Try to open then close a chat
        first_chat = page.locator(f'{SELECTORS["chat_list"]} [role="listitem"]').first
        await first_chat.click(timeout=3_000)
        await asyncio.sleep(random.uniform(1.5, 3.0))
        await page.keyboard.press("Escape")
    except Exception:
        pass  # warmup is best-effort; don't fail the whole batch

    lo, hi = WARMUP["pre_send_wait"]
    await asyncio.sleep(random.uniform(lo, hi))


# ── Ban Detection ─────────────────────────────────────────────────────────────

async def check_for_ban(page) -> Optional[str]:
    """
    Scan the current page for known ban/restriction keywords.
    Returns the matched keyword string, or None if clean.
    """
    try:
        content = await page.content()
        for kw in BAN_KEYWORDS:
            if kw.lower() in content.lower():
                return kw
    except Exception:
        pass
    return None
