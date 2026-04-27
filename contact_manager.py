"""
Phone number parsing, cleaning, validation, and deduplication.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ContactResult:
    index: int
    original: str
    cleaned: str
    status: str          # "valid" | "invalid" | "duplicate" | "blocklisted"
    reason: str = ""


def load_blocklist(path: str) -> set:
    """Load a blocklist file — one number per line."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {_strip(line.strip()) for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def parse_contacts(
    raw_text: str,
    default_country_code: str = "+1",
    blocklist: Optional[set] = None,
) -> Tuple[List[ContactResult], List[ContactResult]]:
    """
    Parse, clean, validate, and deduplicate phone numbers from raw text.

    Accepts numbers separated by newlines, commas, semicolons, or tabs.
    Returns (valid_contacts, all_contacts).
    """
    if blocklist is None:
        blocklist = set()

    # Split on any combination of newline / comma / semicolon / tab
    tokens = re.split(r"[\n,;\t]+", raw_text)
    tokens = [t.strip() for t in tokens if t.strip()]

    seen: set = set()
    results: List[ContactResult] = []

    for idx, token in enumerate(tokens, start=1):
        cleaned = _normalize(token, default_country_code)
        status, reason = _validate(cleaned, seen, blocklist)
        if status == "valid":
            seen.add(cleaned)
        results.append(ContactResult(
            index=idx,
            original=token,
            cleaned=cleaned,
            status=status,
            reason=reason,
        ))

    valid = [r for r in results if r.status == "valid"]
    return valid, results


def parse_csv_contacts(
    file_bytes: bytes,
    phone_column: str,
    default_country_code: str = "+1",
    blocklist: Optional[set] = None,
) -> Tuple[List[ContactResult], List[ContactResult]]:
    """Parse phone numbers from a CSV file given a column name."""
    content = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    raw_numbers = []
    for row in reader:
        val = row.get(phone_column, "").strip()
        if val:
            raw_numbers.append(val)
    return parse_contacts("\n".join(raw_numbers), default_country_code, blocklist)


def get_csv_columns(file_bytes: bytes) -> List[str]:
    """Return the column headers from a CSV file."""
    content = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    return list(reader.fieldnames or [])


# ── Internals ──────────────────────────────────────────────────────────────────

def _strip(number: str) -> str:
    """Remove all non-digit characters except a leading +."""
    number = number.strip()
    has_plus = number.startswith("+")
    digits = re.sub(r"\D", "", number)
    return ("+" if has_plus else "") + digits


def _normalize(raw: str, default_cc: str) -> str:
    """
    Strip non-numeric chars and prepend the default country code
    if the number doesn't already start with + or looks international.
    """
    stripped = _strip(raw)
    if not stripped:
        return ""
    if stripped.startswith("+"):
        return stripped
    # Heuristic: if the number is long enough to already include a country code
    # (> 10 digits), assume it's correct; otherwise prepend default.
    cc_digits = re.sub(r"\D", "", default_cc)
    if len(stripped) > 10:
        return "+" + stripped
    return "+" + cc_digits + stripped


def _validate(
    number: str,
    seen: set,
    blocklist: set,
) -> Tuple[str, str]:
    """Return (status, reason) for a cleaned number."""
    if not number:
        return "invalid", "Empty after cleaning"

    digits = re.sub(r"\D", "", number)

    if len(digits) < 7:
        return "invalid", f"Too short ({len(digits)} digits)"
    if len(digits) > 15:
        return "invalid", f"Too long ({len(digits)} digits)"
    if number in seen:
        return "duplicate", "Duplicate number"
    if number in blocklist:
        return "blocklisted", "On opt-out blocklist"

    return "valid", ""
