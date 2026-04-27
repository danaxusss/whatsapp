"""
Core automation loop: navigate → type → attach → send for each contact.
Every Playwright interaction is wrapped in try/except — the loop never crashes.
"""

import asyncio
import json
from typing import Callable, List, Optional

from config import NAVIGATION_TIMEOUT, SELECTORS, WHATSAPP_URL
from media_handler import MediaFile, attach_files
from safety import (
    CircuitBreakerState,
    CoffeeBreakScheduler,
    DailyLimitTracker,
    async_human_delay,
    check_for_ban,
    media_extra_delay,
    perform_warmup,
)


# ── Send result constants ─────────────────────────────────────────────────────
STATUS_SENT      = "sent"
STATUS_INVALID   = "invalid"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR     = "error"
STATUS_SKIPPED   = "skipped"
STATUS_ABORTED   = "aborted"


async def run_campaign(
    page,
    contacts: List[tuple],            # list of (index, phone_number)
    messages: List[str],               # pre-spun messages, one per contact index
    media_files: List[MediaFile],
    delay_tier: str,
    account_tier: str,
    test_mode: bool = False,
    on_progress: Optional[Callable[[int, str, str, str], None]] = None,
    on_status_update: Optional[Callable[[str], None]] = None,
    on_break_tick: Optional[Callable[[int], None]] = None,
    stop_event: Optional[asyncio.Event] = None,
    pause_event: Optional[asyncio.Event] = None,
) -> dict:
    """
    Send messages to all contacts.

    on_progress(index, phone, status, error) — called after each contact.
    on_status_update(message)               — called to update the UI status line.
    on_break_tick(remaining_seconds)        — called every second during a coffee break.
    stop_event                              — set this to abort the loop.
    pause_event                             — set this to pause the loop.

    Returns a summary dict.
    """
    circuit = CircuitBreakerState()
    coffee = CoffeeBreakScheduler()
    daily = DailyLimitTracker(account_tier)

    if test_mode:
        contacts = contacts[:3]

    if _notify(on_status_update, "Warming up…"):
        pass
    await perform_warmup(page)

    results = {}

    for i, (idx, phone) in enumerate(contacts):
        # ── Stop / Pause checks ────────────────────────────────────────────
        if stop_event and stop_event.is_set():
            break

        while pause_event and pause_event.is_set():
            _notify(on_status_update, "Paused…")
            await asyncio.sleep(1)
            if stop_event and stop_event.is_set():
                break

        if circuit.aborted:
            break

        # ── Hard daily limit ───────────────────────────────────────────────
        if daily.would_exceed_hard_limit():
            _notify(on_status_update, f"Hard daily limit reached ({daily.hard_limit}). Stopping.")
            break

        # ── Ban detection (pre-send) ───────────────────────────────────────
        ban_kw = await check_for_ban(page)
        if ban_kw:
            _notify(on_status_update, f"BAN DETECTED: '{ban_kw}' — aborting immediately!")
            results[idx] = (STATUS_ABORTED, "Ban overlay detected")
            _call(on_progress, idx, phone, STATUS_ABORTED, "Ban overlay detected")
            break

        message = messages[i % len(messages)] if messages else ""
        _notify(on_status_update, f"Sending to {phone} ({i + 1}/{len(contacts)})…")

        status, error = await _send_one(page, phone, message, media_files)
        results[idx] = (status, error)
        _call(on_progress, idx, phone, status, error)

        # ── Circuit breaker ────────────────────────────────────────────────
        if status in (STATUS_ERROR, STATUS_INVALID, STATUS_NOT_FOUND):
            action = circuit.record_failure()
            if action == "abort":
                _notify(
                    on_status_update,
                    "5 consecutive failures — possible ban. Aborting. Wait 24 hours before retrying.",
                )
                break
            if action == "pause":
                _notify(on_status_update, "3 consecutive failures — pausing 60 s…")
                await circuit.pause()
        else:
            circuit.record_success()
            daily.record_sent()

        # ── Coffee break ───────────────────────────────────────────────────
        if coffee.tick():
            _notify(on_status_update, "Coffee break — taking a human-like pause…")
            await coffee.do_break(progress_callback=on_break_tick)
            _notify(on_status_update, "Resuming…")

        # ── Inter-message delay ────────────────────────────────────────────
        if i < len(contacts) - 1:
            await async_human_delay(delay_tier)

    return results


# ── Single-contact sender ─────────────────────────────────────────────────────

async def _send_one(page, phone: str, message: str, media_files: List[MediaFile]):
    """Navigate to the chat, inject the message, attach media, and send."""
    url = f"{WHATSAPP_URL}/send?phone={_clean_phone(phone)}"

    for attempt in range(2):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)

            # ── Detect invalid-number overlay ──────────────────────────────
            error_text = await _get_overlay_text(page)
            if error_text:
                lc = error_text.lower()
                if "invalid" in lc or "not on whatsapp" in lc:
                    return STATUS_INVALID, error_text
                if "not on whatsapp" in lc:
                    return STATUS_NOT_FOUND, error_text

            # ── Wait for message input ─────────────────────────────────────
            try:
                await page.wait_for_selector(
                    SELECTORS["message_input"],
                    timeout=NAVIGATION_TIMEOUT * 1000,
                    state="visible",
                )
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(5)
                    continue
                return STATUS_ERROR, "Message input not found"

            # ── Inject message ─────────────────────────────────────────────
            if message:
                await inject_message(page, message)
                await asyncio.sleep(0.5)

            # ── Attach media ───────────────────────────────────────────────
            if media_files:
                ok = await attach_files(page, media_files)
                if not ok:
                    return STATUS_ERROR, "Media attachment failed"
                await asyncio.sleep(media_extra_delay())

            # ── Send ───────────────────────────────────────────────────────
            await _click_send(page)
            await asyncio.sleep(2.5)

            # ── Post-send ban check ────────────────────────────────────────
            ban_kw = await check_for_ban(page)
            if ban_kw:
                return STATUS_ERROR, f"Ban overlay after send: {ban_kw}"

            return STATUS_SENT, ""

        except Exception as exc:
            if attempt == 0:
                await asyncio.sleep(5)
                continue
            return STATUS_ERROR, str(exc)[:200]

    return STATUS_ERROR, "Unknown error after retries"


async def inject_message(page, message: str) -> None:
    """
    Inject a message into WhatsApp's contenteditable input via the clipboard API.
    Faster and more reliable than character-by-character typing for long messages.
    """
    selector = SELECTORS["message_input"]
    await page.click(selector)
    await page.evaluate(
        f"""
        (msg) => {{
            const input = document.querySelector('{selector}');
            if (!input) return;
            const dt = new DataTransfer();
            dt.setData('text/plain', msg);
            const paste = new ClipboardEvent('paste', {{
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            }});
            input.dispatchEvent(paste);
        }}
        """,
        message,
    )


async def _click_send(page) -> None:
    """Click the send button, falling back to pressing Enter."""
    try:
        btn = page.locator(SELECTORS["send_button"]).first
        await btn.click(timeout=5_000)
    except Exception:
        await page.keyboard.press("Enter")


async def _get_overlay_text(page) -> str:
    """Return text from the invalid-number popup, or empty string."""
    try:
        popup = page.locator(SELECTORS["invalid_number_popup"])
        if await popup.count() > 0:
            return (await popup.first.inner_text()).strip()
    except Exception:
        pass
    return ""


def _clean_phone(phone: str) -> str:
    """Remove the leading + for the URL query parameter."""
    return phone.lstrip("+")


def _notify(callback, message: str) -> bool:
    if callback:
        callback(message)
    return True


def _call(callback, *args) -> None:
    if callback:
        callback(*args)
