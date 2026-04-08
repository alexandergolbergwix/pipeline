#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# MHM Pipeline — macOS .app Bundle Builder
# ─────────────────────────────────────────────────────────────────────
# Creates a self-contained .app bundle with:
#   - Source code (~20 MB)
#   - Mazal authority database (~983 MB)
#   - KIMA place index (~15 MB)
#   - Pre-downloaded NER model (~2.1 GB) + DictaBERT base (~709 MB)
#
# Total bundle: ~3.8 GB uncompressed, ~1.5-2 GB DMG
# Python + pip dependencies are bootstrapped on first launch via uv.
#
# Usage:
#   bash installer/macos/build_app.sh
#
# Output:
#   dist/MHM Pipeline.app
#   dist/MHMPipeline-<version>.dmg
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

VERSION=$(grep -oP '(?<=version = ")[^"]+' pyproject.toml 2>/dev/null || \
          grep 'version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
VERSION="${VERSION:-0.1.0}"

DIST_DIR="$REPO_ROOT/dist"
APP_NAME="MHM Pipeline"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
PIPELINE="$RESOURCES/pipeline"

# HuggingFace cache locations
HF_CACHE="$HOME/.cache/huggingface/hub"
NER_MODEL_ID="models--alexgoldberg--hebrew-manuscript-joint-ner-v2"
DICTABERT_MODEL_ID="models--dicta-il--dictabert"

echo "Building $APP_NAME.app (version $VERSION)..."

# ── Clean previous build ─────────────────────────────────────────────
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES" "$PIPELINE"

# ── Step 1: Copy source code selectively ─────────────────────────────
echo "Step 1/5: Copying source code..."

rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='.mypy_cache' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='.coverage' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='tests' \
    --exclude='scripts' \
    --exclude='modifications' \
    --exclude='*.tex' \
    --exclude='ner/raw-data' \
    --exclude='ner/processed-data' \
    --exclude='ner/*.pt' \
    --exclude='ner/*.bin' \
    --exclude='ner/*.pdf' \
    --exclude='ner/*.json' \
    --exclude='ner/*.log' \
    --exclude='ner/*.md' \
    --exclude='ner/*.txt' \
    --exclude='ner/*.csv' \
    --exclude='ner/joint_entity_role_model' \
    --exclude='ner/joint_entity_role_model_kfold' \
    --exclude='data/NLI_AUTHORITY_XML' \
    --exclude='data/mrc' \
    --exclude='data/output' \
    --exclude='data/samples' \
    --exclude='data/pilot-sample' \
    --exclude='data/annotation_templates' \
    --exclude='data/*.csv' \
    --exclude='data/*.ttl' \
    --exclude='data/*.md' \
    --exclude='data/*.xml' \
    --exclude='converter/tests' \
    --exclude='installer' \
    --exclude='.github' \
    --exclude='.claude' \
    "$REPO_ROOT/" "$PIPELINE/"

# Keep only the NER runtime files
find "$PIPELINE/ner" -name "*.py" ! -name "inference_pipeline.py" \
    ! -name "ner_inference_pipeline.py" \
    ! -name "postprocessing_rules.py" \
    ! -name "train_joint_entity_role_model_kfold.py" \
    ! -name "train_ner_model_kfold.py" \
    ! -name "__init__.py" \
    -delete 2>/dev/null || true

# Clean bytecode cache to prevent stale .pyc from overriding .py source
find "$PIPELINE" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "  Source code: $(du -sh "$PIPELINE" | cut -f1)"

# ── Step 2: Bundle Mazal authority database ──────────────────────────
echo "Step 2/5: Bundling Mazal authority database..."

MAZAL_DB="$REPO_ROOT/converter/authority/mazal_index.db"
if [ -f "$MAZAL_DB" ] && [ "$(wc -c < "$MAZAL_DB")" -gt 10000 ]; then
    cp "$MAZAL_DB" "$PIPELINE/converter/authority/mazal_index.db"
    echo "  Mazal DB: $(du -sh "$PIPELINE/converter/authority/mazal_index.db" | cut -f1)"
else
    echo "  WARNING: Mazal DB not found or is a Git LFS stub."
    echo "  Run 'git lfs pull --include=converter/authority/mazal_index.db' first."
    echo "  Authority matching will not work offline without it."
fi

# ── Step 3: Bundle KIMA place index ──────────────────────────────────
echo "Step 3/5: Bundling KIMA place index..."

KIMA_DB="$REPO_ROOT/data/kima/kima_index.db"
if [ -f "$KIMA_DB" ] && [ "$(wc -c < "$KIMA_DB")" -gt 10000 ]; then
    cp "$KIMA_DB" "$PIPELINE/data/kima/kima_index.db"
    echo "  KIMA DB: $(du -sh "$PIPELINE/data/kima/kima_index.db" | cut -f1)"
else
    echo "  KIMA index will be built from TSVs on first use."
fi

# ── Step 4: Bundle NER models from HuggingFace cache ────────────────
echo "Step 4/5: Bundling NER models..."

MODELS_DIR="$RESOURCES/models"
mkdir -p "$MODELS_DIR"

# Bundle the joint NER model (pytorch_model.bin, ~2.1 GB)
NER_CACHE="$HF_CACHE/$NER_MODEL_ID"
if [ -d "$NER_CACHE/snapshots" ]; then
    NER_SNAPSHOT=$(ls -1 "$NER_CACHE/snapshots/" | head -1)
    if [ -n "$NER_SNAPSHOT" ]; then
        NER_DEST="$MODELS_DIR/hebrew-manuscript-joint-ner-v2"
        mkdir -p "$NER_DEST"
        # Copy snapshot files, resolving symlinks to actual blobs
        cp -L "$NER_CACHE/snapshots/$NER_SNAPSHOT/"* "$NER_DEST/" 2>/dev/null || true
        echo "  NER model: $(du -sh "$NER_DEST" | cut -f1)"
    fi
else
    echo "  WARNING: NER model not found in HuggingFace cache."
    echo "  Run the app once to download it, then rebuild."
fi

# Bundle the DictaBERT base model (config, tokenizer, weights, ~709 MB)
DICTA_CACHE="$HF_CACHE/$DICTABERT_MODEL_ID"
if [ -d "$DICTA_CACHE/snapshots" ]; then
    DICTA_SNAPSHOT=$(ls -1 "$DICTA_CACHE/snapshots/" | head -1)
    if [ -n "$DICTA_SNAPSHOT" ]; then
        DICTA_DEST="$MODELS_DIR/dictabert"
        mkdir -p "$DICTA_DEST"
        cp -L "$DICTA_CACHE/snapshots/$DICTA_SNAPSHOT/"* "$DICTA_DEST/" 2>/dev/null || true
        echo "  DictaBERT: $(du -sh "$DICTA_DEST" | cut -f1)"
    fi
else
    echo "  WARNING: DictaBERT base model not found in HuggingFace cache."
fi

# Bundle provenance NER model (best fold, ~704 MB)
PROV_MODEL="$REPO_ROOT/ner/provenance_ner_model.pt"
if [ -f "$PROV_MODEL" ]; then
    cp "$PROV_MODEL" "$MODELS_DIR/provenance_ner_model.pt"
    echo "  Provenance NER: $(du -sh "$MODELS_DIR/provenance_ner_model.pt" | cut -f1)"
else
    echo "  WARNING: Provenance NER model not found at $PROV_MODEL"
fi

# Bundle contents NER model (best fold, ~704 MB)
CONT_MODEL="$REPO_ROOT/ner/contents_ner_model.pt"
if [ -f "$CONT_MODEL" ]; then
    cp "$CONT_MODEL" "$MODELS_DIR/contents_ner_model.pt"
    echo "  Contents NER: $(du -sh "$MODELS_DIR/contents_ner_model.pt" | cut -f1)"
else
    echo "  WARNING: Contents NER model not found at $CONT_MODEL"
fi

# ── Step 5: Bundle Python venv (if available) ────────────────────────
echo "Step 5/6: Bundling Python environment..."

REPO_VENV="$REPO_ROOT/.venv"
if [ -d "$REPO_VENV/bin/python" ] || [ -L "$REPO_VENV/bin/python" ]; then
    cp -R "$REPO_VENV" "$PIPELINE/.venv"
    # Clean bytecode cache in venv too
    find "$PIPELINE/.venv" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    echo "  Python venv: $(du -sh "$PIPELINE/.venv" | cut -f1)"
    echo "  (Pre-bundled — no first-launch bootstrap needed)"
else
    echo "  No venv found in repo — will bootstrap on first launch."
fi

# ── Step 6: Generate Info.plist + icon + launcher ────────────────────
echo "Step 6/6: Generating bundle metadata..."

cat > "$CONTENTS/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>il.ac.biu.mhm-pipeline</string>
  <key>CFBundleName</key>
  <string>MHM Pipeline</string>
  <key>CFBundleDisplayName</key>
  <string>MHM Pipeline</string>
  <key>CFBundleExecutable</key>
  <string>MHM Pipeline</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>????</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>12.3</string>
  <key>NSHumanReadableCopyright</key>
  <string>Bar-Ilan University, GPL-3.0</string>
</dict>
</plist>
EOF

if [ -f "$REPO_ROOT/assets/icon.icns" ]; then
    cp "$REPO_ROOT/assets/icon.icns" "$RESOURCES/AppIcon.icns"
fi

# Use compiled native binary if available, fall back to bash script
NATIVE_BIN="$REPO_ROOT/installer/macos/$APP_NAME"
if [ -f "$NATIVE_BIN" ] && file "$NATIVE_BIN" | grep -q "Mach-O"; then
    cp "$NATIVE_BIN" "$MACOS/$APP_NAME"
    echo "  Launcher: native Mach-O binary"
else
    cp "$REPO_ROOT/installer/macos/launcher.sh" "$MACOS/$APP_NAME"
    echo "  Launcher: bash script (compile launcher.m for native)"
fi
chmod +x "$MACOS/$APP_NAME"

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "App bundle created: $APP_DIR"
echo "  Source code:  $(du -sh "$PIPELINE" | cut -f1)"
echo "  Models:       $(du -sh "$MODELS_DIR" | cut -f1 2>/dev/null || echo 'none')"
echo "  Total:        $(du -sh "$APP_DIR" | cut -f1)"

# ── Create .dmg ──────────────────────────────────────────────────────
DMG_NAME="MHMPipeline-${VERSION}.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

echo ""
echo "Creating DMG (this may take a few minutes for large bundles)..."

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

echo ""
echo "Build complete:"
echo "  App:     $APP_DIR ($(du -sh "$APP_DIR" | cut -f1))"
echo "  DMG:     $DMG_PATH ($(du -sh "$DMG_PATH" | cut -f1))"
echo "  Version: $VERSION"
