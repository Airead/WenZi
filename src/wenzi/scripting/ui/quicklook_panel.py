"""Quick Look preview panel using the system QLPreviewPanel.

Uses the macOS shared QLPreviewPanel singleton with a data-source
protocol instead of the lower-level QLPreviewView.  QLPreviewPanel is
designed by Apple for rapid file navigation (it's what Finder uses)
and safely handles item switching without the KVO crashes that plague
QLPreviewView.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class QuickLookPanel:
    """System Quick Look preview panel wrapper.

    Uses ``QLPreviewPanel.sharedPreviewPanel()`` with a data-source
    object that vends a single NSURL.  Switching files simply updates
    the URL and calls ``reloadData()`` — the panel handles the rest.

    Parameters:
        on_resign_key: Called when the panel loses key window status.
        on_shift_toggle: Called when Shift is tapped while the panel
            has focus.
    """

    def __init__(self, on_resign_key=None, on_shift_toggle=None) -> None:
        self._native_panel = None  # QLPreviewPanel singleton ref
        self._data_source = None
        self._delegate = None
        self._current_path: Optional[str] = None
        self._on_resign_key = on_resign_key
        self._on_shift_toggle = on_shift_toggle
        self._key_monitor = None
        self._shift_alone: bool = False
        self._shift_down_time: float = 0.0
        self._configured: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        return self._native_panel is not None and self._native_panel.isVisible()

    @property
    def is_key_window(self) -> bool:
        """Return True if the QL panel is currently the key window."""
        if self._native_panel is None:
            return False
        try:
            from AppKit import NSApp

            return NSApp.keyWindow() == self._native_panel
        except Exception:
            return False

    def show(self, path: str, anchor_panel) -> None:
        """Show the Quick Look panel for *path*."""
        if not path or not os.path.exists(path):
            return

        if not self._configured:
            self._configure_panel()

        self._update_preview(path)

        if self._native_panel is not None:
            self._native_panel.orderFront_(None)

    def update(self, path: str) -> None:
        """Update the preview to a different file."""
        if not self._configured or not path or not os.path.exists(path):
            return
        self._update_preview(path)

    def close(self) -> None:
        """Hide the panel and release our data source / delegate."""
        self._remove_key_monitor()
        if self._native_panel is not None:
            self._native_panel.setDelegate_(None)
            self._native_panel.setDataSource_(None)
            self._native_panel.orderOut_(None)
        if self._delegate is not None:
            self._delegate._panel_ref = None
        if self._data_source is not None:
            self._data_source._url = None
        self._native_panel = None
        self._data_source = None
        self._delegate = None
        self._current_path = None
        self._configured = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_preview(self, path: str) -> None:
        """Update the data-source URL and tell the panel to reload."""
        if path == self._current_path:
            return
        self._current_path = path
        try:
            from Foundation import NSURL

            url = NSURL.fileURLWithPath_(path)
            if self._data_source is not None:
                self._data_source._url = url
            if self._native_panel is not None:
                self._native_panel.reloadData()
        except Exception:
            logger.debug("Failed to update preview: %s", path, exc_info=True)

    def _configure_panel(self) -> None:
        """Obtain the shared QLPreviewPanel and configure it."""
        try:
            from AppKit import NSStatusWindowLevel
            from Quartz import QLPreviewPanel

            panel = QLPreviewPanel.sharedPreviewPanel()

            # Data source
            ds_cls = _get_ql_data_source_class()
            data_source = ds_cls.alloc().init()
            panel.setDataSource_(data_source)

            # Delegate (resign-key forwarding)
            delegate_cls = _get_ql_delegate_class()
            delegate = delegate_cls.alloc().init()
            delegate._panel_ref = self
            panel.setDelegate_(delegate)

            # Panel properties
            panel.setLevel_(NSStatusWindowLevel + 1)
            panel.setHidesOnDeactivate_(False)
            panel.setFloatingPanel_(True)
            panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # canJoinAllSpaces | stationary | fullScreenAuxiliary

            self._native_panel = panel
            self._data_source = data_source
            self._delegate = delegate
            self._configured = True
            self._install_key_monitor()
        except Exception:
            logger.exception("Failed to configure Quick Look panel")
            self._configured = False

    def _install_key_monitor(self) -> None:
        """Install a local event monitor to detect Shift-alone taps.

        Only fires when the QL panel is the key window, so it does not
        interfere with the chooser's own Shift handling in WKWebView.
        """
        if self._key_monitor is not None:
            return
        try:
            import time

            from AppKit import NSApp, NSEvent, NSFlagsChangedMask

            _SHIFT_TIMEOUT = 0.4  # seconds

            def _handler(event):
                # Only handle when QL panel is key
                if NSApp.keyWindow() != self._native_panel:
                    return event

                flags = event.modifierFlags()
                shift_pressed = bool(flags & (1 << 17))  # NSEventModifierFlagShift

                if shift_pressed:
                    # Shift went down
                    self._shift_alone = True
                    self._shift_down_time = time.monotonic()
                else:
                    # Shift went up — check for solo tap
                    if (
                        self._shift_alone
                        and (time.monotonic() - self._shift_down_time) < _SHIFT_TIMEOUT
                    ):
                        self._shift_alone = False
                        if self._on_shift_toggle is not None:
                            self._on_shift_toggle()
                            return None  # consume the event
                    self._shift_alone = False

                return event

            self._key_monitor = (
                NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    NSFlagsChangedMask, _handler,
                )
            )
        except Exception:
            logger.debug("Failed to install key monitor", exc_info=True)

    def _remove_key_monitor(self) -> None:
        """Remove the local event monitor."""
        if self._key_monitor is not None:
            try:
                from AppKit import NSEvent

                NSEvent.removeMonitor_(self._key_monitor)
            except Exception:
                pass
            self._key_monitor = None


# ---------------------------------------------------------------------------
# QLPreviewPanelDataSource (lazy-created, unique ObjC class name)
# ---------------------------------------------------------------------------
_QLDataSource = None


def _get_ql_data_source_class():
    """Return an NSObject subclass implementing QLPreviewPanelDataSource."""
    global _QLDataSource
    if _QLDataSource is not None:
        return _QLDataSource

    import objc
    from Foundation import NSObject

    import Quartz  # noqa: F401 — ensure protocol is loaded

    QLPreviewPanelDataSource = objc.protocolNamed("QLPreviewPanelDataSource")

    class QuickLookDataSource(NSObject, protocols=[QLPreviewPanelDataSource]):
        _url = None  # NSURL for the current file

        def numberOfPreviewItemsInPreviewPanel_(self, panel):
            return 1 if self._url is not None else 0

        def previewPanel_previewItemAtIndex_(self, panel, index):
            return self._url  # NSURL conforms to QLPreviewItem

    _QLDataSource = QuickLookDataSource
    return _QLDataSource


# ---------------------------------------------------------------------------
# Panel delegate (lazy-created, unique ObjC class name)
# ---------------------------------------------------------------------------
_QLDelegate = None


def _get_ql_delegate_class():
    """Return an NSObject subclass that forwards resign-key to the panel."""
    global _QLDelegate
    if _QLDelegate is not None:
        return _QLDelegate

    from Foundation import NSObject

    class QuickLookPanelDelegate(NSObject):
        _panel_ref = None

        def windowDidResignKey_(self, notification):
            if self._panel_ref is not None:
                cb = self._panel_ref._on_resign_key
                if cb is not None:
                    cb()

    _QLDelegate = QuickLookPanelDelegate
    return _QLDelegate
