# Project: WenZi (闻字)

## Tech Stack
- Language: Python 3.12
- Platform: macOS 26+ (Tahoe) statusbar app
- UI: PyObjC (AppKit) + WKWebView (Fabric.js for annotation)
- Audio: FunASR, MLX Whisper, Apple Speech, Whisper API
- AI: OpenAI-compatible chat completions for text enhancement
- Packaging: PyInstaller
- Linting: ruff
- Testing: pytest

## Architecture
- Statusbar (accessory) app — no foreground window by default
- Plugin/scripting engine with `wz` namespace API
- CGEventTap via ctypes (not PyObjC) to avoid memory leaks
- NSGlassEffectView (Liquid Glass) for blur panels, never NSVisualEffectView
- All UI must support dark mode with system semantic colors

## Key Conventions
- Tests must never use real user data paths — always use tmp_path
- AES-256-GCM encrypted keychain for plugin secrets
- IOSurface memory management: shrink panels to 1×1 before orderOut_
