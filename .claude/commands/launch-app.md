Launch the MHM Pipeline GUI application.

**Prerequisites:**
- Ensure `README.md` exists (required by `pyproject.toml`)
- Virtual environment at `.venv/` with all dependencies installed

First ensure the setup wizard skip flag is set (safe to run repeatedly):
```bash
PYTHONPATH=src:. .venv/bin/python -c "
from mhm_pipeline.settings.settings_manager import SettingsManager
SettingsManager().first_run_done = True
"
```

Then launch the app in a new Terminal window so it stays open:
```bash
osascript -e 'tell application "Terminal" to do script "cd /Users/alexandergo/Documents/Doctorat/pipeline && PYTHONPATH=src:. .venv/bin/python -m mhm_pipeline.app"'
```

Confirm the window opened. If it fails, run the smoke test instead:
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

**Key paths:**
- Entry point: `src/mhm_pipeline/app.py`
- Main window: `src/mhm_pipeline/gui/main_window.py`
- NER model: `alexgoldberg/hebrew-manuscript-joint-ner-v2`
- Settings: stored via `QSettings` in `SettingsManager`
