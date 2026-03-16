"""Tests for the snippet data source (directory-based storage)."""

import json
import os
import tempfile

from wenzi.scripting.sources.snippet_source import (
    SnippetSource,
    SnippetStore,
    _expand_placeholders,
    _format_snippet_file,
    _parse_frontmatter,
    _sanitize_filename,
)


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_with_keyword(self):
        text = '---\nkeyword: "@@email"\n---\nuser@example.com'
        meta, body = _parse_frontmatter(text)
        assert meta == {"keyword": "@@email"}
        assert body == "user@example.com"

    def test_single_quoted_keyword(self):
        text = "---\nkeyword: '@@hi'\n---\nHello!"
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == "@@hi"
        assert body == "Hello!"

    def test_unquoted_keyword(self):
        text = "---\nkeyword: ;;test\n---\ncontent"
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == ";;test"

    def test_no_frontmatter(self):
        text = "Just plain content"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == "Just plain content"

    def test_empty_string(self):
        meta, body = _parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_only_opening_fence(self):
        text = "---\nkeyword: @@x\nno closing fence"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_multiline_body(self):
        text = '---\nkeyword: ";;sig"\n---\nLine 1\nLine 2\nLine 3'
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == ";;sig"
        assert body == "Line 1\nLine 2\nLine 3"

    def test_comment_lines_ignored(self):
        text = '---\n# comment\nkeyword: "@@x"\n---\nbody'
        meta, body = _parse_frontmatter(text)
        assert meta == {"keyword": "@@x"}

    def test_multiple_keys(self):
        text = '---\nkeyword: "@@x"\nauthor: "me"\n---\nbody'
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == "@@x"
        assert meta["author"] == "me"


class TestFormatSnippetFile:
    def test_with_keyword(self):
        result = _format_snippet_file("@@email", "user@example.com")
        assert result == '---\nkeyword: "@@email"\n---\nuser@example.com'

    def test_without_keyword(self):
        result = _format_snippet_file("", "plain text")
        assert result == "plain text"

    def test_roundtrip(self):
        original_kw = ";;sig"
        original_content = "Best regards,\nAlice"
        text = _format_snippet_file(original_kw, original_content)
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == original_kw
        assert body == original_content


class TestSanitizeFilename:
    def test_safe_name_unchanged(self):
        assert _sanitize_filename("my-snippet") == "my-snippet"

    def test_replaces_slashes(self):
        result = _sanitize_filename("a/b\\c")
        assert "/" not in result
        assert "\\" not in result

    def test_replaces_special_chars(self):
        result = _sanitize_filename('a<b>c:d"e')
        assert "<" not in result
        assert ">" not in result

    def test_empty_string(self):
        assert _sanitize_filename("") == "snippet"

    def test_collapses_underscores(self):
        result = _sanitize_filename("a::b")
        assert "__" not in result


# ---------------------------------------------------------------------------
# SnippetStore tests
# ---------------------------------------------------------------------------


def _write_snippet(base_dir, name, keyword="", content="", category="", ext=".md"):
    """Helper to write a snippet file into the directory structure."""
    cat_dir = os.path.join(base_dir, category) if category else base_dir
    os.makedirs(cat_dir, exist_ok=True)
    file_path = os.path.join(cat_dir, f"{name}{ext}")
    text = _format_snippet_file(keyword, content)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path


class TestSnippetStore:
    def _make_store(self, setup_fn=None):
        """Create a SnippetStore with a temp directory.

        *setup_fn* receives the directory path and can create files in it.
        """
        tmpdir = tempfile.mkdtemp()
        snippets_dir = os.path.join(tmpdir, "snippets")
        if setup_fn is not None:
            os.makedirs(snippets_dir, exist_ok=True)
            setup_fn(snippets_dir)
        return SnippetStore(path=snippets_dir), snippets_dir, tmpdir

    def test_empty_directory(self):
        store, _, _ = self._make_store()
        assert store.snippets == []

    def test_load_single_md_file(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "user@example.com")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        s = store.snippets[0]
        assert s["name"] == "email"
        assert s["keyword"] == "@@email"
        assert s["content"] == "user@example.com"
        assert s["category"] == ""

    def test_load_txt_file(self):
        def setup(d):
            _write_snippet(d, "greeting", ";;hi", "Hello!", ext=".txt")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["name"] == "greeting"

    def test_load_subdirectory_category(self):
        def setup(d):
            _write_snippet(d, "work-email", "@@we", "work@co.com", category="work")
            _write_snippet(d, "personal-email", "@@pe", "me@home.com", category="personal")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        cats = {s["category"] for s in store.snippets}
        assert cats == {"work", "personal"}

    def test_nested_subdirectory(self):
        def setup(d):
            _write_snippet(d, "deep", "", "nested", category="a/b")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["category"] == "a/b"

    def test_no_frontmatter_file(self):
        def setup(d):
            path = os.path.join(d, "plain.md")
            with open(path, "w") as f:
                f.write("Just plain text, no frontmatter.")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        s = store.snippets[0]
        assert s["keyword"] == ""
        assert s["content"] == "Just plain text, no frontmatter."

    def test_hidden_files_skipped(self):
        def setup(d):
            _write_snippet(d, "visible", "", "yes")
            with open(os.path.join(d, ".hidden.md"), "w") as f:
                f.write("hidden")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["name"] == "visible"

    def test_hidden_directories_skipped(self):
        def setup(d):
            _write_snippet(d, "visible", "", "yes")
            _write_snippet(d, "hidden", "", "no", category=".secret")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1

    def test_unsupported_extension_skipped(self):
        def setup(d):
            with open(os.path.join(d, "readme.rst"), "w") as f:
                f.write("not a snippet")
            _write_snippet(d, "real", "", "yes")

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1

    def test_file_path_is_absolute(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        store, _, _ = self._make_store(setup)
        assert os.path.isabs(store.snippets[0]["file_path"])

    # -- CRUD ----------------------------------------------------------------

    def test_add_snippet(self):
        store, sdir, _ = self._make_store()
        assert store.add("greeting", ";;hi", "Hello!") is True
        assert len(store.snippets) == 1
        s = store.snippets[0]
        assert s["keyword"] == ";;hi"
        assert os.path.isfile(s["file_path"])

    def test_add_with_category(self):
        store, sdir, _ = self._make_store()
        assert store.add("sig", ";;sig", "Regards", category="work") is True
        s = store.snippets[0]
        assert s["category"] == "work"
        assert "work" in s["file_path"]

    def test_add_duplicate_keyword_rejected(self):
        store, _, _ = self._make_store()
        assert store.add("A", ";;a", "aaa") is True
        assert store.add("B", ";;a", "bbb") is False
        assert len(store.snippets) == 1

    def test_add_empty_keyword_allowed_multiple(self):
        store, _, _ = self._make_store()
        assert store.add("A", "", "aaa") is True
        assert store.add("B", "", "bbb") is True
        assert len(store.snippets) == 2

    def test_remove_snippet(self):
        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")
            _write_snippet(d, "b", ";;b", "bbb")

        store, _, _ = self._make_store(setup)
        assert store.remove("a") is True
        assert len(store.snippets) == 1
        assert store.snippets[0]["name"] == "b"

    def test_remove_with_category(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com", category="work")

        store, _, _ = self._make_store(setup)
        assert store.remove("email", category="work") is True
        assert len(store.snippets) == 0

    def test_remove_nonexistent(self):
        store, _, _ = self._make_store()
        assert store.remove("nope") is False

    def test_update_content(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "old@x.com")

        store, _, _ = self._make_store(setup)
        assert store.update("email", content="new@x.com") is True
        assert store.snippets[0]["content"] == "new@x.com"
        # Verify file on disk
        with open(store.snippets[0]["file_path"], "r") as f:
            text = f.read()
        assert "new@x.com" in text

    def test_update_keyword(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        store, _, _ = self._make_store(setup)
        assert store.update("email", new_keyword="@@email") is True
        assert store.snippets[0]["keyword"] == "@@email"

    def test_update_rename(self):
        def setup(d):
            _write_snippet(d, "old-name", "", "content")

        store, sdir, _ = self._make_store(setup)
        old_path = store.snippets[0]["file_path"]
        assert store.update("old-name", new_name="new-name") is True
        assert store.snippets[0]["name"] == "new-name"
        assert not os.path.exists(old_path)
        assert os.path.isfile(store.snippets[0]["file_path"])

    def test_update_move_category(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        store, _, _ = self._make_store(setup)
        assert store.update("email", new_category="work") is True
        assert store.snippets[0]["category"] == "work"
        assert "work" in store.snippets[0]["file_path"]

    def test_update_nonexistent(self):
        store, _, _ = self._make_store()
        assert store.update("nope", content="x") is False

    def test_find_by_keyword(self):
        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")
            _write_snippet(d, "b", ";;b", "bbb")

        store, _, _ = self._make_store(setup)
        result = store.find_by_keyword(";;b")
        assert result is not None
        assert result["name"] == "b"

    def test_find_by_keyword_missing(self):
        store, _, _ = self._make_store()
        assert store.find_by_keyword(";;x") is None

    def test_reload(self):
        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")

        store, sdir, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        # Add file externally
        _write_snippet(sdir, "b", ";;b", "bbb")
        store.reload()
        assert len(store.snippets) == 2

    def test_nonexistent_directory(self):
        store = SnippetStore(path="/tmp/nonexistent_snippet_dir_xyz")
        assert store.snippets == []

    def test_mtime_cache_avoids_rescan(self):
        """Accessing snippets twice without changes should not rescan."""
        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")

        store, _, _ = self._make_store(setup)
        _ = store.snippets  # first load
        # Patch _scan_directory to detect if it's called again
        import unittest.mock as mock
        with mock.patch.object(store, "_scan_directory") as mock_scan:
            _ = store.snippets  # second access
            mock_scan.assert_not_called()

    def test_mtime_cache_invalidated_on_file_change(self):
        """Modifying a snippet file should trigger rescan."""
        import time

        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")

        store, sdir, _ = self._make_store(setup)
        _ = store.snippets  # first load
        assert len(store.snippets) == 1

        # Modify a file — bump mtime to ensure change is detected
        file_path = os.path.join(sdir, "a.md")
        time.sleep(0.05)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(_format_snippet_file(";;a", "updated"))

        result = store.snippets
        assert len(result) == 1
        assert result[0]["content"] == "updated"

    def test_load_multi_snippet_file(self):
        def setup(d):
            path = os.path.join(d, "dates.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: "ymd "\n'
                    '    content: "{date}"\n'
                    '  - keyword: "hms "\n'
                    '    content: "{time}"\n'
                    '    name: "current time"\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        kws = {s["keyword"] for s in store.snippets}
        assert kws == {"ymd ", "hms "}
        # Check name fallback to keyword
        by_kw = {s["keyword"]: s for s in store.snippets}
        assert by_kw["ymd "]["name"] == "ymd "
        assert by_kw["hms "]["name"] == "current time"

    def test_multi_snippet_content(self):
        def setup(d):
            path = os.path.join(d, "multi.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: "@@email"\n'
                    '    content: "user@example.com"\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["content"] == "user@example.com"

    def test_multi_snippet_coexists_with_single(self):
        def setup(d):
            # Single-snippet file
            _write_snippet(d, "email", "@@email", "user@example.com")
            # Multi-snippet file
            path = os.path.join(d, "dates.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: "ymd "\n'
                    '    content: "{date}"\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        kws = {s["keyword"] for s in store.snippets}
        assert "@@email" in kws
        assert "ymd " in kws

    def test_multi_snippet_file_path_shared(self):
        def setup(d):
            path = os.path.join(d, "multi.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: ";;a"\n'
                    '    content: "aaa"\n'
                    '  - keyword: ";;b"\n'
                    '    content: "bbb"\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        # Both snippets point to the same file
        assert store.snippets[0]["file_path"] == store.snippets[1]["file_path"]

    def test_multi_and_single_in_same_file(self):
        def setup(d):
            path = os.path.join(d, "mixed.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "@@sig"\n'
                    'snippets:\n'
                    '  - keyword: "ymd "\n'
                    '    content: "{date}"\n'
                    '---\n'
                    'Best regards,\n'
                    'Alice\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        kws = {s["keyword"] for s in store.snippets}
        assert kws == {"ymd ", "@@sig"}
        # The single snippet uses body as content
        sig = [s for s in store.snippets if s["keyword"] == "@@sig"][0]
        assert "Best regards" in sig["content"]

    def test_multi_snippet_with_category(self):
        def setup(d):
            cat_dir = os.path.join(d, "work")
            os.makedirs(cat_dir)
            path = os.path.join(cat_dir, "shortcuts.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: ";;sig"\n'
                    '    content: "Best regards"\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["category"] == "work"

    def test_raw_flag_single_snippet(self):
        def setup(d):
            path = os.path.join(d, "tpl.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;tpl"\n'
                    'raw: true\n'
                    '---\n'
                    'Today is {date}\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        assert store.snippets[0]["raw"] is True

    def test_raw_flag_multi_snippet(self):
        def setup(d):
            path = os.path.join(d, "multi.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'snippets:\n'
                    '  - keyword: ";;date"\n'
                    '    content: "{date}"\n'
                    '  - keyword: ";;tpl"\n'
                    '    content: "Today is {date}"\n'
                    '    raw: true\n'
                    '---\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 2
        by_kw = {s["keyword"]: s for s in store.snippets}
        assert by_kw[";;date"]["raw"] is False
        assert by_kw[";;tpl"]["raw"] is True

    def test_raw_flag_defaults_to_false(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "user@example.com")

        store, _, _ = self._make_store(setup)
        assert store.snippets[0]["raw"] is False

    def test_mtime_cache_invalidated_on_new_file(self):
        """Adding a new snippet file should trigger rescan."""
        import time

        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")

        store, sdir, _ = self._make_store(setup)
        _ = store.snippets
        assert len(store.snippets) == 1

        time.sleep(0.05)
        _write_snippet(sdir, "b", ";;b", "bbb")

        assert len(store.snippets) == 2


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migrate_from_json(self):
        tmpdir = tempfile.mkdtemp()
        json_path = os.path.join(tmpdir, "snippets.json")
        snippets_dir = os.path.join(tmpdir, "snippets")

        data = [
            {"name": "Email", "keyword": "@@email", "content": "user@example.com"},
            {"name": "Phone", "keyword": ";;phone", "content": "123-456"},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        store = SnippetStore(path=snippets_dir)
        result = store.snippets

        assert len(result) == 2
        assert os.path.exists(json_path + ".bak")
        assert not os.path.exists(json_path)

        # Verify files were created
        names = {s["name"] for s in result}
        assert "Email" in names
        assert "Phone" in names

    def test_migrate_idempotent(self):
        tmpdir = tempfile.mkdtemp()
        json_path = os.path.join(tmpdir, "snippets.json")
        bak_path = json_path + ".bak"
        snippets_dir = os.path.join(tmpdir, "snippets")

        # Pre-existing .bak means migration already happened
        with open(bak_path, "w") as f:
            json.dump([{"name": "Old", "keyword": ";;old", "content": "old"}], f)
        with open(json_path, "w") as f:
            json.dump([{"name": "New", "keyword": ";;new", "content": "new"}], f)

        store = SnippetStore(path=snippets_dir)
        # Should skip migration because .bak exists
        assert store.snippets == []
        # Original json untouched
        assert os.path.exists(json_path)

    def test_migrate_sanitizes_filenames(self):
        tmpdir = tempfile.mkdtemp()
        json_path = os.path.join(tmpdir, "snippets.json")
        snippets_dir = os.path.join(tmpdir, "snippets")

        data = [
            {"name": "my/weird:name", "keyword": ";;w", "content": "content"},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        store = SnippetStore(path=snippets_dir)
        result = store.snippets
        assert len(result) == 1
        # Name should be sanitized
        assert "/" not in result[0]["name"]
        assert ":" not in result[0]["name"]

    def test_migrate_duplicate_names(self):
        tmpdir = tempfile.mkdtemp()
        json_path = os.path.join(tmpdir, "snippets.json")
        snippets_dir = os.path.join(tmpdir, "snippets")

        data = [
            {"name": "email", "keyword": ";;a", "content": "aaa"},
            {"name": "email", "keyword": ";;b", "content": "bbb"},
        ]
        with open(json_path, "w") as f:
            json.dump(data, f)

        store = SnippetStore(path=snippets_dir)
        result = store.snippets
        assert len(result) == 2
        names = {s["name"] for s in result}
        assert len(names) == 2  # Different names after dedup


# ---------------------------------------------------------------------------
# Expand placeholders tests
# ---------------------------------------------------------------------------


class TestExpandPlaceholders:
    def test_date_placeholder(self):
        import datetime

        result = _expand_placeholders("Today: {date}")
        expected = datetime.datetime.now().strftime("%Y-%m-%d")
        assert expected in result

    def test_time_placeholder(self):
        result = _expand_placeholders("Now: {time}")
        assert ":" in result  # HH:MM:SS format

    def test_datetime_placeholder(self):
        result = _expand_placeholders("{datetime}")
        assert "-" in result and ":" in result

    def test_no_placeholders(self):
        result = _expand_placeholders("plain text")
        assert result == "plain text"


# ---------------------------------------------------------------------------
# SnippetSource search tests
# ---------------------------------------------------------------------------


class TestSnippetSource:
    def _make_source(self, setup_fn=None):
        tmpdir = tempfile.mkdtemp()
        snippets_dir = os.path.join(tmpdir, "snippets")
        if setup_fn is not None:
            os.makedirs(snippets_dir, exist_ok=True)
            setup_fn(snippets_dir)
        store = SnippetStore(path=snippets_dir)
        return SnippetSource(store)

    def test_empty_store_returns_empty(self):
        source = self._make_source()
        assert source.search("anything") == []

    def test_empty_query_returns_all(self):
        def setup(d):
            _write_snippet(d, "a", ";;a", "aaa")
            _write_snippet(d, "b", ";;b", "bbb")

        source = self._make_source(setup)
        results = source.search("")
        assert len(results) == 2

    def test_search_by_name(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "user@example.com")
            _write_snippet(d, "phone", ";;phone", "123-456")

        source = self._make_source(setup)
        results = source.search("email")
        assert len(results) == 1
        assert "email" in results[0].title

    def test_search_by_keyword(self):
        def setup(d):
            _write_snippet(d, "greeting", ";;hi", "Hello!")

        source = self._make_source(setup)
        results = source.search(";;hi")
        assert len(results) == 1

    def test_search_by_content(self):
        def setup(d):
            _write_snippet(d, "address", ";;addr", "123 Main St")

        source = self._make_source(setup)
        results = source.search("Main")
        assert len(results) == 1

    def test_search_by_category(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com", category="work")

        source = self._make_source(setup)
        results = source.search("work")
        assert len(results) == 1

    def test_title_with_category(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "e@x.com", category="work")

        source = self._make_source(setup)
        results = source.search("")
        title = results[0].title
        assert "email" in title
        assert "[@@email]" in title
        assert "work" in title
        assert "·" in title

    def test_title_without_category(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "e@x.com")

        source = self._make_source(setup)
        results = source.search("")
        title = results[0].title
        assert "email" in title
        assert "[@@email]" in title
        assert "·" not in title

    def test_title_no_keyword_no_category(self):
        def setup(d):
            _write_snippet(d, "plain", "", "content")

        source = self._make_source(setup)
        results = source.search("")
        assert results[0].title == "plain"

    def test_item_id_format(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com", category="work")
            _write_snippet(d, "plain", "", "text")

        source = self._make_source(setup)
        results = source.search("")
        ids = {r.item_id for r in results}
        assert "sn:work/email" in ids
        assert "sn:plain" in ids

    def test_reveal_path_set(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        source = self._make_source(setup)
        results = source.search("")
        assert results[0].reveal_path is not None
        assert os.path.isfile(results[0].reveal_path)

    def test_fuzzy_match(self):
        def setup(d):
            _write_snippet(d, "quick-response", ";;qr", "Thanks!")

        source = self._make_source(setup)
        results = source.search("qr")
        assert len(results) == 1

    def test_has_action_and_secondary(self):
        def setup(d):
            _write_snippet(d, "test", ";;t", "hello")

        source = self._make_source(setup)
        results = source.search("test")
        assert results[0].action is not None
        assert results[0].secondary_action is not None

    def test_preview_is_text_type(self):
        def setup(d):
            _write_snippet(d, "test", ";;t", "hello world")

        source = self._make_source(setup)
        results = source.search("test")
        assert results[0].preview["type"] == "text"
        assert results[0].preview["content"] == "hello world"

    def test_as_chooser_source(self):
        source = self._make_source()
        cs = source.as_chooser_source()
        assert cs.name == "snippets"
        assert cs.prefix == "sn"
        assert cs.priority == 3
        assert cs.search is not None

    def test_long_content_truncated_in_subtitle(self):
        def setup(d):
            _write_snippet(d, "long", ";;l", "a" * 100)

        source = self._make_source(setup)
        results = source.search("long")
        assert len(results[0].subtitle) <= 60
        assert results[0].subtitle.endswith("...")
