"""
File upload and attachment logic for WhatsApp Web media sending.
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from config import (
    ALLOWED_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_VIDEO_EXTENSIONS,
    MAX_FILE_SIZE_MB,
    SELECTORS,
    VIDEO_WARN_SIZE_MB,
)


class MediaFile:
    def __init__(self, path: str, original_name: str, size_mb: float, ext: str):
        self.path = path
        self.original_name = original_name
        self.size_mb = size_mb
        self.ext = ext.lower()

    @property
    def is_image_or_video(self) -> bool:
        return self.ext in IMAGE_VIDEO_EXTENSIONS

    @property
    def is_document(self) -> bool:
        return self.ext in DOCUMENT_EXTENSIONS


def validate_and_save(uploaded_files) -> Tuple[List["MediaFile"], List[str]]:
    """
    Validate Streamlit UploadedFile objects, save them to a temp dir.
    Returns (saved_files, warnings).
    """
    saved: List[MediaFile] = []
    warnings: List[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="wa_media_")

    for uf in uploaded_files:
        ext = Path(uf.name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            warnings.append(f"'{uf.name}' — unsupported type ({ext}). Skipped.")
            continue

        data = uf.read()
        size_mb = len(data) / (1024 * 1024)

        if size_mb > MAX_FILE_SIZE_MB:
            warnings.append(
                f"'{uf.name}' is {size_mb:.1f} MB — exceeds {MAX_FILE_SIZE_MB} MB limit. Skipped."
            )
            continue

        if ext == ".mp4" and size_mb > VIDEO_WARN_SIZE_MB:
            warnings.append(
                f"'{uf.name}' is {size_mb:.1f} MB — WhatsApp may compress or reject videos > {VIDEO_WARN_SIZE_MB} MB."
            )

        dest = os.path.join(tmp_dir, uf.name)
        with open(dest, "wb") as f:
            f.write(data)

        saved.append(MediaFile(path=dest, original_name=uf.name, size_mb=size_mb, ext=ext))

    return saved, warnings


async def attach_files(page, media_files: List[MediaFile]) -> bool:
    """
    Attach one or more files to the current WhatsApp chat.
    Returns True if attachment was successful.
    """
    if not media_files:
        return True

    try:
        # Click the paperclip icon
        await page.click(SELECTORS["attach_button"])
        await page.wait_for_timeout(800)

        # Separate files by type (WhatsApp uses different inputs)
        images_videos = [m for m in media_files if m.is_image_or_video]
        documents = [m for m in media_files if m.is_document]

        if images_videos:
            input_el = page.locator(SELECTORS["attach_image"]).first
            await input_el.set_input_files([m.path for m in images_videos])
            await _wait_for_preview(page)

        if documents:
            if images_videos:
                # Re-open attach menu for documents
                await page.click(SELECTORS["attach_button"])
                await page.wait_for_timeout(800)
            input_el = page.locator(SELECTORS["attach_document"]).first
            await input_el.set_input_files([m.path for m in documents])
            await _wait_for_preview(page)

        return True

    except Exception:
        return False


async def _wait_for_preview(page, timeout: int = 10_000) -> None:
    """Wait for the media thumbnail/preview to appear after file selection."""
    try:
        await page.wait_for_selector(SELECTORS["media_thumbnail"], timeout=timeout)
    except Exception:
        # Fallback: just wait a moment and continue
        await page.wait_for_timeout(3_000)
