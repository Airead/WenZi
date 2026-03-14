"""In-app log viewer panel for viewing and filtering VoiceText logs."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Tuple

logger = logging.getLogger(__name__)

# Log line pattern: "2026-03-13 10:00:01,123 [module] LEVEL: message"
_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r" \[([^\]]+)\]"
    r" (DEBUG|INFO|WARNING|ERROR):"
    r" (.*)$"
)

# Log level display indices in the segmented control
_LEVEL_SEGMENTS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
_ALL_LEVELS = frozenset(_LEVEL_SEGMENTS.keys())
_DEFAULT_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})

# Log level options for the popup button
_LOG_LEVEL_OPTIONS = ("DEBUG", "INFO", "WARNING", "ERROR")


def parse_log_lines(raw_lines: List[str]) -> List[Tuple[str, str, str]]:
    """Parse raw log lines into structured entries.

    Each entry is (level, raw_text, searchable_text_lower).
    Continuation lines (e.g. tracebacks) inherit the level of their parent.

    Args:
        raw_lines: List of raw log file lines (without trailing newlines).

    Returns:
        List of (level, full_text, lower_text) tuples.
    """
    entries: List[Tuple[str, str, str]] = []
    current_level = "INFO"
    current_lines: List[str] = []

    def _flush():
        if current_lines:
            text = "\n".join(current_lines)
            entries.append((current_level, text, text.lower()))

    for line in raw_lines:
        m = _LOG_PATTERN.match(line)
        if m:
            _flush()
            current_level = m.group(3)
            current_lines = [line]
        else:
            # Continuation line (e.g. traceback) — append to current entry
            current_lines.append(line)

    _flush()
    return entries


def filter_entries(
    entries: List[Tuple[str, str, str]],
    enabled_levels: frozenset,
    search_text: str,
) -> List[Tuple[str, str, str]]:
    """Filter parsed log entries by level and search text.

    Args:
        entries: Parsed log entries from parse_log_lines().
        enabled_levels: Set of enabled level strings (e.g. {"INFO", "ERROR"}).
        search_text: Case-insensitive search substring. Empty means no filter.

    Returns:
        Filtered list of entries.
    """
    search_lower = search_text.lower().strip()
    result = []
    for level, text, text_lower in entries:
        if level not in enabled_levels:
            continue
        if search_lower and search_lower not in text_lower:
            continue
        result.append((level, text, text_lower))
    return result


class LogViewerPanel:
    """Floating NSPanel for viewing and filtering application logs.

    Layout (720x560):
        +--------------------------------------------------------------+
        | VoiceText Logs                                          [x]  |
        | [Search...___________________] [DEBUG|INFO|WARN|ERR]         |
        | +----------------------------------------------------------+ |
        | | log content...                                           | |
        | +----------------------------------------------------------+ |
        | [Auto-scroll] [Print Prompt] [Print Request Body]           |
        | [Log Level v]   [Console] [Finder] [Copy Path] [Clear] [R] |
        +--------------------------------------------------------------+
    """

    @staticmethod
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

    _PANEL_WIDTH = 720
    _PANEL_HEIGHT = 560
    _PADDING = 12
    _TOOLBAR_HEIGHT = 28
    _BUTTON_HEIGHT = 28
    _BUTTON_WIDTH = 72

    def __init__(
        self,
        log_file: Path,
        on_log_level_change: Callable[[str], None] | None = None,
        on_print_prompt_toggle: Callable[[bool], None] | None = None,
        on_print_request_body_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        self._log_file = log_file
        self._on_log_level_change = on_log_level_change
        self._on_print_prompt_toggle = on_print_prompt_toggle
        self._on_print_request_body_toggle = on_print_request_body_toggle

        self._panel = None
        self._text_view = None
        self._search_field = None
        self._segment_control = None
        self._auto_scroll_check = None
        self._print_prompt_check = None
        self._print_request_body_check = None
        self._log_level_popup = None
        self._timer = None
        self._mono_font = None

        # State for incremental reading
        self._last_size: int = 0
        self._last_pos: int = 0
        self._all_entries: List[Tuple[str, str, str]] = []
        self._enabled_levels: frozenset = _DEFAULT_LEVELS
        self._search_text: str = ""

    def show(
        self,
        current_level: str = "INFO",
        print_prompt: bool = False,
        print_request_body: bool = False,
    ) -> None:
        """Show the log viewer panel. Must be called on the main thread."""
        from AppKit import NSApp, NSOffState, NSOnState

        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        if self._panel is None:
            self._build_panel()

        # Set initial control states
        if self._log_level_popup is not None:
            idx = _LOG_LEVEL_OPTIONS.index(current_level) if current_level in _LOG_LEVEL_OPTIONS else 1
            self._log_level_popup.selectItemAtIndex_(idx)
        if self._print_prompt_check is not None:
            self._print_prompt_check.setState_(NSOnState if print_prompt else NSOffState)
        if self._print_request_body_check is not None:
            self._print_request_body_check.setState_(NSOnState if print_request_body else NSOffState)

        self._load_full_log()
        self._apply_filters()
        self._panel.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self._start_timer()

    def close(self) -> None:
        """Close the panel and stop the timer. Panel instance is preserved."""
        self._stop_timer()
        if self._panel is not None:
            self._panel.setDelegate_(None)
            self._close_delegate = None
            self._panel.orderOut_(None)
        from AppKit import NSApp

        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    def _build_panel(self) -> None:
        """Build the NSPanel and all subviews."""
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezelBorder,
            NSButton,
            NSClosableWindowMask,
            NSColor,
            NSFont,
            NSOffState,
            NSOnState,
            NSPanel,
            NSPopUpButton,
            NSResizableWindowMask,
            NSScrollView,
            NSSearchField,
            NSSegmentedControl,
            NSSegmentStyleTexturedSquare,
            NSStatusWindowLevel,
            NSTextField,
            NSTextView,
            NSTitledWindowMask,
            NSSwitchButton,
            NSViewWidthSizable,
            NSViewHeightSizable,
            NSViewMinXMargin,
            NSViewMinYMargin,
        )
        from Foundation import NSMakeRect, NSMakeSize

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self._PANEL_WIDTH, self._PANEL_HEIGHT),
            NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        panel.setMinSize_(NSMakeSize(600, 400))
        panel.setTitle_("VoiceText Logs")
        panel.setLevel_(NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.center()

        # Delegate for close button
        delegate_cls = _get_panel_close_delegate_class()
        self._close_delegate = delegate_cls.alloc().init()
        self._close_delegate._panel_ref = self
        panel.setDelegate_(self._close_delegate)

        content = panel.contentView()
        inner_w = self._PANEL_WIDTH - 2 * self._PADDING
        y = self._PADDING

        # --- Bottom toolbar row 2: Log Level popup + Console/Finder/Copy Path/Clear/Refresh ---
        btn_x = self._PADDING
        small_font = NSFont.systemFontOfSize_(11.0)
        label_color = NSColor.secondaryLabelColor()

        output_label = NSTextField.labelWithString_("Output Level:")
        output_label.setFrame_(NSMakeRect(btn_x, y + 4, 82, 18))
        output_label.setFont_(small_font)
        output_label.setTextColor_(label_color)
        content.addSubview_(output_label)
        btn_x += 84

        log_level_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(btn_x, y, 100, self._BUTTON_HEIGHT), False
        )
        for level in _LOG_LEVEL_OPTIONS:
            log_level_popup.addItemWithTitle_(level)
        log_level_popup.selectItemAtIndex_(1)  # default INFO
        log_level_popup.setTarget_(self)
        log_level_popup.setAction_(b"logLevelChanged:")
        content.addSubview_(log_level_popup)
        self._log_level_popup = log_level_popup

        # Action buttons: Console, Finder, Copy Path, Clear, Refresh (right-aligned)
        rx = self._PANEL_WIDTH - self._PADDING
        for title, action in reversed([
            ("Console", b"consoleClicked:"),
            ("Finder", b"finderClicked:"),
            ("Copy Path", b"copyPathClicked:"),
            ("Clear", b"clearClicked:"),
            ("Refresh", b"refreshClicked:"),
        ]):
            w = 80 if title == "Copy Path" else self._BUTTON_WIDTH
            rx -= w + 6
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(rx, y, w, self._BUTTON_HEIGHT)
            )
            btn.setTitle_(title)
            btn.setBezelStyle_(1)
            btn.setTarget_(self)
            btn.setAction_(action)
            content.addSubview_(btn)

        y += self._BUTTON_HEIGHT + 8

        # --- Bottom toolbar row 1: Auto-scroll, Print Prompt, Print Request Body ---
        btn_x = self._PADDING

        auto_scroll = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, y, 110, self._BUTTON_HEIGHT)
        )
        auto_scroll.setButtonType_(NSSwitchButton)
        auto_scroll.setTitle_("Auto-scroll")
        auto_scroll.setState_(NSOnState)
        auto_scroll.setTarget_(self)
        auto_scroll.setAction_(b"autoScrollToggled:")
        content.addSubview_(auto_scroll)
        self._auto_scroll_check = auto_scroll
        btn_x += 120

        print_prompt = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, y, 120, self._BUTTON_HEIGHT)
        )
        print_prompt.setButtonType_(NSSwitchButton)
        print_prompt.setTitle_("Print Prompt")
        print_prompt.setState_(NSOffState)
        print_prompt.setTarget_(self)
        print_prompt.setAction_(b"printPromptToggled:")
        content.addSubview_(print_prompt)
        self._print_prompt_check = print_prompt
        btn_x += 130

        print_req_body = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, y, 160, self._BUTTON_HEIGHT)
        )
        print_req_body.setButtonType_(NSSwitchButton)
        print_req_body.setTitle_("Print Request Body")
        print_req_body.setState_(NSOffState)
        print_req_body.setTarget_(self)
        print_req_body.setAction_(b"printRequestBodyToggled:")
        content.addSubview_(print_req_body)
        self._print_request_body_check = print_req_body

        y += self._BUTTON_HEIGHT + self._PADDING

        # --- Log text view ---
        text_height = (
            self._PANEL_HEIGHT
            - self._PADDING  # bottom
            - (self._BUTTON_HEIGHT + 8)  # bottom toolbar row 2
            - (self._BUTTON_HEIGHT + self._PADDING)  # bottom toolbar row 1
            - self._PADDING  # gap above text
            - self._TOOLBAR_HEIGHT - self._PADDING  # top toolbar
            - 30  # title bar approx
        )
        scroll_frame = NSMakeRect(self._PADDING, y, inner_w, text_height)
        scroll = NSScrollView.alloc().initWithFrame_(scroll_frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, inner_w, text_height)
        )
        tv.setMinSize_(NSMakeRect(0, 0, inner_w, 0).size)
        tv.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.textContainer().setWidthTracksTextView_(True)
        self._mono_font = NSFont.userFixedPitchFontOfSize_(11.0)
        tv.setFont_(self._mono_font)
        tv.setEditable_(False)
        tv.setBackgroundColor_(
            self._dynamic_color(
                (0.97, 0.97, 0.97),  # light: near-white
                (0.15, 0.15, 0.15),  # dark: near-black
            )
        )
        scroll.setDocumentView_(tv)
        content.addSubview_(scroll)
        self._text_view = tv

        y += text_height + self._PADDING

        # --- Top toolbar: Search + Filter label + Level filter ---
        filter_label_w = 38
        seg_width = 240
        search_width = inner_w - filter_label_w - seg_width - 12

        search = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(self._PADDING, y, search_width, self._TOOLBAR_HEIGHT)
        )
        search.setPlaceholderString_("Search logs...")
        search.setTarget_(self)
        search.setAction_(b"searchChanged:")
        search.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        content.addSubview_(search)
        self._search_field = search

        filter_label = NSTextField.labelWithString_("Filter:")
        filter_label.setFrame_(
            NSMakeRect(self._PADDING + search_width + 6, y + 5, filter_label_w, 18)
        )
        filter_label.setFont_(small_font)
        filter_label.setTextColor_(label_color)
        filter_label.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(filter_label)

        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(
                self._PADDING + search_width + 6 + filter_label_w + 2,
                y,
                seg_width,
                self._TOOLBAR_HEIGHT,
            )
        )
        seg.setSegmentCount_(4)
        seg.setSegmentStyle_(NSSegmentStyleTexturedSquare)
        # Tracking mode must be set before selection states
        # 0=SelectOne, 1=SelectAny, 2=Momentary
        seg.setTrackingMode_(1)
        for label, idx in _LEVEL_SEGMENTS.items():
            seg.setLabel_forSegment_(label, idx)
            seg.setSelected_forSegment_(label in _DEFAULT_LEVELS, idx)
            seg.setWidth_forSegment_(seg_width / 4, idx)
        seg.setTarget_(self)
        seg.setAction_(b"levelFilterChanged:")
        seg.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
        content.addSubview_(seg)
        self._segment_control = seg

        self._panel = panel

    _MAX_INITIAL_LINES = 50_000

    def _load_full_log(self) -> None:
        """Load the last _MAX_INITIAL_LINES lines from the log file."""
        try:
            if not self._log_file.exists():
                self._all_entries = []
                self._last_size = 0
                self._last_pos = 0
                return
            raw = self._log_file.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            if len(lines) > self._MAX_INITIAL_LINES:
                lines = lines[-self._MAX_INITIAL_LINES:]
            self._all_entries = parse_log_lines(lines)
            size = self._log_file.stat().st_size
            self._last_size = size
            self._last_pos = size
        except Exception:
            logger.exception("Failed to load log file")

    def _poll_log_file(self) -> None:
        """Incremental read of new log lines. Detect file rotation."""
        try:
            if not self._log_file.exists():
                return
            size = self._log_file.stat().st_size
            if size < self._last_size:
                # File was rotated — reload fully
                self._load_full_log()
                self._apply_filters()
                return
            if size == self._last_pos:
                return  # No new data
            with open(self._log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._last_pos)
                new_data = f.read()
            self._last_pos = size
            self._last_size = size
            if not new_data:
                return
            new_lines = new_data.splitlines()
            new_entries = parse_log_lines(new_lines)
            self._all_entries.extend(new_entries)
            self._apply_filters()
        except Exception:
            logger.exception("Error polling log file")

    def pollLogFile_(self, timer) -> None:
        """NSTimer callback for incremental log polling."""
        self._poll_log_file()

    def _apply_filters(self) -> None:
        """Apply level and search filters, then update the text view."""
        filtered = filter_entries(
            self._all_entries, self._enabled_levels, self._search_text
        )
        self._render_entries(filtered)

    def _render_entries(self, entries: List[Tuple[str, str, str]]) -> None:
        """Render filtered entries into the text view with colored text."""
        tv = self._text_view
        if tv is None:
            return

        from AppKit import NSColor, NSForegroundColorAttributeName
        from Foundation import (
            NSAttributedString,
            NSDictionary,
            NSMutableAttributedString,
        )

        default_color = self._dynamic_color((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        level_colors = {
            "DEBUG": self._dynamic_color((0.5, 0.5, 0.5), (0.6, 0.6, 0.6)),
            "INFO": default_color,
            "WARNING": NSColor.orangeColor(),
            "ERROR": NSColor.redColor(),
        }

        result = NSMutableAttributedString.alloc().init()
        for i, (level, text, _) in enumerate(entries):
            if i > 0:
                nl = NSAttributedString.alloc().initWithString_("\n")
                result.appendAttributedString_(nl)
            color = level_colors.get(level, default_color)
            attrs = NSDictionary.dictionaryWithObjectsAndKeys_(
                color,
                NSForegroundColorAttributeName,
                self._mono_font,
                "NSFont",
                None,
            )
            attr_str = NSAttributedString.alloc().initWithString_attributes_(
                text, attrs
            )
            result.appendAttributedString_(attr_str)

        tv.textStorage().setAttributedString_(result)

        # Auto-scroll to bottom
        if self._auto_scroll_check is not None:
            from AppKit import NSOnState

            if self._auto_scroll_check.state() == NSOnState:
                length = tv.textStorage().length()
                if length > 0:
                    tv.scrollRangeToVisible_((length, 0))

    def _start_timer(self) -> None:
        """Start polling timer (1 second interval)."""
        self._stop_timer()
        from Foundation import NSTimer, NSRunLoop, NSDefaultRunLoopMode

        self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, b"pollLogFile:", None, True
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._timer, NSDefaultRunLoopMode)

    def _stop_timer(self) -> None:
        """Stop the polling timer."""
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _get_enabled_levels(self) -> frozenset:
        """Read currently selected levels from the segmented control."""
        if self._segment_control is None:
            return _ALL_LEVELS
        levels = set()
        for level_name, idx in _LEVEL_SEGMENTS.items():
            if self._segment_control.isSelectedForSegment_(idx):
                levels.add(level_name)
        return frozenset(levels)

    # --- Action handlers ---

    def searchChanged_(self, sender) -> None:
        """NSSearchField action — update search filter."""
        self._search_text = sender.stringValue()
        self._apply_filters()

    def levelFilterChanged_(self, sender) -> None:
        """NSSegmentedControl action — update level filter."""
        self._enabled_levels = self._get_enabled_levels()
        self._apply_filters()

    def autoScrollToggled_(self, sender) -> None:
        """Auto-scroll checkbox toggled."""
        pass  # State is read in _render_entries

    def clearClicked_(self, sender) -> None:
        """Clear the log display (does not delete the file)."""
        self._all_entries = []
        self._apply_filters()

    def refreshClicked_(self, sender) -> None:
        """Force a full reload of the log file."""
        self._load_full_log()
        self._apply_filters()

    def logLevelChanged_(self, sender) -> None:
        """Log level popup changed — notify via callback."""
        title = sender.titleOfSelectedItem()
        if self._on_log_level_change is not None and title:
            self._on_log_level_change(title)

    def printPromptToggled_(self, sender) -> None:
        """Print Prompt checkbox toggled — notify via callback."""
        from AppKit import NSOnState

        enabled = sender.state() == NSOnState
        if self._on_print_prompt_toggle is not None:
            self._on_print_prompt_toggle(enabled)

    def printRequestBodyToggled_(self, sender) -> None:
        """Print Request Body checkbox toggled — notify via callback."""
        from AppKit import NSOnState

        enabled = sender.state() == NSOnState
        if self._on_print_request_body_toggle is not None:
            self._on_print_request_body_toggle(enabled)

    def consoleClicked_(self, sender) -> None:
        """Open the log file in Console.app."""
        try:
            subprocess.Popen(["open", "-a", "Console", str(self._log_file)])
        except Exception:
            logger.exception("Failed to open Console.app")

    def finderClicked_(self, sender) -> None:
        """Reveal the log file in Finder."""
        try:
            subprocess.Popen(["open", "-R", str(self._log_file)])
        except Exception:
            logger.exception("Failed to reveal log in Finder")

    def copyPathClicked_(self, sender) -> None:
        """Copy the log file path to the clipboard."""
        try:
            subprocess.run(
                ["pbcopy"], input=str(self._log_file).encode(), check=True
            )
        except Exception:
            logger.exception("Failed to copy log path")


_PanelCloseDelegate = None


def _get_panel_close_delegate_class():
    """Lazily create and cache the NSObject subclass for NSWindowDelegate."""
    global _PanelCloseDelegate
    if _PanelCloseDelegate is None:
        from Foundation import NSObject

        class LogViewerCloseDelegate(NSObject):
            """NSWindowDelegate that closes the log viewer when X is clicked."""

            _panel_ref = None

            def windowWillClose_(self, notification):
                if self._panel_ref is not None:
                    self._panel_ref.close()

        _PanelCloseDelegate = LogViewerCloseDelegate
    return _PanelCloseDelegate
