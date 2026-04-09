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

Random variant file format::

    ---
    keyword: "thx "
    random: true
    ---
    Thank you so much!
    ===
    Thanks for your help!
    ===
    Much appreciated!

When ``random: true`` is set, the body is split by ``===`` lines (exactly
three equals signs, stripped) into multiple variants.  On each expansion a
random variant is selected.  Use ``\\===`` to output a literal ``===``.

Snippets can also be auto-expanded globally when the user types a keyword.
"""

from __future__ import annotations

import json
import logging
import os
import random as _random
import re
import time

from wenzi.config import DEFAULT_SNIPPET_LAST_CATEGORY_PATH as _CFG_LAST_CAT
from wenzi.config import DEFAULT_SNIPPETS_DIR as _CFG_SNIPPETS_DIR
from wenzi.scripting.sources import (
    ChooserItem,
    ChooserSource,
    ModifierAction,
    copy_to_clipboard,
    fuzzy_match_fields,
    paste_text,
)

logger = logging.getLogger(__name__)

_DEFAULT_SNIPPETS_DIR = os.path.expanduser(_CFG_SNIPPETS_DIR)
_DEFAULT_LAST_CAT_PATH = os.path.expanduser(_CFG_LAST_CAT)

_SUPPORTED_EXTENSIONS = (".md", ".txt")

# Characters not allowed in filenames
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _parse_scalar(val: str):
    """Coerce a raw YAML-like scalar string to a Python type."""
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def _parse_simple_keyval(text: str) -> dict:
    """Parse the subset of YAML used by snippet frontmatter.

    Supports top-level ``key: value`` scalars and one level of list-of-dicts
    (``snippets:`` with ``- key: value`` entries).  Comments are ignored.
    """
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        colon = stripped.find(":")
        if colon == -1:
            i += 1
            continue
        key = stripped[:colon].strip()
        val_part = stripped[colon + 1:].strip()

        # List-of-dicts: key with no inline value, followed by "  - key: val"
        if not val_part and key:
            items: list[dict] = []
            i += 1
            while i < len(lines):
                ln = lines[i]
                ls = ln.lstrip()
                if not ls or ls.startswith("#"):
                    i += 1
                    continue
                # Detect indented "- key: val" (new list item)
                if ls.startswith("- "):
                    entry: dict = {}
                    item_str = ls[2:]  # strip "- "
                    c2 = item_str.find(":")
                    if c2 != -1:
                        entry[item_str[:c2].strip()] = _parse_scalar(item_str[c2 + 1:].strip())
                    items.append(entry)
                    i += 1
                    continue
                # Continuation key of current list item (indented, no "- ")
                indent = len(ln) - len(ls)
                if indent >= 2 and items:
                    c2 = ls.find(":")
                    if c2 != -1:
                        items[-1][ls[:c2].strip()] = _parse_scalar(ls[c2 + 1:].strip())
                    i += 1
                    continue
                break  # back to top-level
            result[key] = items
            continue

        if key:
            result[key] = _parse_scalar(val_part)
        i += 1
    return result


def _parse_frontmatter(text: str) -> tuple[dict, str]:
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
        meta = _parse_simple_keyval(header)
    except Exception:
        logger.warning("Failed to parse YAML frontmatter, falling back to empty")
        meta = {}

    return meta, body


def _split_random_sections(body: str) -> list[str]:
    """Split *body* into variant sections separated by ``===`` lines.

    A separator is a line whose stripped content is exactly ``===`` (three
    equals signs).  ``====`` or longer are NOT separators.

    ``\\===`` on its own line is an escape — it produces a literal ``===``
    in the output and is not treated as a separator.

    Returns a list of section strings with leading/trailing whitespace
    stripped per section.
    """
    lines = body.split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "===":
            sections.append("\n".join(current))
            current = []
        elif stripped == "\\===":
            current.append(line.replace("\\===", "===", 1))
        else:
            current.append(line)

    sections.append("\n".join(current))

    # Strip each section and drop empty leading/trailing sections that
    # result from separators at the very start or end of the body.
    result = [s.strip() for s in sections]
    return [s for s in result if s]


def _format_snippet_file(
    keyword: str, content: str, auto_expand: bool = True,
    *, random: bool = False, variants: list[str] | None = None,
) -> str:
    """Serialize a snippet back to the file format.

    When *random* is ``True`` and *variants* is provided, the body is
    composed by joining variants with ``===`` separators.  Any literal
    ``===`` lines inside a variant are escaped as ``\\===``.
    """
    has_frontmatter = bool(keyword) or not auto_expand or random
    if not has_frontmatter:
        return content

    lines = []
    if keyword:
        lines.append(f'keyword: "{keyword}"')
    if random:
        lines.append("random: true")
    if not auto_expand:
        lines.append("auto_expand: false")
    header = "\n".join(lines)

    # Build body from variants or single content
    if random and variants:
        escaped = []
        for v in variants:
            # Escape literal === lines inside variant content
            v_lines = v.split("\n")
            v_lines = [
                ln.replace("===", "\\===", 1) if ln.strip() == "===" else ln
                for ln in v_lines
            ]
            escaped.append("\n".join(v_lines))
        body = "\n===\n".join(escaped)
    else:
        body = content

    return f"---\n{header}\n---\n{body}"


def _sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters in *name*."""
    result = _UNSAFE_CHARS.sub("_", name)
    # Collapse multiple underscores
    result = re.sub(r"_+", "_", result).strip("_. ")
    return result or "snippet"


def _trash_file(path: str) -> None:
    """Move a file to the macOS Trash, falling back to os.remove."""
    try:
        from Foundation import NSURL, NSFileManager

        url = NSURL.fileURLWithPath_(path)
        fm = NSFileManager.defaultManager()
        ok, _, err = fm.trashItemAtURL_resultingItemURL_error_(url, None, None)
        if not ok:
            raise OSError(str(err) if err else "trashItemAtURL failed")
    except ImportError:
        os.remove(path)


def _delete_and_notify(store, name: str, category: str) -> None:
    """Remove a snippet and show a HUD notification."""
    path = store.snippet_path(name, category)
    ok = store.remove(name, category)
    if ok:
        try:
            from PyObjCTools import AppHelper

            home = os.path.expanduser("~")
            display = path.replace(home, "~")

            def _hud():
                from wenzi.ui.hud import show_hud
                show_hud(f"Trashed\n{display}")

            AppHelper.callAfter(_hud)
        except Exception:
            logger.debug("Failed to show delete HUD", exc_info=True)



def _expand_placeholders(content: str) -> str:
    """Replace dynamic placeholders in snippet content.

    Supported placeholders:
      {date}      — current date (YYYY-MM-DD)
      {time}      — current time (HH:MM:SS)
      {datetime}  — current date and time
      {clipboard} — current clipboard text

    Use doubled braces to output a literal brace/placeholder:
      ``{{date}}`` → ``{date}`` (not expanded)
      ``{{``       → ``{``
      ``}}``       → ``}``
    """
    import datetime

    # Protect escaped braces with sentinels before expanding placeholders
    _LBRACE = "\x00LBRACE\x00"
    _RBRACE = "\x00RBRACE\x00"
    result = content.replace("{{", _LBRACE)
    result = result.replace("}}", _RBRACE)

    now = datetime.datetime.now()
    result = result.replace("{date}", now.strftime("%Y-%m-%d"))
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

    # Restore escaped braces
    result = result.replace(_LBRACE, "{")
    result = result.replace(_RBRACE, "}")

    return result


# ---------------------------------------------------------------------------
# SnippetStore — directory-based storage
# ---------------------------------------------------------------------------


class SnippetStore:
    """Persistent storage for text snippets using a directory structure.

    Each snippet is a ``.md`` or ``.txt`` file. Subdirectories act as
    categories.  Optional YAML frontmatter holds the keyword.
    """

    def __init__(
        self,
        path: str | None = None,
        last_category_path: str | None = None,
    ) -> None:
        self._dir = path or _DEFAULT_SNIPPETS_DIR
        self._last_cat_path = last_category_path or _DEFAULT_LAST_CAT_PATH
        self._snippets: list[dict[str, str]] = []
        self._migrated = False
        self._cached_mtime: float = 0.0  # max mtime across directory tree
        self._last_mtime_check: float = 0.0  # monotonic time of last os.walk

    @property
    def snippets(self) -> list[dict[str, str]]:
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
        # Skip the expensive os.walk mtime check if we checked recently
        now = time.monotonic()
        if self._snippets and now - self._last_mtime_check < 2.0:
            return
        current_mtime = self._get_dir_tree_mtime()
        self._last_mtime_check = now
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
                    with open(file_path, encoding="utf-8") as f:
                        text = f.read()
                except Exception:
                    logger.exception("Failed to read snippet %s", file_path)
                    continue

                meta, body = _parse_frontmatter(text)
                base_name = os.path.splitext(fname)[0]
                auto_expand = str(meta.get("auto_expand", True)).lower() != "false"

                # Multi-snippet: frontmatter contains a "snippets" list
                snippets_list = meta.get("snippets")
                if isinstance(snippets_list, list):
                    for entry in snippets_list:
                        if not isinstance(entry, dict):
                            continue
                        kw = entry.get("keyword", "")
                        ct = entry.get("content", "").rstrip("\n")
                        nm = entry.get("name", "") or kw or base_name
                        raw = bool(entry.get("raw", False))
                        entry_ae = entry.get("auto_expand")
                        if entry_ae is not None:
                            entry_auto = str(entry_ae).lower() != "false"
                        else:
                            entry_auto = auto_expand
                        self._snippets.append({
                            "name": nm,
                            "keyword": kw,
                            "content": ct,
                            "category": category,
                            "file_path": file_path,
                            "raw": raw,
                            "auto_expand": entry_auto,
                        })

                # Single-snippet: keyword in frontmatter, body is content
                if meta.get("keyword") or (body and not snippets_list):
                    is_random = str(meta.get("random", False)).lower() == "true"
                    snippet_body = body.rstrip("\n")

                    if is_random:
                        variants = _split_random_sections(snippet_body)
                        # Store joined variant text as content so that
                        # fuzzy search and subtitle display see clean text
                        # without === separators or \=== escapes.
                        display_content = "\n\n".join(variants)
                    else:
                        variants = None
                        display_content = snippet_body

                    snippet_dict = {
                        "name": base_name,
                        "keyword": meta.get("keyword", ""),
                        "content": display_content,
                        "category": category,
                        "file_path": file_path,
                        "raw": bool(meta.get("raw", False)),
                        "auto_expand": auto_expand,
                    }

                    if is_random:
                        snippet_dict["random"] = True
                        snippet_dict["variants"] = variants

                    self._snippets.append(snippet_dict)

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
            with open(json_path, encoding="utf-8") as f:
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
        self,
        name: str,
        keyword: str,
        content: str,
        category: str = "",
        auto_expand: bool = True,
        *,
        random: bool = False,
        variants: list[str] | None = None,
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

        text = _format_snippet_file(
            keyword, content, auto_expand=auto_expand,
            random=random, variants=variants,
        )
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            logger.exception("Failed to write snippet %s", file_path)
            return False

        actual_variants = (variants or [content]) if random else None
        snippet_dict: dict = {
            "name": safe_name,
            "keyword": keyword,
            "content": "\n\n".join(actual_variants) if actual_variants else content,
            "category": category,
            "file_path": file_path,
            "auto_expand": auto_expand,
        }
        if random:
            snippet_dict["random"] = True
            snippet_dict["variants"] = actual_variants
        self._snippets.append(snippet_dict)
        return True

    def remove(self, name: str, category: str = "") -> bool:
        """Remove a snippet by name and category. Returns True if found."""
        self._ensure_loaded()
        for i, s in enumerate(self._snippets):
            if s["name"] == name and s.get("category", "") == category:
                file_path = s["file_path"]
                try:
                    _trash_file(file_path)
                except Exception:
                    logger.exception("Failed to trash %s", file_path)
                self._snippets.pop(i)
                return True
        return False

    def update(
        self,
        name: str,
        category: str = "",
        *,
        new_name: str | None = None,
        new_keyword: str | None = None,
        content: str | None = None,
        new_category: str | None = None,
        new_auto_expand: bool | None = None,
        new_random: bool | None = None,
        new_variants: list[str] | None = None,
    ) -> bool:
        """Update an existing snippet. Supports rename and category move."""
        self._ensure_loaded()
        for s in self._snippets:
            if s["name"] == name and s.get("category", "") == category:
                kw = new_keyword if new_keyword is not None else s["keyword"]
                ct = content if content is not None else s["content"]
                nm = _sanitize_filename(new_name) if new_name is not None else s["name"]
                cat = new_category if new_category is not None else s.get("category", "")
                ae = new_auto_expand if new_auto_expand is not None else s.get("auto_expand", True)
                is_random = new_random if new_random is not None else s.get("random", False)
                vts = new_variants if new_variants is not None else s.get("variants")

                # Sync variants when only content is updated on a random snippet
                if is_random and content is not None and new_variants is None:
                    vts = [ct]

                # Determine new file path
                cat_dir = os.path.join(self._dir, cat) if cat else self._dir
                ext = os.path.splitext(s["file_path"])[1]
                new_path = os.path.join(cat_dir, f"{nm}{ext}")

                os.makedirs(cat_dir, exist_ok=True)
                text = _format_snippet_file(
                    kw, ct, auto_expand=ae,
                    random=is_random, variants=vts,
                )
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
                s["category"] = cat
                s["file_path"] = new_path
                s["auto_expand"] = ae
                if is_random:
                    s["random"] = True
                    # Sync content and variants:
                    # - new_variants provided → update content from variants
                    # - only content provided → treat as single variant
                    if new_variants is not None:
                        s["variants"] = vts
                        s["content"] = "\n\n".join(vts)
                    elif content is not None:
                        s["variants"] = [ct]
                        s["content"] = ct
                    else:
                        s["variants"] = vts or [ct]
                        s["content"] = ct
                else:
                    s.pop("random", None)
                    s.pop("variants", None)
                    s["content"] = ct
                return True
        return False

    def find_by_keyword(self, keyword: str) -> dict[str, str] | None:
        """Find a snippet by exact keyword match."""
        self._ensure_loaded()
        for s in self._snippets:
            if s.get("keyword") == keyword:
                return s
        return None

    def find_by_content(self, content: str) -> dict[str, str] | None:
        """Find a snippet by exact content match."""
        self._ensure_loaded()
        for s in self._snippets:
            if s.get("content") == content:
                return s
        return None

    def snippet_path(self, name: str, category: str = "") -> str:
        """Return the file path for a snippet with the given name and category."""
        safe_name = _sanitize_filename(name)
        cat_dir = os.path.join(self._dir, category) if category else self._dir
        return os.path.join(cat_dir, f"{safe_name}.md")

    def file_exists(self, name: str, category: str = "") -> bool:
        """Check if a snippet file already exists on disk."""
        return os.path.exists(self.snippet_path(name, category))

    def reload(self) -> None:
        """Force reload from disk."""
        self._snippets = []
        self._cached_mtime = 0.0
        self._last_mtime_check = 0.0
        self._ensure_loaded()

    # -- last category persistence ------------------------------------------

    @property
    def last_category(self) -> str:
        """Read the last used category from disk."""
        try:
            with open(self._last_cat_path, encoding="utf-8") as f:
                return f.read().strip()
        except (OSError, ValueError):
            return ""

    @last_category.setter
    def last_category(self, value: str) -> None:
        """Persist the last used category to disk."""
        os.makedirs(os.path.dirname(self._last_cat_path), exist_ok=True)
        try:
            with open(self._last_cat_path, "w", encoding="utf-8") as f:
                f.write(value)
        except OSError:
            logger.exception("Failed to save last category")


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
        self._editor_panel = None

    def search(self, query: str) -> list[ChooserItem]:
        """Search snippets by name, keyword, content, or category."""
        snippets = self._store.snippets

        if not snippets:
            return []

        q = query.strip()
        results: list[tuple[int, dict[str, str]]] = []

        for s in snippets:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")
            category = s.get("category", "")

            if not q:
                results.append((50, s))
                continue

            matched, score = fuzzy_match_fields(q, (name, keyword, content, category))
            if matched:
                results.append((score, s))

        results.sort(key=lambda x: (-x[0], x[1].get("name", "")))

        def _resolve(c, r):
            return c if r else _expand_placeholders(c)

        def _pick_and_resolve(vs, r):
            return _resolve(_random.choice(vs), r)

        items = []
        for _score, s in results:
            name = s.get("name", "")
            keyword = s.get("keyword", "")
            content = s.get("content", "")
            category = s.get("category", "")
            file_path = s.get("file_path")

            variants = s.get("variants")
            is_random = s.get("random", False) and variants

            # Build title: "Name  [@@kw]  ·  category  (N variants)"
            title = name
            if keyword:
                title = f"{name}  [{keyword}]"
            if category:
                title = f"{title}  ·  {category}"
            if is_random and len(variants) > 1:
                title = f"{title}  ({len(variants)} variants)"

            display_content = content.replace("\n", " ").strip()
            if len(display_content) > 60:
                display_content = display_content[:57] + "..."

            # item_id uses "sn:category/name" format
            if category:
                item_id = f"sn:{category}/{name}"
            else:
                item_id = f"sn:{name}"

            raw = s.get("raw", False)

            # Build preview content
            if is_random and len(variants) > 1:
                preview_parts = []
                for idx, v in enumerate(variants, 1):
                    preview_parts.append(f"── Variant {idx} ──\n{v}")
                preview_content = "\n".join(preview_parts)
            else:
                preview_content = content

            if is_random:
                def _do_edit_random(vs=variants, r=raw, fp=file_path):
                    from wenzi.scripting.ui.quick_edit_panel import (
                        open_quick_edit,
                    )
                    open_quick_edit(
                        _pick_and_resolve(vs, r), reveal_path=fp,
                    )

                items.append(
                    ChooserItem(
                        title=title,
                        subtitle=display_content,
                        item_id=item_id,
                        action=lambda vs=variants, r=raw: paste_text(
                            _pick_and_resolve(vs, r)
                        ),
                        secondary_action=lambda vs=variants, r=raw: copy_to_clipboard(
                            _pick_and_resolve(vs, r)
                        ),
                        reveal_path=file_path,
                        preview={"type": "text", "content": preview_content},
                        delete_action=lambda n=name, cat=category: _delete_and_notify(
                            self._store, n, cat,
                        ),
                        confirm_delete=True,
                        modifiers={"alt": ModifierAction(
                            subtitle="Quick Edit",
                            action=_do_edit_random,
                        )},
                    )
                )
            else:
                def _do_edit(c=content, r=raw, fp=file_path):
                    from wenzi.scripting.ui.quick_edit_panel import (
                        open_quick_edit,
                    )
                    open_quick_edit(_resolve(c, r), reveal_path=fp)

                items.append(
                    ChooserItem(
                        title=title,
                        subtitle=display_content,
                        item_id=item_id,
                        action=lambda c=content, r=raw: paste_text(
                            _resolve(c, r)
                        ),
                        secondary_action=lambda c=content, r=raw: copy_to_clipboard(
                            _resolve(c, r)
                        ),
                        reveal_path=file_path,
                        preview={"type": "text", "content": content},
                        delete_action=lambda n=name, cat=category: _delete_and_notify(
                            self._store, n, cat,
                        ),
                        confirm_delete=True,
                        modifiers={"alt": ModifierAction(
                            subtitle="Quick Edit",
                            action=_do_edit,
                        )},
                    )
                )

        return items

    def create_snippet(self, query: str = "") -> None:
        """Open the snippet editor panel to create a new snippet.

        Args:
            query: Pre-fill the keyword field.
        """
        from PyObjCTools import AppHelper

        def _show():
            from wenzi.scripting.ui.snippet_editor_panel import (
                SnippetEditorPanel,
            )

            self._editor_panel = SnippetEditorPanel(self._store)
            self._editor_panel.show(initial_query=query)

        AppHelper.callAfter(_show)

    def as_chooser_source(self, prefix: str = "sn") -> ChooserSource:
        """Return a ChooserSource wrapping this SnippetSource."""
        from wenzi.i18n import t

        return ChooserSource(
            name="snippets",
            display_name=t("chooser.source.snippets"),
            prefix=prefix,
            search=self.search,
            priority=3,
            description="Text snippets",
            action_hints={
                "enter": t("chooser.action.paste"),
                "cmd_enter": t("chooser.action.copy"),
                "alt_enter": t("chooser.action.edit"),
                "delete": t("chooser.action.delete"),
            },
            show_preview=True,
            create_action=self.create_snippet,
        )
