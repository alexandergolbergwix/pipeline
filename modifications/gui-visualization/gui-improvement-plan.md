# MHM Pipeline GUI Visualization Improvement Plan

## Document Information
- **Date**: 2026-03-28
- **Status**: Planning Phase
- **Scope**: GUI Enhancement - Visual Stage Transformations
- **Related Documents**:
  - `ProjectDefinitionDocument.tex` (Section 3: System Architecture)
  - `SystemDesignDocument.tex` (Section 5: GUI Design)

---

## 1. Executive Summary

### Current State
The existing GUI (`src/mhm_pipeline/gui/main_window.py`) provides basic file selection and text-based logging but **does not visualize** what transformations occur at each pipeline stage. Users cannot see:
- Which MARC fields were extracted
- What entities were identified by NER
- How names were matched to authority files
- The structure of generated RDF triples
- Validation results with context
- Upload progress per entity

### Proposed Solution
Implement **8 new visual components** that provide immediate, visual feedback showing exactly what each stage does to the data. Each component will be integrated into the corresponding stage panel.

---

## 2. Detailed Component Specifications

### Component 1: MarcFieldVisualizer

**Location**: `src/mhm_pipeline/gui/widgets/marc_field_visualizer.py`
**Used In**: `ConvertPanel` (Stage 1)

#### Purpose
Display extracted MARC fields in a tree view with color-coded field types, making it immediately clear which bibliographic elements were successfully parsed.

#### Visual Design
```
┌─ MARC Fields Extracted (47 fields) ──────────┐
│ 📁 001  Control Number                       │
│    └─ 990001234560205171                     │
│ 📁 245  Title Statement [blue]               │
│    ├─ $a Torah                               │
│    ├─ $b with commentary                       │
│    └─ $c by Rashi                            │
│ 📁 500  General Note [yellow]                │
│    └─ Written by scribe Shlomo ben David   │
│ 📁 561  Ownership History [purple]           │
│    └─ Formerly owned by Jewish community     │
└──────────────────────────────────────────────┘
```

#### Field Color Coding
| Field Range | Color | Meaning |
|-------------|-------|---------|
| 001-099 | Gray | Control fields |
| 100-199 | Blue | Names (authors, contributors) |
| 245-246 | Cyan | Titles |
| 260-264 | Orange | Publication/Production |
| 300-399 | Green | Physical description |
| 500-599 | Yellow | Notes (NER targets) |
| 600-699 | Purple | Subjects |
| 700-899 | Pink | Added entries |
| 957 | Red | Local notes (colophon) |

#### Implementation Details
```python
class MarcFieldVisualizer(QWidget):
    """Tree widget showing extracted MARC fields with color coding."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Field", "Tag", "Content"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 50)

    def load_from_extracted_data(self, data: ExtractedData) -> None:
        """Populate tree from parsed MARC data."""
        # Clear existing
        self.tree.clear()

        # Add core fields
        if data.title:
            self._add_field_node("245", "Title Statement", data.title,
                                color="#dbeafe", text_color="#1e40af")

        # Add notes (500 series)
        for note in data.notes:
            self._add_field_node("500", "General Note", note,
                                color="#fef3c7", text_color="#92400e")

        # Add provenance (561)
        if data.provenance:
            self._add_field_node("561", "Provenance", data.provenance,
                                color="#e5dbff", text_color="#5b21b6")

        # Expand all by default
        self.tree.expandAll()

    def _add_field_node(self, tag: str, label: str, content: str,
                       color: str, text_color: str) -> QTreeWidgetItem:
        """Create a field node with styling."""
        item = QTreeWidgetItem([label, tag, content[:50]])
        item.setBackground(0, QColor(color))
        item.setForeground(0, QColor(text_color))
        item.setForeground(2, QColor(text_color))
        self.tree.addTopLevelItem(item)
        return item
```

#### Data Requirements
- Requires access to `ExtractedData` dataclass from `converter/transformer/field_handlers.py`
- Needs to be updated when Stage 1 completes

---

### Component 2: EntityHighlighter

**Location**: `src/mhm_pipeline/gui/widgets/entity_highlighter.py`
**Used In**: `NerPanel` (Stage 2)

#### Purpose
Display original note text with highlighted entity spans, showing exactly what the NER model identified and classified.

#### Visual Design
```
┌─ Entities Found (5 entities) ──────────────┐
│ Original Text:                               │
│ ┌────────────────────────────────────────┐  │
│ │ Written by scribe │███████│ ben │███│   │  │
│ │ in │████│, Jerusalem.                  │  │
│ └────────────────────────────────────────┘  │
│                                              │
│ Legend: ▓▓▓ Person  ░░░ Date  ▒▒▒ Place      │
│                                              │
│ Extracted Entities:                          │
│ ┌─────────────────────────────────────────┐ │
│ │ 👤 Shlomo ben David  →  SCRIBE          │ │
│ │ 📅 1450              →  DATE            │ │
│ │ 📍 Jerusalem         →  PLACE           │ │
│ └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class EntityHighlighter(QWidget):
    """Widget for displaying text with highlighted entity spans."""

    # Color mapping for entity types
    ENTITY_COLORS = {
        "PERSON": ("#c7d2fe", "#3730a3"),      # Light purple, dark text
        "DATE": ("#fed7aa", "#9a3412"),          # Light orange
        "PLACE": ("#bbf7d0", "#166534"),         # Light green
        "WORK": ("#fecaca", "#991b1b"),          # Light red
        "ORG": ("#e5e7eb", "#374151"),          # Light gray
    }

    ROLE_ICONS = {
        "AUTHOR": "✍️",
        "SCRIBE": "🖊️",
        "OWNER": "👤",
        "CENSOR": "✂️",
        "TRANSLATOR": "🌐",
        "COMMENTATOR": "💬",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.entity_list = QListWidget()

    def load_entities(self, text: str, entities: list[Entity]) -> None:
        """Load text and highlight entity spans.

        Args:
            text: Original note text
            entities: List of Entity objects with start/end positions
        """
        # Build HTML with highlights
        html_parts = []
        last_end = 0

        for entity in sorted(entities, key=lambda e: e.start):
            # Add text before entity
            html_parts.append(escape(text[last_end:entity.start]))

            # Add highlighted entity
            bg_color, text_color = self.ENTITY_COLORS.get(
                entity.type, ("#e5e7eb", "#374151")
            )
            entity_text = escape(text[entity.start:entity.end])
            html_parts.append(
                f'<span style="background-color: {bg_color}; '
                f'color: {text_color}; padding: 2px 4px; '
                f'border-radius: 3px;">{entity_text}</span>'
            )

            last_end = entity.end

        # Add remaining text
        html_parts.append(escape(text[last_end:]))

        self.text_edit.setHtml("".join(html_parts))

        # Populate entity list
        self.entity_list.clear()
        for entity in entities:
            icon = self.ROLE_ICONS.get(entity.role, "🏷️")
            item_text = f"{icon} {entity.text} → {entity.type}"
            if entity.role:
                item_text += f" ({entity.role})"
            self.entity_list.addItem(item_text)
```

#### Data Requirements
- Requires NER output format with entity spans (start/end positions)
- Needs integration with `ner/inference_pipeline.py` output

---

### Component 3: AuthorityMatcherView

**Location**: `src/mhm_pipeline/gui/widgets/authority_matcher_view.py`
**Used In**: `AuthorityPanel` (Stage 3)

#### Purpose
Show side-by-side comparison of extracted entity names against matched authority records, with confidence scores and source attribution.

#### Visual Design
```
┌─ Authority Resolution Results ───────────────┐
│                                              │
│ Matched Entities (8/12):                     │
│ ┌───────────────┬───┬───────────────┬────┐ │
│ │ Extracted     │ → │ Authority     │ ✓ %│ │
│ ├───────────────┼───┼───────────────┼────┤ │
│ │ Shlomo ben D..│ → │Mazal:M12345   │ 95%│ │
│ │               │   │ שְׁלֹמֹה בֶּן דָּוִד  │    │ │
│ ├───────────────┼───┼───────────────┼────┤ │
│ │ Jerusalem     │ → │GeoNames:281184│100%│ │
│ │               │   │ Jerusalem, ISR  │    │ │
│ ├───────────────┼───┼───────────────┼────┤ │
│ │ Unknown Author│ → │No match found │ ⚠  │ │
│ │               │   │[Create new]   │    │ │
│ └───────────────┴───┴───────────────┴────┘ │
│                                              │
│ Sources: Mazal  Mazal DB  GeoNames  VIAF    │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class AuthorityMatcherView(QWidget):
    """Widget showing entity-to-authority matching results."""

    SOURCE_ICONS = {
        "mazal": "🏛️",
        "viaf": "🌐",
        "kima": "📚",
        "geonames": "🌍",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Extracted Name", "", "Authority Match", "Source", "Confidence"
        ])
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 30)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 80)

    def add_match(self, extracted: str, authority: AuthorityMatch) -> None:
        """Add a match result row to the table."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Extracted name
        self.table.setItem(row, 0, QTableWidgetItem(extracted))

        # Arrow
        arrow = QTableWidgetItem("→")
        arrow.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, 1, arrow)

        # Authority match
        if authority.found:
            match_text = f"{authority.id}\n{authority.preferred_name}"
            match_item = QTableWidgetItem(match_text)
            match_item.setForeground(QColor("#059669"))
        else:
            match_item = QTableWidgetItem("No match")
            match_item.setForeground(QColor("#d97706"))
        self.table.setItem(row, 2, match_item)

        # Source icon
        icon = self.SOURCE_ICONS.get(authority.source, "❓")
        self.table.setItem(row, 3, QTableWidgetItem(f"{icon} {authority.source}"))

        # Confidence
        if authority.found:
            conf_text = f"{authority.confidence:.0%}"
            conf_item = QTableWidgetItem(conf_text)
            # Color code confidence
            if authority.confidence >= 0.9:
                conf_item.setBackground(QColor("#dcfce7"))
            elif authority.confidence >= 0.7:
                conf_item.setBackground(QColor("#fef3c7"))
            else:
                conf_item.setBackground(QColor("#fee2e2"))
            self.table.setItem(row, 4, conf_item)
```

#### Data Requirements
- AuthorityMatch dataclass with: source, id, preferred_name, confidence, found
- Integration with `converter/authority/` matchers

---

### Component 4: TripleGraphView

**Location**: `src/mhm_pipeline/gui/widgets/triple_graph_view.py`
**Used In**: `RdfPanel` (Stage 4)

#### Purpose
Visualize RDF triples as an interactive graph, making relationships between manuscripts, persons, works, and places tangible and explorable.

#### Visual Design
```
┌─ RDF Graph Visualization (87 triples) ─────┐
│                                              │
│      ┌──────────┐                            │
│      │  ms:001  │ ← Manuscript               │
│      │   📜     │                            │
│      └────┬─────┘                            │
│           │ creator                          │
│           ▼                                  │
│    ┌──────────────┐        ┌──────────┐    │
│    │wd:M12345      │←──────→│  w:Gen   │    │
│    │👤 Shlomo     │manifests│   📚     │    │
│    │  ben David   │        └──────────┘    │
│    └───────┬──────┘                            │
│            │ scribe_of                        │
│            ▼                                   │
│       ┌─────────┐                             │
│       │pl:Jerus │                             │
│       │   🌍    │                             │
│       └─────────┘                             │
│                                               │
│ Legend: 📜 Manuscript  👤 Person  📚 Work  🌍 Place│
└───────────────────────────────────────────────┘
```

#### Implementation Details
```python
class TripleGraphView(QWidget):
    """Interactive graph visualization of RDF triples.

    Uses PyQt6's QGraphicsView for rendering nodes and edges.
    For complex graphs, could integrate with networkx for layout.
    """

    NODE_COLORS = {
        "manuscript": ("#dbeafe", "#1e40af"),    # Blue
        "person": ("#fce7f3", "#be185d"),         # Pink
        "work": ("#dcfce7", "#166534"),           # Green
        "place": ("#fef3c7", "#92400e"),          # Yellow
        "event": ("#e5dbff", "#5b21b6"),          # Purple
        "concept": ("#f3f4f6", "#374151"),       # Gray
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Enable zooming
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )

        self.nodes: dict[str, QGraphicsEllipseItem] = {}
        self.edges: list[QGraphicsLineItem] = []

    def load_from_graph(self, graph: Graph) -> None:
        """Build visual graph from rdflib Graph."""
        self.scene.clear()
        self.nodes.clear()
        self.edges.clear()

        # Extract triples
        triples = list(graph)

        # Build node positions using simple force-directed layout
        # or networkx spring layout
        positions = self._calculate_layout(triples)

        # Create nodes
        for uri, pos in positions.items():
            node_type = self._infer_node_type(uri, graph)
            self._create_node(uri, pos, node_type)

        # Create edges
        for s, p, o in triples:
            if str(s) in self.nodes and str(o) in self.nodes:
                self._create_edge(str(s), str(o), str(p))

    def _create_node(self, uri: str, pos: tuple[float, float],
                    node_type: str) -> QGraphicsEllipseItem:
        """Create a visual node."""
        bg_color, text_color = self.NODE_COLORS.get(
            node_type, ("#e5e7eb", "#374151")
        )

        # Create ellipse
        ellipse = QGraphicsEllipseItem(pos[0]-40, pos[1]-25, 80, 50)
        ellipse.setBrush(QColor(bg_color))
        ellipse.setPen(QPen(QColor(text_color), 2))

        # Add label
        label = QGraphicsTextItem(self._shorten_uri(uri))
        label.setPos(pos[0]-35, pos[1]-10)
        label.setDefaultTextColor(QColor(text_color))

        self.scene.addItem(ellipse)
        self.scene.addItem(label)
        self.nodes[uri] = ellipse

        return ellipse

    def _create_edge(self, source: str, target: str, predicate: str) -> None:
        """Create a visual edge between nodes."""
        source_node = self.nodes[source]
        target_node = self.nodes[target]

        # Get center points
        source_rect = source_node.rect()
        target_rect = target_node.rect()

        line = QGraphicsLineItem(
            source_rect.center().x(), source_rect.center().y(),
            target_rect.center().x(), target_rect.center().y()
        )
        line.setPen(QPen(QColor("#6b7280"), 1))

        # Add predicate label
        mid_x = (source_rect.center().x() + target_rect.center().x()) / 2
        mid_y = (source_rect.center().y() + target_rect.center().y()) / 2
        label = QGraphicsTextItem(self._shorten_predicate(predicate))
        label.setPos(mid_x, mid_y - 10)
        label.setDefaultTextColor(QColor("#4b5563"))

        self.scene.addItem(line)
        self.scene.addItem(label)
        self.edges.append(line)
```

#### Data Requirements
- rdflib Graph object from Stage 4 output
- Requires mapping URIs to display labels

---

### Component 5: ValidationResultView

**Location**: `src/mhm_pipeline/gui/widgets/validation_result_view.py`
**Used In**: `ValidatePanel` (Stage 5)

#### Purpose
Display SHACL validation results with clear pass/fail indicators, filterable by severity level.

#### Visual Design
```
┌─ SHACL Validation Results ───────────────────┐
│                                              │
│ Summary: ✓ 12 passed  ⚠ 2 warnings  ✗ 0 failed│
│                                              │
│ Filter: [All ▼]  [✓ Pass] [⚠ Warn] [✗ Fail] │
│                                              │
│ ┌─ Results ──────────────────────────────┐  │
│ │ ✓ NodeShape:Manuscript (12/12 passed)   │  │
│ │ ✓ PropertyShape:hasTitle (8/8 passed)   │  │
│ │ ⚠ PropertyShape:hasDate                 │  │
│ │   Focus: ms:001                          │  │
│ │   Message: Date format should be ISO8601│  │
│ │   [View in TTL] [Ignore]                │  │
│ │ ✓ PropertyShape:hasCreator (5/5 passed)│  │
│ │ ⚠ PropertyShape:hasPlace                 │  │
│ │   Focus: ms:003                          │  │
│ │   Message: Place not in GeoNames          │  │
│ └──────────────────────────────────────────┘  │
│                                              │
│ [Export Report]  [Re-validate]              │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class ValidationResultView(QWidget):
    """Widget displaying SHACL validation results."""

    SEVERITY_COLORS = {
        "violation": ("#ef4444", "#fee2e2"),      # Red
        "warning": ("#f59e0b", "#fef3c7"),        # Orange
        "info": ("#3b82f6", "#dbeafe"),           # Blue
        "success": ("#22c55e", "#dcfce7"),        # Green
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results_list = QListWidget()
        self.summary_label = QLabel()

        # Filter buttons
        self.filter_all = QPushButton("All")
        self.filter_pass = QPushButton("✓ Pass")
        self.filter_warn = QPushButton("⚠ Warn")
        self.filter_fail = QPushButton("✗ Fail")

    def load_results(self, result: ValidationResult) -> None:
        """Load validation results.

        Args:
            result: ValidationResult from pyshacl
        """
        # Update summary
        total = result.total_checks
        passed = result.passed
        warnings = len(result.warnings)
        failures = len(result.violations)

        summary_text = (
            f"Summary: <span style='color: #22c55e;'>✓ {passed} passed</span> "
            f"<span style='color: #f59e0b;'>⚠ {warnings} warnings</span> "
            f"<span style='color: #ef4444;'>✗ {failures} failed</span>"
        )
        self.summary_label.setText(summary_text)

        # Populate results
        self.results_list.clear()

        for violation in result.violations:
            item = QListWidgetItem()
            widget = self._create_violation_widget(violation)
            item.setSizeHint(widget.sizeHint())
            self.results_list.addItem(item)
            self.results_list.setItemWidget(item, widget)

    def _create_violation_widget(self, violation: Violation) -> QWidget:
        """Create a widget for a single violation."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Header with icon and severity
        header = QHBoxLayout()
        if violation.severity == "violation":
            icon = QLabel("✗")
            icon.setStyleSheet("color: #ef4444; font-size: 16px;")
        elif violation.severity == "warning":
            icon = QLabel("⚠")
            icon.setStyleSheet("color: #f59e0b; font-size: 16px;")
        else:
            icon = QLabel("ℹ")
            icon.setStyleSheet("color: #3b82f6; font-size: 16px;")

        header.addWidget(icon)
        header.addWidget(QLabel(violation.shape_name))
        header.addStretch()
        layout.addLayout(header)

        # Details
        details = QLabel(f"Focus: {violation.focus_node}\n{violation.message}")
        details.setStyleSheet("color: #4b5563; font-size: 12px;")
        layout.addWidget(details)

        return widget
```

#### Data Requirements
- PySHACL ValidationResult object
- Integration with `converter/validation/shacl_validator.py`

---

### Component 6: UploadProgressView

**Location**: `src/mhm_pipeline/gui/widgets/upload_progress_view.py`
**Used In**: `WikidataPanel` (Stage 6)

#### Purpose
Show real-time progress of Wikidata uploads with per-entity status, links to created items, and retry capability for failed uploads.

#### Visual Design
```
┌─ Wikidata Upload Progress ───────────────────┐
│                                              │
│ Overall: ████████████░░░░ 60% (3/5 items)   │
│                                              │
│ Entity Uploads:                              │
│ ┌──────────────────────────────────────────┐ │
│ │ Manuscript: Torah Commentary (ms:001)   │ │
│ │ ████████████ 100% ✓ Q1234567890         │ │
│ │ [View on Wikidata] [Edit]               │ │
│ ├──────────────────────────────────────────┤ │
│ │ Person: Shlomo ben David               │ │
│ │ ████████████ 100% ✓ Q1234567891         │ │
│ │ [View on Wikidata] [Edit]               │ │
│ ├──────────────────────────────────────────┤ │
│ │ Place: Jerusalem                        │ │
│ │ ████████████ 100% ✓ Q1234567892 (exists)│ │
│ │ [View on Wikidata] [Already exists]     │ │
│ ├──────────────────────────────────────────┤ │
│ │ Work: Genesis Commentary                │ │
│ │ ████████░░░░░░ 50% ⟳ Uploading...       │ │
│ │ [Cancel]                                 │ │
│ ├──────────────────────────────────────────┤ │
│ │ Person: Unknown Scribe                  │ │
│ │ ✗ Failed: API timeout                   │ │
│ │ [Retry] [Skip] [View Error]             │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ [Export QuickStatements] [Pause] [Cancel]    │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class UploadProgressView(QWidget):
    """Widget showing Wikidata upload progress."""

    STATUS_ICONS = {
        "pending": "⏳",
        "uploading": "⟳",
        "success": "✓",
        "exists": "✓",
        "failed": "✗",
        "skipped": "⊘",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.overall_progress = QProgressBar()

    def add_entity(self, entity: WikidataEntity) -> EntityProgressWidget:
        """Add an entity to the upload queue.

        Returns:
            EntityProgressWidget for updating status
        """
        widget = EntityProgressWidget(entity)
        self.layout.addWidget(widget)
        return widget

    def update_overall_progress(self, completed: int, total: int) -> None:
        """Update the overall progress bar."""
        self.overall_progress.setMaximum(total)
        self.overall_progress.setValue(completed)


class EntityProgressWidget(QWidget):
    """Widget showing progress for a single entity upload."""

    def __init__(self, entity: WikidataEntity, parent=None):
        super().__init__(parent)
        self.entity = entity

        layout = QVBoxLayout(self)

        # Header row
        header = QHBoxLayout()
        self.status_icon = QLabel(self.STATUS_ICONS["pending"])
        header.addWidget(self.status_icon)

        header.addWidget(QLabel(f"{entity.type}: {entity.label}"))
        header.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        header.addWidget(self.progress_bar)

        layout.addLayout(header)

        # Details row
        details = QHBoxLayout()
        self.qid_label = QLabel()
        details.addWidget(self.qid_label)

        self.view_button = QPushButton("View on Wikidata")
        self.view_button.setVisible(False)
        details.addWidget(self.view_button)

        self.retry_button = QPushButton("Retry")
        self.retry_button.setVisible(False)
        details.addWidget(self.retry_button)

        layout.addLayout(details)

    def set_status(self, status: str, qid: str | None = None) -> None:
        """Update the upload status.

        Args:
            status: One of pending, uploading, success, exists, failed
            qid: Wikidata QID if upload successful
        """
        self.status_icon.setText(self.STATUS_ICONS.get(status, "❓"))

        if status == "success" and qid:
            self.progress_bar.setValue(100)
            self.qid_label.setText(f"<a href='https://wikidata.org/wiki/{qid}'>{qid}</a>")
            self.qid_label.setOpenExternalLinks(True)
            self.view_button.setVisible(True)
        elif status == "failed":
            self.retry_button.setVisible(True)

```

#### Data Requirements
- Wikidata upload API responses
- Entity metadata (label, type, local ID)

---

### Component 7: StageDiffWidget

**Location**: `src/mhm_pipeline/gui/widgets/stage_diff_widget.py`
**Used In**: All panels (optional enhancement)

#### Purpose
Show before/after comparison for any stage, highlighting added, modified, or removed data.

#### Visual Design
```
┌─ Stage 2: NER Diff ──────────────────────────┐
│                                              │
│ Record: 3 of 50                              │
│ [← Prev] [Next →]  [Jump to #___]            │
│                                              │
│ ┌─ Before (Stage 1 Output) ─┬─ After (Stage 2)─┐
│ │ notes: [                 │ notes: [         │
│ │   "Written by scribe      │   {             │
│ │    Shlomo ben David"     │     "text": "...",│
│ │ ]                         │     "entities": [│
│ │                           │       {          │
│ │                           │         "text":  │
│ │                           │         "Shlomo",│
│ │                           │         "type":  │
│ │                           │         "PERSON",│
│ │                           │         "role": │
│ │                           │         "SCRIBE"│
│ │                           │       }          │
│ │                           │     ]            │
│ │                           │   }              │
│ │                           │ ]                │
│ └───────────────────────────┴──────────────────┘
│                                              │
│ Changes: +5 entities added                   │
│ [View Full JSON] [Export Diff]               │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class StageDiffWidget(QWidget):
    """Widget showing before/after diff between pipeline stages."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Splitter for side-by-side view
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Before panel
        self.before_panel = QGroupBox("Before (Input)")
        self.before_text = QTextEdit()
        self.before_text.setReadOnly(True)
        before_layout = QVBoxLayout(self.before_panel)
        before_layout.addWidget(self.before_text)

        # After panel
        self.after_panel = QGroupBox("After (Output)")
        self.after_text = QTextEdit()
        self.after_text.setReadOnly(True)
        after_layout = QVBoxLayout(self.after_panel)
        after_layout.addWidget(self.after_text)

        self.splitter.addWidget(self.before_panel)
        self.splitter.addWidget(self.after_panel)
        self.splitter.setSizes([400, 400])

    def load_comparison(self, before: dict, after: dict) -> None:
        """Load before and after data for comparison.

        Args:
            before: Data before stage processing
            after: Data after stage processing
        """
        # Format as JSON with syntax highlighting
        before_json = json.dumps(before, indent=2, ensure_ascii=False)
        after_json = json.dumps(after, indent=2, ensure_ascii=False)

        # Apply syntax highlighting
        self.before_text.setHtml(self._highlight_json(before_json))
        self.after_text.setHtml(self._highlight_diff(before_json, after_json))

    def _highlight_diff(self, before: str, after: str) -> str:
        """Highlight differences between two JSON strings."""
        # Simple diff: highlight added lines in green
        before_lines = set(before.split('\n'))

        html_parts = ['<pre style="font-family: monospace;">']
        for line in after.split('\n'):
            if line not in before_lines:
                # New line - highlight in green
                html_parts.append(
                    f'<span style="background-color: #dcfce7; color: #166534; '
                    f'display: block;">{escape(line)}</span>'
                )
            else:
                html_parts.append(escape(line))
        html_parts.append('</pre>')

        return ''.join(html_parts)
```

---

### Component 8: PipelineFlowWidget

**Location**: `src/mhm_pipeline/gui/widgets/pipeline_flow_widget.py`
**Used In**: `MainWindow` (new top panel)

#### Purpose
Provide an always-visible overview of the entire pipeline with animated flow indication showing which stage is currently active.

#### Visual Design
```
┌─ Pipeline Flow ──────────────────────────────┐
│                                              │
│    [Parse]──[NER]──[Authority]──[RDF]      │
│       ↓        ↓        ↓         ↓         │
│    ┌────┐   ┌────┐   ┌────┐    ┌────┐    │
│    │ 01 │──→│ 02 │──→│ 03 │──→ │ 04 │    │
│    │ 47 │   │ 12 │   │ 08 │    │ 87 │    │
│    │ fld│   │ ent│   │ mt │    │ tri│    │
│    └────┘   └────┘   └────┘    └────┘    │
│       ║        ║        ║         ║         │
│    [Validate]══[Upload]                    │
│       ↓        ↓                             │
│    ┌────┐   ┌────┐                          │
│    │ 05 │──→│ 06 │                          │
│    │ 2w │   │ 3/5│                          │
│    └────┘   └────┘                          │
│                                              │
│ Currently: Stage 2 - NER Extraction          │
│ Record 23 of 50: Processing note field...    │
│ ████████████░░░░░░░░ 60%                     │
│                                              │
│ [Pause] [Cancel] [Skip to Stage ▼]          │
└──────────────────────────────────────────────┘
```

#### Implementation Details
```python
class PipelineFlowWidget(QWidget):
    """Widget showing animated pipeline flow overview."""

    STAGE_CONFIG = [
        {"name": "Parse", "icon": "📄", "color": "#f59e0b"},
        {"name": "NER", "icon": "🔍", "color": "#eab308"},
        {"name": "Authority", "icon": "🔗", "color": "#8b5cf6"},
        {"name": "RDF", "icon": "🕸️", "color": "#3b82f6"},
        {"name": "Validate", "icon": "✓", "color": "#22c55e"},
        {"name": "Upload", "icon": "☁️", "color": "#ef4444"},
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        self.stage_buttons: list[QPushButton] = []
        self.stage_labels: list[QLabel] = []
        self.current_stage = -1

        self._build_ui()

        # Animation timer for active stage
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self._animate_active_stage)
        self.anim_frame = 0

    def _build_ui(self) -> None:
        """Build the pipeline flow UI."""
        layout = QHBoxLayout(self)

        for i, config in enumerate(self.STAGE_CONFIG):
            # Stage box
            stage_widget = QWidget()
            stage_layout = QVBoxLayout(stage_widget)

            # Icon button
            btn = QPushButton(f"{config['icon']}\n{config['name']}")
            btn.setFixedSize(80, 60)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: #f3f4f6;
                    border: 2px solid {config['color']};
                    border-radius: 8px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: #e5e7eb;
                }}
            """)
            btn.clicked.connect(lambda checked, idx=i: self.stage_clicked.emit(idx))

            # Stats label
            lbl = QLabel("Waiting...")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size: 10px; color: #6b7280;")

            stage_layout.addWidget(btn)
            stage_layout.addWidget(lbl)

            layout.addWidget(stage_widget)
            self.stage_buttons.append(btn)
            self.stage_labels.append(lbl)

            # Arrow (except last)
            if i < len(self.STAGE_CONFIG) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("font-size: 20px; color: #9ca3af;")
                layout.addWidget(arrow)

        layout.addStretch()

    def set_active_stage(self, stage_index: int) -> None:
        """Set which stage is currently active.

        Args:
            stage_index: 0-5 for stages 1-6
        """
        self.current_stage = stage_index

        for i, btn in enumerate(self.stage_buttons):
            if i < stage_index:
                # Completed stage
                btn.setStyleSheet(btn.styleSheet().replace(
                    "#f3f4f6", "#dcfce7"
                ))
            elif i == stage_index:
                # Active stage - will be animated
                self.anim_timer.start(500)
            else:
                # Future stage
                pass

    def update_stage_stats(self, stage_index: int, stats: str) -> None:
        """Update the statistics display for a stage.

        Args:
            stage_index: Stage to update (0-5)
            stats: Statistics string (e.g., "47 fields")
        """
        self.stage_labels[stage_index].setText(stats)

    def _animate_active_stage(self) -> None:
        """Animate the active stage button."""
        if self.current_stage < 0:
            return

        btn = self.stage_buttons[self.current_stage]
        colors = ["#fef3c7", "#fde68a"]  # Light amber shades
        btn.setStyleSheet(btn.styleSheet().replace(
            colors[self.anim_frame % 2], colors[(self.anim_frame + 1) % 2]
        ))
        self.anim_frame += 1

    # Signal emitted when user clicks a stage
    stage_clicked = pyqtSignal(int)
```

---

## 3. Integration Plan

### Modified Files

#### Panel Files (add visualization widgets)
```
src/mhm_pipeline/gui/panels/
├── convert_panel.py    # Add MarcFieldVisualizer
├── ner_panel.py        # Add EntityHighlighter
├── authority_panel.py  # Add AuthorityMatcherView
├── rdf_panel.py        # Add TripleGraphView
├── validate_panel.py   # Add ValidationResultView
└── wikidata_panel.py   # Add UploadProgressView
```

#### New Widget Files
```
src/mhm_pipeline/gui/widgets/
├── marc_field_visualizer.py      # Component 1
├── entity_highlighter.py         # Component 2
├── authority_matcher_view.py     # Component 3
├── triple_graph_view.py          # Component 4
├── validation_result_view.py     # Component 5
├── upload_progress_view.py       # Component 6
├── stage_diff_widget.py          # Component 7
└── pipeline_flow_widget.py       # Component 8
```

#### Main Window (add overview)
```
src/mhm_pipeline/gui/main_window.py
# Add PipelineFlowWidget to top of central widget
```

### Data Flow Integration

```
Stage 1: Parse
├── Input: MARC record
├── Widget: MarcFieldVisualizer
├── Method: load_from_extracted_data(ExtractedData)
└── Trigger: After UnifiedReader processes each record

Stage 2: NER
├── Input: ExtractedData.notes
├── Widget: EntityHighlighter
├── Method: load_entities(text, entities)
└── Trigger: After JointNERPipeline returns results

Stage 3: Authority
├── Input: List[Entity]
├── Widget: AuthorityMatcherView
├── Method: add_match(entity, authority_match)
└── Trigger: After each authority matcher completes

Stage 4: RDF
├── Input: rdflib Graph
├── Widget: TripleGraphView
├── Method: load_from_graph(graph)
└── Trigger: After GraphBuilder serializes

Stage 5: Validate
├── Input: ValidationResult
├── Widget: ValidationResultView
├── Method: load_results(result)
└── Trigger: After ShaclValidator.validate()

Stage 6: Upload
├── Input: Upload status events
├── Widget: UploadProgressView
├── Method: add_entity() + set_status()
└── Trigger: Real-time from Wikidata API calls
```

---

## 4. Implementation Phases

### Phase 1: Core Visualizations (High Priority)
1. **MarcFieldVisualizer** - Shows immediate feedback on parsing
2. **EntityHighlighter** - Visual proof of NER working
3. **ValidationResultView** - Critical for data quality

### Phase 2: Relationship Visualizations (Medium Priority)
4. **AuthorityMatcherView** - Shows entity linking
5. **TripleGraphView** - Shows RDF structure

### Phase 3: Progress & Overview (Lower Priority)
6. **UploadProgressView** - Real-time upload feedback
7. **StageDiffWidget** - Historical comparison
8. **PipelineFlowWidget** - Overall pipeline status

---

## 5. Technical Considerations

### Performance
- All visualizations should be **lazy-loaded** - only when panel is visible
- Large graphs (>1000 triples) should use **sampling** or **clustering**
- Text highlighting should use **efficient HTML generation**, not regex

### Threading
- Widgets must be **thread-safe** - only update from main thread
- Use **signals/slots** for worker thread communication
- Expensive rendering should use **QThread** or **asyncio**

### Memory
- Visualization widgets should **clear data** when switching records
- Graph views should implement **viewport culling** for large graphs
- Cache rendered images to avoid re-rendering

### Testing
```python
# Each widget should have:
- Unit tests for data loading
- UI tests for interactions
- Performance tests for large datasets
```

---

## 6. UI/UX Guidelines

### Color Palette (from Project Standards)
- Primary: `#4a9eed` (blue)
- Success: `#22c55e` (green)
- Warning: `#f59e0b` (amber)
- Error: `#ef4444` (red)
- Neutral: `#6b7280` (gray)

### Typography
- Headings: 14-16px, bold
- Body: 12-13px, regular
- Labels: 11px, medium weight
- Monospace (code): 12px, Courier or system mono

### Spacing
- Widget padding: 12-16px
- Between elements: 8-12px
- Section dividers: 1px solid `#e5e7eb`

### Icons
- Use **Emoji** (no external dependencies)
- Fallback to text labels on hover
- Status indicators: ✓ ✗ ⚠ ℹ ⟳

---

## 7. Future Enhancements

### Post-Implementation Ideas
1. **Export visualizations** as PNG/SVG/PDF
2. **Record-level comparison** - Compare two manuscripts side-by-side
3. **Search/filter** within visualizations
4. **Bookmark/favorite** entities for quick access
5. **Collaborative annotations** - Add manual corrections
6. **Time-travel** - View pipeline state at any point in history
7. **3D graph view** for complex RDF graphs

---

## 8. Related Documentation

- `ProjectDefinitionDocument.tex` Section 3: System Architecture
- `SystemDesignDocument.tex` Section 5: GUI Design
- `CLAUDE.md` Project-specific guidelines
- `ontology/hebrew-manuscripts.ttl` HMO ontology for graph visualization
- `ontology/shacl-shapes.ttl` Validation shapes

---

## 9. Acceptance Criteria

The GUI improvements are complete when:

1. [ ] All 6 stage panels have **visual feedback widgets**
2. [ ] Users can see **what changed** at each stage
3. [ ] Visualizations are **intuitive** (tested with 3 users)
4. [ ] Performance is **acceptable** (< 1s to load 100 records)
5. [ ] All widgets follow **project coding standards**
6. [ ] `SystemDesignDocument.tex` is **updated** to reflect new GUI
7. [ ] Tests cover **at least 80%** of new code

---

*Document Version: 1.0*
*Last Updated: 2026-03-28*
*Author: Claude Code*
