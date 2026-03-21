"""Tests for ChooserPanel search logic and source management.

UI/WKWebView parts are not testable in CI — these tests cover the
pure-Python logic: source registration, search dispatch, item execution.
"""

import json
from unittest.mock import MagicMock, patch

from wenzi.scripting.sources import ChooserItem, ChooserSource, ModifierAction
from wenzi.scripting.sources.query_history import QueryHistory
from wenzi.scripting.sources.usage_tracker import UsageTracker
from wenzi.scripting.ui.chooser_panel import ChooserPanel


def _make_panel():
    """Create a ChooserPanel with _eval_js mocked (no WKWebView)."""
    panel = ChooserPanel()
    panel._eval_js = MagicMock()
    panel._page_loaded = True
    return panel


def _make_source(name, prefix=None, items=None, priority=0):
    """Create a ChooserSource with a simple substring search."""
    items = items or []

    def _search(query):
        return [i for i in items if query.lower() in i.title.lower()]

    return ChooserSource(name=name, prefix=prefix, search=_search, priority=priority)


class TestSourceRegistration:
    def test_register_source(self):
        panel = _make_panel()
        src = _make_source("apps")
        panel.register_source(src)
        assert "apps" in panel._sources

    def test_unregister_source(self):
        panel = _make_panel()
        panel.register_source(_make_source("apps"))
        panel.unregister_source("apps")
        assert "apps" not in panel._sources

    def test_unregister_nonexistent(self):
        panel = _make_panel()
        panel.unregister_source("nope")  # Should not raise

    def test_reset_clears_sources_and_trackers(self):
        panel = _make_panel()
        panel.register_source(_make_source("apps"))
        panel.register_source(_make_source("files"))
        panel._usage_tracker = MagicMock()
        panel._query_history = MagicMock()

        panel.reset()

        assert len(panel._sources) == 0
        assert panel._usage_tracker is None
        assert panel._query_history is None


class TestSearchLogic:
    def test_empty_query_returns_no_results(self):
        panel = _make_panel()
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("")
        assert panel._current_items == []

    def test_whitespace_query_returns_no_results(self):
        panel = _make_panel()
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("   ")
        assert panel._current_items == []

    def test_search_non_prefix_sources(self):
        panel = _make_panel()
        panel.register_source(
            _make_source(
                "apps",
                items=[
                    ChooserItem(title="Safari"),
                    ChooserItem(title="Slack"),
                    ChooserItem(title="WeChat"),
                ],
            )
        )
        panel._do_search("sa")
        assert len(panel._current_items) == 1
        assert panel._current_items[0].title == "Safari"

    def test_search_skips_prefix_sources(self):
        panel = _make_panel()
        panel.register_source(
            _make_source(
                "apps",
                items=[ChooserItem(title="Safari")],
            )
        )
        panel.register_source(
            _make_source(
                "clipboard",
                prefix="cb",
                items=[ChooserItem(title="Safari URL copied")],
            )
        )
        panel._do_search("Safari")
        # Should only get the apps result, not clipboard
        assert len(panel._current_items) == 1
        assert panel._current_items[0].title == "Safari"

    def test_search_with_prefix_activates_source(self):
        panel = _make_panel()
        panel.register_source(
            _make_source(
                "clipboard",
                prefix="cb",
                items=[
                    ChooserItem(title="hello world"),
                    ChooserItem(title="https://github.com"),
                ],
            )
        )
        panel._do_search("cb hello")
        assert len(panel._current_items) == 1
        assert panel._current_items[0].title == "hello world"

    def test_bare_prefix_does_not_activate_source(self):
        """Typing just the prefix (without trailing space) should NOT activate source."""
        panel = _make_panel()
        items = [ChooserItem(title="item1"), ChooserItem(title="item2")]
        panel.register_source(
            _make_source("clipboard", prefix="cb", items=items)
        )
        panel._do_search("cb")
        # Bare prefix without space falls through to general search,
        # which skips prefix sources — no results
        assert panel._current_items == []

    def test_prefix_with_space_only_activates_source(self):
        """Typing prefix + space should activate source with empty query."""
        panel = _make_panel()
        items = [ChooserItem(title="item1"), ChooserItem(title="item2")]
        panel.register_source(
            _make_source("clipboard", prefix="cb", items=items)
        )
        panel._do_search("cb ")
        # prefix + space activates source, empty query matches all
        assert len(panel._current_items) == 2

    def test_prefix_with_space_strips_prefix(self):
        """'cb hello' should search clipboard for 'hello'."""
        panel = _make_panel()
        panel.register_source(
            _make_source(
                "clipboard",
                prefix="cb",
                items=[
                    ChooserItem(title="hello world"),
                    ChooserItem(title="goodbye"),
                ],
            )
        )
        panel._do_search("cb hello")
        assert len(panel._current_items) == 1
        assert panel._current_items[0].title == "hello world"

    def test_search_merges_multiple_non_prefix_sources(self):
        panel = _make_panel()
        panel.register_source(
            _make_source(
                "apps",
                items=[ChooserItem(title="Safari")],
                priority=10,
            )
        )
        panel.register_source(
            _make_source(
                "bookmarks",
                items=[ChooserItem(title="Safari Tips")],
                priority=5,
            )
        )
        panel._do_search("Safari")
        assert len(panel._current_items) == 2
        # Higher priority source first
        assert panel._current_items[0].title == "Safari"
        assert panel._current_items[1].title == "Safari Tips"

    def test_search_error_handling(self):
        """Source raising an exception should not crash the panel."""
        panel = _make_panel()

        def bad_search(query):
            raise RuntimeError("boom")

        panel.register_source(
            ChooserSource(name="broken", search=bad_search)
        )
        panel._do_search("test")
        assert panel._current_items == []


class TestResultTruncation:
    """Tests for P0: _MAX_TOTAL_RESULTS truncation in _do_search."""

    def test_non_prefix_results_truncated(self):
        panel = _make_panel()
        items = [ChooserItem(title=f"item {i}") for i in range(80)]
        panel.register_source(
            ChooserSource(
                name="many",
                search=lambda q: [i for i in items if q.lower() in i.title.lower()],
            )
        )
        panel._do_search("item")
        assert len(panel._current_items) == panel._MAX_TOTAL_RESULTS

    def test_prefix_results_truncated(self):
        panel = _make_panel()
        items = [ChooserItem(title=f"entry {i}") for i in range(80)]
        panel.register_source(
            ChooserSource(
                name="clipboard",
                prefix="cb",
                search=lambda q: items,
            )
        )
        panel._do_search("cb ")
        assert len(panel._current_items) == panel._MAX_TOTAL_RESULTS

    def test_fewer_than_max_not_truncated(self):
        panel = _make_panel()
        items = [ChooserItem(title=f"item {i}") for i in range(5)]
        panel.register_source(
            ChooserSource(
                name="few",
                search=lambda q: items,
            )
        )
        panel._do_search("item")
        assert len(panel._current_items) == 5


class TestItemExecution:
    def test_execute_item(self):
        import time

        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="Safari", action=lambda: called.append("safari")),
            ChooserItem(title="Chrome", action=lambda: called.append("chrome")),
        ]
        # Mock close to avoid NSApp calls
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)
        # Action runs in a deferred thread with 0.15s delay
        time.sleep(0.3)
        assert called == ["safari"]
        panel.close.assert_called_once()

    def test_execute_item_no_action(self):
        """Item with no action should not crash."""
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="No Action")]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)  # Should not raise

    def test_execute_item_out_of_range(self):
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="Only")]
        with patch("PyObjCTools.AppHelper.callAfter"):
            panel._execute_item(5)  # Should not raise

    def test_reveal_item(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Safari",
                reveal_path="/Applications/Safari.app",
            ),
        ]
        panel.close = MagicMock()
        with patch("subprocess.Popen") as mock_popen, \
             patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._reveal_item(0)
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert args == ["open", "-R", "/Applications/Safari.app"]

    def test_reveal_item_no_path(self):
        """Item without reveal_path should be a no-op."""
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="No Path")]

        with patch("subprocess.Popen") as mock_popen:
            panel._reveal_item(0)
            mock_popen.assert_not_called()

    def test_secondary_action(self):
        """Cmd+Enter should call secondary_action when no reveal_path."""
        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Clipboard entry",
                secondary_action=lambda: called.append("copied"),
            ),
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._reveal_item(0)
        assert called == ["copied"]

    def test_stale_version_rejected(self):
        """Execute with an old version should be ignored."""
        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="Old", action=lambda: called.append("old")),
        ]
        panel._items_version = 5
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter"):
            panel._execute_item(0, version=3)  # stale
        assert called == []
        panel.close.assert_not_called()


class TestJSMessageHandling:
    def test_search_message(self):
        panel = _make_panel()
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._handle_js_message({"type": "search", "query": "saf"})
        assert len(panel._current_items) == 1

    def test_close_message(self):
        panel = _make_panel()
        with patch("PyObjCTools.AppHelper.callAfter") as mock_call_after:
            panel._handle_js_message({"type": "close"})
            mock_call_after.assert_called_once()

    def test_execute_message(self):
        import time

        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="Test", action=lambda: called.append(True))
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._handle_js_message({"type": "execute", "index": 0})
        time.sleep(0.3)
        assert called == [True]

    def test_request_preview_message(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Hello",
                preview={"type": "text", "content": "full text"},
            )
        ]
        panel._handle_js_message({"type": "requestPreview", "index": 0})
        call_args = panel._eval_js.call_args[0][0]
        assert "setPreview" in call_args
        assert "full text" in call_args

    def test_request_preview_no_preview(self):
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="No Preview")]
        panel._handle_js_message({"type": "requestPreview", "index": 0})
        call_args = panel._eval_js.call_args[0][0]
        assert "setPreview(null)" in call_args

    def test_request_preview_out_of_range(self):
        panel = _make_panel()
        panel._current_items = []
        panel._handle_js_message({"type": "requestPreview", "index": 5})
        call_args = panel._eval_js.call_args[0][0]
        assert "setPreview(null)" in call_args


class TestPrefixHints:
    def test_push_prefix_hints(self):
        panel = _make_panel()
        panel.register_source(_make_source("apps"))
        panel.register_source(_make_source("clipboard", prefix="cb"))
        panel._push_prefix_hints_to_js()
        call_args = panel._eval_js.call_args[0][0]
        assert "setPrefixHints" in call_args
        assert "cb clipboard" in call_args

    def test_no_prefix_sources(self):
        panel = _make_panel()
        panel.register_source(_make_source("apps"))
        panel._push_prefix_hints_to_js()
        call_args = panel._eval_js.call_args[0][0]
        assert "setPrefixHints([])" in call_args


class TestPushItemsToJS:
    def test_serializes_items(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="Safari", subtitle="Web browser",
                        reveal_path="/Applications/Safari.app"),
            ChooserItem(title="Clipboard entry"),
        ]
        panel._push_items_to_js()
        call_args = panel._eval_js.call_args[0][0]
        assert '"Safari"' in call_args
        assert '"Web browser"' in call_args
        assert '"hasReveal": true' in call_args

    def test_preview_only_for_selected_item(self):
        """Only the selected item (default 0) includes inline preview."""
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="First",
                preview={"type": "text", "content": "hello"},
            ),
            ChooserItem(
                title="Second",
                preview={"type": "text", "content": "world"},
            ),
        ]
        panel._push_items_to_js()
        call_args = panel._eval_js.call_args[0][0]
        sr_part = call_args.split(";")[0]
        inner = sr_part[len("setResults("):-1]
        json_part = inner.rsplit(",", 1)[0]
        parsed = json.loads(json_part)
        # First item (selected) has preview
        assert parsed[0]["preview"]["content"] == "hello"
        # Second item does not
        assert "preview" not in parsed[1]

    def test_version_increments(self):
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="A")]
        panel._push_items_to_js()
        v1 = panel._items_version
        panel._current_items = [ChooserItem(title="B")]
        panel._push_items_to_js()
        v2 = panel._items_version
        assert v2 == v1 + 1

    def test_has_modifiers_flag(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="App",
                modifiers={"alt": ModifierAction(subtitle="/path")},
            ),
        ]
        panel._push_items_to_js()
        call_args = panel._eval_js.call_args[0][0]
        assert '"hasModifiers": true' in call_args


class TestUsageTrackerIntegration:
    def test_usage_boosts_results(self):
        import os
        import tempfile

        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "usage.json")
        tracker = UsageTracker(path=path)

        panel = ChooserPanel(usage_tracker=tracker)
        panel._eval_js = MagicMock()
        panel._page_loaded = True

        items = [
            ChooserItem(title="Safari App", item_id="safari"),
            ChooserItem(title="Safari Tips", item_id="tips"),
            ChooserItem(title="Safari Guide", item_id="guide"),
        ]
        panel.register_source(
            ChooserSource(
                name="test",
                search=lambda q: [i for i in items if q in i.title.lower()],
            )
        )

        # Record "guide" as frequently selected for "saf" queries
        tracker.record("saf", "guide")
        tracker.record("saf", "guide")
        tracker.record("saf", "guide")

        # Search — "guide" should be boosted to the top
        panel._do_search("safari")
        ids = [item.item_id for item in panel._current_items]
        assert len(ids) == 3
        assert ids[0] == "guide"  # Most frequently selected

    def test_execute_records_usage(self):
        import os
        import tempfile

        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "usage.json")
        tracker = UsageTracker(path=path)

        panel = ChooserPanel(usage_tracker=tracker)
        panel._eval_js = MagicMock()
        panel._page_loaded = True
        panel._last_query = "saf"
        panel._current_items = [
            ChooserItem(
                title="Safari",
                item_id="app:Safari",
                action=lambda: None,
            ),
        ]
        panel.close = MagicMock()

        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)

        assert tracker.score("saf", "app:Safari") == 1


class TestCloseReactivation:
    def test_close_reactivates_previous_app(self):
        """close() should reactivate the saved previous app without raising all windows."""
        panel = _make_panel()
        mock_app = MagicMock()
        panel._previous_app = mock_app

        call_order = []
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: (call_order.append(fn), fn(*a, **kw))):
            with patch("wenzi.scripting.ui.chooser_panel.reactivate_app") as mock_reactivate, \
                 patch("wenzi.scripting.ui.chooser_panel.restore_accessory") as mock_restore:
                panel.close()
                mock_reactivate.assert_called_once_with(mock_app)
                mock_restore.assert_called_once()
                # reactivate must be called before restore_accessory
                reactivate_idx = next(
                    i for i, fn in enumerate(call_order)
                    if hasattr(fn, '__code__') and 'reactivate' in (fn.__code__.co_names if hasattr(fn.__code__, 'co_names') else ())
                    or 'activate' in getattr(fn, '__name__', '')
                )
                restore_idx = next(
                    i for i, fn in enumerate(call_order)
                    if hasattr(fn, '__code__') and 'restore' in (fn.__code__.co_names if hasattr(fn.__code__, 'co_names') else ())
                    or 'accessory' in getattr(fn, '__name__', '')
                )
                assert reactivate_idx < restore_idx

    def test_close_clears_previous_app(self):
        """close() should clear _previous_app after use."""
        panel = _make_panel()
        panel._previous_app = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._previous_app is None

    def test_close_without_previous_app(self):
        """close() should not crash when _previous_app is None."""
        panel = _make_panel()
        panel._previous_app = None
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app") as mock_reactivate, \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        mock_reactivate.assert_called_once_with(None)


class TestInitialQuery:
    def test_show_with_initial_query_queues_js(self):
        """show(initial_query=...) should store the pending query."""
        panel = _make_panel()
        panel._page_loaded = False
        panel._pending_initial_query = "cb "
        # Simulate page loaded
        panel._on_page_loaded()
        # The setInputValue call should have been made
        calls = [c[0][0] for c in panel._eval_js.call_args_list]
        set_input_calls = [c for c in calls if "setInputValue" in c]
        assert len(set_input_calls) == 1
        assert '"cb "' in set_input_calls[0]

    def test_show_without_initial_query(self):
        """show() without initial_query should not call setInputValue."""
        panel = _make_panel()
        panel._page_loaded = False
        panel._pending_initial_query = None
        panel._on_page_loaded()
        calls = [c[0][0] for c in panel._eval_js.call_args_list]
        set_input_calls = [c for c in calls if "setInputValue" in c]
        assert len(set_input_calls) == 0

    def test_initial_query_cleared_after_page_load(self):
        """Pending initial query should be consumed after page load."""
        panel = _make_panel()
        panel._page_loaded = False
        panel._pending_initial_query = "sn "
        panel._on_page_loaded()
        assert panel._pending_initial_query is None

    def test_initial_query_triggers_search(self):
        """setInputValue in JS posts a search message, which triggers _do_search."""
        panel = _make_panel()
        items = [ChooserItem(title="item1"), ChooserItem(title="item2")]
        panel.register_source(
            _make_source("clipboard", prefix="cb", items=items)
        )
        # Simulate what happens when JS calls back with the search
        panel._handle_js_message({"type": "search", "query": "cb "})
        assert len(panel._current_items) == 2


class TestQuickLookIntegration:
    def test_shift_preview_open(self):
        """shiftPreview open should create and show QL panel."""
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="test.pdf", reveal_path="/tmp/test.pdf"),
        ]
        with patch("os.path.exists", return_value=True), \
             patch(
                 "wenzi.scripting.ui.quicklook_panel.QuickLookPanel",
             ) as MockQL:
            mock_ql = MagicMock()
            MockQL.return_value = mock_ql
            panel._handle_js_message({
                "type": "shiftPreview", "open": True, "index": 0,
            })
            MockQL.assert_called_once_with(
                on_resign_key=panel._maybe_close,
                on_shift_toggle=panel._on_ql_shift_toggle,
            )
            mock_ql.show.assert_called_once_with(
                "/tmp/test.pdf", anchor_panel=panel._panel,
            )

    def test_shift_preview_close(self):
        """shiftPreview close should close QL panel."""
        panel = _make_panel()
        panel._ql_panel = MagicMock()
        panel._handle_js_message({
            "type": "shiftPreview", "open": False, "index": 0,
        })
        panel._ql_panel.close.assert_called_once()

    def test_ql_navigate_updates_preview(self):
        """qlNavigate should update the QL panel."""
        panel = _make_panel()
        panel._ql_panel = MagicMock()
        panel._ql_panel.is_visible = True
        panel._current_items = [
            ChooserItem(title="a.pdf", reveal_path="/tmp/a.pdf"),
            ChooserItem(title="b.pdf", reveal_path="/tmp/b.pdf"),
        ]
        with patch("os.path.exists", return_value=True):
            panel._handle_js_message({"type": "qlNavigate", "index": 1})
        panel._ql_panel.update.assert_called_once_with("/tmp/b.pdf")

    def test_ql_navigate_when_not_visible_is_noop(self):
        """qlNavigate without visible QL panel should be a no-op."""
        panel = _make_panel()
        panel._ql_panel = MagicMock()
        panel._ql_panel.is_visible = False
        panel._current_items = [
            ChooserItem(title="a", reveal_path="/tmp/a"),
        ]
        panel._handle_js_message({"type": "qlNavigate", "index": 0})
        panel._ql_panel.update.assert_not_called()

    def test_ql_navigate_when_no_ql_panel_is_noop(self):
        """qlNavigate without QL panel should be a no-op."""
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="a", reveal_path="/tmp/a"),
        ]
        panel._handle_js_message({"type": "qlNavigate", "index": 0})
        # Should not raise

    def test_shift_preview_no_reveal_path(self):
        """Items without reveal_path should close QL."""
        panel = _make_panel()
        panel._ql_panel = MagicMock()
        panel._current_items = [
            ChooserItem(title="no path"),
        ]
        panel._handle_js_message({
            "type": "shiftPreview", "open": True, "index": 0,
        })
        panel._ql_panel.close.assert_called_once()

    def test_close_cleans_up_ql(self):
        """close() should close QL panel."""
        panel = _make_panel()
        mock_ql = MagicMock()
        panel._ql_panel = mock_ql
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        mock_ql.close.assert_called_once()
        assert panel._ql_panel is None

    def test_ql_shift_toggle_closes_ql_and_resets_js(self):
        """Shift tap on QL panel should close QL and reset JS state."""
        panel = _make_panel()
        mock_ql = MagicMock()
        panel._ql_panel = mock_ql
        panel._on_ql_shift_toggle()
        mock_ql.close.assert_called_once()
        # Should reset JS qlPreviewOpen
        call_args = panel._eval_js.call_args[0][0]
        assert "qlPreviewOpen=false" in call_args

    def test_maybe_close_keeps_open_when_ql_is_key(self):
        """_maybe_close should not close when QL panel is the key window."""
        panel = _make_panel()
        mock_ql = MagicMock()
        mock_ql.is_key_window = True
        panel._ql_panel = mock_ql
        panel._panel = MagicMock()
        panel.close = MagicMock()

        mock_nsapp = MagicMock()
        # Return something other than chooser panel so first check doesn't match
        mock_nsapp.keyWindow.return_value = MagicMock()

        with patch("PyObjCTools.AppHelper.callLater") as mock_later:
            panel._maybe_close()
            check_fn = mock_later.call_args[0][1]

        with patch("AppKit.NSApp", mock_nsapp):
            check_fn()
        panel.close.assert_not_called()

    def test_maybe_close_keeps_open_when_chooser_is_key(self):
        """_maybe_close should not close when chooser panel is the key window."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel.close = MagicMock()

        mock_nsapp = MagicMock()
        mock_nsapp.keyWindow.return_value = panel._panel

        with patch("PyObjCTools.AppHelper.callLater") as mock_later:
            panel._maybe_close()
            check_fn = mock_later.call_args[0][1]

        with patch("AppKit.NSApp", mock_nsapp):
            check_fn()
        panel.close.assert_not_called()

    def test_maybe_close_closes_when_neither_panel_is_key(self):
        """_maybe_close should close when neither panel is key."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel.close = MagicMock()

        mock_nsapp = MagicMock()
        mock_nsapp.keyWindow.return_value = MagicMock()  # some other window

        with patch("PyObjCTools.AppHelper.callLater") as mock_later:
            panel._maybe_close()
            check_fn = mock_later.call_args[0][1]

        with patch("AppKit.NSApp", mock_nsapp):
            check_fn()
        panel.close.assert_called_once()


class TestModifierActions:
    def test_modifier_subtitle_message(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Safari",
                subtitle="Application",
                modifiers={
                    "alt": ModifierAction(subtitle="/Applications/Safari.app"),
                },
            ),
        ]
        panel._handle_js_message({
            "type": "modifierChange", "index": 0, "modifier": "alt",
        })
        call_args = panel._eval_js.call_args[0][0]
        assert "setModifierSubtitle" in call_args
        assert "/Applications/Safari.app" in call_args

    def test_modifier_release_restores_subtitle(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Safari",
                subtitle="Application",
                modifiers={
                    "alt": ModifierAction(subtitle="/path"),
                },
            ),
        ]
        panel._handle_js_message({
            "type": "modifierChange", "index": 0, "modifier": None,
        })
        call_args = panel._eval_js.call_args[0][0]
        assert "Application" in call_args

    def test_execute_with_modifier(self):
        import time

        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Safari",
                action=lambda: called.append("default"),
                modifiers={
                    "alt": ModifierAction(
                        subtitle="path",
                        action=lambda: called.append("alt"),
                    ),
                },
            ),
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0, modifier="alt")
        time.sleep(0.3)
        assert called == ["alt"]

    def test_execute_without_modifier(self):
        import time

        called = []
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Safari",
                action=lambda: called.append("default"),
                modifiers={
                    "alt": ModifierAction(
                        subtitle="path",
                        action=lambda: called.append("alt"),
                    ),
                },
            ),
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)
        time.sleep(0.3)
        assert called == ["default"]


class TestQueryHistory:
    def test_historyUp_enters_history_mode(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)
        qh.record("safari")
        qh.record("chrome")

        panel = _make_panel()
        panel._query_history = qh
        panel._handle_js_message({"type": "historyUp"})

        # Should call setHistoryQuery with newest entry
        call_args = panel._eval_js.call_args[0][0]
        assert "setHistoryQuery" in call_args
        assert "chrome" in call_args
        assert panel._history_index == 0

    def test_historyUp_navigates_older(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)
        qh.record("alpha")
        qh.record("beta")
        qh.record("gamma")

        panel = _make_panel()
        panel._query_history = qh

        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 0
        call_args = panel._eval_js.call_args[0][0]
        assert "gamma" in call_args

        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 1
        call_args = panel._eval_js.call_args[0][0]
        assert "beta" in call_args

        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 2
        call_args = panel._eval_js.call_args[0][0]
        assert "alpha" in call_args

    def test_historyUp_noop_at_oldest(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)
        qh.record("only")

        panel = _make_panel()
        panel._query_history = qh
        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 0

        # Second press should stay at 0
        panel._eval_js.reset_mock()
        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 0
        # No new JS call since we're already at oldest
        panel._eval_js.assert_not_called()

    def test_historyDown_exits_at_newest(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)
        qh.record("alpha")
        qh.record("beta")

        panel = _make_panel()
        panel._query_history = qh

        # Navigate to second entry
        panel._handle_js_message({"type": "historyUp"})
        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == 1

        # Down once → back to newest
        panel._handle_js_message({"type": "historyDown"})
        assert panel._history_index == 0

        # Down again → exit history mode
        panel._handle_js_message({"type": "historyDown"})
        assert panel._history_index == -1
        call_args = panel._eval_js.call_args[0][0]
        assert "clearInput" in call_args
        assert "exitHistoryMode" in call_args

    def test_execute_records_query_history(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)

        panel = _make_panel()
        panel._query_history = qh
        panel._last_query = "safari"
        panel._current_items = [
            ChooserItem(title="Safari", action=lambda: None),
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)

        assert qh.entries() == ["safari"]

    def test_execute_does_not_record_empty_query(self, tmp_path):
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)

        panel = _make_panel()
        panel._query_history = qh
        panel._last_query = ""
        panel._current_items = [
            ChooserItem(title="Item", action=lambda: None),
        ]
        panel.close = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            panel._execute_item(0)

        assert qh.entries() == []

    def test_exitHistory_resets_index(self):
        panel = _make_panel()
        panel._history_index = 3
        panel._handle_js_message({"type": "exitHistory"})
        assert panel._history_index == -1

    def test_close_resets_history_index(self):
        panel = _make_panel()
        panel._history_index = 5
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._history_index == -1

    def test_history_navigation_without_history(self):
        """historyUp/Down with query_history=None should not crash."""
        panel = _make_panel()
        panel._query_history = None
        panel._handle_js_message({"type": "historyUp"})
        panel._handle_js_message({"type": "historyDown"})
        # No crash, no JS calls for history
        # _eval_js may or may not be called, just ensure no exception

    def test_historyUp_with_empty_history(self, tmp_path):
        """historyUp with no recorded queries should be a no-op."""
        path = str(tmp_path / "history.json")
        qh = QueryHistory(path=path)

        panel = _make_panel()
        panel._query_history = qh
        panel._handle_js_message({"type": "historyUp"})
        assert panel._history_index == -1


# ---------------------------------------------------------------------------
# Calculator pin mode
# ---------------------------------------------------------------------------


def _make_calc_item(expr="2 + 3", result="5"):
    return ChooserItem(
        title=f"{expr} = {result}",
        subtitle="Calculator",
        item_id=f"calc:{expr}",
        action=lambda: None,
    )


class TestCalcMode:
    def test_has_calc_results_true(self):
        panel = _make_panel()
        panel._current_items = [_make_calc_item()]
        assert panel._has_calc_results() is True

    def test_has_calc_results_false(self):
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="Safari")]
        assert panel._has_calc_results() is False

    def test_has_calc_results_empty(self):
        panel = _make_panel()
        panel._current_items = []
        assert panel._has_calc_results() is False

    def test_has_calc_results_mixed(self):
        """Calc items among non-calc items should still be detected."""
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(title="Safari"),
            _make_calc_item(),
        ]
        assert panel._has_calc_results() is True

    def test_update_hides_on_deactivate_with_calc(self):
        """hidesOnDeactivate should be False when calc results are present."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._current_items = [_make_calc_item()]
        panel._update_hides_on_deactivate()
        panel._panel.setHidesOnDeactivate_.assert_called_with(False)

    def test_update_hides_on_deactivate_without_calc(self):
        """hidesOnDeactivate should be True when no calc results."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._current_items = [ChooserItem(title="Safari")]
        panel._update_hides_on_deactivate()
        panel._panel.setHidesOnDeactivate_.assert_called_with(True)

    def test_update_hides_on_deactivate_no_panel(self):
        """Should not crash when panel is None."""
        panel = _make_panel()
        panel._panel = None
        panel._current_items = [_make_calc_item()]
        panel._update_hides_on_deactivate()  # Should not raise

    def test_do_search_sets_hides_on_deactivate_for_calc(self):
        """_do_search should set hidesOnDeactivate=False when calc results appear."""
        panel = _make_panel()
        panel._panel = MagicMock()
        calc_source = _make_source(
            "calculator",
            items=[_make_calc_item()],
            priority=12,
        )
        panel.register_source(calc_source)
        panel._do_search("2 + 3")
        panel._panel.setHidesOnDeactivate_.assert_called_with(False)

    def test_do_search_sets_hides_on_deactivate_for_non_calc(self):
        """_do_search should set hidesOnDeactivate=True when no calc results."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("Safari")
        panel._panel.setHidesOnDeactivate_.assert_called_with(True)

    def test_do_search_empty_query_resets_hides_on_deactivate(self):
        """Empty query should reset hidesOnDeactivate to True."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._do_search("")
        panel._panel.setHidesOnDeactivate_.assert_called_with(True)

    def test_enter_calc_mode(self):
        panel = _make_panel()
        panel._start_esc_tap = MagicMock()
        with patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel._enter_calc_mode()
        assert panel._calc_mode is True
        assert panel._previous_app is None
        panel._start_esc_tap.assert_called_once()

    def test_enter_calc_mode_idempotent(self):
        """Calling _enter_calc_mode twice should not start a second ESC tap."""
        panel = _make_panel()
        panel._start_esc_tap = MagicMock()
        with patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel._enter_calc_mode()
            panel._enter_calc_mode()
        panel._start_esc_tap.assert_called_once()

    def test_exit_calc_mode(self):
        panel = _make_panel()
        panel._calc_mode = True
        panel._stop_esc_tap = MagicMock()
        panel._exit_calc_mode()
        assert panel._calc_mode is False
        panel._stop_esc_tap.assert_called_once()

    def test_exit_calc_mode_noop_when_not_active(self):
        panel = _make_panel()
        panel._stop_esc_tap = MagicMock()
        panel._exit_calc_mode()
        panel._stop_esc_tap.assert_not_called()

    def test_exit_calc_mode_does_not_touch_hides_on_deactivate(self):
        """_exit_calc_mode must NOT change hidesOnDeactivate."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._calc_mode = True
        panel._stop_esc_tap = MagicMock()
        panel._exit_calc_mode()
        panel._panel.setHidesOnDeactivate_.assert_not_called()

    def test_maybe_close_enters_calc_mode_with_calc_results(self):
        """_maybe_close should enter calc mode instead of closing."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._current_items = [_make_calc_item()]
        panel._start_esc_tap = MagicMock()

        with patch("PyObjCTools.AppHelper.callLater") as mock_later, \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel._maybe_close()
            # Extract and run the deferred _check callback
            _check = mock_later.call_args[0][1]
            _check()

        assert panel._calc_mode is True
        panel.close = MagicMock()  # Should NOT have been called

    def test_maybe_close_closes_without_calc_results(self):
        """_maybe_close should close normally without calc results."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._current_items = [ChooserItem(title="Safari")]
        panel.close = MagicMock()

        with patch("PyObjCTools.AppHelper.callLater") as mock_later:
            panel._maybe_close()
            _check = mock_later.call_args[0][1]
            _check()

        panel.close.assert_called_once()

    def test_close_exits_calc_mode(self):
        """close() should exit calc mode."""
        panel = _make_panel()
        panel._calc_mode = True
        panel._stop_esc_tap = MagicMock()
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"):
            panel.close()
        assert panel._calc_mode is False
        panel._stop_esc_tap.assert_called_once()

    def test_calc_sticky_set_on_calc_result(self):
        """_calc_sticky should be set when calc results appear."""
        panel = _make_panel()
        panel._panel = MagicMock()
        calc_source = _make_source(
            "calculator", items=[_make_calc_item()], priority=12,
        )
        panel.register_source(calc_source)
        panel._do_search("2 + 3")
        assert panel._calc_sticky is True

    def test_calc_sticky_persists_for_incomplete_expression(self):
        """_calc_sticky should keep hidesOnDeactivate=False for incomplete expressions."""
        panel = _make_panel()
        panel._panel = MagicMock()
        calc_source = _make_source(
            "calculator", items=[_make_calc_item()], priority=12,
        )
        panel.register_source(calc_source)

        # First: complete expression → sticky set
        panel._do_search("2 + 3")
        assert panel._calc_sticky is True

        # Second: incomplete expression (no calc result) but has digits
        panel._current_items = []  # simulate no calc result
        panel._do_search("2 + 3 +")
        assert panel._calc_sticky is True
        panel._panel.setHidesOnDeactivate_.assert_called_with(False)

    def test_calc_sticky_cleared_on_empty_query(self):
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._calc_sticky = True
        panel._do_search("")
        assert panel._calc_sticky is False

    def test_calc_sticky_cleared_on_no_digits(self):
        """Typing a non-math query should clear _calc_sticky."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._calc_sticky = True
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("safari")
        assert panel._calc_sticky is False

    def test_calc_sticky_kept_when_digits_present(self):
        """Query with digits should keep _calc_sticky."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._calc_sticky = True
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="App")])
        )
        panel._do_search("2+")
        assert panel._calc_sticky is True

    def test_calc_sticky_cleared_on_close(self):
        panel = _make_panel()
        panel._calc_sticky = True
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"):
            panel.close()
        assert panel._calc_sticky is False

    def test_maybe_close_enters_calc_mode_with_sticky(self):
        """_maybe_close should enter calc mode when sticky is set (no calc results)."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._current_items = []  # No calc results
        panel._calc_sticky = True  # But sticky from previous calc
        panel._start_esc_tap = MagicMock()

        with patch("PyObjCTools.AppHelper.callLater") as mock_later, \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel._maybe_close()
            _check = mock_later.call_args[0][1]
            _check()

        assert panel._calc_mode is True


# ---------------------------------------------------------------------------
# Panel resize (collapsed ↔ expanded)
# ---------------------------------------------------------------------------


class TestPanelResize:
    def test_initial_state_is_collapsed(self):
        panel = _make_panel()
        assert panel._is_expanded is False

    def test_resize_expand(self):
        """panelResize expand should set _is_expanded and call setFrame_display_."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=200, y=500),
            size=MagicMock(width=600, height=48),
        )
        panel._panel = mock_panel
        panel._is_expanded = False

        panel._handle_js_message({"type": "panelResize", "expanded": True})

        assert panel._is_expanded is True
        mock_panel.setFrame_display_.assert_called_once()
        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        # Height should be expanded (400)
        assert frame_arg[1][1] == panel._PANEL_HEIGHT_EXPANDED
        # Width stays narrow (600) since show_preview is False
        assert frame_arg[1][0] == panel._PANEL_WIDTH_NARROW
        # Top edge preserved: new_y = 500 + 48 - 400 = 148
        assert frame_arg[0][1] == 148

    def test_resize_collapse(self):
        """panelResize collapse should set _is_expanded and call setFrame_display_."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=200, y=148),
            size=MagicMock(width=600, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True

        panel._handle_js_message({"type": "panelResize", "expanded": False})

        assert panel._is_expanded is False
        mock_panel.setFrame_display_.assert_called_once()
        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        # Height should be collapsed (48)
        assert frame_arg[1][1] == panel._PANEL_HEIGHT_COLLAPSED
        # Top edge preserved: new_y = 148 + 400 - 48 = 500
        assert frame_arg[0][1] == 500

    def test_resize_noop_when_already_expanded(self):
        """Redundant expand should not call setFrame_display_."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True

        panel._resize_panel(True)
        mock_panel.setFrame_display_.assert_not_called()

    def test_resize_noop_when_already_collapsed(self):
        """Redundant collapse should not call setFrame_display_."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = False

        panel._resize_panel(False)
        mock_panel.setFrame_display_.assert_not_called()

    def test_resize_noop_when_no_panel(self):
        """Resize with panel=None should not crash."""
        panel = _make_panel()
        panel._panel = None
        panel._resize_panel(True)  # Should not raise

    def test_close_resets_is_expanded(self):
        """close() should reset _is_expanded to False."""
        panel = _make_panel()
        panel._is_expanded = True
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._is_expanded is False


# ---------------------------------------------------------------------------
# Panel width (narrow ↔ wide for preview)
# ---------------------------------------------------------------------------


class TestPanelPreviewWidth:
    def test_initial_show_preview_is_false(self):
        panel = _make_panel()
        assert panel._show_preview is False

    def test_search_with_preview_source_widens_panel(self):
        """Prefix source with show_preview=True should widen the panel."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=200, y=148),
            size=MagicMock(width=600, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True
        items = [ChooserItem(title="clip1")]
        src = _make_source("clipboard", prefix="cb", items=items)
        src.show_preview = True
        panel.register_source(src)

        panel._do_search("cb ")

        assert panel._show_preview is True
        mock_panel.setFrame_display_.assert_called_once()
        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        assert frame_arg[1][0] == panel._PANEL_WIDTH_WIDE

    def test_search_without_preview_source_stays_narrow(self):
        """General search should keep panel narrow."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )

        panel._do_search("Safari")

        assert panel._show_preview is False
        # No resize needed since already narrow
        mock_panel.setFrame_display_.assert_not_called()

    def test_switch_from_preview_to_no_preview(self):
        """Switching from preview source to general search should narrow."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=100, y=148),
            size=MagicMock(width=960, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True
        panel._show_preview = True  # Was in preview mode

        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("Safari")

        assert panel._show_preview is False
        mock_panel.setFrame_display_.assert_called_once()
        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        assert frame_arg[1][0] == panel._PANEL_WIDTH_NARROW

    def test_push_items_includes_setPreviewVisible(self):
        """_push_items_to_js should include setPreviewVisible call."""
        panel = _make_panel()
        panel._show_preview = True
        panel._current_items = [ChooserItem(title="item")]
        src = _make_source("clipboard", prefix="cb")
        src.show_preview = True
        panel._push_items_to_js(source=src)

        js_call = panel._eval_js.call_args[0][0]
        assert "setPreviewVisible(true)" in js_call

    def test_push_items_preview_false(self):
        """_push_items_to_js should send setPreviewVisible(false) for non-preview sources."""
        panel = _make_panel()
        panel._show_preview = False
        panel._current_items = [ChooserItem(title="item")]
        panel._push_items_to_js(source=None)

        js_call = panel._eval_js.call_args[0][0]
        assert "setPreviewVisible(false)" in js_call

    def test_empty_query_resets_preview(self):
        """Empty query should reset preview mode and narrow the panel."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=100, y=148),
            size=MagicMock(width=960, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True
        panel._show_preview = True

        panel._do_search("")

        assert panel._show_preview is False

    def test_close_resets_show_preview(self):
        """close() should reset _show_preview to False."""
        panel = _make_panel()
        panel._show_preview = True
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._show_preview is False

    def test_resize_width_keeps_centered(self):
        """Width change should keep panel horizontally centered."""
        panel = _make_panel()
        mock_panel = MagicMock()
        # Panel at x=200, width=600 → center at x=500
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=200, y=148),
            size=MagicMock(width=600, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True
        panel._show_preview = False

        panel._resize_panel(True, show_preview=True)

        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        # Center preserved: old_center = 200 + 600/2 = 500
        # new_x = 200 + (600 - 960)/2 = 200 - 180 = 20
        assert frame_arg[0][0] == 20
        assert frame_arg[1][0] == panel._PANEL_WIDTH_WIDE


# ---------------------------------------------------------------------------
# Compact height for calculator-only results
# ---------------------------------------------------------------------------


class TestCompactCalcHeight:
    def test_calc_only_results_use_compact_height(self):
        """When all results are calc items, panel should use compact height."""
        panel = _make_panel()
        mock_panel = MagicMock()
        mock_panel.frame.return_value = MagicMock(
            origin=MagicMock(x=200, y=148),
            size=MagicMock(width=600, height=400),
        )
        panel._panel = mock_panel
        panel._is_expanded = True
        calc_source = _make_source(
            "calculator", items=[_make_calc_item()], priority=12,
        )
        panel.register_source(calc_source)

        panel._do_search("2 + 3")

        assert panel._compact_results is True
        mock_panel.setFrame_display_.assert_called_once()
        frame_arg = mock_panel.setFrame_display_.call_args[0][0]
        assert frame_arg[1][1] == panel._PANEL_HEIGHT_COMPACT

    def test_calc_results_use_calc_action_hints(self):
        """Calc-only results should show calculator action hints, not defaults."""
        panel = _make_panel()
        panel._panel = MagicMock()
        panel._is_expanded = True
        calc_source = _make_source(
            "calculator", items=[_make_calc_item()], priority=12,
        )
        calc_source.action_hints = {"enter": "Paste", "cmd_enter": "Copy"}
        panel.register_source(calc_source)

        panel._do_search("2 + 3")

        js_call = panel._eval_js.call_args[0][0]
        assert '"Copy"' in js_call
        assert '"Paste"' in js_call
        assert '"Open"' not in js_call

    def test_mixed_results_do_not_enter_compact(self):
        """When calc + non-calc results from scratch, should NOT enter compact."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True

        # Source returns both calc and non-calc items
        def mixed_search(query):
            return [
                _make_calc_item(),
                ChooserItem(title="Safari"),
            ]

        mixed_src = ChooserSource(name="mixed", search=mixed_search)
        panel.register_source(mixed_src)

        panel._do_search("2")

        assert panel._compact_results is False

    def test_non_calc_results_not_compact(self):
        """Non-calc results should not trigger compact mode."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )

        panel._do_search("Safari")

        assert panel._compact_results is False

    def test_compact_stays_during_incomplete_expression(self):
        """Once compact, should stay compact even with no results (incomplete expr)."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True
        panel._compact_results = True  # Was in compact from "2+3"

        # Typing "2+3+" yields no results
        panel.register_source(_make_source("apps"))
        panel._do_search("2+3+")

        # Should stay compact
        assert panel._compact_results is True

    def test_compact_stays_when_typing_non_calc_query(self):
        """Once compact, should stay compact even if user types non-calc text."""
        panel = _make_panel()
        mock_panel = MagicMock()
        panel._panel = mock_panel
        panel._is_expanded = True
        panel._compact_results = True

        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("Safari")

        # Still compact — only clearing input exits compact mode
        assert panel._compact_results is True

    def test_compact_cleared_on_empty_query(self):
        """Clearing input should exit compact mode."""
        panel = _make_panel()
        panel._compact_results = True

        panel._do_search("")

        assert panel._compact_results is False

    def test_empty_results_not_compact(self):
        """No results from scratch should not be compact."""
        panel = _make_panel()
        panel.register_source(_make_source("apps"))

        panel._do_search("xyzzy")

        assert panel._compact_results is False

    def test_close_resets_compact(self):
        """close() should reset _compact_results."""
        panel = _make_panel()
        panel._compact_results = True
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._compact_results is False


class TestTabCompletion:
    def test_tab_calls_complete_and_updates_input(self):
        panel = _make_panel()

        def _complete(query, item):
            return "greet "

        src = ChooserSource(
            name="commands",
            prefix=">",
            search=lambda q: [ChooserItem(title="Greet", item_id="cmd:greet")],
            complete=_complete,
        )
        panel.register_source(src)

        # Simulate search to populate items
        panel._do_search("> gre")
        assert len(panel._current_items) == 1

        # Simulate Tab press
        panel._handle_tab_complete(0)
        # Should call setInputValue with prefix + completed query
        panel._eval_js.assert_called_with('setInputValue("> greet ")')

    def test_tab_noop_without_prefix_source(self):
        panel = _make_panel()
        panel.register_source(
            _make_source("apps", items=[ChooserItem(title="Safari")])
        )
        panel._do_search("saf")
        call_count = panel._eval_js.call_count
        panel._handle_tab_complete(0)
        # No additional JS calls — Tab is a no-op
        assert panel._eval_js.call_count == call_count

    def test_tab_noop_without_complete_callback(self):
        panel = _make_panel()
        src = ChooserSource(
            name="clipboard",
            prefix="cb",
            search=lambda q: [ChooserItem(title="Hello")],
            # No complete callback
        )
        panel.register_source(src)
        panel._do_search("cb hello")
        call_count = panel._eval_js.call_count
        panel._handle_tab_complete(0)
        assert panel._eval_js.call_count == call_count

    def test_tab_noop_invalid_index(self):
        panel = _make_panel()
        src = ChooserSource(
            name="commands",
            prefix=">",
            search=lambda q: [ChooserItem(title="Greet", item_id="cmd:greet")],
            complete=lambda q, i: "greet ",
        )
        panel.register_source(src)
        panel._do_search("> gre")
        call_count = panel._eval_js.call_count
        panel._handle_tab_complete(5)  # Out of range
        assert panel._eval_js.call_count == call_count

    def test_tab_complete_returns_none(self):
        panel = _make_panel()
        src = ChooserSource(
            name="commands",
            prefix=">",
            search=lambda q: [ChooserItem(title="Unknown")],
            complete=lambda q, i: None,
        )
        panel.register_source(src)
        panel._do_search("> unk")
        call_count = panel._eval_js.call_count
        panel._handle_tab_complete(0)
        assert panel._eval_js.call_count == call_count

    def test_tab_message_dispatched(self):
        panel = _make_panel()
        src = ChooserSource(
            name="commands",
            prefix=">",
            search=lambda q: [ChooserItem(title="Greet", item_id="cmd:greet")],
            complete=lambda q, i: "greet ",
        )
        panel.register_source(src)
        panel._do_search("> gre")

        panel._handle_js_message({"type": "tab", "index": 0})
        # Verify setInputValue was called
        calls = [str(c) for c in panel._eval_js.call_args_list]
        assert any("setInputValue" in c for c in calls)
