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
    _split_random_sections,
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

    def test_auto_expand_false(self):
        text = '---\nkeyword: "@@email"\nauto_expand: false\n---\ncontent'
        meta, body = _parse_frontmatter(text)
        assert meta["auto_expand"] is False
        assert meta["keyword"] == "@@email"
        assert body == "content"

    def test_auto_expand_true(self):
        text = '---\nkeyword: "@@email"\nauto_expand: true\n---\ncontent'
        meta, body = _parse_frontmatter(text)
        assert meta["auto_expand"] is True


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

    def test_auto_expand_false(self):
        result = _format_snippet_file("@@email", "content", auto_expand=False)
        assert "auto_expand: false" in result
        assert 'keyword: "@@email"' in result

    def test_auto_expand_true_omitted(self):
        result = _format_snippet_file("@@email", "content", auto_expand=True)
        assert "auto_expand" not in result

    def test_auto_expand_false_no_keyword(self):
        result = _format_snippet_file("", "content", auto_expand=False)
        assert result.startswith("---")
        assert "auto_expand: false" in result
        assert "keyword" not in result

    def test_no_keyword_no_auto_expand_no_frontmatter(self):
        result = _format_snippet_file("", "content", auto_expand=True)
        assert result == "content"

    def test_roundtrip_auto_expand_false(self):
        text = _format_snippet_file("@@e", "body", auto_expand=False)
        meta, body = _parse_frontmatter(text)
        assert meta["keyword"] == "@@e"
        assert meta["auto_expand"] is False
        assert body == "body"


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

    def test_trailing_newlines_stripped(self):
        def setup(d):
            # Simulate editors that append trailing newlines on save
            path = os.path.join(d, "trail.md")
            with open(path, "w") as f:
                f.write("---\nkeyword: @@t\n---\nhello\n\n")
            path2 = os.path.join(d, "plain.txt")
            with open(path2, "w") as f:
                f.write("world\n")

        store, _, _ = self._make_store(setup)
        by_name = {s["name"]: s for s in store.snippets}
        assert by_name["trail"]["content"] == "hello"
        assert by_name["plain"]["content"] == "world"

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

    def test_add_with_auto_expand_false(self):
        store, sdir, _ = self._make_store()
        assert store.add("email", "@@e", "e@x.com", auto_expand=False) is True
        s = store.snippets[0]
        assert s["auto_expand"] is False
        # Verify file on disk contains auto_expand: false
        with open(s["file_path"], "r") as f:
            text = f.read()
        assert "auto_expand: false" in text

    def test_add_default_auto_expand_true(self):
        store, _, _ = self._make_store()
        assert store.add("email", "@@e", "e@x.com") is True
        assert store.snippets[0]["auto_expand"] is True

    def test_load_auto_expand_false_from_disk(self):
        def setup(d):
            path = os.path.join(d, "email.md")
            with open(path, "w") as f:
                f.write('---\nkeyword: "@@e"\nauto_expand: false\n---\ne@x.com')

        store, _, _ = self._make_store(setup)
        assert store.snippets[0]["auto_expand"] is False

    def test_load_auto_expand_default_true(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        store, _, _ = self._make_store(setup)
        assert store.snippets[0]["auto_expand"] is True

    def test_update_auto_expand(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "e@x.com")

        store, _, _ = self._make_store(setup)
        assert store.snippets[0]["auto_expand"] is True
        assert store.update("email", new_auto_expand=False) is True
        assert store.snippets[0]["auto_expand"] is False
        # Verify file on disk
        with open(store.snippets[0]["file_path"], "r") as f:
            text = f.read()
        assert "auto_expand: false" in text

    def test_update_preserves_auto_expand(self):
        store, _, _ = self._make_store()
        store.add("email", "@@e", "e@x.com", auto_expand=False)
        assert store.update("email", content="new@x.com") is True
        assert store.snippets[0]["auto_expand"] is False

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

    def test_escaped_date(self):
        result = _expand_placeholders("Use {{date}} for dates")
        assert result == "Use {date} for dates"

    def test_escaped_time(self):
        result = _expand_placeholders("{{time}}")
        assert result == "{time}"

    def test_escaped_datetime(self):
        result = _expand_placeholders("{{datetime}}")
        assert result == "{datetime}"

    def test_escaped_clipboard(self):
        result = _expand_placeholders("{{clipboard}}")
        assert result == "{clipboard}"

    def test_mixed_escaped_and_real(self):
        import datetime

        result = _expand_placeholders("today={date} literal={{date}}")
        expected_date = datetime.datetime.now().strftime("%Y-%m-%d")
        assert result == f"today={expected_date} literal={{date}}"

    def test_lone_double_braces(self):
        result = _expand_placeholders("{{ and }}")
        assert result == "{ and }"

    def test_escaped_unknown_placeholder(self):
        result = _expand_placeholders("{{unknown}}")
        assert result == "{unknown}"


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

    def test_item_has_alt_modifier(self):
        def setup(d):
            _write_snippet(d, "email", "@@email", "user@example.com")

        source = self._make_source(setup)
        results = source.search("")
        item = results[0]
        assert item.modifiers is not None
        assert "alt" in item.modifiers
        assert item.modifiers["alt"].subtitle == "Quick Edit"
        assert callable(item.modifiers["alt"].action)

    def test_action_hints_include_alt_enter(self):
        source = self._make_source()
        cs = source.as_chooser_source()
        assert cs.action_hints is not None
        assert cs.action_hints.get("alt_enter") == "Edit"

    def test_long_content_truncated_in_subtitle(self):
        def setup(d):
            _write_snippet(d, "long", ";;l", "a" * 100)

        source = self._make_source(setup)
        results = source.search("long")
        assert len(results[0].subtitle) <= 60
        assert results[0].subtitle.endswith("...")


# ---------------------------------------------------------------------------
# _split_random_sections tests
# ---------------------------------------------------------------------------


class TestSplitRandomSections:
    def test_single_section(self):
        result = _split_random_sections("Hello world")
        assert result == ["Hello world"]

    def test_two_sections(self):
        result = _split_random_sections("Section 1\n===\nSection 2")
        assert result == ["Section 1", "Section 2"]

    def test_three_sections(self):
        body = "A\n===\nB\n===\nC"
        result = _split_random_sections(body)
        assert result == ["A", "B", "C"]

    def test_four_equals_not_separator(self):
        body = "Line with ====\nstill same section"
        result = _split_random_sections(body)
        assert result == ["Line with ====\nstill same section"]

    def test_five_equals_not_separator(self):
        body = "Above\n=====\nBelow"
        result = _split_random_sections(body)
        assert result == ["Above\n=====\nBelow"]

    def test_escaped_separator(self):
        body = "Has literal\n\\===\nin content"
        result = _split_random_sections(body)
        assert result == ["Has literal\n===\nin content"]

    def test_escaped_and_real_separator(self):
        body = "Section 1\n\\===\nstill 1\n===\nSection 2"
        result = _split_random_sections(body)
        assert result == ["Section 1\n===\nstill 1", "Section 2"]

    def test_whitespace_around_separator(self):
        body = "A\n  ===  \nB"
        result = _split_random_sections(body)
        assert result == ["A", "B"]

    def test_multiline_sections(self):
        body = "Line 1\nLine 2\n===\nLine 3\nLine 4"
        result = _split_random_sections(body)
        assert result == ["Line 1\nLine 2", "Line 3\nLine 4"]

    def test_empty_sections_dropped(self):
        body = "===\nA\n===\n===\nB\n==="
        result = _split_random_sections(body)
        assert result == ["A", "B"]

    def test_only_separators(self):
        body = "===\n===\n==="
        result = _split_random_sections(body)
        assert result == []

    def test_empty_body(self):
        result = _split_random_sections("")
        assert result == []

    def test_triple_dashes_not_separator(self):
        """--- in body is NOT a separator (only === is)."""
        body = "Above\n---\nBelow"
        result = _split_random_sections(body)
        assert result == ["Above\n---\nBelow"]


# ---------------------------------------------------------------------------
# Random snippet store tests
# ---------------------------------------------------------------------------


class TestSnippetStoreRandom:
    def _make_store(self, setup_fn=None):
        tmpdir = tempfile.mkdtemp()
        snippets_dir = os.path.join(tmpdir, "snippets")
        if setup_fn is not None:
            os.makedirs(snippets_dir, exist_ok=True)
            setup_fn(snippets_dir)
        return SnippetStore(path=snippets_dir), snippets_dir, tmpdir

    def test_load_random_snippet(self):
        def setup(d):
            path = os.path.join(d, "thanks.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thank you!\n'
                    '===\n'
                    'Thanks a lot!\n'
                    '===\n'
                    'Much appreciated!\n'
                )

        store, _, _ = self._make_store(setup)
        assert len(store.snippets) == 1
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == ["Thank you!", "Thanks a lot!", "Much appreciated!"]
        assert s["keyword"] == "thx "
        # content is the joined variant text (no --- separators)
        assert s["content"] == "Thank you!\n\nThanks a lot!\n\nMuch appreciated!"

    def test_random_single_variant(self):
        def setup(d):
            path = os.path.join(d, "single.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;s"\n'
                    'random: true\n'
                    '---\n'
                    'Only one variant\n'
                )

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == ["Only one variant"]

    def test_random_with_multiline_variants(self):
        def setup(d):
            path = os.path.join(d, "sig.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;sig"\n'
                    'random: true\n'
                    '---\n'
                    'Best regards,\n'
                    'Alice\n'
                    '===\n'
                    'Kind regards,\n'
                    'Bob\n'
                )

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert s["variants"] == ["Best regards,\nAlice", "Kind regards,\nBob"]

    def test_random_with_escaped_separator(self):
        def setup(d):
            path = os.path.join(d, "hr.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;hr"\n'
                    'random: true\n'
                    '---\n'
                    'Section with\n'
                    '\\===\n'
                    'equals line\n'
                    '===\n'
                    'Another section\n'
                )

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert len(s["variants"]) == 2
        assert s["variants"][0] == "Section with\n===\nequals line"
        assert s["variants"][1] == "Another section"

    def test_random_four_equals_not_split(self):
        def setup(d):
            path = os.path.join(d, "equals.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;d"\n'
                    'random: true\n'
                    '---\n'
                    'Content with ====\n'
                    'still same variant\n'
                )

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert len(s["variants"]) == 1
        assert "====" in s["variants"][0]

    def test_non_random_snippet_no_variants(self):
        def setup(d):
            _write_snippet(d, "normal", ";;n", "plain content")

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert "variants" not in s
        assert "random" not in s

    def test_add_random_snippet(self):
        store, sdir, _ = self._make_store()
        variants = ["Thanks!", "Thank you!"]
        assert store.add(
            "thx", "thx ", "Thanks!",
            random=True, variants=variants,
        ) is True
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == variants
        # content must be synced with variants, not the raw content arg
        assert s["content"] == "Thanks!\n\nThank you!"
        # Verify file on disk has random: true
        with open(s["file_path"], "r") as f:
            text = f.read()
        assert "random: true" in text

    def test_add_random_no_variants_defaults_to_content(self):
        store, _, _ = self._make_store()
        assert store.add("t", ";;t", "fallback", random=True) is True
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == ["fallback"]

    def test_update_preserves_random(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                )

        store, _, _ = self._make_store(setup)
        # Update keyword only — random/variants should be preserved
        assert store.update("thx", new_keyword="thx2 ") is True
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == ["Thanks!", "Thank you!"]
        # Verify file on disk still has random: true
        with open(s["file_path"], "r") as f:
            text = f.read()
        assert "random: true" in text

    def test_update_variants(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                )

        store, _, _ = self._make_store(setup)
        new_variants = ["A", "B", "C"]
        assert store.update("thx", new_variants=new_variants) is True
        s = store.snippets[0]
        assert s["variants"] == new_variants
        # content must be synced with variants
        assert s["content"] == "A\n\nB\n\nC"

    def test_update_content_on_random_syncs_variants(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                )

        store, _, _ = self._make_store(setup)
        # Update content only — variants should become single-element
        assert store.update("thx", content="New text") is True
        s = store.snippets[0]
        assert s["random"] is True
        assert s["variants"] == ["New text"]
        assert s["content"] == "New text"

    def test_update_disable_random(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                )

        store, _, _ = self._make_store(setup)
        assert store.update("thx", new_random=False) is True
        s = store.snippets[0]
        assert "random" not in s
        assert "variants" not in s
        # Verify file on disk no longer has random: true
        with open(s["file_path"], "r") as f:
            text = f.read()
        assert "random" not in text

    def test_random_false_no_variants(self):
        def setup(d):
            path = os.path.join(d, "norandom.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;n"\n'
                    'random: false\n'
                    '---\n'
                    'Section 1\n'
                    '---\n'
                    'Section 2\n'
                )

        store, _, _ = self._make_store(setup)
        s = store.snippets[0]
        assert "variants" not in s
        # Content includes the --- as plain text
        assert "---" in s["content"]


# ---------------------------------------------------------------------------
# _format_snippet_file random tests
# ---------------------------------------------------------------------------


class TestFormatSnippetFileRandom:
    def test_random_with_variants(self):
        result = _format_snippet_file(
            "thx ", "ignored",
            random=True,
            variants=["Thank you!", "Thanks!"],
        )
        assert "random: true" in result
        assert 'keyword: "thx "' in result
        assert "Thank you!\n===\nThanks!" in result

    def test_random_roundtrip(self):
        variants = ["Best regards,\nAlice", "Kind regards,\nBob"]
        text = _format_snippet_file(
            ";;sig", "",
            random=True,
            variants=variants,
        )
        meta, body = _parse_frontmatter(text)
        assert meta["random"] is True
        assert meta["keyword"] == ";;sig"
        parsed_variants = _split_random_sections(body.rstrip("\n"))
        assert parsed_variants == variants

    def test_random_escapes_literal_separator(self):
        variants = ["Has\n===\ninside", "Normal"]
        text = _format_snippet_file(
            ";;t", "",
            random=True,
            variants=variants,
        )
        meta, body = _parse_frontmatter(text)
        parsed = _split_random_sections(body.rstrip("\n"))
        assert parsed == variants

    def test_random_no_variants_uses_content(self):
        result = _format_snippet_file(";;t", "fallback", random=True)
        assert "random: true" in result
        assert "fallback" in result


# ---------------------------------------------------------------------------
# SnippetSource random chooser tests
# ---------------------------------------------------------------------------


class TestSnippetSourceRandom:
    def _make_source(self, setup_fn=None):
        tmpdir = tempfile.mkdtemp()
        snippets_dir = os.path.join(tmpdir, "snippets")
        if setup_fn is not None:
            os.makedirs(snippets_dir, exist_ok=True)
            setup_fn(snippets_dir)
        store = SnippetStore(path=snippets_dir)
        return SnippetSource(store)

    def test_random_title_shows_variant_count(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                    '===\n'
                    'Much appreciated!\n'
                )

        source = self._make_source(setup)
        results = source.search("")
        assert len(results) == 1
        assert "(3 variants)" in results[0].title

    def test_random_single_variant_no_count_in_title(self):
        def setup(d):
            path = os.path.join(d, "single.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: ";;s"\n'
                    'random: true\n'
                    '---\n'
                    'Only one\n'
                )

        source = self._make_source(setup)
        results = source.search("")
        assert "variants" not in results[0].title

    def test_random_preview_shows_all_variants(self):
        def setup(d):
            path = os.path.join(d, "thx.md")
            with open(path, "w") as f:
                f.write(
                    '---\n'
                    'keyword: "thx "\n'
                    'random: true\n'
                    '---\n'
                    'Thanks!\n'
                    '===\n'
                    'Thank you!\n'
                )

        source = self._make_source(setup)
        results = source.search("")
        preview = results[0].preview["content"]
        assert "Variant 1" in preview
        assert "Variant 2" in preview
        assert "Thanks!" in preview
        assert "Thank you!" in preview

    def test_normal_snippet_no_variant_in_title(self):
        def setup(d):
            _write_snippet(d, "normal", ";;n", "content")

        source = self._make_source(setup)
        results = source.search("")
        assert "variants" not in results[0].title
