#!/usr/bin/env bash
# Build VoiceText.app with PyInstaller, re-sign, and package as DMG.
# Usage: ./scripts/build-dmg.sh [version]
# Example: ./scripts/build-dmg.sh 0.1.0
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

# Read version from pyproject.toml
PYPROJECT_VERSION=$(python3 -c "
import sys
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
with open('$PROJECT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    # Try to extract from git tag
    VERSION=$(git -C "$PROJECT_DIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "")
fi
if [ -z "$VERSION" ]; then
    VERSION="$PYPROJECT_VERSION"
fi

# Validate version matches pyproject.toml
if [ "$VERSION" != "$PYPROJECT_VERSION" ]; then
    echo "ERROR: Version mismatch!"
    echo "  Requested: $VERSION"
    echo "  pyproject.toml: $PYPROJECT_VERSION"
    echo "Update pyproject.toml or use matching version."
    exit 1
fi

DMG_PATH="$DIST_DIR/VoiceText-${VERSION}-arm64.dmg"

cd "$PROJECT_DIR"

echo "==> Building VoiceText v${VERSION}..."

echo "==> Injecting build info..."
uv run python scripts/inject_build_info.py

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

echo "==> Creating DMG..."
DMG_DIR=$(mktemp -d)
cp -R "$APP_PATH" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"
hdiutil create -volname "VoiceText" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "$DMG_PATH"
rm -rf "$DMG_DIR"

APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
echo ""
echo "==> Build complete!"
echo "    App: $APP_PATH ($APP_SIZE)"
echo "    DMG: $DMG_PATH ($DMG_SIZE)"
echo "    Run with: open $APP_PATH"
