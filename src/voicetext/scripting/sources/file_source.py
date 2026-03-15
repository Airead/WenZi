"""File search data source for the Chooser.

Uses macOS Spotlight (mdfind) for fast file search.  Activated via
the "f" prefix (e.g. "f readme").
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

from voicetext.scripting.sources import ChooserItem, ChooserSource

logger = logging.getLogger(__name__)

_MAX_RESULTS = 30
_MDFIND_TIMEOUT = 3  # seconds


def _mdfind(query: str, max_results: int = _MAX_RESULTS) -> list[str]:
    """Run mdfind and return a list of file paths."""
    try:
        result = subprocess.run(
            ["mdfind", "-name", query],
            capture_output=True,
            text=True,
            timeout=_MDFIND_TIMEOUT,
        )
        lines = result.stdout.strip().splitlines()
        return lines[:max_results]
    except subprocess.TimeoutExpired:
        logger.debug("mdfind timed out for query: %s", query)
        return []
    except Exception:
        logger.debug("mdfind failed for query: %s", query, exc_info=True)
        return []


def _open_file(path: str) -> None:
    """Open a file with the default application."""
    try:
        subprocess.Popen(  # noqa: S603
            ["open", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.exception("Failed to open file: %s", path)


def _file_type_label(path: str) -> str:
    """Return a short label describing the file type."""
    if os.path.isdir(path):
        return "Folder"
    ext = os.path.splitext(path)[1].lower()
    labels = {
        ".pdf": "PDF",
        ".txt": "Text",
        ".md": "Markdown",
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".html": "HTML",
        ".css": "CSS",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".xml": "XML",
        ".png": "Image",
        ".jpg": "Image",
        ".jpeg": "Image",
        ".gif": "Image",
        ".svg": "Image",
        ".mp4": "Video",
        ".mov": "Video",
        ".mp3": "Audio",
        ".zip": "Archive",
        ".gz": "Archive",
        ".dmg": "Disk Image",
        ".app": "Application",
    }
    return labels.get(ext, "File")


class FileSource:
    """File search data source using macOS Spotlight."""

    def __init__(self, max_results: int = _MAX_RESULTS) -> None:
        self._max_results = max_results

    def search(self, query: str) -> List[ChooserItem]:
        """Search files by name using mdfind."""
        if not query.strip():
            return []

        paths = _mdfind(query, self._max_results)
        items = []
        for path in paths:
            if not os.path.exists(path):
                continue
            name = os.path.basename(path)
            parent = os.path.dirname(path)
            # Shorten home directory
            home = os.path.expanduser("~")
            if parent.startswith(home):
                parent = "~" + parent[len(home):]

            type_label = _file_type_label(path)
            subtitle = f"{type_label}  {parent}"

            items.append(
                ChooserItem(
                    title=name,
                    subtitle=subtitle,
                    action=lambda p=path: _open_file(p),
                    reveal_path=path,
                    preview=_make_file_preview(path),
                )
            )

        return items

    def as_chooser_source(self) -> ChooserSource:
        """Return a ChooserSource wrapping this FileSource."""
        return ChooserSource(
            name="files",
            prefix="f",
            search=self.search,
            priority=3,
        )


def _make_file_preview(path: str) -> Optional[dict]:
    """Build a preview dict for a file path."""
    return {"type": "path", "content": path}
