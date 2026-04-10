# Recording Session Keyboard Shortcuts

Keyboard shortcuts available during voice recording and streaming enhancement.

## Recording Phase (Indicator Visible)

All shortcuts require **holding the trigger hotkey** (default: `fn`) while pressing the action key.

| Key | Action | Description |
|-----|--------|-------------|
| Release trigger | Stop recording | Ends audio capture, transitions to transcription/enhancement |
| Space | Cancel | Discard recording, return to idle |
| Cmd | Restart | Discard current recording, start a new one immediately |
| Z | Preview history | Stop recording, open history preview panel |
| Left / Up arrow | Previous mode | Switch to the previous AI enhancement mode |
| Right / Down arrow | Next mode | Switch to the next AI enhancement mode |

> **Note:** When `fn` is the trigger hotkey, macOS converts fn+Arrow into Home/End/PageUp/PageDown. These keys are also recognized as mode navigation shortcuts, so fn+Arrow works as expected.

### Configuration

The cancel, restart, and history keys are configurable in `config.json` under `feedback`:

```json
{
  "feedback": {
    "cancel_key": "space",
    "restart_key": "cmd",
    "preview_history_key": "z"
  }
}
```

## Streaming Overlay Phase (Enhancement in Progress)

These keys are captured by the overlay's own CGEventTap and **swallowed** (not passed to the focused application).

| Key | Action | Description |
|-----|--------|-------------|
| ESC | Cancel enhancement | Stop the AI enhancement, close overlay, return to idle |
| Enter | Confirm ASR | Skip enhancement, output the raw transcription text directly (only active in direct mode with background STT) |
| Left / Up arrow | Previous mode | Switch to the previous AI enhancement mode (handled by main hotkey listener) |
| Right / Down arrow | Next mode | Switch to the next AI enhancement mode (handled by main hotkey listener) |

## Implementation References

- Trigger hotkey listener: `src/wenzi/hotkey.py` (`MultiHotkeyListener`)
- Action dispatch: `src/wenzi/controllers/recording_flow.py` (`Action` enum, `_handle_inline_action`)
- Streaming overlay key tap: `src/wenzi/ui/streaming_overlay.py` (`_key_tap_callback`)
