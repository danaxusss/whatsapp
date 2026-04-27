"""
WhatsApp Bulk Marketing Tool — Streamlit entry point.
Run with: streamlit run app.py
"""

import asyncio
import os
import sys
import threading
import time
from typing import Optional

import streamlit as st

# ── Page config must be the first Streamlit call ──────────────────────────────
st.set_page_config(
    page_title="WhatsApp Bulk Marketing",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Add project root to path so modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ACCOUNT_TIERS, BLOCKLIST_FILE, DELAYS, HISTORY_FILE
from contact_manager import (
    ContactResult,
    get_csv_columns,
    load_blocklist,
    parse_contacts,
    parse_csv_contacts,
)
from logger import CampaignLogger, load_history
from media_handler import MediaFile, validate_and_save
from message_spinner import spin_messages
from safety import DailyLimitTracker
from sender import STATUS_ABORTED, run_campaign
from session_manager import SessionManager


# ── Session state initialisation ──────────────────────────────────────────────

def _init_state():
    defaults = {
        "session_mgr": None,
        "connected": False,
        "contacts_valid": [],
        "contacts_all": [],
        "spun_messages": [],
        "media_files": [],
        "media_warnings": [],
        "campaign_logger": CampaignLogger(),
        "campaign_running": False,
        "campaign_paused": False,
        "stop_event": None,
        "pause_event": None,
        "status_message": "Idle",
        "break_remaining": 0,
        "api_key": "",
        "ai_provider": "openai",
        "ai_model": "",
        "delay_tier": "normal",
        "account_tier": "established",
        "test_mode": False,
        "default_cc": "+1",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()

# ── Persistent event loop ─────────────────────────────────────────────────────
# All Playwright operations MUST run on the same event loop — mixing loops
# causes silent failures because asyncio primitives are loop-bound.

class _LoopThread:
    """Single daemon thread that keeps one asyncio event loop alive."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        t = threading.Thread(target=self.loop.run_forever, daemon=True)
        t.start()

    def run(self, coro):
        """Submit a coroutine and block the calling thread until it completes."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def submit(self, coro):
        """Submit a coroutine and return immediately (fire-and-forget)."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


if "loop_thread" not in st.session_state:
    st.session_state.loop_thread = _LoopThread()

_loop: _LoopThread = st.session_state.loop_thread


def _run_async(coro):
    return _loop.run(coro)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("💬 WA Bulk Marketer")
    st.markdown("---")

    # Session controls
    st.subheader("Session")
    conn_status = "🟢 Connected" if st.session_state.connected else "🔴 Disconnected"
    st.markdown(f"**Status:** {conn_status}")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Connect", use_container_width=True):
            with st.spinner("Launching browser…"):
                mgr = SessionManager()
                ok = _run_async(mgr.start(headless=False))
                st.session_state.session_mgr = mgr
                st.session_state.connected = ok
            if ok:
                st.success("Connected!")
            else:
                st.error("Could not connect. Check that you scanned the QR code.")
    with col_b:
        if st.button("Disconnect", use_container_width=True):
            if st.session_state.session_mgr:
                _run_async(st.session_state.session_mgr.close())
            st.session_state.session_mgr = None
            st.session_state.connected = False
            st.rerun()

    st.markdown("---")

    # AI Settings
    st.subheader("AI Spinner")
    st.session_state.ai_provider = st.selectbox(
        "Provider", ["openai", "groq"], index=["openai", "groq"].index(st.session_state.ai_provider)
    )
    st.session_state.api_key = st.text_input(
        "API Key", value=st.session_state.api_key, type="password", placeholder="sk-…"
    )
    model_defaults = {"openai": "gpt-4o-mini", "groq": "llama3-8b-8192"}
    st.session_state.ai_model = st.text_input(
        "Model (leave blank for default)",
        value=st.session_state.ai_model or model_defaults[st.session_state.ai_provider],
    )

    st.markdown("---")

    # Safety Settings
    st.subheader("Safety")
    tier_labels = {k: v["label"] for k, v in ACCOUNT_TIERS.items()}
    st.session_state.account_tier = st.selectbox(
        "Account Tier",
        list(tier_labels.keys()),
        format_func=lambda k: tier_labels[k],
        index=list(tier_labels.keys()).index(st.session_state.account_tier),
    )
    daily = DailyLimitTracker(st.session_state.account_tier)
    st.caption(f"Recommended: {daily.recommended} / Hard limit: {daily.hard_limit} msgs/day")

    st.session_state.delay_tier = st.selectbox(
        "Delay Tier",
        list(DELAYS.keys()),
        index=list(DELAYS.keys()).index(st.session_state.delay_tier),
        format_func=lambda k: {
            "fast": "Fast (8–15 s)",
            "normal": "Normal (15–35 s)",
            "safe": "Safe (35–60 s)",
            "ultra": "Ultra (60–120 s)",
        }[k],
    )
    st.session_state.test_mode = st.checkbox("Test Mode (first 3 contacts only)", value=st.session_state.test_mode)
    st.session_state.default_cc = st.text_input(
        "Default Country Code", value=st.session_state.default_cc, placeholder="+1"
    )

    st.markdown("---")
    st.subheader("About")
    st.caption("v1.0 — Use responsibly. Ensure all recipients have opted in.")
    st.caption("⚠️ You are solely responsible for compliance with WhatsApp ToS.")


# ── Main Tabs ─────────────────────────────────────────────────────────────────

tab_compose, tab_dashboard, tab_history = st.tabs(["📝 Compose", "📊 Live Dashboard", "📁 History"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 1: COMPOSE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_compose:
    st.header("1. Contacts")

    input_method = st.radio("Input method", ["Paste numbers", "Upload CSV"], horizontal=True)

    blocklist = load_blocklist(BLOCKLIST_FILE)
    valid_contacts = []
    all_contacts = []

    if input_method == "Paste numbers":
        raw = st.text_area(
            "Phone numbers (one per line, or comma/tab separated)",
            height=180,
            placeholder="+12125551234\n+442071234567\n…",
        )
        if raw.strip():
            valid_contacts, all_contacts = parse_contacts(
                raw,
                default_country_code=st.session_state.default_cc,
                blocklist=blocklist,
            )

    else:
        csv_file = st.file_uploader("Upload CSV", type=["csv"])
        if csv_file:
            file_bytes = csv_file.read()
            columns = get_csv_columns(file_bytes)
            if columns:
                phone_col = st.selectbox("Phone number column", columns)
                valid_contacts, all_contacts = parse_csv_contacts(
                    file_bytes,
                    phone_col,
                    default_country_code=st.session_state.default_cc,
                    blocklist=blocklist,
                )

    if all_contacts:
        st.success(f"**{len(valid_contacts)} valid contacts** out of {len(all_contacts)} total.")
        _df_data = [
            {
                "#": c.index,
                "Original": c.original,
                "Cleaned": c.cleaned,
                "Status": c.status.capitalize(),
                "Reason": c.reason,
            }
            for c in all_contacts
        ]
        st.dataframe(_df_data, use_container_width=True, height=200)

    st.session_state.contacts_valid = valid_contacts
    st.session_state.contacts_all = all_contacts

    st.markdown("---")
    st.header("2. Message")

    base_message = st.text_area(
        "Base message",
        height=180,
        placeholder="Type your marketing message here…",
    )
    char_count = len(base_message)
    st.caption(f"{char_count} characters")

    spin_col, preview_col = st.columns([1, 2])
    with spin_col:
        if st.button("Spin Messages (AI)", disabled=not base_message.strip()):
            if not st.session_state.api_key:
                st.warning("Enter an API key in the sidebar to use AI spinning. Using local fallback.")
            n = max(len(valid_contacts), 1)
            with st.spinner(f"Generating {n} variations…"):
                spun = spin_messages(
                    base_message,
                    count=n,
                    provider=st.session_state.ai_provider,
                    api_key=st.session_state.api_key,
                    model=st.session_state.ai_model,
                )
            st.session_state.spun_messages = spun
            st.success(f"{len(spun)} variations generated.")

    with preview_col:
        if st.session_state.spun_messages:
            import random as _rnd
            samples = _rnd.sample(st.session_state.spun_messages, min(3, len(st.session_state.spun_messages)))
            for i, s in enumerate(samples, 1):
                with st.expander(f"Variation {i} preview"):
                    st.write(s)

    # If no spun messages yet, use the base message for all contacts
    if base_message.strip() and not st.session_state.spun_messages:
        st.info("Click 'Spin Messages' to generate unique variations, or the base message will be sent to all contacts.")

    st.markdown("---")
    st.header("3. Media (optional)")

    uploaded = st.file_uploader(
        "Attach files (images, PDFs, videos)",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "gif", "webp", "pdf", "mp4"],
    )
    if uploaded:
        saved, warnings = validate_and_save(uploaded)
        st.session_state.media_files = saved
        st.session_state.media_warnings = warnings
        if warnings:
            for w in warnings:
                st.warning(w)
        if saved:
            st.success(f"{len(saved)} file(s) ready to attach.")

    st.markdown("---")

    # ── Launch button ──────────────────────────────────────────────────────
    can_start = (
        st.session_state.connected
        and len(st.session_state.contacts_valid) > 0
        and bool(base_message.strip() or st.session_state.spun_messages)
        and not st.session_state.campaign_running
    )

    if not st.session_state.connected:
        st.warning("Connect your WhatsApp session in the sidebar first.")
    if not st.session_state.contacts_valid:
        st.warning("Add at least one valid contact.")

    daily_tracker = DailyLimitTracker(st.session_state.account_tier)
    n_contacts = len(st.session_state.contacts_valid)
    if daily_tracker.would_exceed_recommended(n_contacts):
        st.warning(
            f"This batch ({n_contacts}) exceeds the recommended daily limit "
            f"({daily_tracker.recommended}) for your account tier."
        )

    if st.button("🚀 Start Sending", disabled=not can_start, type="primary", use_container_width=True):
        messages = st.session_state.spun_messages or [base_message] * n_contacts
        # Ensure enough messages for all contacts
        while len(messages) < n_contacts:
            messages.append(base_message)

        stop_ev = threading.Event()
        pause_ev = threading.Event()
        st.session_state.stop_event = stop_ev
        st.session_state.pause_event = pause_ev
        st.session_state.campaign_running = True
        st.session_state.campaign_paused = False

        logger: CampaignLogger = st.session_state.campaign_logger
        logger.start_campaign()

        # Pre-populate all contacts as pending
        for c in st.session_state.contacts_valid:
            logger.add_pending(c.index, c.cleaned)

        contacts_tuples = [(c.index, c.cleaned) for c in st.session_state.contacts_valid]

        def on_progress(idx, phone, status, error):
            logger.upsert(idx, phone, status, error_detail=error,
                          message_full=messages[min(idx - 1, len(messages) - 1)])
            st.session_state.status_message = f"Processed {phone}: {status}"

        def on_status(msg):
            st.session_state.status_message = msg

        def on_break_tick(rem):
            st.session_state.break_remaining = rem

        async def _campaign():
            page = await st.session_state.session_mgr.get_page()
            await run_campaign(
                page=page,
                contacts=contacts_tuples,
                messages=messages,
                media_files=st.session_state.media_files,
                delay_tier=st.session_state.delay_tier,
                account_tier=st.session_state.account_tier,
                test_mode=st.session_state.test_mode,
                on_progress=on_progress,
                on_status_update=on_status,
                on_break_tick=on_break_tick,
                stop_event=stop_ev,
                pause_event=pause_ev,
            )
            st.session_state.campaign_running = False
            logger.save_to_history()

        # Submit to the persistent loop so the page object stays on its original loop
        _loop.submit(_campaign())
        st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 2: LIVE DASHBOARD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_dashboard:
    st.header("Live Dashboard")

    logger: CampaignLogger = st.session_state.campaign_logger
    counts = logger.counts()
    total = len(logger.entries)
    done = total - counts.get("pending", 0)

    # Status line
    status_color = "🔴" if "BAN" in st.session_state.status_message.upper() else "🟡" if st.session_state.campaign_running else "⚪"
    st.markdown(f"**Status:** {status_color} {st.session_state.status_message}")

    if st.session_state.break_remaining > 0:
        mins, secs = divmod(st.session_state.break_remaining, 60)
        st.info(f"☕ Coffee break — resuming in **{mins}:{secs:02d}**")

    # Progress
    if total > 0:
        st.progress(done / total if total else 0, text=f"{done} / {total} contacts processed")

    # Counters
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("✅ Sent",      counts.get("sent", 0))
    c2.metric("❌ Invalid",   counts.get("invalid", 0))
    c3.metric("🔍 Not Found", counts.get("not_found", 0))
    c4.metric("⚠️ Error",    counts.get("error", 0))
    c5.metric("⏳ Pending",   counts.get("pending", 0))

    # ETA
    eta = logger.eta_seconds(total)
    if eta is not None:
        mins, secs = divmod(int(eta), 60)
        st.caption(f"Estimated time remaining: {mins}m {secs}s")

    # Controls
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
    with ctrl1:
        if st.button("⏸ Pause", disabled=not st.session_state.campaign_running or st.session_state.campaign_paused):
            if st.session_state.pause_event:
                st.session_state.pause_event.set()
            st.session_state.campaign_paused = True
    with ctrl2:
        if st.button("▶ Resume", disabled=not st.session_state.campaign_paused):
            if st.session_state.pause_event:
                st.session_state.pause_event.clear()
            st.session_state.campaign_paused = False
    with ctrl3:
        if st.button("⏹ Abort", disabled=not st.session_state.campaign_running):
            if st.session_state.stop_event:
                st.session_state.stop_event.set()
            st.session_state.campaign_running = False
    with ctrl4:
        if st.button("🔄 Refresh"):
            st.rerun()

    st.markdown("---")

    # Real-time log table
    rows = logger.to_display_rows()
    if rows:
        st.dataframe(rows, use_container_width=True, height=400)
    else:
        st.info("No campaign data yet. Start a campaign from the Compose tab.")

    # Auto-refresh while running
    if st.session_state.campaign_running:
        time.sleep(3)
        st.rerun()

    # CSV download
    if rows:
        csv_bytes = logger.to_csv_bytes()
        st.download_button(
            "📥 Download Log as CSV",
            data=csv_bytes,
            file_name="campaign_log.csv",
            mime="text/csv",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAB 3: HISTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_history:
    st.header("Campaign History")

    history = load_history()
    if not history:
        st.info("No past campaigns found.")
    else:
        for i, record in enumerate(reversed(history)):
            with st.expander(f"{record.get('name', 'Campaign')} — {record.get('timestamp', '')}"):
                col_s, col_f, col_t = st.columns(3)
                col_s.metric("Sent",   record.get("sent", 0))
                col_f.metric("Failed", record.get("failed", 0))
                col_t.metric("Total",  record.get("total", 0))

                failed_nums = CampaignLogger.failed_contacts_from_history(record)
                if failed_nums:
                    st.markdown(f"**Failed contacts ({len(failed_nums)}):**")
                    st.code("\n".join(failed_nums))
                    if st.button(f"Re-send to failed ({len(failed_nums)})", key=f"resend_{i}"):
                        st.session_state.contacts_valid, st.session_state.contacts_all = parse_contacts(
                            "\n".join(failed_nums),
                            default_country_code=st.session_state.default_cc,
                        )
                        st.success(f"Loaded {len(st.session_state.contacts_valid)} failed contacts. Switch to Compose tab.")

                # Per-entry detail
                entries = record.get("entries", [])
                if entries:
                    st.dataframe(
                        [
                            {
                                "#": e["index"],
                                "Phone": e["phone"],
                                "Status": e["status"],
                                "Error": e.get("error_detail", ""),
                                "Time": e.get("timestamp", ""),
                            }
                            for e in entries
                        ],
                        use_container_width=True,
                    )
