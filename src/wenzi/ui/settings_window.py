"""Settings window with NSTabView for centralized configuration management."""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _dynamic_color(light_rgb, dark_rgb):
    """Create an appearance-aware dynamic NSColor from (r, g, b) tuples."""
    from AppKit import NSColor

    def _provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]
        )
        r, g, b = dark_rgb if name and "Dark" in str(name) else light_rgb
        return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

    return NSColor.colorWithName_dynamicProvider_(None, _provider)


# Cached delegate class
_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    """Create (once) an NSObject subclass that calls panel.close() on windowWillClose:."""
    global _PanelCloseDelegate
    if _PanelCloseDelegate is not None:
        return _PanelCloseDelegate

    from Foundation import NSObject
    import objc

    class SettingsPanelCloseDelegate(NSObject):
        _panel_ref = None

        @objc.python_method
        def windowWillClose_(self, notification):
            if self._panel_ref is not None:
                self._panel_ref.close()

    _PanelCloseDelegate = SettingsPanelCloseDelegate
    return _PanelCloseDelegate


class SettingsPanel:
    """Floating NSPanel with NSTabView for centralized settings management.

    Layout (520 x 480):
        ┌────────────────────────────────────┐
        │ Settings                           │
        │ [General] [Models] [AI]            │
        │ ┌────────────────────────────────┐ │
        │ │ Tab content...                 │ │
        │ └────────────────────────────────┘ │
        │ [Show Config] [Edit Config] [Reload Config] │
        └────────────────────────────────────┘
    """

    _PANEL_WIDTH = 520
    _PANEL_HEIGHT = 480
    _PADDING = 16
    _TOOLBAR_HEIGHT = 32
    _LABEL_HEIGHT = 18
    _CONTROL_HEIGHT = 22
    _SECTION_GAP = 16
    _ROW_GAP = 4
    _HINT_HEIGHT = 14
    _HINT_GAP = 2

    def __init__(self) -> None:
        self._panel = None
        self._tab_view = None
        self._delegate = None
        self._close_delegate = None

        # Callbacks (set by app.py before show())
        self._callbacks: Dict[str, Callable] = {}

        # Button metadata: id(button) -> dict of attributes
        # (NSButton doesn't allow arbitrary Python attribute assignment)
        self._btn_meta: Dict[int, Dict] = {}

        # Control references for state updates
        self._hotkey_checks: Dict[str, object] = {}
        self._hotkey_mode_popups: Dict[str, object] = {}
        self._sound_check = None
        self._visual_check = None
        self._preview_check = None
        self._web_preview_check = None
        self._stt_buttons: Dict[str, object] = {}
        self._stt_remote_buttons: Dict[Tuple[str, str], object] = {}
        self._llm_buttons: Dict[Tuple[str, str], object] = {}
        self._enhance_mode_buttons: Dict[str, object] = {}
        self._enhance_edit_buttons: Dict[str, object] = {}
        self._doc_link_buttons: list = []
        self._scripting_check = None
        self._thinking_check = None
        self._vocab_check = None
        self._restart_key_popup = None
        self._cancel_key_popup = None
        self._auto_build_check = None
        self._vocab_build_model_popup = None
        self._history_check = None
        self._config_dir_field = None

        # Launcher tab controls
        self._launcher_source_checks: Dict[str, object] = {}
        self._launcher_prefix_fields: Dict[str, object] = {}
        self._launcher_hotkey_label = None
        self._launcher_hotkey_btn = None
        self._launcher_source_hotkey_labels: Dict[str, object] = {}
        self._launcher_source_hotkey_btns: Dict[str, object] = {}
        self._new_snippet_hotkey_label = None
        self._new_snippet_hotkey_btn = None

    def show(
        self,
        state: Dict,
        callbacks: Dict[str, Callable],
    ) -> None:
        """Show the settings panel with current state.

        Args:
            state: Current settings state dict with keys:
                - hotkeys: dict of {key_name: enabled}
                - sound_enabled: bool
                - visual_indicator: bool
                - preview: bool
                - current_preset_id: str or None
                - current_remote_asr: (provider, model) or None
                - stt_presets: list of (id, display_name, available)
                - stt_remote_models: list of (provider, model, display_name)
                - llm_models: list of (provider, model, display_name)
                - current_llm: (provider, model) or None
                - enhance_modes: list of (mode_id, label)
                - current_enhance_mode: str
                - thinking: bool
                - vocab_enabled: bool
                - vocab_count: int
                - auto_build: bool
                - history_enabled: bool
            callbacks: Dict of callback name -> callable:
                - on_hotkey_toggle: (key_name, enabled) -> None
                - on_record_hotkey: () -> None
                - on_sound_toggle: (enabled) -> None
                - on_visual_toggle: (enabled) -> None
                - on_preview_toggle: (enabled) -> None
                - on_stt_select: (preset_id) -> None
                - on_stt_remote_select: (provider, model) -> None
                - on_stt_add_provider: () -> None
                - on_stt_remove_provider: (provider) -> None
                - on_llm_select: (provider, model) -> None
                - on_llm_add_provider: () -> None
                - on_llm_remove_provider: (provider) -> None
                - on_enhance_mode_select: (mode_id) -> None
                - on_enhance_add_mode: () -> None
                - on_thinking_toggle: (enabled) -> None
                - on_vocab_toggle: (enabled) -> None
                - on_auto_build_toggle: (enabled) -> None
                - on_history_toggle: (enabled) -> None
                - on_history_max_entries: (value) -> None
                - on_history_refresh_threshold: (value) -> None
                - on_vocab_build: () -> None
                - on_reveal_config_folder: () -> None
        """
        from AppKit import NSApp

        self._callbacks = callbacks

        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular

        # Always rebuild to reflect latest state
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._panel.orderOut_(None)
            self._panel = None

        self._build_panel(state)

        # Restore last active tab
        last_tab = state.get("last_tab", "general")
        if self._tab_view is not None and last_tab != "general":
            self._tab_view.selectTabViewItemWithIdentifier_(last_tab)

        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel."""
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            self._panel.orderOut_(None)
        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    @property
    def is_visible(self) -> bool:
        """Return True if the panel is currently visible."""
        return self._panel is not None and self._panel.isVisible()

    def _build_panel(self, state: Dict) -> None:
        """Build the NSPanel and all subviews."""
        self._doc_link_buttons.clear()
        from AppKit import (
            NSBackingStoreBuffered,
            NSButton,
            NSClosableWindowMask,
            NSPanel,
            NSStatusWindowLevel,
            NSTabView,
            NSTabViewItem,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect, NSMakeSize

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(self._PANEL_WIDTH, self._PANEL_HEIGHT))
        panel.setMaxSize_(NSMakeSize(self._PANEL_WIDTH, self._PANEL_HEIGHT))
        panel.setTitle_("Settings")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # Close delegate
        delegate_cls = _get_panel_close_delegate_class()
        self._close_delegate = delegate_cls.alloc().init()
        self._close_delegate._panel_ref = self
        panel.setDelegate_(self._close_delegate)

        content = panel.contentView()
        pad = self._PADDING
        inner_w = self._PANEL_WIDTH - 2 * pad

        # --- Bottom toolbar ---
        y = pad
        btn_w = 100
        btn_h = self._TOOLBAR_HEIGHT
        btn_gap = 8

        reveal_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad, y, btn_w * 2 + btn_gap, btn_h)
        )
        reveal_btn.setTitle_("Reveal Config Folder")
        reveal_btn.setBezelStyle_(1)  # NSRoundedBezelStyle
        reveal_btn.setTarget_(self)
        reveal_btn.setAction_(b"revealConfigFolderClicked:")
        content.addSubview_(reveal_btn)

        y += btn_h + pad

        # --- NSTabView ---
        tab_height = self._PANEL_HEIGHT - y - 8  # leave a bit of margin at top
        tab_view = NSTabView.alloc().initWithFrame_(
            NSMakeRect(pad, y, inner_w, tab_height)
        )

        # Build tabs
        general_tab = NSTabViewItem.alloc().initWithIdentifier_("general")
        general_tab.setLabel_("General")
        self._build_general_tab(general_tab, state, inner_w)
        tab_view.addTabViewItem_(general_tab)

        stt_tab = NSTabViewItem.alloc().initWithIdentifier_("stt")
        stt_tab.setLabel_("STT")
        self._build_stt_tab(stt_tab, state, inner_w)
        tab_view.addTabViewItem_(stt_tab)

        llm_tab = NSTabViewItem.alloc().initWithIdentifier_("llm")
        llm_tab.setLabel_("LLM")
        self._build_llm_tab(llm_tab, state, inner_w)
        tab_view.addTabViewItem_(llm_tab)

        ai_tab = NSTabViewItem.alloc().initWithIdentifier_("ai")
        ai_tab.setLabel_("AI")
        self._build_ai_tab(ai_tab, state, inner_w)
        tab_view.addTabViewItem_(ai_tab)

        launcher_tab = NSTabViewItem.alloc().initWithIdentifier_("launcher")
        launcher_tab.setLabel_("Launcher")
        self._build_launcher_tab(launcher_tab, state, inner_w)
        tab_view.addTabViewItem_(launcher_tab)

        # Set tab delegate for scroll-to-top on tab switch
        tab_view.setDelegate_(self)

        content.addSubview_(tab_view)
        self._tab_view = tab_view
        self._panel = panel

    # ── Tab builders ─────────────────────────────────────────────────

    def _build_general_tab(self, tab_item, state: Dict, tab_width: float) -> None:
        """Build the General tab: Hotkeys, Feedback, Output."""
        from AppKit import (
            NSButton,
            NSFont,
            NSScrollView,
            NSView,
            NSSwitchButton,
        )
        from Foundation import NSMakeRect

        content_w = tab_width - 24
        content_h = self._PANEL_HEIGHT - self._TOOLBAR_HEIGHT - self._PADDING * 2 - 80

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)

        pad = 12
        label_font = NSFont.boldSystemFontOfSize_(13.0)
        small_font = NSFont.systemFontOfSize_(12.0)

        hotkeys = state.get("hotkeys", {})
        # Use a large initial height for layout (top-down); actual height
        # is determined after all controls are placed and the frame is
        # resized to fit, avoiding fragile hard-coded row counts.
        total_h = 5000

        doc_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w - 20, total_h)
        )
        y = total_h - pad

        # --- Hotkeys section ---
        y -= self._LABEL_HEIGHT
        hotkey_label = self._make_label("Hotkeys", pad, y, content_w, label_font)
        doc_view.addSubview_(hotkey_label)
        self._add_doc_link(hotkey_label, "user-guide.html#your-first-transcription", doc_view)

        self._hotkey_checks.clear()
        self._hotkey_mode_popups.clear()
        enhance_modes = state.get("enhance_modes", [])
        check_w = 120
        popup_w = 150
        for key_name, value in sorted(hotkeys.items()):
            enabled = bool(value)
            # Extract per-hotkey mode from config value
            hotkey_mode = value.get("mode") if isinstance(value, dict) else None

            y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
            check = NSButton.alloc().initWithFrame_(
                NSMakeRect(pad + 12, y, check_w, self._CONTROL_HEIGHT)
            )
            check.setButtonType_(NSSwitchButton)
            check.setTitle_(key_name)
            check.setFont_(small_font)
            check.setState_(1 if enabled else 0)
            check.setTarget_(self)
            check.setAction_(b"hotkeyCheckChanged:")
            self._set_meta(check, key_name=key_name)
            doc_view.addSubview_(check)
            self._hotkey_checks[key_name] = check

            # Mode dropdown for this hotkey
            from AppKit import NSMenuItem, NSPopUpButton
            is_fn = (key_name.strip().lower() == "fn")

            mode_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(pad + 12 + check_w + 4, y - 2, popup_w,
                           self._CONTROL_HEIGHT + 4),
                False,
            )
            mode_popup.setFont_(small_font)

            # Build items: Default AI Mode, Off, [modes...], separator, Delete
            mode_popup.addItemWithTitle_("Default AI Mode")
            mode_popup.lastItem().setRepresentedObject_("_default")

            mode_popup.addItemWithTitle_("Off")
            mode_popup.lastItem().setRepresentedObject_("off")

            for mode_id, mode_label, _order in enhance_modes:
                mode_popup.addItemWithTitle_(mode_label)
                mode_popup.lastItem().setRepresentedObject_(mode_id)

            if not is_fn:
                mode_popup.menu().addItem_(NSMenuItem.separatorItem())
                mode_popup.addItemWithTitle_("Delete Hotkey")
                mode_popup.lastItem().setRepresentedObject_("_delete")

            # Select current value
            selected_mode = hotkey_mode if hotkey_mode else "_default"
            for i in range(mode_popup.numberOfItems()):
                item = mode_popup.itemAtIndex_(i)
                if item.representedObject() == selected_mode:
                    mode_popup.selectItemAtIndex_(i)
                    break

            mode_popup.setTarget_(self)
            mode_popup.setAction_(b"hotkeyModeChanged:")
            doc_view.addSubview_(mode_popup)
            self._set_meta(mode_popup, key_name=key_name)
            self._hotkey_mode_popups[key_name] = mode_popup

        # Record Hotkey button
        y -= (28 + self._ROW_GAP)
        record_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 12, y, 140, 28)
        )
        record_btn.setTitle_("Record Hotkey...")
        record_btn.setBezelStyle_(1)
        record_btn.setFont_(small_font)
        record_btn.setTarget_(self)
        record_btn.setAction_(b"recordHotkeyClicked:")
        doc_view.addSubview_(record_btn)

        # Restart / Cancel key dropdowns
        from wenzi.config import MODIFIER_KEY_CHOICES
        _key_choices = MODIFIER_KEY_CHOICES
        label_w = 80
        popup_w = 140

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP + 6)
        restart_label = self._make_label(
            "Restart Key", pad + 12, y, label_w, small_font
        )
        doc_view.addSubview_(restart_label)
        self._restart_key_popup = self._make_popup(
            _key_choices, state.get("restart_key", "cmd"),
            pad + 12 + label_w + 4, y - 2, popup_w, small_font,
            b"restartKeyChanged:", doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP + 4)
        cancel_label = self._make_label(
            "Cancel Key", pad + 12, y, label_w, small_font
        )
        doc_view.addSubview_(cancel_label)
        self._cancel_key_popup = self._make_popup(
            _key_choices, state.get("cancel_key", "space"),
            pad + 12 + label_w + 4, y - 2, popup_w, small_font,
            b"cancelKeyChanged:", doc_view,
        )

        y = self._add_hint(
            "Hold hotkey + press key to restart or cancel recording",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Feedback section ---
        y -= self._LABEL_HEIGHT
        fb_label = self._make_label("Feedback", pad, y, content_w, label_font)
        doc_view.addSubview_(fb_label)
        self._add_doc_link(fb_label, "user-guide.html#recording-feedback", doc_view)

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._sound_check = self._make_switch(
            "Sound Feedback", pad + 12, y, content_w - 24,
            state.get("sound_enabled", True), small_font,
            b"soundCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Adds ~350ms delay before recording to avoid capturing the sound",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._visual_check = self._make_switch(
            "Visual Indicator", pad + 12, y, content_w - 24,
            state.get("visual_indicator", True), small_font,
            b"visualCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Show a floating indicator while recording",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._device_name_check = self._make_switch(
            "Show Device Name", pad + 12, y, content_w - 24,
            state.get("show_device_name", False), small_font,
            b"deviceNameCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Show the input device name on the recording indicator",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Output section ---
        y -= self._LABEL_HEIGHT
        out_label = self._make_label("Output", pad, y, content_w, label_font)
        doc_view.addSubview_(out_label)
        self._add_doc_link(out_label, "user-guide.html#preview-mode-vs-direct-mode", doc_view)

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._preview_check = self._make_switch(
            "Preview", pad + 12, y, content_w - 24,
            state.get("preview", True), small_font,
            b"previewCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Show a preview panel before inserting text, allowing edits",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Scripting section ---
        y -= self._LABEL_HEIGHT
        scripting_label = self._make_label("Scripting", pad, y, content_w, label_font)
        doc_view.addSubview_(scripting_label)
        self._add_doc_link(scripting_label, "scripting.html#quick-start", doc_view)

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._scripting_check = self._make_switch(
            "Enable Scripting", pad + 12, y, content_w - 24,
            state.get("scripting_enabled", False), small_font,
            b"scriptingCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Load user scripts from ~/.config/WenZi/scripts/init.py "
            "(requires app restart)",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Config Directory section ---
        y -= self._LABEL_HEIGHT
        cfg_label = self._make_label("Config Directory", pad, y, content_w, label_font)
        doc_view.addSubview_(cfg_label)
        self._add_doc_link(cfg_label, "configuration.html#config-directory-resolution", doc_view)

        config_dir = state.get("config_dir", "~/.config/WenZi")
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._config_dir_field = self._make_readonly_field(
            config_dir, pad + 12, y, content_w - 24, small_font,
        )
        doc_view.addSubview_(self._config_dir_field)

        y -= (28 + self._ROW_GAP + 4)
        bx = pad + 12
        browse_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 100, 28)
        )
        browse_btn.setTitle_("Browse...")
        browse_btn.setBezelStyle_(1)
        browse_btn.setFont_(small_font)
        browse_btn.setTarget_(self)
        browse_btn.setAction_(b"configDirBrowseClicked:")
        doc_view.addSubview_(browse_btn)

        bx += 108
        reset_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 80, 28)
        )
        reset_btn.setTitle_("Reset")
        reset_btn.setBezelStyle_(1)
        reset_btn.setFont_(small_font)
        reset_btn.setTarget_(self)
        reset_btn.setAction_(b"configDirResetClicked:")
        doc_view.addSubview_(reset_btn)

        y = self._add_hint(
            "Changes require app restart to take effect",
            pad + 12, y, content_w - 24, doc_view,
        )

        # Shrink doc_view to actual content height and shift all subviews
        # so that the first control starts at the top.
        actual_h = max(content_h, total_h - y + pad)
        offset = total_h - actual_h
        from AppKit import NSMakeSize
        doc_view.setFrameSize_(NSMakeSize(content_w - 20, actual_h))
        for subview in doc_view.subviews():
            frame = subview.frame()
            subview.setFrameOrigin_((frame.origin.x, frame.origin.y - offset))

        scroll.setDocumentView_(doc_view)
        tab_item.setView_(scroll)

    def _build_stt_tab(self, tab_item, state: Dict, tab_width: float) -> None:
        """Build the STT tab: local presets + remote providers."""
        from AppKit import NSButton, NSFont, NSScrollView, NSView
        from Foundation import NSMakeRect

        content_w = tab_width - 24
        content_h = self._PANEL_HEIGHT - self._TOOLBAR_HEIGHT - self._PADDING * 2 - 80

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)

        pad = 12
        label_font = NSFont.boldSystemFontOfSize_(13.0)
        small_font = NSFont.systemFontOfSize_(12.0)

        stt_presets = state.get("stt_presets", [])
        stt_remote = state.get("stt_remote_models", [])
        current_preset = state.get("current_preset_id")
        current_remote_asr = state.get("current_remote_asr")

        n_rows = len(stt_presets) + len(stt_remote) + 6
        total_h = max(content_h, n_rows * (self._CONTROL_HEIGHT + self._ROW_GAP) + 80)

        doc_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w - 20, total_h)
        )
        y = total_h - pad

        # --- Local presets ---
        y -= self._LABEL_HEIGHT
        local_label = self._make_label("Local", pad, y, content_w, label_font)
        doc_view.addSubview_(local_label)
        self._add_doc_link(local_label, "provider-model-guide.html#asr-model-selection", doc_view)
        y = self._add_hint(
            "Speech recognition models running on your device",
            pad + 12, y, content_w - 24, doc_view,
        )

        self._stt_buttons.clear()
        self._stt_remote_buttons.clear()

        stt_model_sizes = state.get("stt_model_sizes", {})

        for preset_id, display_name, available in sorted(stt_presets, key=lambda x: x[1]):
            y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
            is_selected = (
                current_remote_asr is None and preset_id == current_preset
            )
            # Append model size if available
            size_bytes = stt_model_sizes.get(preset_id)
            if size_bytes is not None:
                size_str = self._format_size(size_bytes)
                title_base = display_name if available else f"{display_name} (N/A)"
                title = f"{title_base}  [{size_str}]"
            else:
                title = display_name if available else f"{display_name} (N/A)"
            btn = self._make_radio(
                title, pad + 12, y, content_w - 24,
                is_selected, small_font, doc_view,
            )
            btn.setTarget_(self)
            btn.setAction_(b"sttModelSelected:")
            self._set_meta(btn, preset_id=preset_id, is_available=available)
            btn.setEnabled_(available)
            self._stt_buttons[preset_id] = btn

        # --- Remote providers ---
        y -= self._SECTION_GAP
        y -= self._LABEL_HEIGHT
        remote_label = self._make_label("Remote", pad, y, content_w, label_font)
        doc_view.addSubview_(remote_label)
        self._add_doc_link(remote_label, "provider-model-guide.html#remote-asr-providers", doc_view)
        y = self._add_hint(
            "Cloud-based speech recognition services",
            pad + 12, y, content_w - 24, doc_view,
        )

        if stt_remote:
            for provider, model, display_name in sorted(stt_remote, key=lambda x: x[2]):
                y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
                key = (provider, model)
                is_selected = current_remote_asr == key
                btn = self._make_radio(
                    display_name, pad + 12, y, content_w - 24,
                    is_selected, small_font, doc_view,
                )
                btn.setTarget_(self)
                btn.setAction_(b"sttRemoteSelected:")
                self._set_meta(btn, provider=provider, model=model)
                self._stt_remote_buttons[key] = btn

        # Add/Remove buttons
        y -= (28 + self._ROW_GAP + 4)
        bx = pad + 12
        add_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 120, 28)
        )
        add_btn.setTitle_("Add Provider...")
        add_btn.setBezelStyle_(1)
        add_btn.setFont_(small_font)
        add_btn.setTarget_(self)
        add_btn.setAction_(b"sttAddProviderClicked:")
        doc_view.addSubview_(add_btn)

        bx += 128
        remove_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 100, 28)
        )
        remove_btn.setTitle_("Remove...")
        remove_btn.setBezelStyle_(1)
        remove_btn.setFont_(small_font)
        remove_btn.setTarget_(self)
        remove_btn.setAction_(b"sttRemoveProviderClicked:")
        doc_view.addSubview_(remove_btn)

        scroll.setDocumentView_(doc_view)
        tab_item.setView_(scroll)

    def _build_llm_tab(self, tab_item, state: Dict, tab_width: float) -> None:
        """Build the LLM tab: provider/model list."""
        from AppKit import NSButton, NSFont, NSScrollView, NSView
        from Foundation import NSMakeRect

        content_w = tab_width - 24
        content_h = self._PANEL_HEIGHT - self._TOOLBAR_HEIGHT - self._PADDING * 2 - 80

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)

        pad = 12
        label_font = NSFont.boldSystemFontOfSize_(13.0)
        small_font = NSFont.systemFontOfSize_(12.0)

        llm_models = state.get("llm_models", [])
        current_llm = state.get("current_llm")

        n_rows = len(llm_models) + 4
        total_h = max(content_h, n_rows * (self._CONTROL_HEIGHT + self._ROW_GAP) + 80)

        doc_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w - 20, total_h)
        )
        y = total_h - pad

        y -= self._LABEL_HEIGHT
        pm_label = self._make_label("Provider / Model", pad, y, content_w, label_font)
        doc_view.addSubview_(pm_label)
        self._add_doc_link(pm_label, "provider-model-guide.html#ai-llm-provider-configuration", doc_view)
        y = self._add_hint(
            "Language model used for AI enhancement features",
            pad + 12, y, content_w - 24, doc_view,
        )

        self._llm_buttons.clear()
        for provider, model, display_name in sorted(llm_models, key=lambda x: x[2]):
            y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
            key = (provider, model)
            is_selected = current_llm == key
            btn = self._make_radio(
                display_name, pad + 12, y, content_w - 24,
                is_selected, small_font, doc_view,
            )
            btn.setTarget_(self)
            btn.setAction_(b"llmModelSelected:")
            self._set_meta(btn, provider=provider, model=model)
            self._llm_buttons[key] = btn

        # Add/Remove buttons
        y -= (28 + self._ROW_GAP + 4)
        bx = pad + 12
        add_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 120, 28)
        )
        add_btn.setTitle_("Add Provider...")
        add_btn.setBezelStyle_(1)
        add_btn.setFont_(small_font)
        add_btn.setTarget_(self)
        add_btn.setAction_(b"llmAddProviderClicked:")
        doc_view.addSubview_(add_btn)

        bx += 128
        remove_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bx, y, 100, 28)
        )
        remove_btn.setTitle_("Remove...")
        remove_btn.setBezelStyle_(1)
        remove_btn.setFont_(small_font)
        remove_btn.setTarget_(self)
        remove_btn.setAction_(b"llmRemoveProviderClicked:")
        doc_view.addSubview_(remove_btn)

        scroll.setDocumentView_(doc_view)
        tab_item.setView_(scroll)

    def _build_ai_tab(self, tab_item, state: Dict, tab_width: float) -> None:
        """Build the AI tab: Enhance Mode, Options."""
        from AppKit import (
            NSButton,
            NSFont,
            NSScrollView,
            NSView,
        )
        from Foundation import NSMakeRect

        content_w = tab_width - 24
        content_h = self._PANEL_HEIGHT - self._TOOLBAR_HEIGHT - self._PADDING * 2 - 80

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)

        pad = 12
        label_font = NSFont.boldSystemFontOfSize_(13.0)
        small_font = NSFont.systemFontOfSize_(12.0)

        enhance_modes = state.get("enhance_modes", [])
        n_rows = len(enhance_modes) + 10
        total_h = max(content_h, n_rows * (self._CONTROL_HEIGHT + self._ROW_GAP) + 200)

        doc_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w - 20, total_h)
        )
        y = total_h - pad

        # --- Enhance Mode section ---
        y -= self._LABEL_HEIGHT
        mode_label = self._make_label("Enhance Mode", pad, y, content_w, label_font)
        doc_view.addSubview_(mode_label)
        self._add_doc_link(mode_label, "enhance-modes.html#how-it-works", doc_view)
        y = self._add_hint(
            "AI post-processing mode applied to transcribed text",
            pad + 12, y, content_w - 24, doc_view,
        )

        current_mode = state.get("current_enhance_mode", "off")
        self._enhance_mode_buttons.clear()
        self._enhance_edit_buttons.clear()

        # Always include "Off"
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        off_btn = self._make_radio(
            "Off", pad + 12, y, content_w - 24,
            current_mode == "off", small_font, doc_view,
        )
        off_btn.setTarget_(self)
        off_btn.setAction_(b"enhanceModeSelected:")
        self._set_meta(off_btn, mode_id="off")
        self._enhance_mode_buttons["off"] = off_btn

        edit_btn_w = 52
        edit_font = NSFont.systemFontOfSize_(11.0)
        edit_x = pad + 12 + 168
        for mode_id, label, order in enhance_modes:
            y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
            display_label = f"{label} (#{order})"
            btn = self._make_radio(
                display_label, pad + 12, y, 160,
                current_mode == mode_id, small_font, doc_view,
            )
            btn.setTarget_(self)
            btn.setAction_(b"enhanceModeSelected:")
            self._set_meta(btn, mode_id=mode_id)
            self._enhance_mode_buttons[mode_id] = btn

            edit_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(edit_x, y, edit_btn_w, self._CONTROL_HEIGHT)
            )
            edit_btn.setTitle_("Edit")
            edit_btn.setBezelStyle_(1)
            edit_btn.setFont_(edit_font)
            edit_btn.setTarget_(self)
            edit_btn.setAction_(b"enhanceModeEditClicked:")
            self._set_meta(edit_btn, mode_id=mode_id)
            doc_view.addSubview_(edit_btn)
            self._enhance_edit_buttons[mode_id] = edit_btn

        # Add Mode button
        y -= (28 + self._ROW_GAP)
        add_mode_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 12, y, 120, 28)
        )
        add_mode_btn.setTitle_("Add Mode...")
        add_mode_btn.setBezelStyle_(1)
        add_mode_btn.setFont_(small_font)
        add_mode_btn.setTarget_(self)
        add_mode_btn.setAction_(b"addModeClicked:")
        doc_view.addSubview_(add_mode_btn)

        y -= self._SECTION_GAP

        # --- Options section ---
        y -= self._LABEL_HEIGHT
        opt_label = self._make_label("Options", pad, y, content_w, label_font)
        doc_view.addSubview_(opt_label)
        self._add_doc_link(opt_label, "configuration.html#ai-enhancement", doc_view)

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._auto_build_check = self._make_switch(
            "Auto Build Vocabulary", pad + 12, y, content_w - 24,
            state.get("auto_build", True), small_font,
            b"autoBuildCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Automatically update vocabulary from your text input history",
            pad + 12, y, content_w - 24, doc_view,
        )

        # Vocab build model popup
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        bm_label = self._make_label(
            "Build Model", pad + 28, y, 90, small_font,
        )
        doc_view.addSubview_(bm_label)

        llm_models = state.get("llm_models", [])
        current_build_model = state.get("vocab_build_model")  # (provider, model) or None
        bm_items = [(("", ""), "Default")]
        for provider, model, display_name in sorted(llm_models, key=lambda x: x[2]):
            bm_items.append(((provider, model), display_name))

        self._vocab_build_model_popup = self._make_popup(
            bm_items, current_build_model or ("", ""),
            pad + 28 + 90, y, content_w - 28 - 90 - 12, small_font,
            b"vocabBuildModelChanged:", doc_view,
        )
        y = self._add_hint(
            "LLM used for vocabulary extraction (Default = same as AI enhance)",
            pad + 28, y, content_w - 40, doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._history_check = self._make_switch(
            "Conversation History", pad + 12, y, content_w - 24,
            state.get("history_enabled", False), small_font,
            b"historyCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Include recent conversation context for better AI enhancement",
            pad + 12, y, content_w - 24, doc_view,
        )

        # History cache tuning: base entries + max entries before rebuild
        label_x = pad + 28
        popup_w = 70
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)

        base_label = self._make_label(
            "Base entries", label_x, y, 90, small_font,
        )
        doc_view.addSubview_(base_label)

        base_items = [(v, str(v)) for v in (5, 10, 15, 20, 30, 50)]
        current_base = state.get("history_max_entries", 10)
        self._history_base_popup = self._make_popup(
            base_items, current_base,
            label_x + 90, y, popup_w, small_font,
            b"historyBaseChanged:", doc_view,
        )

        max_label = self._make_label(
            "Max entries", label_x + 90 + popup_w + 16, y, 90, small_font,
        )
        doc_view.addSubview_(max_label)

        max_items = [(v, str(v)) for v in (20, 30, 50, 80, 100, 200)]
        current_max = state.get("history_refresh_threshold", 50)
        self._history_max_popup = self._make_popup(
            max_items, current_max,
            label_x + 90 + popup_w + 16 + 90, y, popup_w, small_font,
            b"historyMaxChanged:", doc_view,
        )
        y = self._add_hint(
            "Base: entries kept after rebuild. Max: triggers a rebuild for cache optimization.",
            pad + 28, y, content_w - 40, doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._thinking_check = self._make_switch(
            "Thinking", pad + 12, y, content_w - 24,
            state.get("thinking", False), small_font,
            b"thinkingCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Enable extended thinking for more accurate AI processing (slower)",
            pad + 12, y, content_w - 24, doc_view,
        )

        vocab_count = state.get("vocab_count", 0)
        vocab_title = f"Vocabulary ({vocab_count})" if vocab_count > 0 else "Vocabulary"
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._vocab_check = self._make_switch(
            vocab_title, pad + 12, y, content_w - 24,
            state.get("vocab_enabled", False), small_font,
            b"vocabCheckChanged:", doc_view,
        )
        y = self._add_hint(
            "Use a custom vocabulary to improve recognition of domain-specific terms",
            pad + 12, y, content_w - 24, doc_view,
        )

        # Build Vocabulary button
        y -= (28 + self._ROW_GAP + 4)
        build_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 12, y, 160, 28)
        )
        build_btn.setTitle_("Build Vocabulary...")
        build_btn.setBezelStyle_(1)
        build_btn.setFont_(small_font)
        build_btn.setTarget_(self)
        build_btn.setAction_(b"buildVocabClicked:")
        doc_view.addSubview_(build_btn)

        scroll.setDocumentView_(doc_view)
        tab_item.setView_(scroll)

    def _build_launcher_tab(self, tab_item, state: Dict, tab_width: float) -> None:
        """Build the Launcher tab: source toggles, prefix config, hotkey."""
        from AppKit import (
            NSButton,
            NSColor,
            NSFont,
            NSScrollView,
            NSTextField,
            NSView,
        )
        from Foundation import NSMakeRect

        content_w = tab_width - 24
        content_h = (
            self._PANEL_HEIGHT - self._TOOLBAR_HEIGHT - self._PADDING * 2 - 80
        )

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)

        pad = 12
        label_font = NSFont.boldSystemFontOfSize_(13.0)
        small_font = NSFont.systemFontOfSize_(12.0)

        total_h = 5000
        doc_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w - 20, total_h)
        )
        y = total_h - pad

        launcher_state = state.get("launcher", {})
        prefixes = launcher_state.get("prefixes", {})

        # --- Scripting dependency warning ---
        if not state.get("scripting_enabled", False):
            y -= (self._HINT_HEIGHT + self._HINT_GAP)
            warn = self._make_hint(
                "\u26a0 Launcher requires Scripting to be enabled "
                "(General \u2192 Scripting)",
                pad, y, content_w - 24,
            )
            from AppKit import NSColor
            warn.setTextColor_(NSColor.systemOrangeColor())
            doc_view.addSubview_(warn)

        # --- Enable Launcher toggle ---
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._launcher_enabled_check = self._make_switch(
            "Enable Launcher", pad, y, content_w - 24,
            launcher_state.get("enabled", True), label_font,
            b"launcherEnabledToggled:", doc_view,
        )
        y = self._add_hint(
            "Disable to skip launcher registration and hotkey binding",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Hotkey section ---
        y -= self._LABEL_HEIGHT
        hk_label = self._make_label("Hotkey", pad, y, content_w, label_font)
        doc_view.addSubview_(hk_label)
        self._add_doc_link(hk_label, "scripting.html#activation", doc_view)
        y = self._add_hint(
            "Global hotkey to toggle the launcher panel",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        hotkey_val = launcher_state.get("hotkey", "")
        hotkey_title_label = self._make_label(
            "Hotkey:", pad + 12, y, 60, small_font,
        )
        doc_view.addSubview_(hotkey_title_label)

        hk_display = NSTextField.labelWithString_(hotkey_val or "None")
        hk_display.setFrame_(
            NSMakeRect(pad + 105, y + 2, 120, self._CONTROL_HEIGHT)
        )
        hk_display.setFont_(small_font)
        hk_display.setTextColor_(NSColor.secondaryLabelColor())
        doc_view.addSubview_(hk_display)
        self._launcher_hotkey_label = hk_display

        if hotkey_val:
            hk_btn_title = "Clear"
            hk_btn_action = b"launcherHotkeyClear:"
        else:
            hk_btn_title = "Record"
            hk_btn_action = b"launcherHotkeyRecord:"
        hk_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 230, y - 1, 60, 22)
        )
        hk_btn.setTitle_(hk_btn_title)
        hk_btn.setBezelStyle_(1)
        hk_btn.setFont_(NSFont.systemFontOfSize_(10.0))
        hk_btn.setTarget_(self)
        hk_btn.setAction_(hk_btn_action)
        doc_view.addSubview_(hk_btn)
        self._launcher_hotkey_btn = hk_btn

        # New Snippet hotkey row (same layout as Hotkey: row above)
        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        new_sn_hotkey = launcher_state.get("new_snippet_hotkey", "")
        doc_view.addSubview_(self._make_label(
            "New Snippet:", pad + 12, y, 80, small_font,
        ))

        new_sn_hk_label = NSTextField.labelWithString_(new_sn_hotkey or "None")
        new_sn_hk_label.setFrame_(
            NSMakeRect(pad + 105, y + 2, 120, self._CONTROL_HEIGHT)
        )
        new_sn_hk_label.setFont_(small_font)
        new_sn_hk_label.setTextColor_(NSColor.secondaryLabelColor())
        doc_view.addSubview_(new_sn_hk_label)
        self._new_snippet_hotkey_label = new_sn_hk_label

        if new_sn_hotkey:
            btn_title = "Clear"
            btn_action = b"newSnippetHotkeyClear:"
        else:
            btn_title = "Record"
            btn_action = b"newSnippetHotkeyRecord:"
        new_sn_hk_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 230, y - 1, 60, 22)
        )
        new_sn_hk_btn.setTitle_(btn_title)
        new_sn_hk_btn.setBezelStyle_(1)
        new_sn_hk_btn.setFont_(NSFont.systemFontOfSize_(10.0))
        new_sn_hk_btn.setTarget_(self)
        new_sn_hk_btn.setAction_(btn_action)
        doc_view.addSubview_(new_sn_hk_btn)
        self._new_snippet_hotkey_btn = new_sn_hk_btn

        y -= self._SECTION_GAP

        # --- Data Sources section ---
        y -= self._LABEL_HEIGHT
        ds_label = self._make_label("Data Sources", pad, y, content_w, label_font)
        doc_view.addSubview_(ds_label)
        self._add_doc_link(ds_label, "scripting.html#built-in-data-sources", doc_view)
        y = self._add_hint(
            "Enable/disable sources and customize their prefix triggers",
            pad + 12, y, content_w - 24, doc_view,
        )

        sources = [
            ("app_search", "Applications", None),
            ("clipboard_history", "Clipboard History", "clipboard"),
            ("file_search", "File Search", "files"),
            ("snippets", "Snippets", "snippets"),
            ("bookmarks", "Bookmarks", "bookmarks"),
        ]

        self._launcher_source_checks.clear()
        self._launcher_prefix_fields.clear()
        self._launcher_source_hotkey_labels.clear()
        self._launcher_source_hotkey_btns.clear()

        source_hotkeys = launcher_state.get("source_hotkeys", {})
        prefix_label_w = 50
        prefix_field_w = 60

        for config_key, label, prefix_key in sources:
            enabled = launcher_state.get(config_key, True)
            prefix_val = prefixes.get(prefix_key, "") if prefix_key else ""

            y -= (self._CONTROL_HEIGHT + self._ROW_GAP)

            # Enable/disable checkbox
            check = self._make_switch(
                label, pad + 12, y, 140,
                enabled, small_font,
                b"launcherSourceToggled:", doc_view,
            )
            self._set_meta(check, config_key=config_key)
            self._launcher_source_checks[config_key] = check

            # Prefix input field (only for sources that have prefixes)
            if prefix_key:
                prefix_label = self._make_label(
                    "Prefix:", pad + 155, y + 2, prefix_label_w, small_font,
                )
                doc_view.addSubview_(prefix_label)

                prefix_field = NSTextField.alloc().initWithFrame_(
                    NSMakeRect(
                        pad + 155 + prefix_label_w + 4, y,
                        prefix_field_w, self._CONTROL_HEIGHT,
                    )
                )
                prefix_field.setStringValue_(prefix_val)
                prefix_field.setFont_(small_font)
                prefix_field.setEditable_(True)
                prefix_field.setBezeled_(True)
                prefix_field.setDrawsBackground_(True)
                prefix_field.setBackgroundColor_(
                    NSColor.controlBackgroundColor()
                )
                prefix_field.setTextColor_(NSColor.labelColor())
                prefix_field.setPlaceholderString_(prefix_val)
                prefix_field.setTarget_(self)
                prefix_field.setAction_(b"launcherPrefixChanged:")
                self._set_meta(prefix_field, prefix_key=prefix_key)
                doc_view.addSubview_(prefix_field)
                self._launcher_prefix_fields[prefix_key] = prefix_field

                # Source hotkey label + Record/Clear button
                hotkey_val = source_hotkeys.get(prefix_key, "")
                hk_x = pad + 155 + prefix_label_w + 4 + prefix_field_w + 8

                hk_label = NSTextField.labelWithString_(hotkey_val or "None")
                hk_label.setFrame_(
                    NSMakeRect(hk_x, y + 2, 90, self._CONTROL_HEIGHT)
                )
                hk_label.setFont_(small_font)
                hk_label.setTextColor_(NSColor.secondaryLabelColor())
                doc_view.addSubview_(hk_label)
                self._launcher_source_hotkey_labels[prefix_key] = hk_label

                btn_x = hk_x + 94
                if hotkey_val:
                    btn_title = "Clear"
                    btn_action = b"launcherSourceHotkeyClear:"
                else:
                    btn_title = "Record"
                    btn_action = b"launcherSourceHotkeyRecord:"
                hk_btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(btn_x, y - 1, 60, 22)
                )
                hk_btn.setTitle_(btn_title)
                hk_btn.setBezelStyle_(1)
                hk_btn.setFont_(NSFont.systemFontOfSize_(10.0))
                hk_btn.setTarget_(self)
                hk_btn.setAction_(btn_action)
                self._set_meta(hk_btn, source_key=prefix_key)
                doc_view.addSubview_(hk_btn)
                self._launcher_source_hotkey_btns[prefix_key] = hk_btn

            if config_key == "clipboard_history":
                y = self._add_warning(
                    "\u26a0 Not fully verified \u2014 may record sensitive data "
                    "such as passwords and keys.",
                    pad + 12, y, content_w - 24, doc_view,
                )

        y -= self._SECTION_GAP

        # --- Options section ---
        y -= self._LABEL_HEIGHT
        doc_view.addSubview_(
            self._make_label("Options", pad, y, content_w, label_font)
        )

        y -= (self._CONTROL_HEIGHT + self._ROW_GAP)
        self._make_switch(
            "Usage Learning", pad + 12, y, content_w - 24,
            launcher_state.get("usage_learning", True), small_font,
            b"launcherUsageLearningToggled:", doc_view,
        )
        y = self._add_hint(
            "Learn from your selections to rank frequently used items higher",
            pad + 12, y, content_w - 24, doc_view,
        )

        y -= self._SECTION_GAP

        # --- Maintenance section ---
        y -= self._LABEL_HEIGHT
        doc_view.addSubview_(
            self._make_label("Maintenance", pad, y, content_w, label_font)
        )

        from AppKit import NSButton

        y -= (28 + self._ROW_GAP)
        refresh_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(pad + 12, y, 180, 28)
        )
        refresh_btn.setTitle_("Refresh Icon Cache")
        refresh_btn.setBezelStyle_(1)
        refresh_btn.setFont_(small_font)
        refresh_btn.setTarget_(self)
        refresh_btn.setAction_(b"launcherRefreshIconsClicked:")
        doc_view.addSubview_(refresh_btn)
        y = self._add_hint(
            "Clear cached app and browser icons and re-extract them",
            pad + 12, y, content_w - 24, doc_view,
        )

        # --- Disable controls based on scripting/launcher state ---
        scripting_on = state.get("scripting_enabled", False)
        launcher_on = launcher_state.get("enabled", True)

        # Controls that require scripting to be enabled (everything
        # except the warning label itself)
        all_launcher_controls = [
            self._launcher_enabled_check,
            self._launcher_hotkey_btn,
            refresh_btn,
        ]
        for check in self._launcher_source_checks.values():
            all_launcher_controls.append(check)
        for field in self._launcher_prefix_fields.values():
            all_launcher_controls.append(field)
        for btn in self._launcher_source_hotkey_btns.values():
            all_launcher_controls.append(btn)

        # Controls below the "Enable Launcher" toggle that also require
        # the launcher itself to be enabled
        sub_controls = [c for c in all_launcher_controls
                        if c is not self._launcher_enabled_check]

        if not scripting_on:
            for ctrl in all_launcher_controls:
                ctrl.setEnabled_(False)
        elif not launcher_on:
            for ctrl in sub_controls:
                ctrl.setEnabled_(False)

        scroll.setDocumentView_(doc_view)
        tab_item.setView_(scroll)

    # ── Helper methods ───────────────────────────────────────────────

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format byte size as human-readable string (MB or GB)."""
        if size_bytes >= 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 ** 3):.1f} GB"
        return f"{size_bytes / (1024 ** 2):.0f} MB"

    @staticmethod
    def _import_ns_color():
        from AppKit import NSColor
        return NSColor

    @staticmethod
    def _make_label(text, x, y, width, font):
        """Create a non-editable label NSTextField."""
        from AppKit import NSTextField
        from Foundation import NSMakeRect

        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y, width, 18))
        label.setFont_(font)
        return label

    @staticmethod
    def _make_readonly_field(text, x, y, width, font):
        """Create a selectable but non-editable text field with border."""
        from AppKit import NSColor, NSTextField
        from Foundation import NSMakeRect

        field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(x, y, width, 22)
        )
        field.setStringValue_(text)
        field.setFont_(font)
        field.setEditable_(False)
        field.setSelectable_(True)
        field.setBezeled_(True)
        field.setDrawsBackground_(True)
        field.setBackgroundColor_(NSColor.controlBackgroundColor())
        field.setTextColor_(NSColor.labelColor())
        return field

    @staticmethod
    def _make_hint(text, x, y, width):
        """Create a 10pt secondary-color hint label."""
        from AppKit import NSColor, NSFont, NSTextField
        from Foundation import NSMakeRect

        hint = NSTextField.labelWithString_(text)
        hint.setFrame_(NSMakeRect(x, y, width, SettingsPanel._HINT_HEIGHT))
        hint.setFont_(NSFont.systemFontOfSize_(10.0))
        hint.setTextColor_(NSColor.secondaryLabelColor())
        return hint

    def _add_hint(self, text, x, y, width, parent):
        """Add a hint label below the current y and return the updated y."""
        y -= (self._HINT_HEIGHT + self._HINT_GAP)
        hint = self._make_hint(text, x, y, width)
        parent.addSubview_(hint)
        return y

    def _add_warning(self, text, x, y, width, parent):
        """Add a red warning label below the current y and return the updated y."""
        from AppKit import NSColor, NSFont, NSTextField
        from Foundation import NSMakeRect

        y -= (self._HINT_HEIGHT + self._HINT_GAP)
        warn = NSTextField.labelWithString_(text)
        warn.setFrame_(NSMakeRect(x, y, width, self._HINT_HEIGHT))
        warn.setFont_(NSFont.systemFontOfSize_(10.0))
        warn.setTextColor_(NSColor.systemRedColor())
        parent.addSubview_(warn)
        return y

    _DOCS_BASE_URL = "https://airead.github.io/WenZi"

    @staticmethod
    def _doc_url(path: str) -> str:
        """Build a full documentation URL with locale-aware prefix.

        *path* should be relative, e.g. ``"enhance-modes.html#how-it-works"``.
        """
        import locale

        current_locale = locale.getlocale()[0] or ""
        if current_locale.startswith("zh"):
            return f"{SettingsPanel._DOCS_BASE_URL}/zh/docs/{path}"
        return f"{SettingsPanel._DOCS_BASE_URL}/docs/{path}"

    def _add_doc_link(self, label, doc_path: str, parent) -> None:
        """Add a small 'Learn more' link button right after a section label.

        Shrinks *label* to fit its text so it does not overlap the button.
        """
        from AppKit import NSBezelStyleInline, NSButton, NSColor, NSFont
        from Foundation import NSMakeRect

        label.sizeToFit()
        lf = label.frame()
        btn_x = lf.origin.x + lf.size.width + 8
        btn_y = lf.origin.y

        btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, btn_y, 80, self._LABEL_HEIGHT)
        )
        btn.setTitle_("Learn more")
        btn.setBezelStyle_(NSBezelStyleInline)
        btn.setFont_(NSFont.systemFontOfSize_(11.0))
        btn.setContentTintColor_(NSColor.linkColor())

        url = self._doc_url(doc_path)
        self._set_meta(btn, doc_url=url)
        btn.setTarget_(self)
        btn.setAction_(b"docLinkClicked:")
        parent.addSubview_(btn)
        self._doc_link_buttons.append(btn)

    def docLinkClicked_(self, sender):
        import webbrowser

        meta = self._get_meta(sender)
        url = meta.get("doc_url")
        if url:
            try:
                webbrowser.open(url)
            except Exception:
                logger.exception("Failed to open doc URL: %s", url)

    def _make_switch(self, title, x, y, width, state_on, font, action, parent):
        """Create a NSSwitchButton checkbox and add to parent."""
        from AppKit import NSButton, NSSwitchButton
        from Foundation import NSMakeRect

        check = NSButton.alloc().initWithFrame_(
            NSMakeRect(x, y, width, self._CONTROL_HEIGHT)
        )
        check.setButtonType_(NSSwitchButton)
        check.setTitle_(title)
        check.setFont_(font)
        check.setState_(1 if state_on else 0)
        check.setTarget_(self)
        check.setAction_(action)
        parent.addSubview_(check)
        return check

    def _make_radio(self, title, x, y, width, selected, font, parent):
        """Create a radio-style NSButton and add to parent."""
        from AppKit import NSButton, NSRadioButton
        from Foundation import NSMakeRect

        btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(x, y, width, self._CONTROL_HEIGHT)
        )
        btn.setButtonType_(NSRadioButton)
        btn.setTitle_(title)
        btn.setFont_(font)
        btn.setState_(1 if selected else 0)
        parent.addSubview_(btn)
        return btn

    def _make_popup(self, items, selected, x, y, width, font, action, parent):
        """Create an NSPopUpButton dropdown and add to parent.

        Args:
            items: list of (value, display_label) tuples.
            selected: the value that should be initially selected.
            width: width of the popup button.
        """
        from AppKit import NSPopUpButton
        from Foundation import NSMakeRect

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(x, y, width, self._CONTROL_HEIGHT + 4), False,
        )
        popup.setFont_(font)
        for value, label in items:
            popup.addItemWithTitle_(label)
            popup.lastItem().setRepresentedObject_(value)
        # Select current value
        for i, (value, _label) in enumerate(items):
            if value == selected:
                popup.selectItemAtIndex_(i)
                break
        popup.setTarget_(self)
        popup.setAction_(action)
        parent.addSubview_(popup)
        return popup

    def _set_meta(self, btn, **kwargs) -> None:
        """Store metadata for a button (NSButton doesn't allow arbitrary attrs)."""
        self._btn_meta[id(btn)] = kwargs

    def _get_meta(self, btn) -> Dict:
        """Retrieve metadata for a button."""
        return self._btn_meta.get(id(btn), {})

    def _call(self, name: str, *args) -> None:
        """Invoke a callback by name if registered."""
        cb = self._callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception:
                logger.exception("Settings callback %s failed", name)
        else:
            logger.warning("No callback registered for: %s", name)

    # ── Tab view delegate ──────────────────────────────────────────────

    def tabView_didSelectTabViewItem_(self, tab_view, tab_item):
        """Scroll to top when switching tabs and notify controller."""
        try:
            view = tab_item.view()
            if view is not None and hasattr(view, "documentView"):
                doc_view = view.documentView()
                if doc_view is not None:
                    from Foundation import NSMakePoint

                    total_h = doc_view.frame().size.height
                    doc_view.scrollPoint_(NSMakePoint(0, total_h))
        except Exception:
            logger.debug("Tab scroll reset failed", exc_info=True)

        try:
            tab_id = tab_item.identifier()
            if tab_id:
                self._call("on_tab_change", str(tab_id))
        except Exception:
            logger.debug("Tab change callback failed", exc_info=True)

    # ── Radio group helpers ──────────────────────────────────────────

    def _select_radio_in_group(self, group: Dict, selected_key) -> None:
        """Set exactly one radio button to ON in a group, others to OFF."""
        for key, btn in group.items():
            btn.setState_(1 if key == selected_key else 0)

    # ── Action handlers (called by NSButton targets) ─────────────────

    # PyObjC action methods need to be registered; we use setTarget_(self)
    # so we need actual ObjC-compatible methods. Since SettingsPanel is a
    # plain Python class (not NSObject), we rely on PyObjC's informal
    # protocol support where setTarget_ accepts any Python object and
    # setAction_ dispatches via Python attribute lookup.

    def revealConfigFolderClicked_(self, sender):
        self._call("on_reveal_config_folder")

    def hotkeyCheckChanged_(self, sender):
        meta = self._get_meta(sender)
        key_name = meta.get("key_name")
        if key_name:
            enabled = bool(sender.state())
            self._call("on_hotkey_toggle", key_name, enabled)

    def hotkeyModeChanged_(self, sender):
        meta = self._get_meta(sender)
        key_name = meta.get("key_name")
        if not key_name:
            return
        value = sender.selectedItem().representedObject()
        if value is None:
            return
        value = str(value)
        if value == "_delete":
            self._call("on_hotkey_delete", key_name)
            # Rebuild settings to remove the deleted hotkey row
            self._call("on_tab_change", "general")
            # Close and reopen to refresh UI
            from PyObjCTools import AppHelper
            AppHelper.callAfter(self._reopen_settings)
        elif value == "_default":
            self._call("on_hotkey_mode_select", key_name, None)
        else:
            self._call("on_hotkey_mode_select", key_name, value)

    def _reopen_settings(self):
        """Close and reopen settings panel to refresh UI."""
        self.close()
        # The panel will be rebuilt when on_open_settings is called
        self._callbacks.get("_reopen", lambda: None)()

    def recordHotkeyClicked_(self, sender):
        self._call("on_record_hotkey")

    def restartKeyChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value:
            self._call("on_restart_key_select", str(value))

    def cancelKeyChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value:
            self._call("on_cancel_key_select", str(value))

    def scriptingCheckChanged_(self, sender):
        self._call("on_scripting_toggle", bool(sender.state()))

    def soundCheckChanged_(self, sender):
        self._call("on_sound_toggle", bool(sender.state()))

    def visualCheckChanged_(self, sender):
        self._call("on_visual_toggle", bool(sender.state()))

    def deviceNameCheckChanged_(self, sender):
        self._call("on_device_name_toggle", bool(sender.state()))

    def previewCheckChanged_(self, sender):
        self._call("on_preview_toggle", bool(sender.state()))

    def sttModelSelected_(self, sender):
        meta = self._get_meta(sender)
        preset_id = meta.get("preset_id")
        if not meta.get("is_available", True):
            sender.setState_(0)
            return
        if preset_id:
            self._select_radio_in_group(self._stt_buttons, preset_id)
            self._select_radio_in_group(self._stt_remote_buttons, None)
            self._call("on_stt_select", preset_id)

    def sttRemoteSelected_(self, sender):
        meta = self._get_meta(sender)
        provider = meta.get("provider")
        model = meta.get("model")
        if provider and model:
            key = (provider, model)
            self._select_radio_in_group(self._stt_buttons, None)
            self._select_radio_in_group(self._stt_remote_buttons, key)
            self._call("on_stt_remote_select", provider, model)

    def sttAddProviderClicked_(self, sender):
        self._call("on_stt_add_provider")

    def sttRemoveProviderClicked_(self, sender):
        self._call("on_stt_remove_provider")

    def llmModelSelected_(self, sender):
        meta = self._get_meta(sender)
        provider = meta.get("provider")
        model = meta.get("model")
        if provider and model:
            key = (provider, model)
            self._select_radio_in_group(self._llm_buttons, key)
            self._call("on_llm_select", provider, model)

    def llmAddProviderClicked_(self, sender):
        self._call("on_llm_add_provider")

    def llmRemoveProviderClicked_(self, sender):
        self._call("on_llm_remove_provider")

    def enhanceModeEditClicked_(self, sender):
        meta = self._get_meta(sender)
        mode_id = meta.get("mode_id")
        if mode_id:
            self._call("on_enhance_mode_edit", mode_id)

    def enhanceModeSelected_(self, sender):
        meta = self._get_meta(sender)
        mode_id = meta.get("mode_id")
        if mode_id:
            self._select_radio_in_group(self._enhance_mode_buttons, mode_id)
            self._call("on_enhance_mode_select", mode_id)

    def addModeClicked_(self, sender):
        self._call("on_enhance_add_mode")

    def thinkingCheckChanged_(self, sender):
        self._call("on_thinking_toggle", bool(sender.state()))

    def vocabCheckChanged_(self, sender):
        self._call("on_vocab_toggle", bool(sender.state()))

    def autoBuildCheckChanged_(self, sender):
        self._call("on_auto_build_toggle", bool(sender.state()))

    def vocabBuildModelChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value is None:
            value = ("", "")
        provider, model = value
        self._call("on_vocab_build_model_select", provider, model)

    def historyCheckChanged_(self, sender):
        self._call("on_history_toggle", bool(sender.state()))

    def historyBaseChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value is not None:
            self._call("on_history_max_entries", int(value))

    def historyMaxChanged_(self, sender):
        value = sender.selectedItem().representedObject()
        if value is not None:
            self._call("on_history_refresh_threshold", int(value))

    def buildVocabClicked_(self, sender):
        self._call("on_vocab_build")

    def configDirBrowseClicked_(self, sender):
        self._call("on_config_dir_browse")

    def configDirResetClicked_(self, sender):
        self._call("on_config_dir_reset")

    def launcherEnabledToggled_(self, sender):
        enabled = sender.state() == 1
        self._call("on_launcher_toggle", enabled)

    def launcherHotkeyRecord_(self, sender):
        self._call("on_launcher_hotkey_record")

    def launcherHotkeyClear_(self, sender):
        self._call("on_launcher_hotkey_clear")

    def launcherSourceToggled_(self, sender):
        meta = self._get_meta(sender)
        config_key = meta.get("config_key", "")
        enabled = sender.state() == 1
        if config_key:
            self._call("on_launcher_source_toggle", config_key, enabled)

    def launcherPrefixChanged_(self, sender):
        meta = self._get_meta(sender)
        prefix_key = meta.get("prefix_key", "")
        value = str(sender.stringValue()).strip()
        if prefix_key:
            self._call("on_launcher_prefix_change", prefix_key, value)

    def launcherUsageLearningToggled_(self, sender):
        enabled = sender.state() == 1
        self._call("on_launcher_usage_learning_toggle", enabled)

    def newSnippetHotkeyRecord_(self, sender):
        self._call("on_new_snippet_hotkey_record")

    def newSnippetHotkeyClear_(self, sender):
        self._call("on_new_snippet_hotkey_clear")

    def launcherSourceHotkeyRecord_(self, sender):
        meta = self._get_meta(sender)
        source_key = meta.get("source_key", "")
        if source_key:
            self._call("on_launcher_source_hotkey_record", source_key)

    def launcherSourceHotkeyClear_(self, sender):
        meta = self._get_meta(sender)
        source_key = meta.get("source_key", "")
        if source_key:
            self._call("on_launcher_source_hotkey_clear", source_key)

    def launcherRefreshIconsClicked_(self, sender):
        self._call("on_launcher_refresh_icons")

    # ── State update methods (called from app.py for sync) ───────────

    def update_hotkey(self, key_name: str, enabled: bool) -> None:
        """Update a hotkey checkbox state."""
        check = self._hotkey_checks.get(key_name)
        if check:
            check.setState_(1 if enabled else 0)

    def update_enhance_mode(self, mode_id: str) -> None:
        """Update the selected enhance mode."""
        self._select_radio_in_group(self._enhance_mode_buttons, mode_id)

    def update_thinking(self, enabled: bool) -> None:
        """Update the thinking checkbox."""
        if self._thinking_check:
            self._thinking_check.setState_(1 if enabled else 0)

    def update_vocab(self, enabled: bool, count: int = 0) -> None:
        """Update vocabulary checkbox and count."""
        if self._vocab_check:
            self._vocab_check.setState_(1 if enabled else 0)
            title = f"Vocabulary ({count})" if count > 0 else "Vocabulary"
            self._vocab_check.setTitle_(title)

    def update_stt_model(
        self,
        preset_id: Optional[str],
        remote_asr: Optional[Tuple[str, str]],
    ) -> None:
        """Update STT model selection."""
        if remote_asr:
            self._select_radio_in_group(self._stt_buttons, None)
            self._select_radio_in_group(self._stt_remote_buttons, remote_asr)
        else:
            self._select_radio_in_group(self._stt_buttons, preset_id)
            self._select_radio_in_group(self._stt_remote_buttons, None)

    def update_llm_model(self, provider: str, model: str) -> None:
        """Update LLM model selection."""
        self._select_radio_in_group(self._llm_buttons, (provider, model))

    def update_config_dir(self, path: str) -> None:
        """Update the config directory display field."""
        if self._config_dir_field:
            self._config_dir_field.setStringValue_(path)

    def _update_hotkey_ui(
        self, label, btn, hotkey: str,
        clear_action: bytes, record_action: bytes,
    ) -> None:
        """Update a hotkey label + Record/Clear button pair."""
        if label:
            label.setStringValue_(hotkey or "None")
        if btn:
            if hotkey:
                btn.setTitle_("Clear")
                btn.setAction_(clear_action)
            else:
                btn.setTitle_("Record")
                btn.setAction_(record_action)

    def update_launcher_hotkey(self, hotkey: str) -> None:
        """Update the launcher hotkey label and button after recording."""
        self._update_hotkey_ui(
            self._launcher_hotkey_label, self._launcher_hotkey_btn, hotkey,
            b"launcherHotkeyClear:", b"launcherHotkeyRecord:",
        )

    def update_source_hotkey(self, source_key: str, hotkey: str) -> None:
        """Update the hotkey label and button for a source after recording."""
        self._update_hotkey_ui(
            self._launcher_source_hotkey_labels.get(source_key),
            self._launcher_source_hotkey_btns.get(source_key),
            hotkey,
            b"launcherSourceHotkeyClear:", b"launcherSourceHotkeyRecord:",
        )

    def update_new_snippet_hotkey(self, hotkey: str) -> None:
        """Update the New Snippet hotkey label and button."""
        self._update_hotkey_ui(
            self._new_snippet_hotkey_label, self._new_snippet_hotkey_btn,
            hotkey,
            b"newSnippetHotkeyClear:", b"newSnippetHotkeyRecord:",
        )
