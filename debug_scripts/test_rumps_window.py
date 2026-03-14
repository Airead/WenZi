"""Minimal test to debug statusbar window/alert in menu callbacks."""

from AppKit import NSAlert, NSApp
from voicetext.statusbar import (
    InputWindow,
    StatusBarApp,
    StatusMenuItem,
)


class TestApp(StatusBarApp):
    def __init__(self):
        super().__init__("Test", title="T")

        self.menu = [
            StatusMenuItem("Alert (no fix)", callback=self._on_alert_raw),
            StatusMenuItem("Alert (with policy)", callback=self._on_alert_fix),
            StatusMenuItem("Window (with policy)", callback=self._on_window_fix),
        ]

    def _activate_for_dialog(self):
        """Set activation policy so modal dialogs can show."""
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        NSApp.activateIgnoringOtherApps_(True)

    def _restore_accessory(self):
        """Restore accessory policy (statusbar-only)."""
        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    def _on_alert_raw(self, _):
        print("[raw] callback fired", flush=True)
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Test")
        alert.setInformativeText_("No fix applied")
        result = alert.runModal()
        print(f"[raw] result: {result}", flush=True)

    def _on_alert_fix(self, _):
        print("[fix] callback fired", flush=True)
        self._activate_for_dialog()
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Test")
        alert.setInformativeText_("With activation policy fix")
        result = alert.runModal()
        self._restore_accessory()
        print(f"[fix] result: {result}", flush=True)

    def _on_window_fix(self, _):
        print("[window] callback fired", flush=True)
        self._activate_for_dialog()
        w = InputWindow("Enter text:", "Test Window", "hello", ok="OK", cancel="Cancel")
        resp = w.run()
        self._restore_accessory()
        print(f"[window] clicked={resp.clicked}, text={resp.text}", flush=True)


if __name__ == "__main__":
    TestApp().run()
