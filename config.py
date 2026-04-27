"""
Central configuration — all magic numbers, selectors, and thresholds live here.
Update selectors in the SELECTORS dict when WhatsApp changes their DOM.
"""

import os

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
HISTORY_FILE = os.path.join(BASE_DIR, "campaign_history.json")
BLOCKLIST_FILE = os.path.join(BASE_DIR, "blocklist.txt")

# ── WhatsApp Web ───────────────────────────────────────────────────────────────
WHATSAPP_URL = "https://web.whatsapp.com"
QR_TIMEOUT = 90          # seconds to wait for QR scan
HEALTH_CHECK_TIMEOUT = 10
NAVIGATION_TIMEOUT = 15  # seconds to wait for message input after navigation

# ── Selectors (update these when WhatsApp changes their DOM) ───────────────────
SELECTORS = {
    # The left-side chat pane — indicates a logged-in session
    "chat_list": "#pane-side",
    # The editable div where the user types a message
    "message_input": 'div[contenteditable="true"][data-tab="10"]',
    # The send button in the message toolbar
    "send_button": 'button[aria-label="Send"]',
    # The paperclip / attachment icon in the toolbar
    "attach_button": 'div[title="Attach"]',
    # Hidden file input for images and videos
    "attach_image": 'input[accept="image/*,video/mp4,video/3gpp,video/quicktime"]',
    # Hidden file input for documents (PDF, etc.)
    "attach_document": 'input[accept="*"]',
    # Modal that appears when a number is invalid
    "invalid_number_popup": 'div[data-animate-modal-popup="true"]',
    # Banner shown when the phone is disconnected / needs reconnection
    "phone_not_connected": 'div[data-testid="intro-md-beta-logo-dark"]',
    # QR code canvas element
    "qr_code": 'canvas[aria-label="Scan me!"]',
    # Media caption input (appears after attaching a file)
    "media_caption": 'div[contenteditable="true"][data-tab="11"]',
    # Thumbnail shown after a file is successfully attached
    "media_thumbnail": 'div[data-testid="media-preview-thumbnail"]',
    # "Phone number shared via url is invalid" error text
    "invalid_phone_text": 'div[data-animate-modal-popup="true"] span',
}

# ── Ban-Detection Strings ──────────────────────────────────────────────────────
# If any of these strings appear in page text, abort immediately.
BAN_KEYWORDS = [
    "This account can no longer use WhatsApp",
    "You need the official WhatsApp to log in",
    "Phone number shared with too many devices",
    "your account has been temporarily banned",
    "Your account has been suspended",
]

# ── Delay Tiers (seconds) ──────────────────────────────────────────────────────
DELAYS = {
    "fast":   {"min": 8,   "max": 15},
    "normal": {"min": 15,  "max": 35},
    "safe":   {"min": 35,  "max": 60},
    "ultra":  {"min": 60,  "max": 120},
}

MEDIA_EXTRA_DELAY = {"min": 3, "max": 8}   # additional seconds after media upload

# ── Coffee Break ───────────────────────────────────────────────────────────────
COFFEE_BREAK = {
    "trigger_range": (15, 25),    # pause after this many messages (randomized)
    "duration_range": (120, 300), # pause duration in seconds
}

# ── Circuit Breaker ────────────────────────────────────────────────────────────
CIRCUIT_BREAKER = {
    "pause_after": 3,    # consecutive failures before a temporary pause
    "abort_after": 5,    # consecutive failures before aborting the batch
    "pause_duration": 60, # seconds to pause
}

# ── Daily Sending Limits ───────────────────────────────────────────────────────
ACCOUNT_TIERS = {
    "new":         {"label": "New (< 1 week)",  "recommended": 20, "hard_limit": 30},
    "established": {"label": "Established",      "recommended": 50, "hard_limit": 80},
    "trusted":     {"label": "Trusted",          "recommended": 100, "hard_limit": 150},
}

# ── Session Warmup ─────────────────────────────────────────────────────────────
WARMUP = {
    "scroll_steps": 3,       # number of scroll actions in the chat list
    "pre_send_wait": (5, 10), # seconds to wait after warmup before first send
}

# ── Browser Fingerprinting Reduction ──────────────────────────────────────────
VIEWPORT_RANGE = {
    "width":  (1200, 1400),
    "height": (800, 900),
}

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]

# ── AI Spinner ────────────────────────────────────────────────────────────────
SPINNER_BATCH_SIZE = 50   # split LLM calls into batches of this size
SPINNER_MAX_RETRIES = 2

SPINNER_SYSTEM_PROMPT = """\
You are a multilingual message rewriter. You will receive a marketing message and a number N.

Rules:
1. Generate exactly N unique variations.
2. Preserve the EXACT meaning, tone, intent, and any call-to-action (URLs, phone numbers, promo codes).
3. NEVER change, invent, or omit any factual content (prices, dates, names, links).
4. Vary: sentence structure, greeting style, emoji placement/selection, word choice, paragraph breaks.
5. Each variation must feel like it was written by a different person — not a template with swapped synonyms.
6. Respect the original language. If the input is in Arabic/French/Spanish, output in the same language.
7. Output ONLY a JSON array of strings. No explanation, no markdown fences, no numbering.

Example output format:
["variation 1 text here", "variation 2 text here", ...]
"""

# ── Media ─────────────────────────────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".mp4"}
IMAGE_VIDEO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4"}
DOCUMENT_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE_MB = 100
VIDEO_WARN_SIZE_MB = 16

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
