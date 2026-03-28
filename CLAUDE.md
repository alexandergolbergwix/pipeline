# MHM Pipeline — Claude Instructions

## Before Any Planning or Implementation

**Always read these two documents first:**

- `ProjectDefinitionDocument.tex` — defines the pipeline's research context, all six stages, MARC field mappings, data inventory, component interfaces, and technical requirements.
- `SystemDesignDocument.tex` — defines the chosen framework (PyQt6), distribution strategy (uv + native installers), application architecture, module structure, GUI design, and clean code standards.

Do not propose or implement anything that contradicts or ignores these documents without first flagging the conflict to the user.

## When to Update the Design Documents

Update `SystemDesignDocument.tex` whenever:

- The application architecture changes (new layers, new components, removed components)
- The GUI design changes (new screens, renamed tabs, new workflows)
- The distribution or installer strategy changes
- The module/package structure changes (`src/mhm_pipeline/` layout, entry points)
- New cross-platform considerations are identified
- The clean code toolchain changes (e.g., replacing ruff, switching to a different test runner)

Update `ProjectDefinitionDocument.tex` whenever:

- A new pipeline stage is added or an existing stage is modified
- MARC field mappings change
- A new external API or authority source is integrated
- Hardware or software requirements change
- The data inventory changes (new model files, new data files)

**Rule:** A code change that alters the system design is not complete until the relevant `.tex` document is also updated. Treat the documents as the source of truth for architecture decisions.

## Project Overview

MHM (Mapping Hebrew Manuscripts) is an end-to-end MARC-to-RDF conversion pipeline:

1. **Stage 1** — MARC Input Parsing (`UnifiedReader` + `field_handlers.py`)
2. **Stage 2** — NER Extraction (HalleluBERT + NeoDictaBERT)
3. **Stage 3** — Authority Resolution (Mazal/NLI, VIAF, KIMA)
4. **Stage 4** — RDF Graph Construction (`MarcToRdfMapper`, HMO ontology)
5. **Stage 5** — SHACL Validation (`pyshacl`)
6. **Stage 6** — Wikidata Upload (API + QuickStatements)

Key paths:
- GUI entry point: `src/mhm_pipeline/app.py`
- Main window: `src/mhm_pipeline/gui/main_window.py`
- NER inference: `ner/inference_pipeline.py` (`JointNERPipeline`, model: `alexgoldberg/hebrew-manuscript-joint-ner-v2`)
- RDF mapper: `converter/transformer/mapper.py` (`MarcToRdfMapper`)
- Mazal authority DB: `converter/authority/mazal_index.db`
- KIMA authority DB: `data/kima/kima_index.db` (built from TSVs in `data/kima/`)
- KIMA data: `data/kima/` — three TSV files (places, Hebrew variants, Maagarim)
- Ontology: `ontology/hebrew-manuscripts.ttl`
- SHACL shapes: `ontology/shacl-shapes.ttl`

## Claude Code Skills (Slash Commands)

Project-specific slash commands are stored in `.claude/commands/`. Use them with `/skill-name` in the chat:

| Command | Description |
|---|---|
| `/run-tests` | Run the full test suite (`tests/`) |
| `/run-e2e` | Run only the e2e integration tests |
| `/check-coverage` | Measure ontology class/property coverage over 200 TSV records |
| `/launch-app` | Launch the PyQt6 GUI (opens a new Terminal window) |
| `/update-docs` | Check and update `SystemDesignDocument.tex` / `ProjectDefinitionDocument.tex` |

## Code Standards

This project is open source (GPL). Follow these rules on every change:

- Use `pyproject.toml` as the single source of dependency and tool configuration
- All Python code must have type annotations; never use `Any`
- Format and lint with **ruff** before committing
- Type-check with **mypy** (strict mode)
- Test files use `.spec.py` extension under `tests/`
- Use `pathlib.Path` for all file paths — never `os.path` string concatenation
- GPU device selection must always fall through: MPS → CUDA → CPU
- Never hardcode absolute paths; use `platformdirs` for app data directories

---

## Learned Rules — Avoid Known Pitfalls

These rules were derived from real errors hit during development. Follow them exactly to avoid repeating them.

### 1. Always create README.md before running uv sync

`pyproject.toml` contains `readme = "README.md"`. If the file does not exist, `uv sync` and `uv build` will fail with `OSError: Readme file does not exist`. Always ensure `README.md` exists at the repo root before running any uv command.

### 2. Never import torch or transformers at module top level

`torch` and `transformers` are optional and may not be installed (e.g. during GUI-only testing). Any module that uses them must import lazily inside the function body:

```python
# WRONG — breaks when torch is not installed
import torch

# CORRECT — lazy import inside the function
def get_device() -> str:
    try:
        import torch  # noqa: PLC0415
        ...
    except ImportError:
        return "cpu"
```

This applies to all files in `src/mhm_pipeline/platform_/`, `controller/workers.py`, and any file that imports from `ner/`.

### 3. Always specify --python 3.12 explicitly with uv

Running `uv venv` or `uv sync` without `--python 3.12` will pick the newest available Python (currently 3.14), creating a venv that is incompatible with pinned dependencies. Always use:

```bash
uv venv --python 3.12
uv sync --python 3.12
```

### 4. Run uv lock before uv sync --frozen

`uv sync --frozen` requires `uv.lock` to exist. If it does not exist (e.g. after a fresh clone or after editing `pyproject.toml`), run `uv lock` first:

```bash
uv lock
uv sync --frozen
```

### 5. Always set PYTHONPATH=src:. when running the app from the repo root

The project uses a `src/` layout. Without `PYTHONPATH=src:.` the `mhm_pipeline` package is not importable:

```bash
# WRONG
python -m mhm_pipeline.app

# CORRECT
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

### 6. Use Read tool on background task output files — never block with TaskOutput

`TaskOutput` with a large timeout causes "not responding" in the UI. Background tasks write their output to a file path returned in the task result. Use the `Read` tool on that path to check progress non-blockingly:

```
# WRONG — blocks and causes "not responding"
TaskOutput(task_id=..., block=True, timeout=240000)

# CORRECT — non-blocking check
Read(file_path="/private/tmp/.../tasks/<id>.output")
```

### 7. Set first_run_done=True when testing to skip the setup wizard

On first launch, `app.py` shows a `QWizard` for model download. In a terminal test this wizard may open and close silently, causing `sys.exit(0)` before the main window appears. Skip it by setting the flag once:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
from mhm_pipeline.settings.settings_manager import SettingsManager
SettingsManager().first_run_done = True
"
```

### 8. Launch the GUI with & to keep it running from a terminal

`app.exec()` blocks until the window is closed. When launched synchronously from a Claude tool call, the process exits immediately after the window closes. Use `&` to background it:

```bash
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app &
```

To test window creation without keeping it open:

```python
from PyQt6.QtCore import QTimer
QTimer.singleShot(1500, app.quit)
sys.exit(app.exec())
```

### 9. Never run two concurrent uv installs into the same venv

Running `uv sync` and `uv pip install` simultaneously into the same `.venv` causes partial installs and version conflicts. Always wait for one uv operation to complete before starting another. Check with `ps aux | grep uv` before starting a new install.

### 10. The correct launch command (always use this as the reference)

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app
```

For a smoke test without the event loop blocking:

```bash
PYTHONPATH=src:. .venv/bin/python -c "
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from mhm_pipeline.settings.settings_manager import SettingsManager
from mhm_pipeline.controller.pipeline_controller import PipelineController
from mhm_pipeline.gui.main_window import MainWindow
app = QApplication(sys.argv)
window = MainWindow(SettingsManager(), PipelineController(SettingsManager()))
window.show()
print('visible:', window.isVisible(), '| size:', window.size())
QTimer.singleShot(1500, app.quit)
sys.exit(app.exec())
"
```
