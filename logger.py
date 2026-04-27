"""
Structured in-memory logging with CSV export and campaign history persistence.
"""

import csv
import io
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from config import HISTORY_FILE, LOG_DATE_FORMAT


# ── Log Entry ─────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    index: int
    phone: str
    message_preview: str        # first 50 chars of the message sent
    message_full: str
    status: str                 # "sent" | "invalid" | "not_found" | "error" | "pending" | "skipped"
    error_detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().strftime(LOG_DATE_FORMAT))

    def status_icon(self) -> str:
        icons = {
            "sent":      "✅ Sent",
            "invalid":   "❌ Invalid",
            "not_found": "❌ Not Found",
            "error":     "⚠️ Error",
            "pending":   "⏳ Pending",
            "skipped":   "⏩ Skipped",
        }
        return icons.get(self.status, self.status)


# ── Campaign Logger ────────────────────────────────────────────────────────────

class CampaignLogger:
    def __init__(self):
        self._entries: List[LogEntry] = []
        self._start_time: Optional[float] = None
        self._sent_times: List[float] = []  # epoch times of successful sends

    # ── Entry management ──────────────────────────────────────────────────────

    def start_campaign(self) -> None:
        self._entries.clear()
        self._sent_times.clear()
        self._start_time = time.time()

    def add_pending(self, index: int, phone: str) -> None:
        self._entries.append(LogEntry(
            index=index,
            phone=phone,
            message_preview="",
            message_full="",
            status="pending",
        ))

    def update_entry(
        self,
        index: int,
        status: str,
        message_full: str = "",
        error_detail: str = "",
    ) -> None:
        for entry in self._entries:
            if entry.index == index:
                entry.status = status
                entry.message_full = message_full
                entry.message_preview = message_full[:50] if message_full else ""
                entry.error_detail = error_detail
                entry.timestamp = datetime.now().strftime(LOG_DATE_FORMAT)
                if status == "sent":
                    self._sent_times.append(time.time())
                return
        # Entry not yet created — add it now
        preview = message_full[:50] if message_full else ""
        self._entries.append(LogEntry(
            index=index,
            phone="",
            message_preview=preview,
            message_full=message_full,
            status=status,
            error_detail=error_detail,
        ))

    def upsert(
        self,
        index: int,
        phone: str,
        status: str,
        message_full: str = "",
        error_detail: str = "",
    ) -> None:
        existing = next((e for e in self._entries if e.index == index), None)
        if existing:
            existing.phone = phone
            existing.status = status
            existing.message_full = message_full
            existing.message_preview = message_full[:50]
            existing.error_detail = error_detail
            existing.timestamp = datetime.now().strftime(LOG_DATE_FORMAT)
            if status == "sent":
                self._sent_times.append(time.time())
        else:
            preview = message_full[:50]
            self._entries.append(LogEntry(
                index=index,
                phone=phone,
                message_preview=preview,
                message_full=message_full,
                status=status,
                error_detail=error_detail,
            ))
            if status == "sent":
                self._sent_times.append(time.time())

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def entries(self) -> List[LogEntry]:
        return list(self._entries)

    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {"sent": 0, "invalid": 0, "not_found": 0, "error": 0, "pending": 0, "skipped": 0}
        for e in self._entries:
            result[e.status] = result.get(e.status, 0) + 1
        return result

    def eta_seconds(self, total: int) -> Optional[float]:
        """Estimate remaining seconds based on average send pace."""
        done = len([e for e in self._entries if e.status != "pending"])
        if not self._sent_times or done == 0:
            return None
        elapsed = time.time() - self._start_time if self._start_time else 0
        avg = elapsed / done
        remaining = total - done
        return avg * remaining

    # ── Export ────────────────────────────────────────────────────────────────

    def to_csv_bytes(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["#", "Phone", "Message Preview", "Full Message", "Status", "Error", "Timestamp"])
        for e in self._entries:
            writer.writerow([
                e.index,
                e.phone,
                e.message_preview,
                e.message_full,
                e.status,
                e.error_detail,
                e.timestamp,
            ])
        return buf.getvalue().encode("utf-8")

    def to_display_rows(self) -> List[Dict]:
        return [
            {
                "#": e.index,
                "Phone": e.phone,
                "Message Preview": e.message_preview,
                "Status": e.status_icon(),
                "Error": e.error_detail,
                "Time": e.timestamp,
            }
            for e in self._entries
        ]

    # ── History persistence ───────────────────────────────────────────────────

    def save_to_history(self, campaign_name: str = "") -> None:
        counts = self.counts()
        record = {
            "name": campaign_name or datetime.now().strftime("Campaign %Y-%m-%d %H:%M"),
            "timestamp": datetime.now().strftime(LOG_DATE_FORMAT),
            "sent": counts.get("sent", 0),
            "failed": counts.get("error", 0) + counts.get("invalid", 0) + counts.get("not_found", 0),
            "total": len(self._entries),
            "entries": [asdict(e) for e in self._entries],
        }
        history = _load_history()
        history.append(record)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    @staticmethod
    def failed_contacts_from_history(record: Dict) -> List[str]:
        """Extract phone numbers that failed in a past campaign."""
        return [
            e["phone"]
            for e in record.get("entries", [])
            if e.get("status") in ("error", "invalid", "not_found")
        ]


def _load_history() -> List[Dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_history() -> List[Dict]:
    return _load_history()
