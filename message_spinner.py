"""
LLM-powered message spinner — generates N unique variations of a base message.
Falls back to a local rule-based spinner when the API is unavailable.
"""

import json
import random
import re
import time
from typing import List

from config import SPINNER_BATCH_SIZE, SPINNER_MAX_RETRIES, SPINNER_SYSTEM_PROMPT

# ── Greeting / emoji pools for the local fallback spinner ─────────────────────
_GREETINGS = [
    "Hey!", "Hi!", "Hello!", "Greetings!", "Howdy!",
    "Dear customer,", "Good day!", "Hi there!",
]
_EMOJIS = ["✨", "🎉", "🔥", "💫", "🌟", "👋", "🙌", "💡", "🎊", "🚀"]


# ── Public API ────────────────────────────────────────────────────────────────

def spin_messages(
    base_message: str,
    count: int,
    provider: str,
    api_key: str,
    model: str = "",
) -> List[str]:
    """
    Generate `count` unique variations of `base_message`.

    provider: "openai" | "groq"
    Falls back to local spinner on API failure.
    """
    if count <= 0:
        return []
    if not api_key:
        return _local_spin(base_message, count)

    try:
        variations = _llm_spin(base_message, count, provider, api_key, model)
        if len(variations) == count:
            return variations
        # Pad or trim if the LLM returned wrong count
        while len(variations) < count:
            variations.extend(_local_spin(base_message, count - len(variations)))
        return variations[:count]
    except Exception:
        return _local_spin(base_message, count)


# ── LLM Spinner ───────────────────────────────────────────────────────────────

def _llm_spin(
    base_message: str,
    count: int,
    provider: str,
    api_key: str,
    model: str,
) -> List[str]:
    """Call the LLM API in batches of SPINNER_BATCH_SIZE."""
    results: List[str] = []
    batches = _chunked(count, SPINNER_BATCH_SIZE)

    for batch_count in batches:
        for attempt in range(SPINNER_MAX_RETRIES + 1):
            try:
                batch = _call_api(base_message, batch_count, provider, api_key, model)
                results.extend(batch)
                break
            except Exception as exc:
                if attempt == SPINNER_MAX_RETRIES:
                    raise exc
                time.sleep(2 ** attempt)

    return results


def _call_api(
    base_message: str,
    count: int,
    provider: str,
    api_key: str,
    model: str,
) -> List[str]:
    user_prompt = (
        f"Generate exactly {count} unique variations of the following message:\n\n"
        f"{base_message}"
    )

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        chosen_model = model or "gpt-4o-mini"
        response = client.chat.completions.create(
            model=chosen_model,
            messages=[
                {"role": "system", "content": SPINNER_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.9,
        )
        raw = response.choices[0].message.content or "[]"

    elif provider == "groq":
        from groq import Groq
        client = Groq(api_key=api_key)
        chosen_model = model or "llama3-8b-8192"
        response = client.chat.completions.create(
            model=chosen_model,
            messages=[
                {"role": "system", "content": SPINNER_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.9,
        )
        raw = response.choices[0].message.content or "[]"

    else:
        raise ValueError(f"Unknown provider: {provider}")

    return _parse_llm_output(raw, count)


def _parse_llm_output(raw: str, expected_count: int) -> List[str]:
    """Extract the JSON array from LLM output, stripping any markdown fences."""
    # Strip ```json … ``` fences if present
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract the first [...] block
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            raise ValueError("LLM did not return a JSON array")
        parsed = json.loads(match.group())

    if not isinstance(parsed, list):
        raise ValueError("LLM output is not a list")

    # Ensure uniqueness
    seen: set = set()
    unique: List[str] = []
    for item in parsed:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            unique.append(item)

    return unique


# ── Local Fallback Spinner ────────────────────────────────────────────────────

def _local_spin(base_message: str, count: int) -> List[str]:
    """
    Simple local spinner: rotates greeting and appends a random emoji.
    Not as good as the LLM version, but never fails.
    """
    variations: List[str] = []
    greetings = _GREETINGS[:]
    random.shuffle(greetings)

    for i in range(count):
        greeting = greetings[i % len(greetings)]
        emoji = random.choice(_EMOJIS)
        # Try to replace an existing greeting at the start, else prepend one
        body = re.sub(
            r"^(hey|hi|hello|greetings|dear\s+\w+)[!,.]?\s*",
            "",
            base_message,
            flags=re.IGNORECASE,
        ).strip()
        variation = f"{greeting} {body} {emoji}"
        variations.append(variation)

    return variations


# ── Helpers ───────────────────────────────────────────────────────────────────

def _chunked(total: int, size: int) -> List[int]:
    """Split `total` into a list of batch sizes no larger than `size`."""
    chunks = []
    remaining = total
    while remaining > 0:
        chunks.append(min(remaining, size))
        remaining -= size
    return chunks
