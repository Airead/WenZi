"""Tests for ChooserPanel search logic and source management.

UI/WKWebView parts are not testable in CI — these tests cover the
pure-Python logic: source registration, search dispatch, item execution.
"""

import json
from unittest.mock import MagicMock, patch

from wenzi.scripting.sources import ChooserItem, ChooserSource, ModifierAction
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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
             patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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

    def test_serializes_preview(self):
        panel = _make_panel()
        panel._current_items = [
            ChooserItem(
                title="Test",
                preview={"type": "text", "content": "hello world"},
            ),
        ]
        panel._push_items_to_js()
        call_args = panel._eval_js.call_args[0][0]
        # Format: setResults([...],version) — extract the JSON array
        inner = call_args[len("setResults("):-1]
        json_part = inner.rsplit(",", 1)[0]
        parsed = json.loads(json_part)
        assert parsed[0]["preview"]["type"] == "text"
        assert parsed[0]["preview"]["content"] == "hello world"

    def test_no_preview_key_when_none(self):
        panel = _make_panel()
        panel._current_items = [ChooserItem(title="Test")]
        panel._push_items_to_js()
        call_args = panel._eval_js.call_args[0][0]
        inner = call_args[len("setResults("):-1]
        json_part = inner.rsplit(",", 1)[0]
        parsed = json.loads(json_part)
        assert "preview" not in parsed[0]

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

        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
            panel._execute_item(0)

        assert tracker.score("saf", "app:Safari") == 1


class TestCloseReactivation:
    def test_close_reactivates_previous_app(self):
        """close() should reactivate the saved previous app without raising all windows."""
        panel = _make_panel()
        mock_app = MagicMock()
        panel._previous_app = mock_app

        call_order = []
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: (call_order.append(fn), fn())):
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()), \
             patch("wenzi.scripting.ui.chooser_panel.reactivate_app"), \
             patch("wenzi.scripting.ui.chooser_panel.restore_accessory"):
            panel.close()
        assert panel._previous_app is None

    def test_close_without_previous_app(self):
        """close() should not crash when _previous_app is None."""
        panel = _make_panel()
        panel._previous_app = None
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()), \
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
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
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn: fn()):
            panel._execute_item(0)
        time.sleep(0.3)
        assert called == ["default"]
