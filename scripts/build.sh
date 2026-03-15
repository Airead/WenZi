#!/usr/bin/env bash
# Build VoiceText.app with PyInstaller and re-sign for macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"
APP_PATH="$DIST_DIR/VoiceText.app"
# Resolve signing identity: env var > auto-detect fingerprint > ad-hoc
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    SIGN_IDENTITY="$CODESIGN_IDENTITY"
    SIGN_MODE="identity"
else
    SIGN_IDENTITY=$(security find-identity -p codesigning \
        | grep -m1 ')' | awk '{print $2}')
    if [ -n "$SIGN_IDENTITY" ]; then
        SIGN_MODE="identity"
    else
        echo "WARNING: No codesigning identity found in keychain, falling back to ad-hoc signing."
        SIGN_MODE="adhoc"
    fi
fi

cd "$PROJECT_DIR"

echo "==> Cleaning previous build..."
rm -rf build dist
find "$PROJECT_DIR/src" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "==> Running PyInstaller..."
uv run pyinstaller VoiceText.spec --clean --noconfirm

if [ "$SIGN_MODE" = "identity" ]; then
    echo "==> Re-signing app bundle (identity: $SIGN_IDENTITY)..."
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_PATH"
else
    echo "==> Re-signing app bundle (ad-hoc)..."
    codesign --force --deep --sign - "$APP_PATH"
fi

echo "==> Verifying signature..."
codesign --verify --verbose "$APP_PATH"

APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
echo ""
echo "==> Build complete: $APP_PATH ($APP_SIZE)"
echo "    Run with: open $APP_PATH"
