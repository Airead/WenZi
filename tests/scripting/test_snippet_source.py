"""Tests for the snippet data source."""

import json
import os
import tempfile

from voicetext.scripting.sources.snippet_source import (
    SnippetSource,
    SnippetStore,
    _expand_placeholders,
)


class TestSnippetStore:
    def _make_store(self, snippets=None):
        """Create a SnippetStore with a temp file."""
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "snippets.json")
        if snippets is not None:
            with open(path, "w") as f:
                json.dump(snippets, f)
        return SnippetStore(path=path), path

    def test_empty_store(self):
        store, _ = self._make_store()
        assert store.snippets == []

    def test_load_from_file(self):
        snippets = [
            {"name": "Email", "keyword": "@@email", "content": "user@example.com"},
        ]
        store, _ = self._make_store(snippets)
        assert len(store.snippets) == 1
        assert store.snippets[0]["name"] == "Email"

    def test_add_snippet(self):
        store, path = self._make_store([])
        store.add("Greeting", ";;hi", "Hello, World!")
        assert len(store.snippets) == 1
        assert store.snippets[0]["keyword"] == ";;hi"
        # Verify persisted
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 1

    def test_remove_snippet(self):
        snippets = [
            {"name": "A", "keyword": ";;a", "content": "aaa"},
            {"name": "B", "keyword": ";;b", "content": "bbb"},
        ]
        store, _ = self._make_store(snippets)
        assert store.remove(";;a") is True
        assert len(store.snippets) == 1
        assert store.snippets[0]["keyword"] == ";;b"

    def test_remove_nonexistent(self):
        store, _ = self._make_store([])
        assert store.remove(";;nope") is False

    def test_update_snippet(self):
        snippets = [{"name": "A", "keyword": ";;a", "content": "old"}]
        store, _ = self._make_store(snippets)
        assert store.update(";;a", content="new") is True
        assert store.snippets[0]["content"] == "new"

    def test_update_nonexistent(self):
        store, _ = self._make_store([])
        assert store.update(";;nope", content="x") is False

    def test_find_by_keyword(self):
        snippets = [
            {"name": "A", "keyword": ";;a", "content": "aaa"},
            {"name": "B", "keyword": ";;b", "content": "bbb"},
        ]
        store, _ = self._make_store(snippets)
        result = store.find_by_keyword(";;b")
        assert result is not None
        assert result["name"] == "B"

    def test_find_by_keyword_missing(self):
        store, _ = self._make_store([])
        assert store.find_by_keyword(";;x") is None

    def test_reload(self):
        snippets = [{"name": "A", "keyword": ";;a", "content": "aaa"}]
        store, path = self._make_store(snippets)
        # Access to trigger load
        assert len(store.snippets) == 1
        # Write new data externally
        with open(path, "w") as f:
            json.dump([], f)
        store.reload()
        assert store.snippets == []

    def test_nonexistent_file(self):
        store = SnippetStore(path="/tmp/nonexistent_snippets.json")
        assert store.snippets == []


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


class TestSnippetSource:
    def _make_source(self, snippets=None):
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "snippets.json")
        if snippets is not None:
            with open(path, "w") as f:
                json.dump(snippets, f)
        store = SnippetStore(path=path)
        return SnippetSource(store)

    def test_empty_store_returns_empty(self):
        source = self._make_source([])
        assert source.search("anything") == []

    def test_empty_query_returns_all(self):
        snippets = [
            {"name": "A", "keyword": ";;a", "content": "aaa"},
            {"name": "B", "keyword": ";;b", "content": "bbb"},
        ]
        source = self._make_source(snippets)
        results = source.search("")
        assert len(results) == 2

    def test_search_by_name(self):
        snippets = [
            {"name": "Email", "keyword": "@@email", "content": "user@example.com"},
            {"name": "Phone", "keyword": ";;phone", "content": "123-456"},
        ]
        source = self._make_source(snippets)
        results = source.search("email")
        assert len(results) == 1
        assert "Email" in results[0].title

    def test_search_by_keyword(self):
        snippets = [
            {"name": "Greeting", "keyword": ";;hi", "content": "Hello!"},
        ]
        source = self._make_source(snippets)
        results = source.search(";;hi")
        assert len(results) == 1

    def test_search_by_content(self):
        snippets = [
            {"name": "Address", "keyword": ";;addr", "content": "123 Main St"},
        ]
        source = self._make_source(snippets)
        results = source.search("Main")
        assert len(results) == 1

    def test_fuzzy_match(self):
        snippets = [
            {"name": "Quick Response", "keyword": ";;qr", "content": "Thanks!"},
        ]
        source = self._make_source(snippets)
        results = source.search("qr")  # initials of Quick Response
        assert len(results) == 1

    def test_has_action_and_secondary(self):
        snippets = [
            {"name": "Test", "keyword": ";;t", "content": "hello"},
        ]
        source = self._make_source(snippets)
        results = source.search("test")
        assert results[0].action is not None
        assert results[0].secondary_action is not None

    def test_preview_is_text_type(self):
        snippets = [
            {"name": "Test", "keyword": ";;t", "content": "hello world"},
        ]
        source = self._make_source(snippets)
        results = source.search("test")
        assert results[0].preview["type"] == "text"
        assert results[0].preview["content"] == "hello world"

    def test_as_chooser_source(self):
        source = self._make_source([])
        cs = source.as_chooser_source()
        assert cs.name == "snippets"
        assert cs.prefix == "sn"
        assert cs.priority == 3
        assert cs.search is not None

    def test_long_content_truncated_in_subtitle(self):
        snippets = [
            {"name": "Long", "keyword": ";;l", "content": "a" * 100},
        ]
        source = self._make_source(snippets)
        results = source.search("long")
        assert len(results[0].subtitle) <= 60
        assert results[0].subtitle.endswith("...")
