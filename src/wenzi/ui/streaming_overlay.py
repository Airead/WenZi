"""Floating overlay panel for Direct mode streaming AI enhancement output.

Uses native AppKit views (NSGlassEffectView + NSTextView) for instant
rendering, dynamic height, and seamless visual transition from the
recording indicator.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Panel dimensions
_PANEL_WIDTH = 504
_MIN_HEIGHT = 96
_MAX_HEIGHT = 432
_CORNER_RADIUS = 12
_PADDING_H = 17
_PADDING_V = 12
_SECTION_SPACING = 10
_ASR_MAX_HEIGHT = 72  # max before ASR section scrolls
_LABEL_HEIGHT = 17
_HINT_GAP = 10
_PROGRESS_HEIGHT = 3.6
_PROGRESS_CORNER = 1.8

# Font
_FONT_SIZE = 15.6

# Key codes
_ESC_KEY_CODE = 53
_RETURN_KEY_CODE = 36

# Delayed close
_CLOSE_DELAY = 3.0
_HOVER_RECHECK_INTERVAL = 0.5
_FADE_OUT_DURATION = 0.3

# Height recalc debounce
_RECALC_DEBOUNCE = 0.05


# Decoration-only view that passes through all mouse/scroll events
try:
    from AppKit import NSView as _NSViewBase

    class _OverlayOutlineView(_NSViewBase):
        """NSView that returns nil from hitTest_ so it never intercepts events."""

        def hitTest_(self, point):
            return None

except Exception:
    _OverlayOutlineView = None


class StreamingOverlayPanel:
    """Non-interactive floating overlay that displays streaming AI enhancement.

    Shows ASR original text at top, streaming enhanced text below.
    Uses native AppKit views with NSGlassEffectView for Liquid Glass
    appearance matching the recording indicator.
    """

    def __init__(self) -> None:
        self._panel: object = None
        self._content_box: object = None
        self._asr_title_label: object = None
        self._asr_text_view: object = None
        self._asr_scroll: object = None
        self._separator: object = None
        self._status_label: object = None
        self._stream_text_view: object = None
        self._stream_scroll: object = None
        self._tap_runner: object = None
        self._cancel_event: threading.Event | None = None
        self._on_cancel: object = None
        self._on_confirm_asr: object = None
        self._loading_timer: object = None
        self._loading_seconds: int = 0
        self._llm_info: str = ""
        self._close_timer: object = None
        self._has_thinking: bool = False
        self._transcribing: bool = False  # animate "Transcribing..." dots
        self._recalc_timer: object = None
        self._hint_label: object = None
        self._complete: bool = False
        self._progress_view: object = None
        # Screen centre anchor (for height growth animation)
        self._center_x: float = 0.0
        self._center_y: float = 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ai_label(self, suffix: str) -> str:
        """Build the AI status label with optional LLM info prefix."""
        base = "\u2728 AI"
        if self._llm_info:
            base += f" ({self._llm_info})"
        if suffix:
            return f"{base}  {suffix}"
        return base

    @staticmethod
    def _make_label(text: str):
        """Create a small, muted section header label."""
        from AppKit import NSColor, NSFont, NSTextField

        label = NSTextField.labelWithString_(text)
        label.setFont_(NSFont.systemFontOfSize_weight_(11.4, 0.23))
        label.setTextColor_(NSColor.tertiaryLabelColor())
        label.setSelectable_(False)
        label.setDrawsBackground_(False)
        label.setBezeled_(False)
        label.setEditable_(False)
        return label

    @staticmethod
    def _make_text_view(width: float, font_size: float = _FONT_SIZE):
        """Create a scrollable NSTextView pair. Returns (scroll_view, text_view)."""
        from AppKit import (
            NSColor,
            NSFont,
            NSScrollView,
            NSTextView,
        )
        from Foundation import NSMakeRect

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 20))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        scroll.setBorderType_(0)  # NSNoBorder

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 20))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setRichText_(True)
        tv.setFont_(NSFont.systemFontOfSize_(font_size))
        tv.setTextColor_(NSColor.labelColor())
        tv.setTextContainerInset_((0, 0))
        # Allow unlimited height, fixed width
        tv.textContainer().setWidthTracksTextView_(True)
        tv.textContainer().setContainerSize_((width, 1e7))
        tv.setHorizontallyResizable_(False)
        tv.setVerticallyResizable_(True)
        # Hide scrollbar visually but keep scrolling
        scroll.setScrollerStyle_(1)  # NSScrollerStyleOverlay

        scroll.setDocumentView_(tv)
        return scroll, tv

    # ------------------------------------------------------------------
    # Show / position
    # ------------------------------------------------------------------

    def show(
        self,
        asr_text: str = "",
        cancel_event: threading.Event | None = None,
        animate_from_frame: object = None,
        stt_info: str = "",
        llm_info: str = "",
        on_cancel: object = None,
        on_confirm_asr: object = None,
    ) -> None:
        """Create and show the overlay panel. Must be called on main thread."""
        try:
            from AppKit import (
                NSColor,
                NSGlassEffectView,
                NSPanel,
                NSScreen,
                NSStatusWindowLevel,
            )
            from Foundation import NSMakeRect

            if self._panel is not None:
                self._do_close()

            self._cancel_event = cancel_event
            self._on_cancel = on_cancel
            self._on_confirm_asr = on_confirm_asr
            self._loading_seconds = 0
            self._llm_info = llm_info
            self._has_thinking = False

            # -- Build labels --
            asr_title_text = "\U0001f3a4 ASR"
            if stt_info:
                asr_title_text += f"  ({stt_info})"

            content_w = _PANEL_WIDTH - _PADDING_H * 2
            self._asr_title_label = self._make_label(asr_title_text)
            self._asr_scroll, self._asr_text_view = self._make_text_view(
                content_w,
            )
            if asr_text:
                self._set_text(self._asr_text_view, asr_text)
                self._transcribing = False
            else:
                self._set_text(
                    self._asr_text_view,
                    "Transcribing",
                    italic=True,
                )
                self._transcribing = True

            # Soft separator (thin semi-transparent view)
            from AppKit import NSView as _NSView

            sep = _NSView.alloc().initWithFrame_(NSMakeRect(0, 0, content_w, 1))
            sep.setWantsLayer_(True)
            sep.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
            sep.layer().setOpacity_(0.4)
            self._separator = sep

            self._status_label = self._make_label(self._ai_label(""))
            self._stream_scroll, self._stream_text_view = self._make_text_view(
                content_w,
            )

            hint_parts = ["ESC cancel"]
            if on_confirm_asr:
                hint_parts.append("\u23ce use original")
            hint_parts.append("\u2318C copy")
            self._hint_label = self._make_label("  \u00b7  ".join(hint_parts))

            progress = _NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, _PROGRESS_HEIGHT))
            progress.setWantsLayer_(True)
            progress.layer().setBackgroundColor_(NSColor.controlAccentColor().CGColor())
            progress.layer().setCornerRadius_(_PROGRESS_CORNER)
            self._progress_view = progress

            # -- Panel --
            init_h = self._compute_height()
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, _PANEL_WIDTH, init_h),
                0,
                2,
                False,
            )
            panel.setLevel_(NSStatusWindowLevel + 1)
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.clearColor())
            panel.setIgnoresMouseEvents_(False)
            panel.setHasShadow_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))

            # -- Liquid Glass background --
            from wenzi.ui_helpers import configure_glass_appearance

            glass = NSGlassEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, _PANEL_WIDTH, init_h))
            glass.setCornerRadius_(_CORNER_RADIUS)
            glass.setWantsLayer_(True)
            glass.layer().setMasksToBounds_(True)
            configure_glass_appearance(glass)
            panel.setContentView_(glass)

            # Container for subviews inside glass
            from AppKit import NSView as _NSView

            box = _NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _PANEL_WIDTH, init_h))
            box.setAutoresizingMask_(0x12)  # flex W+H
            glass.setContentView_(box)
            self._content_box = box

            # Subtle outline for edge definition — restrained for content-heavy panel.
            # Wrapped separately: decoration only, safe to skip on failure.
            try:
                if _OverlayOutlineView is not None:
                    from wenzi.ui_helpers import dynamic_color

                    ol_color = dynamic_color((1.0, 1.0, 1.0, 0.22), (1.0, 1.0, 1.0, 0.10))
                    outline = _OverlayOutlineView.alloc().initWithFrame_(
                        NSMakeRect(0, 0, _PANEL_WIDTH, init_h)
                    )
                    outline.setWantsLayer_(True)
                    outline.layer().setCornerRadius_(_CORNER_RADIUS)
                    outline.layer().setBorderWidth_(0.5)
                    outline.layer().setBorderColor_(ol_color.CGColor())
                    outline.setAutoresizingMask_(0x12)  # flex W+H — tracks panel resize
                    glass.addSubview_(outline)
            except Exception:
                pass

            self._panel = panel

            # -- Add subviews once (repositioned by _layout_subviews) --
            box.addSubview_(self._asr_title_label)
            box.addSubview_(self._asr_scroll)
            box.addSubview_(self._separator)
            box.addSubview_(self._status_label)
            box.addSubview_(self._stream_scroll)
            if self._hint_label is not None:
                box.addSubview_(self._hint_label)
            if self._progress_view is not None:
                box.addSubview_(self._progress_view)

            # -- Position at screen centre --
            screen = NSScreen.mainScreen()
            if screen:
                sf = screen.visibleFrame()
                self._center_x = sf.origin.x + sf.size.width / 2.0
                self._center_y = sf.origin.y + sf.size.height / 2.0

            # Layout subviews, position, and show
            target_frame = self._frame_for_height(init_h)

            if animate_from_frame is not None:
                # Start at recording indicator's position, animate to target
                panel.setAlphaValue_(0.6)
                panel.setFrame_display_(animate_from_frame, False)
                panel.orderFront_(None)
                self._layout_subviews(init_h)  # layout for target size
                from AppKit import NSAnimationContext

                NSAnimationContext.beginGrouping()
                ctx = NSAnimationContext.currentContext()
                ctx.setDuration_(0.25)
                panel.animator().setFrame_display_(target_frame, True)
                panel.animator().setAlphaValue_(1.0)
                NSAnimationContext.endGrouping()
            else:
                self._layout_subviews(init_h)
                panel.setFrame_display_(target_frame, True)
                panel.orderFront_(None)

            self._register_key_tap()
            self._start_loading_timer()
            logger.debug("Streaming overlay shown")
        except Exception:
            logger.error("Failed to show streaming overlay", exc_info=True)

    def _frame_for_height(self, h: float):
        """Return an NSRect centred on screen with the given height."""
        from Foundation import NSMakeRect

        x = self._center_x - _PANEL_WIDTH / 2.0
        y = self._center_y - h / 2.0
        return NSMakeRect(x, y, _PANEL_WIDTH, h)

    def _layout_subviews(self, panel_h: float) -> None:
        """Reposition subviews within the VFX view for a given panel height.

        Subviews are added once in show(); this method only adjusts frames.
        """
        from Foundation import NSMakeRect

        if self._content_box is None:
            return

        cw = _PANEL_WIDTH - _PADDING_H * 2
        y = panel_h - _PADDING_V  # start from top

        y -= _LABEL_HEIGHT
        self._asr_title_label.setFrame_(NSMakeRect(_PADDING_H, y, cw, _LABEL_HEIGHT))

        y -= 2  # small gap
        asr_h = min(self._text_content_height(self._asr_text_view), _ASR_MAX_HEIGHT)
        asr_h = max(asr_h, 16)
        y -= asr_h
        self._asr_scroll.setFrame_(NSMakeRect(_PADDING_H, y, cw, asr_h))

        y -= _SECTION_SPACING
        self._separator.setFrame_(NSMakeRect(_PADDING_H, y, cw, 1))
        y -= _SECTION_SPACING

        y -= _LABEL_HEIGHT
        self._status_label.setFrame_(NSMakeRect(_PADDING_H, y, cw, _LABEL_HEIGHT))

        if self._hint_label is not None:
            hint_y = _PADDING_V
            self._hint_label.setFrame_(NSMakeRect(_PADDING_H, hint_y, cw, _LABEL_HEIGHT))
            bottom_for_stream = hint_y + _LABEL_HEIGHT + _HINT_GAP
        else:
            bottom_for_stream = _PADDING_V

        # Stream text fills remaining space
        y -= 2
        stream_h = max(y - bottom_for_stream, 16)
        self._stream_scroll.setFrame_(NSMakeRect(_PADDING_H, bottom_for_stream, cw, stream_h))

        if self._progress_view is not None:
            pw = self._progress_view.frame().size.width
            self._progress_view.setFrame_(NSMakeRect(0, panel_h - _PROGRESS_HEIGHT, pw, _PROGRESS_HEIGHT))

    def _compute_height(self) -> float:
        """Compute ideal panel height from content."""
        asr_h = min(
            self._text_content_height(self._asr_text_view),
            _ASR_MAX_HEIGHT,
        )
        asr_h = max(asr_h, 16)
        stream_h = self._text_content_height(self._stream_text_view)
        stream_h = max(stream_h, 16)

        total = (
            _PADDING_V
            + _LABEL_HEIGHT
            + 2
            + asr_h
            + _SECTION_SPACING
            + 1
            + _SECTION_SPACING
            + _LABEL_HEIGHT
            + 2
            + stream_h
            + _LABEL_HEIGHT
            + _HINT_GAP
            + _PADDING_V
        )
        return max(_MIN_HEIGHT, min(total, _MAX_HEIGHT))

    def _recalculate_height(self) -> None:
        """Schedule debounced height recalculation."""
        if self._panel is None or self._recalc_timer is not None:
            return
        try:
            from Foundation import NSTimer

            self._recalc_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                _RECALC_DEBOUNCE,
                self,
                b"_doRecalcHeight:",
                None,
                False,
            )
        except Exception:
            self._do_recalculate_height()

    def _doRecalcHeight_(self, timer) -> None:
        self._recalc_timer = None
        try:
            self._do_recalculate_height()
        except Exception:
            logger.error("Recalc height timer error", exc_info=True)

    def _do_recalculate_height(self) -> None:
        """Recompute panel height and animate the change."""
        if self._panel is None:
            return

        new_h = self._compute_height()
        try:
            old_h = float(self._panel.frame().size.height)
        except (TypeError, ValueError):
            old_h = 0.0
        if abs(new_h - old_h) < 2:
            # Still relayout at current height (content may have changed)
            self._layout_subviews(old_h)
            return

        from AppKit import NSAnimationContext

        target = self._frame_for_height(new_h)
        NSAnimationContext.beginGrouping()
        ctx = NSAnimationContext.currentContext()
        ctx.setDuration_(0.12)

        def _after_resize():
            try:
                self._layout_subviews(new_h)
            except Exception:
                logger.error("After-resize completion error", exc_info=True)

        ctx.setCompletionHandler_(_after_resize)
        self._panel.animator().setFrame_display_(target, True)
        NSAnimationContext.endGrouping()

    @staticmethod
    def _text_content_height(text_view) -> float:
        """Measure the used height of an NSTextView's content."""
        if text_view is None:
            return 0
        try:
            lm = text_view.layoutManager()
            tc = text_view.textContainer()
            lm.ensureLayoutForTextContainer_(tc)
            h = lm.usedRectForTextContainer_(tc).size.height
            return float(h)
        except (TypeError, ValueError, AttributeError):
            return 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Text manipulation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _set_text(
        text_view,
        text: str,
        secondary: bool = False,
        italic: bool = False,
    ) -> None:
        """Replace all text in an NSTextView."""
        from AppKit import NSColor, NSFont, NSFontManager
        from Foundation import (
            NSAttributedString,
            NSMutableDictionary,
        )

        attrs = NSMutableDictionary.dictionary()
        if italic:
            attrs["NSFont"] = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
                NSFont.systemFontOfSize_(_FONT_SIZE),
                1,  # NSItalicFontMask
            )
        else:
            attrs["NSFont"] = NSFont.systemFontOfSize_(_FONT_SIZE)
        if secondary:
            attrs["NSColor"] = NSColor.secondaryLabelColor()
        else:
            attrs["NSColor"] = NSColor.labelColor()

        astr = NSAttributedString.alloc().initWithString_attributes_(
            text,
            attrs,
        )
        text_view.textStorage().setAttributedString_(astr)

    @staticmethod
    def _append_attributed(text_view, text: str, attrs: dict) -> None:
        """Append attributed text and auto-scroll to bottom."""
        from Foundation import NSAttributedString, NSMakeRange

        astr = NSAttributedString.alloc().initWithString_attributes_(
            text,
            attrs,
        )
        ts = text_view.textStorage()
        ts.appendAttributedString_(astr)
        text_view.scrollRangeToVisible_(NSMakeRange(ts.length(), 0))

    # ------------------------------------------------------------------
    # Key tap (ESC / Enter) — CGEventTap swallows the keys
    # ------------------------------------------------------------------

    def _register_key_tap(self) -> None:
        if self._tap_runner is not None:
            return
        from wenzi import _cgeventtap as cg

        self._tap_runner = cg.CGEventTapRunner()
        mask = cg.CGEventMaskBit(cg.kCGEventKeyDown)
        self._tap_runner.start(mask, self._key_tap_callback)

    def _key_tap_callback(self, proxy, event_type, event, refcon):
        from wenzi import _cgeventtap as cg

        try:
            if event_type == cg.kCGEventTapDisabledByTimeout:
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, True)
                return event

            if event_type != cg.kCGEventKeyDown:
                return event

            keycode = cg.CGEventGetIntegerValueField(
                event,
                cg.kCGKeyboardEventKeycode,
            )

            if keycode == _ESC_KEY_CODE:
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, False)
                if self._cancel_event is not None:
                    self._cancel_event.set()
                from PyObjCTools import AppHelper

                on_cancel = self._on_cancel
                if on_cancel is not None:
                    AppHelper.callAfter(on_cancel)
                AppHelper.callAfter(self._do_close)
                logger.info("Streaming cancelled via ESC key")
                return None

            flags = cg.CGEventGetFlags(event)

            if keycode == 8 and (flags & cg.kCGEventFlagMaskCommand):  # Cmd+C
                from PyObjCTools import AppHelper

                AppHelper.callAfter(self._copy_stream_text)
                return None

            if keycode == _RETURN_KEY_CODE and self._on_confirm_asr is not None:
                if self._tap_runner is not None and self._tap_runner.tap is not None:
                    cg.CGEventTapEnable(self._tap_runner.tap, False)
                from PyObjCTools import AppHelper

                on_confirm = self._on_confirm_asr
                AppHelper.callAfter(on_confirm)
                AppHelper.callAfter(self._do_close)
                logger.info("ASR confirmed via Enter key")
                return None

            if self._complete:
                from PyObjCTools import AppHelper

                AppHelper.callAfter(self._do_close)
                return event  # close panel but let the key through

        except Exception:
            logger.warning("Key tap callback error", exc_info=True)
        return event

    def _copy_stream_text(self) -> None:
        """Copy stream text to clipboard with visual feedback."""
        if self._stream_text_view is None:
            return
        text = self._stream_text_view.textStorage().string()
        if not text.strip():
            return
        from wenzi.input import set_clipboard_text

        set_clipboard_text(text)
        if self._status_label is not None:
            self._status_label.setStringValue_("\u2713 Copied to clipboard")
        logger.debug("Stream text copied to clipboard")

    def _remove_key_tap(self) -> None:
        if self._tap_runner is not None:
            self._tap_runner.stop()
            self._tap_runner = None

    # ------------------------------------------------------------------
    # Loading timer
    # ------------------------------------------------------------------

    def _start_loading_timer(self) -> None:
        self._stop_loading_timer()
        self._loading_seconds = 0
        self._tick_count = 0
        try:
            from Foundation import NSTimer

            self._loading_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.5,
                self,
                b"tickLoadingTimer:",
                None,
                True,
            )
        except Exception:
            logger.error("Failed to start loading timer", exc_info=True)

    def _stop_loading_timer(self) -> None:
        if self._loading_timer is not None:
            try:
                self._loading_timer.invalidate()
            except Exception:
                pass
            self._loading_timer = None

    def tickLoadingTimer_(self, timer) -> None:
        try:
            self._tick_count += 1

            # Animate "Transcribing..." dots (cycles every 3 ticks)
            if self._transcribing and self._asr_text_view is not None:
                dots = "." * ((self._tick_count % 3) + 1)
                self._set_text(
                    self._asr_text_view,
                    f"Transcribing{dots}",
                    italic=True,
                )

            # Update AI status every second (every 2 ticks at 0.5s interval)
            if self._tick_count % 2 == 0:
                self._loading_seconds += 1
                if self._status_label is not None:
                    self._status_label.setStringValue_(self._ai_label(f"\u23f3 {self._loading_seconds}s"))
        except Exception:
            logger.error("Loading timer tick error", exc_info=True)

    # ------------------------------------------------------------------
    # Streaming text updates (all thread-safe via callAfter)
    # ------------------------------------------------------------------

    def append_text(self, chunk: str, completion_tokens: int = 0) -> None:
        """Append content text to the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            if self._stream_text_view is None:
                return

            from AppKit import NSColor, NSFont

            attrs = {
                "NSFont": NSFont.systemFontOfSize_(_FONT_SIZE),
                "NSColor": NSColor.labelColor(),
            }

            if self._has_thinking:
                from Foundation import NSAttributedString, NSMakeRange

                astr = NSAttributedString.alloc().initWithString_attributes_(chunk, attrs)
                ts = self._stream_text_view.textStorage()
                ts.replaceCharactersInRange_withAttributedString_(NSMakeRange(0, ts.length()), astr)
                self._has_thinking = False
            else:
                self._append_attributed(self._stream_text_view, chunk, attrs)

            if completion_tokens and self._status_label:
                self._status_label.setStringValue_(self._ai_label(f"Chars: \u2193{completion_tokens}"))

            self._recalculate_height()

        AppHelper.callAfter(_append)

    def append_thinking_text(self, chunk: str, thinking_tokens: int = 0) -> None:
        """Append thinking/reasoning text in italic. Thread-safe."""
        from PyObjCTools import AppHelper

        def _append():
            self._stop_loading_timer()
            if self._stream_text_view is None:
                return

            from AppKit import NSColor, NSFont, NSFontManager

            self._has_thinking = True
            attrs = {
                "NSFont": NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
                    NSFont.systemFontOfSize_(_FONT_SIZE),
                    1,  # NSItalicFontMask
                ),
                "NSColor": NSColor.tertiaryLabelColor(),
            }
            self._append_attributed(self._stream_text_view, chunk, attrs)

            if thinking_tokens and self._status_label:
                self._status_label.setStringValue_(self._ai_label(f"\u25b6 Thinking: {thinking_tokens} chars"))

            self._recalculate_height()

        AppHelper.callAfter(_append)

    def set_status(self, text: str) -> None:
        """Update the status label. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._status_label is not None:
                self._status_label.setStringValue_(text)

        AppHelper.callAfter(_update)

    def set_asr_text(self, text: str) -> None:
        """Update the ASR text after transcription completes. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._asr_text_view is None:
                return
            self._transcribing = False
            self._set_text(self._asr_text_view, text)
            self._recalculate_height()

        AppHelper.callAfter(_update)

    def set_cancel_event(self, cancel_event: threading.Event) -> None:
        """Attach a cancel event and register ESC monitor. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            self._cancel_event = cancel_event
            if self._tap_runner is None:
                self._register_key_tap()

        AppHelper.callAfter(_update)

    def set_complete(self, usage: dict | None = None) -> None:
        """Mark enhancement complete, show final token usage. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            self._stop_loading_timer()
            self._complete = True
            if self._status_label is None:
                return

            if usage and usage.get("total_tokens"):
                prompt = usage.get("prompt_tokens", 0)
                completion = usage.get("completion_tokens", 0)
                total = usage["total_tokens"]
                cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                if cached:
                    up = f"\u2191{cached}+{prompt - cached}"
                else:
                    up = f"\u2191{prompt}"
                label = f"{self._ai_label('')}  Tokens: {total} ({up} \u2193{completion})"
            else:
                label = self._ai_label("")
            self._status_label.setStringValue_(label)

        AppHelper.callAfter(_update)

    def set_progress(self, step: int, total: int) -> None:
        """Update chain progress bar. Thread-safe."""
        from PyObjCTools import AppHelper

        def _update():
            if self._progress_view is None or self._panel is None or total <= 0:
                return
            from Foundation import NSMakeRect

            w = _PANEL_WIDTH * (step / total)
            try:
                panel_h = float(self._panel.frame().size.height)
            except (TypeError, ValueError):
                return
            from AppKit import NSAnimationContext

            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(0.2)
            self._progress_view.animator().setFrame_(NSMakeRect(0, panel_h - _PROGRESS_HEIGHT, w, _PROGRESS_HEIGHT))
            NSAnimationContext.endGrouping()

        AppHelper.callAfter(_update)

    def clear_text(self) -> None:
        """Clear the streaming text view. Thread-safe."""
        from PyObjCTools import AppHelper

        def _clear():
            if self._stream_text_view is None:
                return
            from Foundation import NSAttributedString

            self._stream_text_view.textStorage().setAttributedString_(NSAttributedString.alloc().init())
            self._has_thinking = False
            self._recalculate_height()

        AppHelper.callAfter(_clear)

    # ------------------------------------------------------------------
    # Delayed close with hover detection
    # ------------------------------------------------------------------

    def close_with_delay(self, delay: float = _CLOSE_DELAY) -> None:
        """Close after *delay* seconds, postponed if mouse is hovering. Thread-safe."""
        from PyObjCTools import AppHelper

        def _schedule():
            self._stop_close_timer()
            try:
                from Foundation import NSTimer

                self._close_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    delay,
                    self,
                    b"_delayedCloseCheck:",
                    None,
                    False,
                )
            except Exception:
                logger.error("Failed to schedule delayed close", exc_info=True)

        AppHelper.callAfter(_schedule)

    def _delayedCloseCheck_(self, timer) -> None:
        try:
            self._close_timer = None
            if self._panel is None:
                return

            if self._is_mouse_over_panel():
                try:
                    from Foundation import NSTimer

                    self._close_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        _HOVER_RECHECK_INTERVAL,
                        self,
                        b"_delayedCloseCheck:",
                        None,
                        False,
                    )
                except Exception:
                    self._do_close()
            else:
                self._fade_out_and_close()
        except Exception:
            logger.error("Delayed close check error", exc_info=True)

    def _is_mouse_over_panel(self) -> bool:
        try:
            from AppKit import NSEvent
            from Foundation import NSPointInRect

            mouse_loc = NSEvent.mouseLocation()
            return bool(NSPointInRect(mouse_loc, self._panel.frame()))
        except Exception:
            return False

    def _fade_out_and_close(self) -> None:
        if self._panel is None:
            return
        try:
            from AppKit import NSAnimationContext

            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(_FADE_OUT_DURATION)

            def _safe_close():
                try:
                    self._do_close()
                except Exception:
                    logger.error("Fade-out close error", exc_info=True)

            ctx.setCompletionHandler_(_safe_close)
            self._panel.animator().setAlphaValue_(0.0)
            NSAnimationContext.endGrouping()
        except Exception:
            self._do_close()

    def _stop_close_timer(self) -> None:
        if self._close_timer is not None:
            try:
                self._close_timer.invalidate()
            except Exception:
                pass
            self._close_timer = None

    def _do_close(self) -> None:
        self._stop_loading_timer()
        self._stop_close_timer()
        self._remove_key_tap()
        self._cancel_event = None
        self._on_cancel = None
        self._on_confirm_asr = None
        self._has_thinking = False
        self._transcribing = False
        self._complete = False

        if self._recalc_timer:
            try:
                self._recalc_timer.invalidate()
            except Exception:
                pass
            self._recalc_timer = None

        if self._panel is not None:
            from wenzi.ui_helpers import release_panel_surfaces

            release_panel_surfaces(self._panel)
            self._panel.orderOut_(None)
            self._panel = None

        self._content_box = None
        self._asr_title_label = None
        self._asr_text_view = None
        self._asr_scroll = None
        self._separator = None
        self._status_label = None
        self._stream_text_view = None
        self._stream_scroll = None
        self._hint_label = None
        self._progress_view = None
        logger.debug("Streaming overlay closed")

    def close_now(self) -> None:
        """Close and clean up immediately. Must be on main thread."""
        self._do_close()

    def close(self) -> None:
        """Close and clean up immediately. Thread-safe."""
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._do_close)
