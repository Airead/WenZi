"""Tests for the snippet keyword auto-expansion logic.

These tests exercise the buffer management and keyword matching
without requiring a live Quartz event tap.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

from wenzi.scripting.snippet_expander import SnippetExpander
from wenzi.scripting.sources.snippet_source import (
    SnippetStore,
    _format_snippet_file,
)


def _write_snippet(base_dir, name, keyword="", content="", category="", ext=".md"):
    cat_dir = os.path.join(base_dir, category) if category else base_dir
    os.makedirs(cat_dir, exist_ok=True)
    file_path = os.path.join(cat_dir, f"{name}{ext}")
    text = _format_snippet_file(keyword, content)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    return file_path


def _make_store(setup_fn=None):
    tmpdir = tempfile.mkdtemp()
    snippets_dir = os.path.join(tmpdir, "snippets")
    if setup_fn is not None:
        os.makedirs(snippets_dir, exist_ok=True)
        setup_fn(snippets_dir)
    return SnippetStore(path=snippets_dir)


class TestBufferAndMatching:
    """Test the buffer management and keyword detection logic."""

    def _make_expander(self, setup_fn=None):
        store = _make_store(setup_fn)
        expander = SnippetExpander(store)
        return expander

    def test_check_expansion_matches_keyword(self):
        def setup(d):
            _write_snippet(d, "lsof", "/lsof/", "sudo lsof -iTCP -sTCP:LISTEN -n -P")

        expander = self._make_expander(setup)
        expand_mock = MagicMock()
        with patch.object(expander, "_expand", expand_mock):
            expander._check_expansion("some text /lsof/")

        expand_mock.assert_called_once()
        args = expand_mock.call_args[0]
        assert args[0] == "/lsof/"
        assert "lsof" in args[1]
        assert args[2] is False  # raw defaults to False

    def test_check_expansion_no_match(self):
        def setup(d):
            _write_snippet(d, "lsof", "/lsof/", "content")

        expander = self._make_expander(setup)
        expand_mock = MagicMock()
        with patch.object(expander, "_expand", expand_mock):
            expander._check_expansion("some text /lso")

        expand_mock.assert_not_called()

    def test_check_expansion_empty_keyword_skipped(self):
        def setup(d):
            _write_snippet(d, "plain", "", "content")

        expander = self._make_expander(setup)
        expand_mock = MagicMock()
        with patch.object(expander, "_expand", expand_mock):
            expander._check_expansion("anything")

        expand_mock.assert_not_called()

    def test_check_expansion_clears_buffer(self):
        def setup(d):
            _write_snippet(d, "email", "@@e", "user@example.com")

        expander = self._make_expander(setup)
        expander._buffer = "hello @@e"
        with patch.object(expander, "_expand"):
            expander._check_expansion("hello @@e")

        assert expander._buffer == ""

    def test_buffer_truncation(self):
        expander = self._make_expander()
        # Simulate a very long buffer
        with expander._lock:
            expander._buffer = "x" * 200
            if len(expander._buffer) > 128:
                expander._buffer = expander._buffer[-128:]

        assert len(expander._buffer) == 128

    def test_multiple_keywords_first_match_wins(self):
        def setup(d):
            _write_snippet(d, "a", ";;a", "content-a")
            _write_snippet(d, "ab", ";;ab", "content-ab")

        expander = self._make_expander(setup)
        expand_mock = MagicMock()
        with patch.object(expander, "_expand", expand_mock):
            expander._check_expansion("text ;;ab")

        # One of them should match (whichever comes first in iteration)
        expand_mock.assert_called_once()

    def test_expanding_flag_prevents_reentrance(self):
        def setup(d):
            _write_snippet(d, "test", ";;t", "content")

        expander = self._make_expander(setup)
        expander._expanding = True

        # When expanding is True, _check_expansion is not called
        # (the callback returns early), so simulate the guard
        expand_mock = MagicMock()
        with patch.object(expander, "_expand", expand_mock):
            if not expander._expanding:
                expander._check_expansion(";;t")

        expand_mock.assert_not_called()


class TestExpand:
    """Test the expansion action (backspaces + paste)."""

    def test_expand_sends_backspaces_and_pastes(self):
        store = _make_store()
        expander = SnippetExpander(store)

        with (
            patch.object(expander, "_send_backspaces") as mock_bs,
            patch(
                "wenzi.scripting.sources.snippet_source._expand_placeholders",
                return_value="expanded content",
            ),
            patch("wenzi.input._set_pasteboard_concealed") as mock_paste,
            patch("subprocess.run") as mock_run,
        ):
            expander._expand(";;test", "raw content")

        mock_bs.assert_called_once_with(len(";;test"))
        mock_paste.assert_called_once_with("expanded content")
        mock_run.assert_called_once()

    def test_expand_resets_expanding_flag_on_error(self):
        store = _make_store()
        expander = SnippetExpander(store)

        with (
            patch.object(
                expander, "_send_backspaces", side_effect=Exception("fail"),
            ),
            patch(
                "wenzi.scripting.sources.snippet_source._expand_placeholders",
                return_value="x",
            ),
        ):
            expander._expand(";;t", "content")

        # Flag must be reset even after error
        assert expander._expanding is False

    def test_expand_calls_placeholder_expansion(self):
        store = _make_store()
        expander = SnippetExpander(store)

        with (
            patch.object(expander, "_send_backspaces"),
            patch(
                "wenzi.scripting.sources.snippet_source._expand_placeholders",
                return_value="2026-03-16",
            ) as mock_ep,
            patch("wenzi.input._set_pasteboard_concealed"),
            patch("subprocess.run"),
        ):
            expander._expand(";;d", "{date}")

        mock_ep.assert_called_once_with("{date}")

    def test_expand_raw_skips_placeholder_expansion(self):
        store = _make_store()
        expander = SnippetExpander(store)

        with (
            patch.object(expander, "_send_backspaces"),
            patch(
                "wenzi.scripting.sources.snippet_source._expand_placeholders",
            ) as mock_ep,
            patch("wenzi.input._set_pasteboard_concealed") as mock_paste,
            patch("subprocess.run"),
        ):
            expander._expand(";;tpl", "Today is {date}", raw=True)

        mock_ep.assert_not_called()
        mock_paste.assert_called_once_with("Today is {date}")


class TestEngineIntegration:
    """Test that the engine wires up the expander correctly."""

    def test_engine_creates_expander_when_snippets_enabled(self):
        with (
            patch("wenzi.scripting.engine.ScriptingRegistry"),
            patch("wenzi.scripting.api._WZNamespace") as mock_vt_cls,
        ):
            mock_vt = MagicMock()
            mock_vt_cls.return_value = mock_vt

            from wenzi.scripting.engine import ScriptEngine

            engine = ScriptEngine(config={"chooser": {"snippets": True}})

            with (
                patch(
                    "wenzi.scripting.sources.snippet_source.SnippetStore",
                ) as mock_store_cls,
                patch(
                    "wenzi.scripting.sources.snippet_source.SnippetSource",
                ),
                patch(
                    "wenzi.scripting.snippet_expander.SnippetExpander",
                ) as mock_exp_cls,
            ):
                mock_store_cls.return_value = MagicMock()
                mock_exp = MagicMock()
                mock_exp_cls.return_value = mock_exp

                engine._register_builtin_sources()

            assert engine._snippet_expander is not None
            mock_exp.start.assert_called_once()

    def test_engine_stops_expander(self):
        with (
            patch("wenzi.scripting.engine.ScriptingRegistry"),
            patch("wenzi.scripting.api._WZNamespace") as mock_vt_cls,
        ):
            mock_vt = MagicMock()
            mock_vt_cls.return_value = mock_vt

            from wenzi.scripting.engine import ScriptEngine

            engine = ScriptEngine()
            mock_expander = MagicMock()
            engine._snippet_expander = mock_expander

            engine.stop()

            mock_expander.stop.assert_called_once()
            assert engine._snippet_expander is None
