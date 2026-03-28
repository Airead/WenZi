"""Tests for UniversalActionController."""

from unittest.mock import MagicMock, patch

from wenzi.controllers.universal_action_controller import UniversalActionController


class TestBuildActionItems:
    """Test action item collection from commands, sources, and enhance modes."""

    def _make_controller(self):
        app = MagicMock()
        app._enhancer = MagicMock()
        app._enhancer.available_modes = [
            ("proofread", "纠错润色"),
            ("translate_en", "Translate EN"),
        ]
        # Commands: one with UA, one without
        cmd_ua = MagicMock()
        cmd_ua.name = "define"
        cmd_ua.title = "Define"
        cmd_ua.subtitle = "Dictionary"
        cmd_ua.icon = ""
        cmd_ua.universal_action = True
        cmd_ua.action = MagicMock()
        cmd_no_ua = MagicMock()
        cmd_no_ua.name = "reload"
        cmd_no_ua.title = "Reload"
        cmd_no_ua.universal_action = False
        app._script_engine._wz.chooser._command_source._commands = {
            "define": cmd_ua,
            "reload": cmd_no_ua,
        }
        # Sources: one with UA, one without
        src_ua = MagicMock()
        src_ua.name = "dict"
        src_ua.description = "Dictionary"
        src_ua.universal_action = True
        src_no_ua = MagicMock()
        src_no_ua.name = "files"
        src_no_ua.universal_action = False
        app._script_engine._wz.chooser._panel._sources = {
            "dict": src_ua,
            "files": src_no_ua,
        }
        ctrl = UniversalActionController(app)
        ctrl._selected_text = "test text"
        return ctrl

    def test_includes_all_enhance_modes(self):
        ctrl = self._make_controller()
        items = ctrl._build_action_items()
        titles = [item.title for item in items]
        assert "纠错润色" in titles
        assert "Translate EN" in titles

    def test_includes_ua_commands_only(self):
        ctrl = self._make_controller()
        items = ctrl._build_action_items()
        titles = [item.title for item in items]
        assert "Define" in titles
        assert "Reload" not in titles

    def test_includes_ua_sources_only(self):
        ctrl = self._make_controller()
        items = ctrl._build_action_items()
        item_ids = [item.item_id for item in items]
        assert any("dict" in iid for iid in item_ids)
        assert not any("files" in iid for iid in item_ids)

    def test_enhance_items_have_correct_item_id(self):
        ctrl = self._make_controller()
        items = ctrl._build_action_items()
        enhance_ids = [i.item_id for i in items if i.item_id.startswith("ua:enhance:")]
        assert "ua:enhance:proofread" in enhance_ids
        assert "ua:enhance:translate_en" in enhance_ids


class TestTrigger:
    @patch("wenzi.controllers.universal_action_controller.get_selected_text")
    def test_trigger_captures_selected_text(self, mock_get):
        mock_get.return_value = "hello"
        app = MagicMock()
        app._enhancer = MagicMock()
        app._enhancer.available_modes = []
        app._script_engine._wz.chooser._command_source._commands = {}
        app._script_engine._wz.chooser._panel._sources = {}
        app._recording_controller._is_busy = False
        ctrl = UniversalActionController(app)

        # Patch callAfter to execute the callback immediately so we can
        # verify that _show_ua_panel stores the captured text.
        with patch("PyObjCTools.AppHelper.callAfter", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
            ctrl.trigger()

        mock_get.assert_called_once()
        assert ctrl._selected_text == "hello"

    @patch("wenzi.controllers.universal_action_controller.get_selected_text")
    def test_trigger_skips_when_busy(self, mock_get):
        app = MagicMock()
        app._recording_controller._is_busy = True
        ctrl = UniversalActionController(app)
        ctrl.trigger()
        mock_get.assert_not_called()
