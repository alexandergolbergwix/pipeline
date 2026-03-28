#!/usr/bin/env bash
set -euo pipefail

VERSION=$(grep -Po '(?<=version = ")[^"]+' pyproject.toml 2>/dev/null || echo "0.1.0")
DIST_DIR=dist

mkdir -p "$DIST_DIR"

echo "Building macOS .pkg (version $VERSION)..."
pkgbuild \
    --root . \
    --identifier edu.biu.mhm-pipeline \
    --version "$VERSION" \
    --scripts installer/macos/ \
    --install-location /Applications/MHMPipeline \
    "$DIST_DIR/MHMPipeline.pkg"

echo "Wrapping in .dmg..."
hdiutil create \
    -volname "MHM Pipeline" \
    -srcfolder "$DIST_DIR/MHMPipeline.pkg" \
    -ov \
    -format UDZO \
    "$DIST_DIR/MHMPipeline.dmg"

echo "Created:"
echo "  $DIST_DIR/MHMPipeline.pkg"
echo "  $DIST_DIR/MHMPipeline.dmg"
