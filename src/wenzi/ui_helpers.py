"""UI helper functions for topmost dialogs and input windows.

These were originally static methods on WenZiApp. They are used throughout
the app to show modal dialogs from a statusbar (accessory) process.
"""

from __future__ import annotations

import logging
import threading

from .statusbar import InputWindow

logger = logging.getLogger(__name__)


def release_panel_surfaces(panel) -> None:
    """Deactivate NSVisualEffectView(s) and shrink panel to release IOSurface memory.

    Call before or after ``orderOut_`` when hiding any panel that uses
    ``NSVisualEffectView`` with behind-window blending.  See CLAUDE.md
    "NSVisualEffectView Memory Management" for why this is needed.

    Safe to call on any NSPanel/NSWindow — no-ops gracefully if no VFX view.
    """
    if panel is None:
        return
    # Deactivate all NSVisualEffectViews (content view itself or direct subviews)
    try:
        from AppKit import NSVisualEffectView

        cv = panel.contentView()
        if isinstance(cv, NSVisualEffectView):
            cv.setState_(0)  # NSVisualEffectStateInactive
        if cv is not None:
            for sv in cv.subviews():
                if isinstance(sv, NSVisualEffectView):
                    sv.setState_(0)
    except Exception:
        pass
    # Shrink to 1×1 to force Core Animation to release the CA Whippet
    # Drawable backing store (RGBA half-float IOSurface ~72 MB at retina).
    try:
        from Foundation import NSMakeRect

        f = panel.frame()
        panel.setFrame_display_(NSMakeRect(f.origin.x, f.origin.y, 1, 1), False)
    except Exception:
        pass


def activate_for_dialog() -> None:
    """Set activation policy so modal dialogs can show from non-bundled process.

    Safe to call from any thread.
    """
    def _do():
        from AppKit import NSApp
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        NSApp.activateIgnoringOtherApps_(True)

    if threading.current_thread() is threading.main_thread():
        _do()
    else:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_do)


def restore_accessory() -> None:
    """Restore accessory activation policy (statusbar-only).

    Safe to call from any thread.
    """
    def _do():
        from AppKit import NSApp
        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    if threading.current_thread() is threading.main_thread():
        _do()
    else:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_do)


def get_frontmost_app():
    """Return the currently frontmost application (NSRunningApplication).

    Returns None if the frontmost app cannot be determined.
    """
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception as exc:
        logger.debug("Failed to get frontmost app: %s", exc)
        return None


def reactivate_app(running_app) -> None:
    """Reactivate a previously saved NSRunningApplication without raising all windows.

    Uses ``activateWithOptions_`` with only ``NSApplicationActivateIgnoringOtherApps``
    (value 2) and deliberately omits ``NSApplicationActivateAllWindows`` (value 1)
    so that only the previously focused window regains focus — other windows of the
    same application are not brought to the front.

    Safe to call from any thread.
    """
    if running_app is None:
        return

    def _do():
        try:
            # NSApplicationActivateIgnoringOtherApps = 1 << 1 = 2
            running_app.activateWithOptions_(2)
        except Exception as exc:
            logger.debug("Failed to reactivate app: %s", exc)

    if threading.current_thread() is threading.main_thread():
        _do()
    else:
        from PyObjCTools import AppHelper
        AppHelper.callAfter(_do)


def topmost_alert(title=None, message="", ok=None, cancel=None):
    """Show an NSAlert at NSStatusWindowLevel so it stays on top.

    Safe to call from any thread — dispatches to main thread if needed.
    """
    from PyObjCTools import AppHelper

    result_holder = {"value": 0}
    done_event = threading.Event()

    def _show():
        from AppKit import NSAlert, NSStatusWindowLevel

        activate_for_dialog()

        alert = NSAlert.alloc().init()
        if title is not None:
            alert.setMessageText_(str(title))
        if message:
            alert.setInformativeText_(str(message))
        alert.addButtonWithTitle_(ok or "OK")
        if cancel:
            cancel_text = cancel if isinstance(cancel, str) else "Cancel"
            alert.addButtonWithTitle_(cancel_text)
        alert.setAlertStyle_(0)  # informational
        alert.window().setLevel_(NSStatusWindowLevel)
        alert.window().setFloatingPanel_(True)
        alert.window().setHidesOnDeactivate_(False)

        # NSAlertFirstButtonReturn = 1000, NSAlertSecondButtonReturn = 1001
        result = alert.runModal()
        result_holder["value"] = 1 if result == 1000 else 0
        done_event.set()

    if threading.current_thread() is threading.main_thread():
        _show()
    else:
        AppHelper.callAfter(_show)
        done_event.wait()

    return result_holder["value"]


def run_window(title: str, message: str, default_text: str = "",
               ok: str = "OK", cancel: str = "Cancel",
               dimensions: tuple = (320, 22), secure: bool = False):
    """Run an InputWindow with proper app activation.

    Safe to call from any thread — dispatches to main thread if needed.
    Returns Response or None on cancel.
    """
    from PyObjCTools import AppHelper

    result_holder = {"resp": None}
    done_event = threading.Event()

    def _show():
        from AppKit import NSStatusWindowLevel

        activate_for_dialog()
        w = InputWindow(
            title=title, message=message, default_text=default_text,
            ok=ok, cancel=cancel, dimensions=dimensions, secure=secure,
        )
        w.alert.window().setLevel_(NSStatusWindowLevel)
        w.alert.window().setFloatingPanel_(True)
        w.alert.window().setHidesOnDeactivate_(False)
        resp = w.run()
        result_holder["resp"] = resp if resp.clicked == 1 else None
        done_event.set()

    if threading.current_thread() is threading.main_thread():
        _show()
    else:
        AppHelper.callAfter(_show)
        done_event.wait()

    return result_holder["resp"]


def run_multiline_window(title: str, message: str, default_text: str = "",
                         ok: str = "OK", cancel: str = "Cancel",
                         dimensions: tuple = (380, 180)):
    """Show a floating NSPanel with a multiline NSTextView (Enter = newline).

    Must be called from a background thread.  The panel is created on the
    main thread via ``callAfter`` and the caller blocks on a
    ``threading.Event`` — the same pattern used by ResultPreviewPanel so
    that ``setFloatingPanel_(True)`` reliably keeps the window on top.

    Returns a Response-like object with .clicked and .text, or None on cancel.
    """
    from PyObjCTools import AppHelper

    result_holder = {"clicked": 0, "text": ""}
    done_event = threading.Event()

    # Store panel ref at method level to prevent garbage collection
    panel_holder = [None]

    def _show():
        try:
            from AppKit import (
                NSApp,
                NSBackingStoreBuffered,
                NSBezelBorder,
                NSButton,
                NSClosableWindowMask,
                NSFont,
                NSPanel,
                NSScrollView,
                NSStatusWindowLevel,
                NSTextField,
                NSTextView,
                NSTitledWindowMask,
            )
            from Foundation import NSMakeRect

            padding = 12
            btn_h = 32
            btn_w = 90
            line_count = max(message.count("\n") + 1, 1)
            label_h = 16 * line_count
            width, height = dimensions
            panel_w = width + 2 * padding
            panel_h = padding + btn_h + padding + height + padding + label_h + padding

            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, panel_w, panel_h),
                NSTitledWindowMask | NSClosableWindowMask,
                NSBackingStoreBuffered,
                False,
            )
            panel.setTitle_(title)
            panel.setLevel_(NSStatusWindowLevel)
            panel.setFloatingPanel_(True)
            panel.setHidesOnDeactivate_(False)
            panel.center()

            content = panel.contentView()
            y = padding

            # -- helper to close panel and signal the waiting thread ------
            text_view_holder = [None]

            def _finish(clicked):
                tv = text_view_holder[0]
                text_val = tv.string() if tv is not None else ""
                result_holder["clicked"] = clicked
                result_holder["text"] = text_val
                panel.setDelegate_(None)
                panel.orderOut_(None)
                restore_accessory()
                done_event.set()

            # Action target for OK / Cancel / Close
            from .app import _get_multiline_panel_target_class
            target_cls = _get_multiline_panel_target_class()
            btn_target = target_cls.alloc().init()
            btn_target._finish_callback = _finish

            # Buttons row (right-aligned)
            cancel_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(panel_w - padding - btn_w, y, btn_w, btn_h)
            )
            cancel_btn.setTitle_(cancel)
            cancel_btn.setBezelStyle_(1)
            cancel_btn.setKeyEquivalent_("\x1b")  # ESC
            cancel_btn.setTarget_(btn_target)
            cancel_btn.setAction_(b"cancelClicked:")
            content.addSubview_(cancel_btn)

            ok_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(panel_w - padding - 2 * btn_w - 8, y, btn_w, btn_h)
            )
            ok_btn.setTitle_(ok)
            ok_btn.setBezelStyle_(1)
            ok_btn.setKeyEquivalent_("")
            ok_btn.setTarget_(btn_target)
            ok_btn.setAction_(b"okClicked:")
            content.addSubview_(ok_btn)

            y += btn_h + padding

            # Multiline text view
            scroll_view = NSScrollView.alloc().initWithFrame_(
                NSMakeRect(padding, y, width, height)
            )
            scroll_view.setHasVerticalScroller_(True)
            scroll_view.setBorderType_(NSBezelBorder)

            text_view = NSTextView.alloc().initWithFrame_(
                NSMakeRect(0, 0, width, height)
            )
            text_view.setMinSize_(NSMakeRect(0, 0, width, 0).size)
            text_view.setMaxSize_(NSMakeRect(0, 0, 1e7, 1e7).size)
            text_view.setVerticallyResizable_(True)
            text_view.setHorizontallyResizable_(False)
            text_view.textContainer().setWidthTracksTextView_(True)
            text_view.setFont_(NSFont.userFixedPitchFontOfSize_(12.0))
            text_view.setString_(default_text)
            scroll_view.setDocumentView_(text_view)
            content.addSubview_(scroll_view)
            text_view_holder[0] = text_view

            y += height + padding

            # Message label
            msg_label = NSTextField.labelWithString_(message)
            msg_label.setFrame_(NSMakeRect(padding, y, width, label_h))
            msg_label.setFont_(NSFont.systemFontOfSize_(12))
            content.addSubview_(msg_label)

            # Handle close button (X) as cancel
            panel.setDelegate_(btn_target)

            # Keep refs alive until panel is dismissed
            panel_holder[0] = (panel, btn_target)

            activate_for_dialog()
            panel.makeKeyAndOrderFront_(None)
            panel.makeFirstResponder_(text_view)
            NSApp.activateIgnoringOtherApps_(True)
        except Exception as e:
            logger.error("run_multiline_window _show failed: %s", e, exc_info=True)
            done_event.set()

    AppHelper.callAfter(_show)
    done_event.wait()

    if result_holder["clicked"] != 1:
        return None

    class _Response:
        pass

    resp = _Response()
    resp.clicked = 1
    resp.text = result_holder["text"]
    return resp
