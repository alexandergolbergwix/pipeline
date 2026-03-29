Rebuild and reinstall the MHM Pipeline macOS app to the Applications folder.

This should be run after code modifications to ensure the installed app reflects the latest changes.

## When to Run This

**ALWAYS run this after modifying:**
- GUI widgets (`src/mhm_pipeline/gui/widgets/`)
- GUI panels (`src/mhm_pipeline/gui/panels/`)
- Main window or app entry point
- Any code that affects the user interface

**ALSO run after:**
- Adding new Python files to the project
- Modifying imports in GUI-related files
- Changes to `field_handlers.py` (affects parsing results display)
- Changes to `workers.py` (affects stage results display)
- Changes to `pyproject.toml` dependencies

## Pre-Reinstall Checklist (CRITICAL - prevents crashes)

### 1. **Sync Dependencies** ⚠️ MOST COMMON CAUSE OF CRASHES

The app uses `.venv/bin/python`, not system Python. Missing packages cause immediate crashes.

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline

# Sync dependencies (required after any pyproject.toml change)
uv sync

# Verify critical packages are in venv
.venv/bin/python -c "import pymarc; print('pymarc OK')"
.venv/bin/python -c "import rdflib; print('rdflib OK')"
.venv/bin/python -c "import pyshacl; print('pyshacl OK')"
.venv/bin/python -c "import PyQt6; print('PyQt6 OK')"
```

### 2. **Verify Python Syntax**

```bash
python3 -m py_compile src/mhm_pipeline/gui/main_window.py
python3 -m py_compile src/mhm_pipeline/controller/workers.py
python3 -m py_compile converter/transformer/field_handlers.py
```

### 3. **Verify Imports from Venv Context** ⚠️ CRITICAL

Tests use `PYTHONPATH=src:.` but the app uses `.venv/bin/python`. These MUST both work:

```bash
# Test 1: With PYTHONPATH (how tests run)
PYTHONPATH=src:. python3 -c "from mhm_pipeline.gui.main_window import MainWindow; print('OK - PYTHONPATH')"

# Test 2: From venv (how app runs) - THIS IS THE CRITICAL ONE
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from mhm_pipeline.gui.main_window import MainWindow
print('OK - venv')
"
```

### 4. **Test Stage 0 Import (Most Common Crash)**

Stage 1 (MARC Parse) crashes if `pymarc` or parser imports fail:

```bash
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from converter.parser.unified_reader import UnifiedReader
from converter.transformer.field_handlers import extract_all_data
print('Stage 0 imports OK')
"
```

## Installation Procedure

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline

# Step 1: Ensure dependencies are synced
uv sync

# Step 2: Create the app bundle structure
APP_DIR="/Users/alexandergo/Applications/MHM Pipeline.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

# Create directories
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Create launcher script
cat > "$MACOS_DIR/MHM Pipeline" << 'EOF'
#!/bin/bash

# MHM Pipeline Launcher
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE_DIR="/Users/alexandergo/Documents/Doctorat/pipeline"

cd "$PIPELINE_DIR" || exit 1
export PYTHONPATH=src:.

# Launch the app
"$PIPELINE_DIR/.venv/bin/python" -m mhm_pipeline.app
EOF

# Make launcher executable
chmod +x "$MACOS_DIR/MHM Pipeline"

# Update or create Info.plist
VERSION=$(grep -oP '(?<=version = ")[^"]+' pyproject.toml 2>/dev/null || echo "0.1.0")

cat > "$CONTENTS_DIR/Info.plist" << EOF
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
  <string>$(echo $VERSION | cut -d. -f1-2)</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>????</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>12.3</string>
  <key>NSHumanReadableCopyright</key>
  <string>Bar-Ilan University, GPL-3.0</string>
</dict>
</plist>
EOF

echo "MHM Pipeline.app reinstalled to ~/Applications/"
echo "Version: $VERSION"
```

## Verification Steps (After Reinstall)

### Step 1: Smoke Test

```bash
# Test from terminal to see any errors
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app &
APP_PID=$!
sleep 3
ps aux | grep -i "mhm" | grep -v grep
kill $APP_PID 2>/dev/null || true
```

### Step 2: Test Stage 0 (MARC Parse)

```bash
# This catches 90% of runtime crashes
.venv/bin/python -c "
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, 'src')

from mhm_pipeline.controller.workers import MarcParseWorker

with tempfile.TemporaryDirectory() as tmp:
    worker = MarcParseWorker(
        Path('data/tsvs/17th_century_samples.tsv'),
        Path(tmp),
        'cpu',
        start=0,
        end=2
    )
    worker.run()
    print('Stage 0 execution OK')
"
```

### Step 3: Launch App

```bash
open "/Users/alexandergo/Applications/MHM Pipeline.app"
```

## Common Errors After Reinstall

| Error | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError` | Dependencies not synced | Run `uv sync` before reinstall |
| `ModuleNotFoundError: pymarc` | Parser import failed | Check `uv sync` output |
| Import works with PYTHONPATH but not venv | Packages missing in venv | Run `uv sync` |
| App opens then closes on Stage 1 | Import error in worker | Check imports from venv context |
| Stage 0 (Parse) crashes | Missing pymarc or parser deps | Run dependency sync |
| GUI elements missing | Widget not added to layout | Check `layout.addWidget()` calls |

## Critical Lesson Learned

**Tests pass ≠ App works**

Tests run with `PYTHONPATH=src:.` which finds packages via the filesystem. The installed app uses `.venv/bin/python` which only sees packages installed in the venv.

**Always run `uv sync` before reinstalling the app.**
