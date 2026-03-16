"""Snippet data source for the Chooser.

Manages text snippets stored as individual files in a directory structure.
Subdirectories serve as categories. Each file uses optional YAML frontmatter
for the keyword, with the body as snippet content.

Single-snippet file format::

    ---
    keyword: "@@email"
    ---
    user@example.com

Multi-snippet file format (all snippets defined in frontmatter)::

    ---
    snippets:
      - keyword: "ymd "
        content: "{date}"
      - keyword: "hms "
        content: "{time}"
        name: "current time"
    ---

Snippets can also be auto-expanded globally when the user types a keyword.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from wenzi.scripting.sources import ChooserItem, ChooserSource, fuzzy_match

logger = logging.getLogger(__name__)

_DEFAULT_SNIPPETS_DIR = os.path.expanduser("~/.config/WenZi/snippets")

_SUPPORTED_EXTENSIONS = (".md", ".txt")

# Characters not allowed in filenames
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Parse optional YAML frontmatter from *text*.

    Returns ``(metadata_dict, body)``.  If no frontmatter is present,
    returns ``({}, text)``.
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    header = text[3:end]
    body = text[end + 4:]  # skip past "\n---"
    if body.startswith("\n"):
        body = body[1:]

    try:
        import yaml

        meta = yaml.safe_load(header)
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        logger.warning("Failed to parse YAML frontmatter, falling back to empty")
        meta = {}

    return meta, body


def _format_snippet_file(keyword: str, content: str) -> str:
    """Serialize a snippet back to the file format."""
    if keyword:
        return f'---\nkeyword: "{keyword}"\n---\n{content}'
    return content


def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters in *name*."""
    result = _UNSAFE_CHARS.sub("_", name)
    # Collapse multiple underscores
    result = re.sub(r"_+", "_", result).strip("_. ")
    return result or "snippet"


def _paste_text(text: str) -> None:
    """Write text to clipboard and simulate Cmd+V to paste at cursor."""
    try:
        from wenzi.input import _set_pasteboard_concealed

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
        from wenzi.input import _set_pasteboard_concealed

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


# ---------------------------------------------------------------------------
# SnippetStore — directory-based storage
# ---------------------------------------------------------------------------


class SnippetStore:
    """Persistent storage for text snippets using a directory structure.

    Each snippet is a ``.md`` or ``.txt`` file. Subdirectories act as
    categories.  Optional YAML frontmatter holds the keyword.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._dir = path or _DEFAULT_SNIPPETS_DIR
        self._snippets: List[Dict[str, str]] = []
        self._migrated = False
        self._cached_mtime: float = 0.0  # max mtime across directory tree

    @property
    def snippets(self) -> List[Dict[str, str]]:
        self._ensure_loaded()
        return list(self._snippets)

    # -- loading -------------------------------------------------------------

    def _get_dir_tree_mtime(self) -> float:
        """Return the max mtime across the snippet directory tree.

        Checks the root directory and all subdirectories so that adding,
        editing, or deleting any snippet file is detected.
        """
        if not os.path.isdir(self._dir):
            return 0.0
        max_mtime = os.path.getmtime(self._dir)
        for dirpath, dirnames, filenames in os.walk(self._dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                try:
                    mt = os.path.getmtime(os.path.join(dirpath, name))
                    if mt > max_mtime:
                        max_mtime = mt
                except OSError:
                    pass
            try:
                mt = os.path.getmtime(dirpath)
                if mt > max_mtime:
                    max_mtime = mt
            except OSError:
                pass
        return max_mtime

    def _ensure_loaded(self) -> None:
        if not self._migrated:
            self._migrated = True
            self._maybe_migrate()
        current_mtime = self._get_dir_tree_mtime()
        if self._snippets and current_mtime == self._cached_mtime:
            return
        self._scan_directory()
        self._cached_mtime = current_mtime

    def _scan_directory(self) -> None:
        """Recursively scan the snippet directory for .md/.txt files."""
        self._snippets = []
        if not os.path.isdir(self._dir):
            return

        for dirpath, dirnames, filenames in os.walk(self._dir):
            # Skip hidden directories
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]

            rel_dir = os.path.relpath(dirpath, self._dir)
            category = "" if rel_dir == "." else rel_dir

            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                _base, ext = os.path.splitext(fname)
                if ext.lower() not in _SUPPORTED_EXTENSIONS:
                    continue

                file_path = os.path.join(dirpath, fname)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        text = f.read()
                except Exception:
                    logger.exception("Failed to read snippet %s", file_path)
                    continue

                meta, body = _parse_frontmatter(text)
                base_name = os.path.splitext(fname)[0]

                # Multi-snippet: frontmatter contains a "snippets" list
                snippets_list = meta.get("snippets")
                if isinstance(snippets_list, list):
                    for entry in snippets_list:
                        if not isinstance(entry, dict):
                            continue
                        kw = entry.get("keyword", "")
                        ct = entry.get("content", "")
                        nm = entry.get("name", "") or kw or base_name
                        raw = bool(entry.get("raw", False))
                        self._snippets.append({
                            "name": nm,
                            "keyword": kw,
                            "content": ct,
                            "category": category,
                            "file_path": file_path,
                            "raw": raw,
                        })

                # Single-snippet: keyword in frontmatter, body is content
                if meta.get("keyword") or (body and not snippets_list):
                    self._snippets.append({
                        "name": base_name,
                        "keyword": meta.get("keyword", ""),
                        "content": body,
                        "category": category,
                        "file_path": file_path,
                        "raw": bool(meta.get("raw", False)),
                    })

        logger.info(
            "Loaded %d snippets from %s", len(self._snippets), self._dir,
        )

    # -- migration -----------------------------------------------------------

    def _maybe_migrate(self) -> None:
        """Migrate from legacy ``snippets.json`` if it exists."""
        parent = os.path.dirname(self._dir)
        json_path = os.path.join(parent, "snippets.json")
        bak_path = json_path + ".bak"

        if not os.path.isfile(json_path) or os.path.exists(bak_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return

            os.makedirs(self._dir, exist_ok=True)
            used_names: set[str] = set()

            for entry in data:
                name = _sanitize_filename(entry.get("name", "snippet"))
                base_name = name
                counter = 1
                while name in used_names:
                    name = f"{base_name}_{counter}"
                    counter += 1
                used_names.add(name)

                file_path = os.path.join(self._dir, f"{name}.md")
                content = _format_snippet_file(
                    entry.get("keyword", ""),
                    entry.get("content", ""),
                )
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

            os.rename(json_path, bak_path)
            logger.info(
                "Migrated %d snippets from %s to %s",
                len(data), json_path, self._dir,
            )
        except Exception:
            logger.exception("Failed to migrate snippets from %s", json_path)

    # -- CRUD ----------------------------------------------------------------

    def add(
        self, name: str, keyword: str, content: str, category: str = "",
    ) -> bool:
        """Add a new snippet. Returns False if keyword already exists."""
        self._ensure_loaded()
        if keyword and any(s.get("keyword") == keyword for s in self._snippets):
            logger.warning("Snippet keyword %r already exists", keyword)
            return False

        safe_name = _sanitize_filename(name)
        cat_dir = os.path.join(self._dir, category) if category else self._dir
        os.makedirs(cat_dir, exist_ok=True)
        file_path = os.path.join(cat_dir, f"{safe_name}.md")

        text = _format_snippet_file(keyword, content)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            logger.exception("Failed to write snippet %s", file_path)
            return False

        self._snippets.append({
            "name": safe_name,
            "keyword": keyword,
            "content": content,
            "category": category,
            "file_path": file_path,
        })
        return True

    def remove(self, name: str, category: str = "") -> bool:
        """Remove a snippet by name and category. Returns True if found."""
        self._ensure_loaded()
        for i, s in enumerate(self._snippets):
            if s["name"] == name and s.get("category", "") == category:
                file_path = s["file_path"]
                try:
                    os.remove(file_path)
                except OSError:
                    logger.exception("Failed to delete %s", file_path)
                self._snippets.pop(i)
                return True
        return False

    def update(
        self,
        name: str,
        category: str = "",
        *,
        new_name: Optional[str] = None,
        new_keyword: Optional[str] = None,
        content: Optional[str] = None,
        new_category: Optional[str] = None,
    ) -> bool:
        """Update an existing snippet. Supports rename and category move."""
        self._ensure_loaded()
        for s in self._snippets:
            if s["name"] == name and s.get("category", "") == category:
                kw = new_keyword if new_keyword is not None else s["keyword"]
                ct = content if content is not None else s["content"]
                nm = _sanitize_filename(new_name) if new_name is not None else s["name"]
                cat = new_category if new_category is not None else s.get("category", "")

                # Determine new file path
                cat_dir = os.path.join(self._dir, cat) if cat else self._dir
                ext = os.path.splitext(s["file_path"])[1]
                new_path = os.path.join(cat_dir, f"{nm}{ext}")

                os.makedirs(cat_dir, exist_ok=True)
                text = _format_snippet_file(kw, ct)
                try:
                    with open(new_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception:
                    logger.exception("Failed to write %s", new_path)
                    return False

                # Remove old file if path changed
                old_path = s["file_path"]
                if os.path.normpath(old_path) != os.path.normpath(new_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

                s["name"] = nm
                s["keyword"] = kw
                s["content"] = ct
                s["category"] = cat
                s["file_path"] = new_path
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
        self._snippets = []
        self._cached_mtime = 0.0
        self._ensure_loaded()


# ---------------------------------------------------------------------------
# SnippetSource — Chooser data source
# ---------------------------------------------------------------------------


class SnippetSource:
    """Snippet search data source for the Chooser.

    Activated via the "sn" prefix.  Searches by name, keyword, content,
    and category using fuzzy matching.
    """

    def __init__(self, store: SnippetStore) -> None:
        self._store = store

    def search(self, query: str) -> List[ChooserItem]:
        """Search snippets by name, keyword, content, or category."""
        snippets = self._store.snippets

        if not snippets:
            return []

        q = query.strip()
        results: list[tuple[int, Dict[str, str]]] = []

        for s in snippets:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")
            category = s.get("category", "")

            if not q:
                results.append((50, s))
                continue

            best_score = 0
            for field_val in (name, keyword, content, category):
                matched, score = fuzzy_match(q, field_val)
                if matched and score > best_score:
                    best_score = score
            if best_score > 0:
                results.append((best_score, s))

        results.sort(key=lambda x: (-x[0], x[1].get("name", "")))

        items = []
        for _score, s in results:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")
            category = s.get("category", "")
            file_path = s.get("file_path")

            # Build title: "Name  [@@kw]  ·  category"
            title = name
            if keyword:
                title = f"{name}  [{keyword}]"
            if category:
                title = f"{title}  ·  {category}"

            display_content = content.replace("\n", " ").strip()
            if len(display_content) > 60:
                display_content = display_content[:57] + "..."

            # item_id uses "sn:category/name" format
            if category:
                item_id = f"sn:{category}/{name}"
            else:
                item_id = f"sn:{name}"

            raw = s.get("raw", False)

            def _resolve(c, r):
                return c if r else _expand_placeholders(c)

            items.append(
                ChooserItem(
                    title=title,
                    subtitle=display_content,
                    item_id=item_id,
                    action=lambda c=content, r=raw: _paste_text(
                        _resolve(c, r)
                    ),
                    secondary_action=lambda c=content, r=raw: _copy_to_clipboard(
                        _resolve(c, r)
                    ),
                    reveal_path=file_path,
                    preview={"type": "text", "content": content},
                )
            )

        return items

    def as_chooser_source(self, prefix: str = "sn") -> ChooserSource:
        """Return a ChooserSource wrapping this SnippetSource."""
        return ChooserSource(
            name="snippets",
            prefix=prefix,
            search=self.search,
            priority=3,
        )
