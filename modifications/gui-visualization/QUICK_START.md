# GUI Visualization Quick-Start Guide

## Getting Started

### Prerequisites
```bash
# Ensure you're in the project directory
cd /Users/alexandergo/Documents/Doctorat/pipeline

# Activate virtual environment
source .venv/bin/activate

# Verify PyQt6 is installed
python -c "from PyQt6.QtWidgets import QWidget; print('PyQt6 OK')"
```

### Project Structure
```
pipeline/
├── src/mhm_pipeline/
│   ├── gui/
│   │   ├── main_window.py          ⬅️ Main window (add flow widget here)
│   │   ├── panels/                 ⬅️ Add viz widgets to these
│   │   │   ├── convert_panel.py
│   │   │   ├── ner_panel.py
│   │   │   ├── authority_panel.py
│   │   │   ├── rdf_panel.py
│   │   │   ├── validate_panel.py
│   │   │   └── wikidata_panel.py
│   │   └── widgets/                ⬅️ CREATE THIS FOLDER
│   │       ├── __init__.py
│   │       ├── marc_field_visualizer.py
│   │       ├── entity_highlighter.py
│   │       └── ... (6 more)
│   └── controller/
│       └── pipeline_controller.py  ⬅️ Wire data flow here
```

---

## Step-by-Step Implementation

### Step 1: Create Widgets Directory

```bash
mkdir -p src/mhm_pipeline/gui/widgets
touch src/mhm_pipeline/gui/widgets/__init__.py
```

### Step 2: Create Base Widget Class

Create `src/mhm_pipeline/gui/widgets/base_visualization_widget.py`:

```python
"""Base class for all visualization widgets."""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


class BaseVisualizationWidget(QWidget):
    """Abstract base class for pipeline visualization widgets.

    All visualization widgets should inherit from this class
    to ensure consistent interface and behavior.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_data = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the widget UI. Override in subclasses."""
        self._layout = QVBoxLayout(self)
        self._placeholder = QLabel("No data loaded")
        self._placeholder.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self._layout.addWidget(self._placeholder)

    def clear_data(self) -> None:
        """Clear all data and return to placeholder state."""
        self._has_data = False
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        """Show the placeholder message."""
        self._placeholder.setVisible(True)

    def _hide_placeholder(self) -> None:
        """Hide the placeholder message."""
        self._placeholder.setVisible(False)

    @property
    def has_data(self) -> bool:
        """Return True if widget has data loaded."""
        return self._has_data
```

### Step 3: Implement First Widget (MarcFieldVisualizer)

Create `src/mhm_pipeline/gui/widgets/marc_field_visualizer.py`:

```python
"""MARC field visualization widget."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from converter.transformer.field_handlers import ExtractedData
from .base_visualization_widget import BaseVisualizationWidget


class MarcFieldVisualizer(BaseVisualizationWidget):
    """Tree view showing extracted MARC fields with color coding.

    Usage:
        widget = MarcFieldVisualizer()
        widget.load_from_extracted_data(extracted_data)
    """

    # Field tag to (background_color, text_color)
    FIELD_COLORS: dict[str, tuple[str, str]] = {
        "001": ("#f3f4f6", "#374151"),  # Control number
        "245": ("#dbeafe", "#1e40af"),  # Title
        "500": ("#fef3c7", "#92400e"),  # Notes
        "561": ("#e5dbff", "#5b21b6"),  # Provenance
        "700": ("#fce7f3", "#be185d"),  # Names
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """Set up the tree widget."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Field", "Tag", "Content"])
        self._tree.setColumnWidth(0, 150)
        self._tree.setColumnWidth(1, 50)
        self._tree.setColumnWidth(2, 300)

        layout.addWidget(self._tree)

        # Create placeholder
        self._placeholder = QLabel("No MARC data loaded")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #9ca3af; padding: 20px;")
        layout.addWidget(self._placeholder)

        self._tree.setVisible(False)

    def load_from_extracted_data(
        self,
        data: ExtractedData
    ) -> None:
        """Populate tree from parsed MARC data.

        Args:
            data: ExtractedData from field_handlers
        """
        self._tree.clear()
        self._hide_placeholder()
        self._tree.setVisible(True)

        # Add fields based on what was extracted
        if data.title:
            self._add_field("245", "Title", data.title)

        if data.notes:
            for note in data.notes:
                self._add_field("500", "Note", note)

        if data.provenance:
            self._add_field("561", "Provenance", data.provenance)

        # Expand all nodes
        self._tree.expandAll()
        self._has_data = True

    def _add_field(
        self,
        tag: str,
        label: str,
        content: str
    ) -> None:
        """Add a field node to the tree."""
        bg_color, text_color = self.FIELD_COLORS.get(
            tag, ("#f3f4f6", "#374151")
        )

        item = QTreeWidgetItem([label, tag, content[:100]])
        item.setBackground(0, QColor(bg_color))
        item.setForeground(0, QColor(text_color))
        item.setForeground(2, QColor(text_color))

        self._tree.addTopLevelItem(item)

    def _hide_placeholder(self) -> None:
        self._placeholder.setVisible(False)
        self._tree.setVisible(True)

    def _show_placeholder(self) -> None:
        self._placeholder.setVisible(True)
        self._tree.setVisible(False)
```

### Step 4: Update ConvertPanel

Modify `src/mhm_pipeline/gui/panels/convert_panel.py`:

```python
# Add import at top
from mhm_pipeline.gui.widgets.marc_field_visualizer import MarcFieldVisualizer

# In __init__, after log viewer:
self._field_visualizer = MarcFieldVisualizer()
layout.addWidget(self._field_visualizer)

# Add method to receive data:
def display_extracted_data(self, data: ExtractedData) -> None:
    """Display extracted MARC data in visualizer."""
    self._field_visualizer.load_from_extracted_data(data)
```

### Step 5: Wire Up Data Flow

Update controller to send data to widget:

```python
# In controller/pipeline_controller.py
# After Stage 1 completes:
self._stage_finished.connect(self._on_stage_finished)

def _on_stage_finished(self, index: int, output: Path) -> None:
    if index == 0:  # Stage 1 complete
        # Load extracted data and send to panel
        data = self._load_extracted_data(output)
        self._main_window._convert_panel.display_extracted_data(data)
```

### Step 6: Test the Widget

```bash
# Run the app
PYTHONPATH=src:. python -m mhm_pipeline.app

# Or run specific tests
python -m pytest tests/test_marc_field_visualizer.py -v
```

---

## Common Patterns

### Pattern 1: Lazy Loading

```python
class MyWidget(BaseVisualizationWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = None  # Don't process until shown

    def showEvent(self, event):
        """Process data when widget becomes visible."""
        super().showEvent(event)
        if self._data and not self._has_data:
            self._process_and_display(self._data)
```

### Pattern 2: Thread-Safe Updates

```python
from PyQt6.QtCore import pyqtSignal

class MyWidget(BaseVisualizationWidget):
    # Signal for thread-safe UI updates
    data_ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Connect signal to slot
        self.data_ready.connect(self._update_ui)

    def load_data_async(self, data):
        """Can be called from worker thread."""
        # Process in background...
        result = expensive_operation(data)
        # Emit signal (thread-safe)
        self.data_ready.emit(result)

    def _update_ui(self, result):
        """Always runs on main thread."""
        self._display_result(result)
```

### Pattern 3: Color Coding

```python
class MyWidget(BaseVisualizationWidget):
    STATUS_COLORS = {
        "success": ("#dcfce7", "#166534"),  # bg, text
        "warning": ("#fef3c7", "#92400e"),
        "error": ("#fee2e2", "#991b1b"),
    }

    def _colorize_item(self, item, status):
        bg, text = self.STATUS_COLORS[status]
        item.setBackground(QColor(bg))
        item.setForeground(QColor(text))
```

---

## Troubleshooting

### Widget Not Showing
- Check `layout.addWidget(widget)` was called
- Verify `widget.setVisible(True)`
- Check parent widget has proper layout

### Data Not Loading
- Ensure signal/slot connections are set up
- Check data file paths are correct
- Verify `ExtractedData` format matches expected

### Performance Issues
- Use `QTimer` to defer expensive rendering
- Implement pagination for large datasets
- Cache rendered results

### Import Errors
```python
# Add to src/mhm_pipeline/gui/widgets/__init__.py
from .marc_field_visualizer import MarcFieldVisualizer
from .entity_highlighter import EntityHighlighter
# ... etc

# Then import as:
from mhm_pipeline.gui.widgets import MarcFieldVisualizer
```

---

## Next Steps

1. ✅ Implement `MarcFieldVisualizer` (follow guide above)
2. ⏭️ Implement `EntityHighlighter` (see full plan doc)
3. ⏭️ Implement remaining 6 widgets
4. ⏭️ Update all panels
5. ⏭️ Add tests
6. ⏭️ Update documentation

---

*For full specifications, see: `gui-improvement-plan.md`*
*For checklist, see: `IMPLEMENTATION_CHECKLIST.md`*
