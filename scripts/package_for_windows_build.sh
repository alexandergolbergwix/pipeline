#!/usr/bin/env bash
# Stage MHM Pipeline source + bundled models + Windows build scripts into a
# single zip the operator uploads to a Windows host. The Windows host then
# unzips and double-clicks `installer\windows\Build Installer.bat` to produce
# `dist\MHMPipeline-Setup-0.1.0.exe`.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
OUT="${ROOT}/dist/mhm-pipeline-source.zip"
STAGING="${ROOT}/dist/_winstage"

echo "=== MHM Pipeline — Windows source bundler ==="
echo "Repo root: ${ROOT}"

rm -rf "$STAGING" "$OUT"
mkdir -p "$STAGING" "${ROOT}/dist"

echo
echo "[1/4] Staging source tree (rsync)..."
rsync -a \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.venv*' \
  --exclude='dist' \
  --exclude='build' \
  --exclude='paper' \
  --exclude='tests' \
  --exclude='data/tsvs' \
  --exclude='data/NLI_AUTHORITY_XML' \
  --exclude='ner/raw-data' \
  --exclude='ner/processed-data' \
  --exclude='ner/*_model_kfold' \
  --exclude='ner/training_runs' \
  --exclude='ner/*_fold_*.pt' \
  --exclude='ner/*_head.pt' \
  --exclude='*.dmg' \
  --exclude='*.app' \
  "$ROOT/" "$STAGING/"

echo
echo "[2/4] Verifying critical assets are present in stage..."
REQUIRED=(
  "converter/authority/mazal_index.db"
  "data/kima/kima_index.db"
  "ner/provenance_ner_model.pt"
  "ner/contents_ner_model.pt"
  "ontology/hebrew-manuscripts.ttl"
  "ontology/shacl-shapes.ttl"
  "installer/windows/MHMPipeline.spec"
  "installer/windows/build_installer.iss"
  "installer/windows/Build Installer.bat"
  "src/mhm_pipeline/app.py"
  "pyproject.toml"
)
MISSING=0
for asset in "${REQUIRED[@]}"; do
  if [ ! -e "$STAGING/$asset" ]; then
    echo "  MISSING: $asset"
    MISSING=1
  else
    echo "  OK:      $asset"
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo
  echo "ERROR: one or more required assets are missing. Aborting." >&2
  exit 1
fi

echo
echo "[3/4] Bundling Hugging Face snapshots..."
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
JOINT_SRC="${HF_CACHE}/models--alexgoldberg--hebrew-manuscript-joint-ner-v2"
DICTA_SRC="${HF_CACHE}/models--dicta-il--dictabert"

if [ ! -d "$JOINT_SRC" ]; then
  echo "ERROR: HF snapshot not found at: $JOINT_SRC" >&2
  echo "Run the app once on this machine to populate the HF cache, then retry." >&2
  exit 1
fi
if [ ! -d "$DICTA_SRC" ]; then
  echo "ERROR: HF snapshot not found at: $DICTA_SRC" >&2
  echo "Run the app once on this machine to populate the HF cache, then retry." >&2
  exit 1
fi

mkdir -p "$STAGING/models"

# Flatten the HF cache snapshot into a top-level model directory so that
# `transformers.from_pretrained("models/<name>")` finds config.json /
# model.safetensors at the root — the same layout the macOS .app uses.
#
# This (a) fixes the Windows-only bug where transformers couldn't locate
# the model files inside the HF-cache snapshot/blobs structure, and
# (b) implicitly drops blobs/, which would otherwise double the payload
# after `cp -R` dereferences symlinks (saves ~3 GB on the bundle and
# keeps the Inno Setup payload under its 4.2 GB single-file ceiling).
flatten_hf_snapshot() {
  local src="$1"   # source HF cache dir, e.g. ~/.cache/huggingface/hub/models--owner--name
  local dest="$2"  # flattened destination, e.g. .../models/name
  local snapshot_dir
  snapshot_dir=$(ls -d "$src/snapshots/"*/ 2>/dev/null | head -1)
  if [ -z "$snapshot_dir" ]; then
    echo "ERROR: no snapshot directory under $src/snapshots/" >&2
    return 1
  fi
  mkdir -p "$dest"
  # cp -L follows symlinks (HF cache snapshot files are symlinks to blobs/),
  # producing a real flat directory of model files at $dest.
  cp -RL "$snapshot_dir"/. "$dest/"
}

echo "  Flattening hebrew-manuscript-joint-ner-v2 snapshot..."
flatten_hf_snapshot "$JOINT_SRC" "$STAGING/models/hebrew-manuscript-joint-ner-v2"
echo "  Flattening dictabert snapshot..."
flatten_hf_snapshot "$DICTA_SRC" "$STAGING/models/dictabert"

echo
echo "[4/4] Zipping (fastest compression — final compression happens in Inno Setup)..."
cd "${ROOT}/dist"
zip -r -q -1 "$OUT" "_winstage/"

rm -rf "$STAGING"

SIZE="$(du -h "$OUT" | cut -f1)"
echo
echo "=== DONE ==="
echo "Output: $OUT"
echo "Size:   $SIZE"
echo
echo "Next steps:"
echo "  1. Upload $OUT to your Windows build host (OneDrive / Google Drive / SCP / shared folder)."
echo "  2. On Windows, unzip it into a working folder."
echo "  3. Double-click  installer\\windows\\Build Installer.bat"
echo "  4. After ~30 minutes, find  dist\\MHMPipeline-Setup-0.1.0.exe  (~4-5 GB)."
echo "  5. Send that single .exe to the supervisor."
