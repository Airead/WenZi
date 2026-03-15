"""Snippet data source for the Chooser.

Manages text snippets stored in a JSON file and provides search
via the "sn" prefix.  Snippets can also be auto-expanded globally
when the user types a keyword.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from voicetext.scripting.sources import ChooserItem, ChooserSource, fuzzy_match

logger = logging.getLogger(__name__)

_DEFAULT_SNIPPETS_PATH = os.path.expanduser(
    "~/.config/VoiceText/snippets.json"
)


def _paste_text(text: str) -> None:
    """Write text to clipboard and simulate Cmd+V to paste at cursor."""
    try:
        from voicetext.input import _set_pasteboard_concealed

        import subprocess
        import time

        _set_pasteboard_concealed(text)
        time.sleep(0.05)
        subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True, timeout=5,
        )
    except Exception:
        logger.exception("Failed to paste snippet text")


def _copy_to_clipboard(text: str) -> None:
    """Write text to the system clipboard without pasting."""
    try:
        from voicetext.input import _set_pasteboard_concealed

        _set_pasteboard_concealed(text)
    except Exception:
        logger.exception("Failed to copy snippet to clipboard")


def _expand_placeholders(content: str) -> str:
    """Replace dynamic placeholders in snippet content.

    Supported placeholders:
      {date}      — current date (YYYY-MM-DD)
      {time}      — current time (HH:MM:SS)
      {datetime}  — current date and time
      {clipboard} — current clipboard text
    """
    import datetime

    now = datetime.datetime.now()
    result = content.replace("{date}", now.strftime("%Y-%m-%d"))
    result = result.replace("{time}", now.strftime("%H:%M:%S"))
    result = result.replace("{datetime}", now.strftime("%Y-%m-%d %H:%M:%S"))

    if "{clipboard}" in result:
        try:
            from AppKit import NSPasteboard

            pb = NSPasteboard.generalPasteboard()
            text = pb.stringForType_("public.utf8-plain-text")
            result = result.replace("{clipboard}", text or "")
        except Exception:
            result = result.replace("{clipboard}", "")

    return result


class SnippetStore:
    """Persistent storage for text snippets.

    Each snippet is a dict with keys: name, keyword, content.
    Stored as a JSON array in *path*.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path or _DEFAULT_SNIPPETS_PATH
        self._snippets: List[Dict[str, str]] = []
        self._loaded = False

    @property
    def snippets(self) -> List[Dict[str, str]]:
        self._ensure_loaded()
        return list(self._snippets)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._snippets = data
            logger.info("Loaded %d snippets from %s", len(self._snippets), self._path)
        except Exception:
            logger.exception("Failed to load snippets from %s", self._path)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._snippets, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Failed to save snippets to %s", self._path)

    def add(self, name: str, keyword: str, content: str) -> None:
        """Add a new snippet."""
        self._ensure_loaded()
        self._snippets.append({
            "name": name,
            "keyword": keyword,
            "content": content,
        })
        self._save()

    def remove(self, keyword: str) -> bool:
        """Remove a snippet by keyword. Returns True if found."""
        self._ensure_loaded()
        before = len(self._snippets)
        self._snippets = [s for s in self._snippets if s.get("keyword") != keyword]
        if len(self._snippets) < before:
            self._save()
            return True
        return False

    def update(
        self, keyword: str, name: Optional[str] = None,
        new_keyword: Optional[str] = None, content: Optional[str] = None,
    ) -> bool:
        """Update an existing snippet by keyword. Returns True if found."""
        self._ensure_loaded()
        for s in self._snippets:
            if s.get("keyword") == keyword:
                if name is not None:
                    s["name"] = name
                if new_keyword is not None:
                    s["keyword"] = new_keyword
                if content is not None:
                    s["content"] = content
                self._save()
                return True
        return False

    def find_by_keyword(self, keyword: str) -> Optional[Dict[str, str]]:
        """Find a snippet by exact keyword match."""
        self._ensure_loaded()
        for s in self._snippets:
            if s.get("keyword") == keyword:
                return s
        return None

    def reload(self) -> None:
        """Force reload from disk."""
        self._loaded = False
        self._snippets = []
        self._ensure_loaded()


class SnippetSource:
    """Snippet search data source for the Chooser.

    Activated via the "sn" prefix.  Searches by name, keyword, and
    content using fuzzy matching.
    """

    def __init__(self, store: SnippetStore) -> None:
        self._store = store

    def search(self, query: str) -> List[ChooserItem]:
        """Search snippets by name, keyword, or content."""
        snippets = self._store.snippets

        if not snippets:
            return []

        q = query.strip()
        results: list[tuple[int, Dict[str, str]]] = []

        for s in snippets:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")

            if not q:
                # Empty query: show all snippets
                results.append((50, s))
                continue

            best_score = 0
            for field in (name, keyword, content):
                matched, score = fuzzy_match(q, field)
                if matched and score > best_score:
                    best_score = score
            if best_score > 0:
                results.append((best_score, s))

        # Sort by score descending, then name
        results.sort(key=lambda x: (-x[0], x[1].get("name", "")))

        items = []
        for _score, s in results:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")
            display_content = content.replace("\n", " ").strip()
            if len(display_content) > 60:
                display_content = display_content[:57] + "..."

            items.append(
                ChooserItem(
                    title=f"{name}  [{keyword}]" if keyword else name,
                    subtitle=display_content,
                    action=lambda c=content: _paste_text(
                        _expand_placeholders(c)
                    ),
                    secondary_action=lambda c=content: _copy_to_clipboard(
                        _expand_placeholders(c)
                    ),
                    preview={"type": "text", "content": content},
                )
            )

        return items

    def as_chooser_source(self) -> ChooserSource:
        """Return a ChooserSource wrapping this SnippetSource."""
        return ChooserSource(
            name="snippets",
            prefix="sn",
            search=self.search,
            priority=3,
        )
