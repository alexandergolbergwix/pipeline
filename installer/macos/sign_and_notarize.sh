#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# MHM Pipeline — macOS Code Signing and Notarization
# ─────────────────────────────────────────────────────────────────────
# Signs the .app bundle, creates a signed .dmg, submits to Apple's
# notary service, and staples the ticket.
#
# Prerequisites:
#   - Apple Developer ID Application certificate in Keychain
#   - Environment variables:
#     DEVELOPER_ID_APPLICATION  - signing identity name
#     APPLE_ID                  - Apple ID email
#     APPLE_ID_PASSWORD         - app-specific password
#     APPLE_TEAM_ID             - Developer Team ID
#
# Usage:
#   bash installer/macos/sign_and_notarize.sh
#
# If DEVELOPER_ID_APPLICATION is not set, the script skips signing
# (for local development builds).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
APP_NAME="MHM Pipeline"
APP_DIR="$DIST_DIR/$APP_NAME.app"

VERSION=$(grep -oP '(?<=version = ")[^"]+' "$REPO_ROOT/pyproject.toml" 2>/dev/null || \
          grep 'version' "$REPO_ROOT/pyproject.toml" | head -1 | sed 's/.*"\(.*\)".*/\1/')
VERSION="${VERSION:-0.1.0}"

DMG_NAME="MHMPipeline-${VERSION}.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

if [ -z "${DEVELOPER_ID_APPLICATION:-}" ]; then
    echo "DEVELOPER_ID_APPLICATION not set — skipping code signing."
    echo "The .app and .dmg are unsigned (suitable for local testing only)."
    exit 0
fi

echo "Signing $APP_NAME.app with identity: $DEVELOPER_ID_APPLICATION"

# ── Sign all nested binaries (bottom-up) ─────────────────────────────
echo "Signing nested .so and .dylib files..."
find "$APP_DIR" \( -name "*.so" -o -name "*.dylib" \) -exec \
    codesign --force --sign "$DEVELOPER_ID_APPLICATION" \
             --timestamp --options runtime {} \;

echo "Signing frameworks..."
find "$APP_DIR" -name "*.framework" -exec \
    codesign --force --sign "$DEVELOPER_ID_APPLICATION" \
             --timestamp --options runtime {} \;

echo "Signing the app bundle..."
codesign --force --sign "$DEVELOPER_ID_APPLICATION" \
         --timestamp --options runtime \
         --entitlements /dev/stdin "$APP_DIR" << 'ENTITLEMENTS'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <key>com.apple.security.network.client</key>
  <true/>
</dict>
</plist>
ENTITLEMENTS

echo "Verifying signature..."
codesign --verify --deep --strict "$APP_DIR"
echo "  Signature OK"

# ── Rebuild signed DMG ───────────────────────────────────────────────
echo "Creating signed DMG..."
rm -f "$DMG_PATH"

DMG_STAGING="$DIST_DIR/dmg_staging"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP_DIR" "$DMG_STAGING/"
ln -sf /Applications "$DMG_STAGING/Applications"

hdiutil create \
    -volname "MHM Pipeline" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_STAGING"

codesign --force --sign "$DEVELOPER_ID_APPLICATION" --timestamp "$DMG_PATH"

# ── Notarize ─────────────────────────────────────────────────────────
if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_ID_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    echo "Submitting to Apple notary service..."
    xcrun notarytool submit "$DMG_PATH" \
        --apple-id "$APPLE_ID" \
        --password "$APPLE_ID_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" \
        --wait

    echo "Stapling notarization ticket..."
    xcrun stapler staple "$DMG_PATH"
    echo "  Notarization complete"
else
    echo "Notarization credentials not set — skipping notarization."
    echo "Set APPLE_ID, APPLE_ID_PASSWORD, and APPLE_TEAM_ID to enable."
fi

echo ""
echo "Signed DMG: $DMG_PATH"
