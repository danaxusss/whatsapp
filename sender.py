"""
Core sending loop: navigate → type → attach → send (sync Playwright).
Every Playwright interaction is wrapped in try/except — the loop never crashes.
"""

import time
from typing import Callable, List, Optional

from config import NAVIGATION_TIMEOUT, SELECTORS, WHATSAPP_URL
from media_handler import MediaFile, attach_files
from safety import (
    CircuitBreakerState,
    CoffeeBreakScheduler,
    DailyLimitTracker,
    check_for_ban,
    human_delay,
    media_extra_delay,
    perform_warmup,
)


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_SENT      = "sent"
STATUS_INVALID   = "invalid"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR     = "error"
STATUS_SKIPPED   = "skipped"
STATUS_ABORTED   = "aborted"


def run_campaign(
    page,
    contacts: List[tuple],
    messages: List[str],
    media_files: List[MediaFile],
    delay_tier: str,
    account_tier: str,
    test_mode: bool = False,
    on_progress: Optional[Callable] = None,
    on_status_update: Optional[Callable] = None,
    on_break_tick: Optional[Callable] = None,
    stop_event=None,
    pause_event=None,
) -> dict:
    """Send messages to all contacts. Runs synchronously in a background thread."""
    circuit = CircuitBreakerState()
    coffee  = CoffeeBreakScheduler()
    daily   = DailyLimitTracker(account_tier)

    if test_mode:
        contacts = contacts[:3]

    _notify(on_status_update, "Warming up…")
    perform_warmup(page)

    results = {}

    for i, (idx, phone) in enumerate(contacts):
        # ── Stop / Pause ───────────────────────────────────────────────────
        if stop_event and stop_event.is_set():
            break

        while pause_event and pause_event.is_set():
            _notify(on_status_update, "Paused…")
            time.sleep(1)
            if stop_event and stop_event.is_set():
                break

        if circuit.aborted:
            break

        # ── Daily hard limit ───────────────────────────────────────────────
        if daily.would_exceed_hard_limit():
            _notify(on_status_update, f"Hard daily limit ({daily.hard_limit}) reached. Stopping.")
            break

        # ── Ban check (pre-send) ───────────────────────────────────────────
        ban_kw = check_for_ban(page)
        if ban_kw:
            _notify(on_status_update, f"BAN DETECTED: '{ban_kw}' — aborting immediately!")
            results[idx] = (STATUS_ABORTED, "Ban overlay detected")
            _call(on_progress, idx, phone, STATUS_ABORTED, "Ban overlay detected")
            break

        message = messages[i % len(messages)] if messages else ""
        _notify(on_status_update, f"Sending to {phone} ({i + 1}/{len(contacts)})…")

        status, error = _send_one(page, phone, message, media_files)
        results[idx] = (status, error)
        _call(on_progress, idx, phone, status, error)

        # ── Circuit breaker ────────────────────────────────────────────────
        if status in (STATUS_ERROR, STATUS_INVALID, STATUS_NOT_FOUND):
            action = circuit.record_failure()
            if action == "abort":
                _notify(on_status_update, "5 consecutive failures — possible ban. Aborting. Wait 24 hours.")
                break
            if action == "pause":
                _notify(on_status_update, "3 consecutive failures — pausing 60 s…")
                circuit.pause()
        else:
            circuit.record_success()
            daily.record_sent()

        # ── Coffee break ───────────────────────────────────────────────────
        if coffee.tick():
            _notify(on_status_update, "Coffee break — taking a human-like pause…")
            coffee.do_break(progress_callback=on_break_tick)
            _notify(on_status_update, "Resuming…")

        # ── Inter-message delay ────────────────────────────────────────────
        if i < len(contacts) - 1:
            time.sleep(human_delay(delay_tier))

    return results


# ── Single-contact sender ─────────────────────────────────────────────────────

def _send_one(page, phone: str, message: str, media_files: List[MediaFile]):
    url = f"{WHATSAPP_URL}/send?phone={_clean_phone(phone)}"

    for attempt in range(2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(2)

            # Detect invalid-number overlay
            error_text = _get_overlay_text(page)
            if error_text:
                lc = error_text.lower()
                if "invalid" in lc:
                    return STATUS_INVALID, error_text
                if "not on whatsapp" in lc:
                    return STATUS_NOT_FOUND, error_text

            # Wait for message input box
            try:
                page.wait_for_selector(
                    SELECTORS["message_input"],
                    timeout=NAVIGATION_TIMEOUT * 1000,
                    state="visible",
                )
            except Exception:
                if attempt == 0:
                    time.sleep(5)
                    continue
                return STATUS_ERROR, "Message input not found"

            # Inject message via clipboard API
            if message:
                inject_message(page, message)
                time.sleep(0.5)

            # Attach media
            if media_files:
                ok = attach_files(page, media_files)
                if not ok:
                    return STATUS_ERROR, "Media attachment failed"
                time.sleep(media_extra_delay())

            # Send
            _click_send(page)
            time.sleep(2.5)

            # Post-send ban check
            ban_kw = check_for_ban(page)
            if ban_kw:
                return STATUS_ERROR, f"Ban overlay after send: {ban_kw}"

            return STATUS_SENT, ""

        except Exception as exc:
            if attempt == 0:
                time.sleep(5)
                continue
            return STATUS_ERROR, str(exc)[:200]

    return STATUS_ERROR, "Unknown error after retries"


def inject_message(page, message: str) -> None:
    """Inject message into WhatsApp's contenteditable input via clipboard paste event."""
    selector = SELECTORS["message_input"]
    page.click(selector)
    page.evaluate(
        """
        (msg) => {
            const input = document.querySelector('div[contenteditable="true"][data-tab="10"]');
            if (!input) return;
            const dt = new DataTransfer();
            dt.setData('text/plain', msg);
            const paste = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            });
            input.dispatchEvent(paste);
        }
        """,
        message,
    )


def _click_send(page) -> None:
    try:
        page.locator(SELECTORS["send_button"]).first.click(timeout=5_000)
    except Exception:
        page.keyboard.press("Enter")


def _get_overlay_text(page) -> str:
    try:
        popup = page.locator(SELECTORS["invalid_number_popup"])
        if popup.count() > 0:
            return popup.first.inner_text().strip()
    except Exception:
        pass
    return ""


def _clean_phone(phone: str) -> str:
    return phone.lstrip("+")


def _notify(callback, message: str) -> None:
    if callback:
        callback(message)


def _call(callback, *args) -> None:
    if callback:
        callback(*args)
