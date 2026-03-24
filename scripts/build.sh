#!/usr/bin/env bash
# Build WenZi.app with PyInstaller and re-sign for macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"
APP_PATH="$DIST_DIR/WenZi.app"
FRAMEWORKS_DIR="$APP_PATH/Contents/Frameworks"

# Resolve signing identity: env var > auto-detect fingerprint > ad-hoc
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    SIGN_IDENTITY="$CODESIGN_IDENTITY"
    SIGN_MODE="identity"
else
    SIGN_IDENTITY=$(security find-identity -p codesigning \
        | grep -m1 ')' | awk '{print $2}' || true)
    if [ -n "$SIGN_IDENTITY" ]; then
        SIGN_MODE="identity"
    else
        echo "WARNING: No codesigning identity found in keychain, falling back to ad-hoc signing."
        SIGN_MODE="adhoc"
    fi
fi

cd "$PROJECT_DIR"

echo "==> Syncing dependencies (including local-asr extras)..."
uv sync --all-extras

if [ "${1:-}" = "--fast" ]; then
    echo "==> Fast build (incremental, skipping clean)..."
    CLEAN_FLAG=""
else
    echo "==> Cleaning previous build..."
    rm -rf build dist
    find "$PROJECT_DIR/src" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    CLEAN_FLAG="--clean"
fi

echo "==> Running PyInstaller..."
uv run pyinstaller WenZi.spec $CLEAN_FLAG --noconfirm

if [ "$SIGN_MODE" = "identity" ]; then
    echo "==> Re-signing app bundle (identity: $SIGN_IDENTITY)..."
    codesign --force --deep --sign "$SIGN_IDENTITY" "$APP_PATH"
else
    echo "==> Re-signing app bundle (ad-hoc)..."
    codesign --force --deep --sign - "$APP_PATH"
fi

echo "==> Verifying signature..."
codesign --verify --verbose "$APP_PATH"

# Verify bundled resources: scan src/wenzi/ for non-Python files
# and check they exist in the packaged app bundle
echo "==> Verifying bundled resources..."
MISSING=0
FOUND=0
while IFS= read -r src_file; do
    # Convert src/wenzi/audio/sounds/start_default.wav -> wenzi/audio/sounds/start_default.wav
    rel_path="${src_file#$PROJECT_DIR/src/}"
    if [ -f "$FRAMEWORKS_DIR/$rel_path" ]; then
        echo "    OK: $rel_path"
        FOUND=$((FOUND + 1))
    else
        echo "    MISSING: $rel_path"
        MISSING=$((MISSING + 1))
    fi
done < <(find "$PROJECT_DIR/src/wenzi" -type f \
    ! -name "*.py" \
    ! -name "*.pyc" \
    ! -path "*/__pycache__/*" \
    ! -path "*/.DS_Store" \
    ! -name "*.egg-info" \
    ! -path "*.egg-info/*")

if [ "$MISSING" -gt 0 ]; then
    echo ""
    echo "ERROR: $MISSING resource(s) missing from app bundle!"
    echo "       Add them to WenZi.spec datas= section."
    exit 1
fi
echo "    $FOUND resource(s) verified."

APP_SIZE=$(du -sh "$APP_PATH" | cut -f1)
echo ""
echo "==> Build complete: $APP_PATH ($APP_SIZE)"
echo "    Run with: open $APP_PATH"
