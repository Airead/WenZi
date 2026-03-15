"""History browser panel for viewing and editing conversation history."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Filter labels
_MODE_ALL = "All"
_MODEL_ALL = "All Models"


def _format_timestamp(ts: str) -> str:
    """Format ISO timestamp as 'YYYY-MM-DD HH:MM'."""
    try:
        return ts[:16].replace("T", " ")
    except Exception:
        return ts


def _corrected_row_color():
    """Return a dynamic light-blue color that adapts to dark mode."""
    from AppKit import (
        NSAppearanceNameAqua,
        NSAppearanceNameDarkAqua,
        NSColor,
    )

    def provider(appearance):
        name = appearance.bestMatchFromAppearancesWithNames_([
            NSAppearanceNameAqua, NSAppearanceNameDarkAqua,
        ])
        if name == NSAppearanceNameDarkAqua:
            return NSColor.colorWithSRGBRed_green_blue_alpha_(0.15, 0.25, 0.45, 1.0)
        return NSColor.colorWithSRGBRed_green_blue_alpha_(0.85, 0.93, 1.0, 1.0)

    return NSColor.colorWithName_dynamicProvider_("correctedRow", provider)


class HistoryBrowserPanel:
    """Floating NSPanel for browsing conversation history.

    Layout (780x600, resizable):
        +----------------------------------------------------------+
        | Conversation History                               [x]   |
        | [Search..._______________________] [Mode: All v]         |
        | +------------------------------------------------------+ |
        | | 2026-03-13 14:30  proofread  | text preview...       | |
        | | 2026-03-13 14:25  translate  | text preview...       | |
        | +------------------------------------------------------+ |
        | Detail                                                   |
        | ASR (model):      (read-only)                            |
        | Enhanced (model): (read-only)                            |
        | Final:    [editable____________________________]         |
        | Mode: proofread   Time: 2026-03-13 14:30                 |
        |                                    [Save]  [Close]       |
        +----------------------------------------------------------+
    """

    _PANEL_WIDTH = 780
    _PANEL_HEIGHT = 600
    _PADDING = 12
    _BUTTON_HEIGHT = 28
    _BUTTON_WIDTH = 80
    _SEARCH_HEIGHT = 28
    _DETAIL_LABEL_HEIGHT = 18
    _TEXT_VIEW_HEIGHT = 60
    _FIELD_HEIGHT = 28

    def __init__(self) -> None:
        self._panel = None
        self._search_field = None
        self._mode_popup = None
        self._model_popup = None
        self._table_view = None
        self._scroll_view = None
        self._asr_label = None
        self._asr_text_view = None
        self._enhanced_label = None
        self._enhanced_text_view = None
        self._final_text_field = None
        self._mode_label = None
        self._time_label = None
        self._save_btn = None
        self._close_delegate = None
        self._table_delegate = None
        self._text_field_delegate = None

        self._all_records: List[Dict[str, Any]] = []
        self._filtered_records: List[Dict[str, Any]] = []
        self._selected_index: int = -1
        self._conversation_history = None
        self._on_save: Optional[Callable[[str, str], None]] = None
        self._search_text: str = ""
        self._filter_mode: str = _MODE_ALL
        self._filter_model: str = _MODEL_ALL
        self._filter_corrected_only: bool = False
        self._corrected_checkbox = None

    def show(
        self,
        conversation_history,
        on_save: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Show the history browser panel.

        Args:
            conversation_history: ConversationHistory instance.
            on_save: Callback(timestamp, new_final_text) when user saves an edit.
        """
        from AppKit import NSApp

        self._conversation_history = conversation_history
        self._on_save = on_save

        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular

        if self._panel is None:
            self._build_panel()
        else:
            # Restore delegate after close() cleared it
            delegate_cls = _get_panel_close_delegate_class()
            self._close_delegate = delegate_cls.alloc().init()
            self._close_delegate._panel_ref = self
            self._panel.setDelegate_(self._close_delegate)

        self._reload_data()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def close(self) -> None:
        """Close the panel."""
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            self._panel.orderOut_(None)

        if self._conversation_history is not None:
            self._conversation_history.release_full_cache()

        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    def _reload_data(self) -> None:
        """Reload all records from conversation history and apply filters."""
        if self._conversation_history is None:
            return
        if self._search_text:
            self._all_records = self._conversation_history.search(
                self._search_text
            )
        else:
            self._all_records = self._conversation_history.get_all()

        self._rebuild_mode_popup()
        self._rebuild_model_popup()
        self._apply_filters()

        self._selected_index = -1
        if self._table_view is not None:
            self._table_view.reloadData()
        self._clear_detail()

    def _rebuild_mode_popup(self) -> None:
        """Rebuild mode filter popup items from current data."""
        if self._mode_popup is None:
            return
        # Collect unique modes
        modes: Set[str] = set()
        for r in self._all_records:
            m = r.get("enhance_mode", "")
            if m:
                modes.add(m)

        self._mode_popup.removeAllItems()
        self._mode_popup.addItemWithTitle_(_MODE_ALL)
        for m in sorted(modes):
            self._mode_popup.addItemWithTitle_(m)

        # Restore previous selection if still valid
        if self._filter_mode != _MODE_ALL:
            idx = self._mode_popup.indexOfItemWithTitle_(self._filter_mode)
            if idx >= 0:
                self._mode_popup.selectItemAtIndex_(idx)
            else:
                self._filter_mode = _MODE_ALL

    def _rebuild_model_popup(self) -> None:
        """Rebuild model filter popup items from current data."""
        if self._model_popup is None:
            return
        models: Set[str] = set()
        for r in self._all_records:
            for key in ("stt_model", "llm_model"):
                m = r.get(key, "")
                if m:
                    models.add(m)

        self._model_popup.removeAllItems()
        self._model_popup.addItemWithTitle_(_MODEL_ALL)
        for m in sorted(models):
            self._model_popup.addItemWithTitle_(m)

        if self._filter_model != _MODEL_ALL:
            idx = self._model_popup.indexOfItemWithTitle_(self._filter_model)
            if idx >= 0:
                self._model_popup.selectItemAtIndex_(idx)
            else:
                self._filter_model = _MODEL_ALL

    def _apply_filters(self) -> None:
        """Filter _all_records by selected mode, model and corrected flag."""
        from voicetext.enhance.conversation_history import ConversationHistory

        records = self._all_records
        if self._filter_mode != _MODE_ALL:
            records = [
                r for r in records
                if r.get("enhance_mode", "") == self._filter_mode
            ]
        if self._filter_model != _MODEL_ALL:
            records = [
                r for r in records
                if r.get("stt_model", "") == self._filter_model
                or r.get("llm_model", "") == self._filter_model
            ]
        if self._filter_corrected_only:
            records = [
                r for r in records
                if ConversationHistory._is_corrected(r)
            ]
        self._filtered_records = records

    def _clear_detail(self) -> None:
        """Clear the detail section."""
        if self._asr_text_view:
            self._asr_text_view.setString_("")
        if self._enhanced_text_view:
            self._enhanced_text_view.setString_("")
        if self._final_text_field:
            self._final_text_field.setStringValue_("")
            self._final_text_field.setEditable_(False)
        if self._asr_label:
            self._asr_label.setStringValue_("ASR:")
        if self._enhanced_label:
            self._enhanced_label.setStringValue_("Enhanced:")
        if self._mode_label:
            self._mode_label.setStringValue_("")
        if self._time_label:
            self._time_label.setStringValue_("")
        if self._save_btn:
            self._save_btn.setEnabled_(False)

    def _show_detail(self, record: Dict[str, Any]) -> None:
        """Populate the detail section with a record."""
        if self._asr_text_view:
            self._asr_text_view.setString_(record.get("asr_text", ""))
        if self._enhanced_text_view:
            self._enhanced_text_view.setString_(
                record.get("enhanced_text", "") or ""
            )
        if self._final_text_field:
            self._final_text_field.setStringValue_(record.get("final_text", ""))
            self._final_text_field.setEditable_(True)

        # ASR label with STT model
        if self._asr_label:
            stt = record.get("stt_model", "")
            self._asr_label.setStringValue_(
                f"ASR ({stt}):" if stt else "ASR:"
            )

        # Enhanced label with LLM model
        if self._enhanced_label:
            llm = record.get("llm_model", "")
            self._enhanced_label.setStringValue_(
                f"Enhanced ({llm}):" if llm else "Enhanced:"
            )

        if self._mode_label:
            self._mode_label.setStringValue_(
                f"Mode: {record.get('enhance_mode', 'off')}"
            )
        if self._time_label:
            ts = record.get("timestamp", "")
            edited = record.get("edited_at", "")
            label = f"Time: {_format_timestamp(ts)}"
            if edited:
                label += f"  (edited: {_format_timestamp(edited)})"
            self._time_label.setStringValue_(label)
        if self._save_btn:
            self._save_btn.setEnabled_(False)

    def _build_panel(self) -> None:
        """Build the NSPanel and all subviews."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSClosableWindowMask,
            NSPanel,
            NSResizableWindowMask,
            NSStatusWindowLevel,
            NSTitledWindowMask,
        )
        from Foundation import NSMakeRect, NSMakeSize

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(600, 450))
        panel.setTitle_("Conversation History")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # Close delegate (NSObject subclass)
        delegate_cls = _get_panel_close_delegate_class()
        self._close_delegate = delegate_cls.alloc().init()
        self._close_delegate._panel_ref = self
        panel.setDelegate_(self._close_delegate)

        content = panel.contentView()
        inner_w = self._PANEL_WIDTH - 2 * self._PADDING
        y = self._PADDING

        y = self._build_buttons(content, y, inner_w)
        y = self._build_detail_info(content, y, inner_w)
        y = self._build_final_text(content, y, inner_w)
        y = self._build_enhanced_text(content, y, inner_w)
        y = self._build_asr_text(content, y, inner_w)
        y = self._build_table_view(content, y, inner_w)
        y = self._build_toolbar(content, y, inner_w)

        self._panel = panel

    def _build_buttons(self, content_view, y, inner_w):
        """Build Close and Save buttons at the bottom."""
        from AppKit import NSButton
        from Foundation import NSMakeRect

        close_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH - self._PADDING - self._BUTTON_WIDTH,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        close_btn.setTitle_("Close")
        close_btn.setBezelStyle_(1)
        close_btn.setKeyEquivalent_("\x1b")  # Escape
        close_btn.setTarget_(self)
        close_btn.setAction_(b"closeClicked:")
        content_view.addSubview_(close_btn)

        save_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PANEL_WIDTH
                - self._PADDING
                - 2 * self._BUTTON_WIDTH
                - 8,
                y,
                self._BUTTON_WIDTH,
                self._BUTTON_HEIGHT,
            )
        )
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)
        save_btn.setTarget_(self)
        save_btn.setAction_(b"saveClicked:")
        save_btn.setEnabled_(False)
        content_view.addSubview_(save_btn)
        self._save_btn = save_btn

        y += self._BUTTON_HEIGHT + self._PADDING
        return y

    def _build_detail_info(self, content_view, y, inner_w):
        """Build Time and Mode labels."""
        from AppKit import NSColor, NSFont, NSTextField
        from Foundation import NSMakeRect

        small_font = NSFont.systemFontOfSize_(11.0)
        label_color = NSColor.secondaryLabelColor()

        self._time_label = NSTextField.labelWithString_("")
        self._time_label.setFrame_(
            NSMakeRect(self._PADDING + inner_w // 3, y, inner_w * 2 // 3, self._DETAIL_LABEL_HEIGHT)
        )
        self._time_label.setFont_(small_font)
        self._time_label.setTextColor_(label_color)
        content_view.addSubview_(self._time_label)

        self._mode_label = NSTextField.labelWithString_("")
        self._mode_label.setFrame_(
            NSMakeRect(self._PADDING, y, inner_w // 3, self._DETAIL_LABEL_HEIGHT)
        )
        self._mode_label.setFont_(small_font)
        self._mode_label.setTextColor_(label_color)
        content_view.addSubview_(self._mode_label)

        y += self._DETAIL_LABEL_HEIGHT + 4
        return y

    def _build_final_text(self, content_view, y, inner_w):
        """Build Final text editable field."""
        from AppKit import NSColor, NSFont, NSTextField
        from Foundation import NSMakeRect

        final_label = NSTextField.labelWithString_("Final:")
        final_label.setFrame_(
            NSMakeRect(self._PADDING, y + self._FIELD_HEIGHT, 50, self._DETAIL_LABEL_HEIGHT)
        )
        final_label.setFont_(NSFont.boldSystemFontOfSize_(11.0))
        final_label.setTextColor_(NSColor.labelColor())
        content_view.addSubview_(final_label)

        self._final_text_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, inner_w, self._FIELD_HEIGHT)
        )
        self._final_text_field.setEditable_(False)
        self._final_text_field.setBezeled_(True)
        self._final_text_field.setFont_(NSFont.systemFontOfSize_(13.0))
        content_view.addSubview_(self._final_text_field)

        # NSTextField delegate for detecting edits (NSObject subclass)
        tf_delegate_cls = _get_text_field_delegate_class()
        self._text_field_delegate = tf_delegate_cls.alloc().init()
        self._text_field_delegate._panel_ref = self
        self._final_text_field.setDelegate_(self._text_field_delegate)

        y += self._FIELD_HEIGHT + self._DETAIL_LABEL_HEIGHT + 6
        return y

    def _build_enhanced_text(self, content_view, y, inner_w):
        """Build Enhanced text read-only view."""
        from AppKit import (
            NSBezelBorder,
            NSColor,
            NSFont,
            NSScrollView,
            NSTextField,
            NSTextView,
        )
        from Foundation import NSMakeRect

        self._enhanced_label = NSTextField.labelWithString_("Enhanced:")
        self._enhanced_label.setFrame_(
            NSMakeRect(self._PADDING, y + self._TEXT_VIEW_HEIGHT, inner_w, self._DETAIL_LABEL_HEIGHT)
        )
        self._enhanced_label.setFont_(NSFont.boldSystemFontOfSize_(11.0))
        self._enhanced_label.setTextColor_(NSColor.labelColor())
        content_view.addSubview_(self._enhanced_label)

        enhanced_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, inner_w, self._TEXT_VIEW_HEIGHT)
        )
        enhanced_scroll.setHasVerticalScroller_(True)
        enhanced_scroll.setBorderType_(NSBezelBorder)
        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w, self._TEXT_VIEW_HEIGHT)
        )
        tv.setEditable_(False)
        tv.setFont_(NSFont.systemFontOfSize_(12.0))
        tv.setBackgroundColor_(NSColor.textBackgroundColor())
        tv.setTextColor_(NSColor.labelColor())
        enhanced_scroll.setDocumentView_(tv)
        content_view.addSubview_(enhanced_scroll)
        self._enhanced_text_view = tv

        y += self._TEXT_VIEW_HEIGHT + self._DETAIL_LABEL_HEIGHT + 6
        return y

    def _build_asr_text(self, content_view, y, inner_w):
        """Build ASR text read-only view."""
        from AppKit import (
            NSBezelBorder,
            NSColor,
            NSFont,
            NSScrollView,
            NSTextField,
            NSTextView,
        )
        from Foundation import NSMakeRect

        self._asr_label = NSTextField.labelWithString_("ASR:")
        self._asr_label.setFrame_(
            NSMakeRect(self._PADDING, y + self._TEXT_VIEW_HEIGHT, inner_w, self._DETAIL_LABEL_HEIGHT)
        )
        self._asr_label.setFont_(NSFont.boldSystemFontOfSize_(11.0))
        self._asr_label.setTextColor_(NSColor.labelColor())
        content_view.addSubview_(self._asr_label)

        asr_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, inner_w, self._TEXT_VIEW_HEIGHT)
        )
        asr_scroll.setHasVerticalScroller_(True)
        asr_scroll.setBorderType_(NSBezelBorder)
        tv2 = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w, self._TEXT_VIEW_HEIGHT)
        )
        tv2.setEditable_(False)
        tv2.setFont_(NSFont.systemFontOfSize_(12.0))
        tv2.setBackgroundColor_(NSColor.textBackgroundColor())
        tv2.setTextColor_(NSColor.labelColor())
        asr_scroll.setDocumentView_(tv2)
        content_view.addSubview_(asr_scroll)
        self._asr_text_view = tv2

        y += self._TEXT_VIEW_HEIGHT + self._DETAIL_LABEL_HEIGHT + 8
        return y

    def _build_table_view(self, content_view, y, inner_w):
        """Build Records table."""
        from AppKit import (
            NSBezelBorder,
            NSScrollView,
            NSTableColumn,
            NSTableView,
            NSViewWidthSizable,
            NSViewHeightSizable,
        )
        from Foundation import NSMakeRect, NSMakeSize

        table_height = (
            self._PANEL_HEIGHT
            - y
            - self._SEARCH_HEIGHT
            - self._PADDING * 2
            - 30  # title bar
        )

        table_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, inner_w, table_height)
        )
        table_scroll.setHasVerticalScroller_(True)
        table_scroll.setBorderType_(NSBezelBorder)
        table_scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        table = NSTableView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w, table_height)
        )

        col_time = NSTableColumn.alloc().initWithIdentifier_("time")
        col_time.setWidth_(130)
        col_time.headerCell().setStringValue_("Time")
        table.addTableColumn_(col_time)

        col_mode = NSTableColumn.alloc().initWithIdentifier_("mode")
        col_mode.setWidth_(80)
        col_mode.headerCell().setStringValue_("Mode")
        table.addTableColumn_(col_mode)

        col_preview = NSTableColumn.alloc().initWithIdentifier_("preview")
        col_preview.setWidth_(inner_w - 130 - 80 - 20)
        col_preview.headerCell().setStringValue_("Content")
        table.addTableColumn_(col_preview)

        # NSTableView dataSource/delegate must be NSObject subclasses
        table_delegate_cls = _get_table_delegate_class()
        self._table_delegate = table_delegate_cls.alloc().init()
        self._table_delegate._panel_ref = self
        table.setDataSource_(self._table_delegate)
        table.setDelegate_(self._table_delegate)
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setIntercellSpacing_(NSMakeSize(0, 2))
        table.setRowHeight_(22)

        table_scroll.setDocumentView_(table)
        content_view.addSubview_(table_scroll)
        self._table_view = table
        self._scroll_view = table_scroll

        y += table_height + self._PADDING
        return y

    def _build_toolbar(self, content_view, y, inner_w):
        """Build Search, filters, and corrected checkbox."""
        from AppKit import (
            NSButton,
            NSFont,
            NSPopUpButton,
            NSSearchField,
            NSViewWidthSizable,
            NSViewMinYMargin,
        )
        from Foundation import NSMakeRect

        mode_popup_w = 120
        model_popup_w = 140
        corrected_cb_w = 90
        search_w = inner_w - mode_popup_w - model_popup_w - corrected_cb_w - 24

        search = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, search_w, self._SEARCH_HEIGHT)
        )
        search.setPlaceholderString_("Search history...")
        search.setTarget_(self)
        search.setAction_(b"searchChanged:")
        search.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        content_view.addSubview_(search)
        self._search_field = search

        mode_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(
                self._PADDING + search_w + 8,
                y,
                mode_popup_w,
                self._SEARCH_HEIGHT,
            ),
            False,
        )
        mode_popup.addItemWithTitle_(_MODE_ALL)
        mode_popup.setTarget_(self)
        mode_popup.setAction_(b"modeFilterChanged:")
        mode_popup.setAutoresizingMask_(NSViewMinYMargin)
        content_view.addSubview_(mode_popup)
        self._mode_popup = mode_popup

        model_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(
                self._PADDING + search_w + 8 + mode_popup_w + 8,
                y,
                model_popup_w,
                self._SEARCH_HEIGHT,
            ),
            False,
        )
        model_popup.addItemWithTitle_(_MODEL_ALL)
        model_popup.setTarget_(self)
        model_popup.setAction_(b"modelFilterChanged:")
        model_popup.setAutoresizingMask_(NSViewMinYMargin)
        content_view.addSubview_(model_popup)
        self._model_popup = model_popup

        corrected_cb = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                self._PADDING + search_w + 8 + mode_popup_w + 8 + model_popup_w + 8,
                y,
                corrected_cb_w,
                self._SEARCH_HEIGHT,
            )
        )
        corrected_cb.setButtonType_(3)  # NSSwitchButton
        corrected_cb.setTitle_("Corrected")
        corrected_cb.setFont_(NSFont.systemFontOfSize_(11.0))
        corrected_cb.setState_(0)
        corrected_cb.setTarget_(self)
        corrected_cb.setAction_(b"correctedFilterChanged:")
        corrected_cb.setAutoresizingMask_(NSViewMinYMargin)
        content_view.addSubview_(corrected_cb)
        self._corrected_checkbox = corrected_cb

        return y

    # --- Data access for NSObject delegates ---

    def numberOfRowsInTableView_(self, table_view) -> int:
        return len(self._filtered_records)

    def tableView_objectValueForTableColumn_row_(self, table_view, column, row):
        if row < 0 or row >= len(self._filtered_records):
            return ""
        record = self._filtered_records[row]
        col_id = column.identifier()
        if col_id == "time":
            return _format_timestamp(record.get("timestamp", ""))
        elif col_id == "mode":
            return record.get("enhance_mode", "off")
        elif col_id == "preview":
            text = record.get("final_text", "") or record.get("asr_text", "")
            text = text.replace("\n", " ")
            return text[:80] if len(text) > 80 else text
        return ""

    def tableView_willDisplayCell_forTableColumn_row_(
        self, table_view, cell, column, row,
    ) -> None:
        """Set light blue background for user-corrected rows."""
        if row < 0 or row >= len(self._filtered_records):
            return
        record = self._filtered_records[row]
        from voicetext.enhance.conversation_history import ConversationHistory

        if ConversationHistory._is_corrected(record):
            cell.setDrawsBackground_(True)
            cell.setBackgroundColor_(_corrected_row_color())
        else:
            cell.setDrawsBackground_(False)

    def tableViewSelectionDidChange_(self, notification) -> None:
        table = notification.object()
        row = table.selectedRow()
        if row < 0 or row >= len(self._filtered_records):
            self._selected_index = -1
            self._clear_detail()
            return
        self._selected_index = row
        self._show_detail(self._filtered_records[row])

    def _on_final_text_changed(self) -> None:
        """Called by text field delegate when final text is edited."""
        if self._selected_index < 0 or self._selected_index >= len(
            self._filtered_records
        ):
            return
        record = self._filtered_records[self._selected_index]
        current_text = self._final_text_field.stringValue()
        original_text = record.get("final_text", "")
        if self._save_btn:
            self._save_btn.setEnabled_(current_text != original_text)

    # --- Action handlers (target-action works with plain Python objects) ---

    def searchChanged_(self, sender) -> None:
        """NSSearchField action -- filter records."""
        self._search_text = sender.stringValue()
        self._reload_data()

    def modeFilterChanged_(self, sender) -> None:
        """Mode filter popup changed."""
        self._filter_mode = sender.titleOfSelectedItem() or _MODE_ALL
        self._apply_filters()
        self._selected_index = -1
        if self._table_view is not None:
            self._table_view.reloadData()
        self._clear_detail()

    def modelFilterChanged_(self, sender) -> None:
        """Model filter popup changed."""
        self._filter_model = sender.titleOfSelectedItem() or _MODEL_ALL
        self._apply_filters()
        self._selected_index = -1
        if self._table_view is not None:
            self._table_view.reloadData()
        self._clear_detail()

    def correctedFilterChanged_(self, sender) -> None:
        """Corrected-only checkbox toggled."""
        self._filter_corrected_only = sender.state() == 1
        self._apply_filters()
        self._selected_index = -1
        if self._table_view is not None:
            self._table_view.reloadData()
        self._clear_detail()

    def saveClicked_(self, sender) -> None:
        """Save edited final_text back to conversation history."""
        if self._selected_index < 0 or self._selected_index >= len(
            self._filtered_records
        ):
            return
        record = self._filtered_records[self._selected_index]
        new_text = self._final_text_field.stringValue()
        timestamp = record.get("timestamp", "")
        if not timestamp:
            return

        if self._conversation_history:
            ok = self._conversation_history.update_final_text(timestamp, new_text)
            if ok:
                record["final_text"] = new_text
                if self._table_view:
                    self._table_view.reloadData()
                if self._save_btn:
                    self._save_btn.setEnabled_(False)
                if self._on_save:
                    self._on_save(timestamp, new_text)

    def closeClicked_(self, sender) -> None:
        """Close button clicked."""
        self.close()


# ---------------------------------------------------------------------------
# NSObject subclasses for Objective-C protocol conformance
# ---------------------------------------------------------------------------
# These are lazily created and cached to avoid duplicate ObjC class errors.

_HistoryBrowserCloseDelegate = None
_HistoryBrowserTableDelegate = None
_HistoryBrowserTextFieldDelegate = None


def _get_panel_close_delegate_class():
    """NSWindowDelegate for the panel close button."""
    global _HistoryBrowserCloseDelegate
    if _HistoryBrowserCloseDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserCloseDelegate(NSObject):
            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.close()

        _HistoryBrowserCloseDelegate = HistoryBrowserCloseDelegate
    return _HistoryBrowserCloseDelegate


def _get_table_delegate_class():
    """NSTableViewDataSource + NSTableViewDelegate for the history list."""
    global _HistoryBrowserTableDelegate
    if _HistoryBrowserTableDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserTableDelegate(NSObject):
            _panel_ref = None

            def numberOfRowsInTableView_(self, table_view) -> int:
                if self._panel_ref is not None:
                    return self._panel_ref.numberOfRowsInTableView_(table_view)
                return 0

            def tableView_objectValueForTableColumn_row_(self, table_view, column, row):
                if self._panel_ref is not None:
                    return self._panel_ref.tableView_objectValueForTableColumn_row_(
                        table_view, column, row
                    )
                return ""

            def tableView_willDisplayCell_forTableColumn_row_(
                self, table_view, cell, column, row,
            ):
                if self._panel_ref is not None:
                    self._panel_ref.tableView_willDisplayCell_forTableColumn_row_(
                        table_view, cell, column, row,
                    )

            def tableViewSelectionDidChange_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.tableViewSelectionDidChange_(notification)

        _HistoryBrowserTableDelegate = HistoryBrowserTableDelegate
    return _HistoryBrowserTableDelegate


def _get_text_field_delegate_class():
    """NSTextField delegate for detecting edits to the final text field."""
    global _HistoryBrowserTextFieldDelegate
    if _HistoryBrowserTextFieldDelegate is None:
        from Foundation import NSObject

        class HistoryBrowserTextFieldDelegate(NSObject):
            _panel_ref = None

            def controlTextDidChange_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref._on_final_text_changed()

        _HistoryBrowserTextFieldDelegate = HistoryBrowserTextFieldDelegate
    return _HistoryBrowserTextFieldDelegate
