#!/usr/bin/env bash
# MHM Pipeline — macOS Application Launcher (CFBundleExecutable)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENTS_DIR="$(dirname "$SCRIPT_DIR")"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
PIPELINE_DIR="$RESOURCES_DIR/pipeline"
PYTHON="$PIPELINE_DIR/.venv/bin/python"
MODELS_DIR="$RESOURCES_DIR/models"

# Set bundled model paths
if [ -d "$MODELS_DIR/hebrew-manuscript-joint-ner-v2" ]; then
    export MHM_BUNDLED_NER_MODEL="$MODELS_DIR/hebrew-manuscript-joint-ner-v2"
fi
if [ -d "$MODELS_DIR/dictabert" ]; then
    export MHM_BUNDLED_DICTABERT="$MODELS_DIR/dictabert"
fi

cd "$PIPELINE_DIR"
export PYTHONPATH="$PIPELINE_DIR/src:$PIPELINE_DIR"

# Ensure first_run_done is set (models are pre-bundled, no wizard needed)
"$PYTHON" -c "
from mhm_pipeline.settings.settings_manager import SettingsManager
s = SettingsManager()
if not s.first_run_done:
    s.first_run_done = True
" 2>/dev/null

"$PYTHON" -m mhm_pipeline.app "$@"
