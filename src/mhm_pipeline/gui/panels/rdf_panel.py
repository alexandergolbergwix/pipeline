"""Stage 4 — RDF serialisation panel with interactive graph viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mhm_pipeline.gui.widgets.file_selector import FileSelector
from mhm_pipeline.gui.widgets.knowledge_graph_view import KnowledgeGraphView
from mhm_pipeline.gui.widgets.log_viewer import LogViewer
from mhm_pipeline.gui.widgets.percent_progress import PercentProgressWidget
from mhm_pipeline.gui.widgets.ttl_preview import TtlPreview


class RdfPanel(QWidget):
    """Panel for Stage 4: RDF graph serialisation with interactive viewer."""

    run_requested = pyqtSignal(Path, Path, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        # file selectors
        self._input_selector = FileSelector(
            "Enriched JSON:", mode="open", filter="JSON files (*.json)"
        )
        self._output_selector = FileSelector("Output Dir:", mode="directory")
        layout.addWidget(self._input_selector)
        layout.addWidget(self._output_selector)

        # serialisation format
        fmt_layout = QHBoxLayout()
        fmt_layout.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        self._format_combo.addItems(["Turtle", "JSON-LD", "N-Triples"])
        fmt_layout.addWidget(self._format_combo)
        fmt_layout.addStretch()
        layout.addLayout(fmt_layout)

        # Buttons row
        btn_layout = QHBoxLayout()
        self._run_btn = QPushButton("Build RDF Graph")
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._load_btn = QPushButton("Load Results")
        self._load_btn.setToolTip("Load a previously generated TTL file")
        self._load_btn.clicked.connect(self._on_load_results)
        btn_layout.addWidget(self._load_btn)

        self._fullscreen_btn = QPushButton("Open in Full Window")
        self._fullscreen_btn.clicked.connect(self._on_open_fullscreen)
        self._fullscreen_btn.setEnabled(False)
        btn_layout.addWidget(self._fullscreen_btn)

        layout.addLayout(btn_layout)

        # Progress bar
        self._progress = PercentProgressWidget()
        layout.addWidget(self._progress)

        # Tabbed results: TTL Preview + Interactive Graph
        self._results_tabs = QTabWidget()
        self._results_tabs.setDocumentMode(True)

        self._preview = TtlPreview()
        self._results_tabs.addTab(self._preview, "TTL Preview")

        self._graph_view = KnowledgeGraphView()
        self._results_tabs.addTab(self._graph_view, "Interactive Graph")

        layout.addWidget(self._results_tabs, stretch=3)

        # log viewer
        self._log_viewer = LogViewer()
        layout.addWidget(self._log_viewer, stretch=1)

        self._current_ttl_path: Path | None = None

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def log_viewer(self) -> LogViewer:
        return self._log_viewer

    @property
    def preview(self) -> TtlPreview:
        return self._preview

    @property
    def stage_progress(self) -> PercentProgressWidget:
        return self._progress

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        input_path = self._input_selector.path
        output_path = self._output_selector.path
        if input_path is None:
            self._log_viewer.append_line("Error: select an enriched JSON file first.")
            return
        if output_path is None:
            output_path = input_path.parent
            self._output_selector.path = output_path
        self.run_requested.emit(input_path, output_path, self._format_combo.currentText())

    def _on_load_results(self) -> None:
        from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Load RDF File",
            "",
            "Turtle files (*.ttl);;All files (*)",
        )
        if path_str:
            self.display_graph(Path(path_str))

    def _on_open_fullscreen(self) -> None:
        if not self._current_ttl_path:
            QMessageBox.information(
                self,
                "No Results",
                "No RDF graph loaded. Build RDF or load results first.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"RDF Knowledge Graph — {self._current_ttl_path.name}")

        screen = self.screen()
        if screen:
            geom = screen.availableGeometry()
            dialog.resize(geom.width() * 9 // 10, geom.height() * 9 // 10)
        else:
            dialog.resize(1200, 800)

        dlg_layout = QVBoxLayout(dialog)

        # Reuse the existing SQLite store via its DB path (avoids re-parsing TTL)
        from mhm_pipeline.gui.widgets.graph_store import GraphStore  # noqa: PLC0415

        full_graph = KnowledgeGraphView()
        existing_store = getattr(self._graph_view, "_store", None)
        if existing_store is not None:
            # Open a read-only connection to the same SQLite DB
            full_graph._store = GraphStore(existing_store.db_path)
            full_graph._ensure_web_view()
            if full_graph._web_view is not None:
                full_graph._render_summary()
        else:
            full_graph.load_from_file(self._current_ttl_path)
        dlg_layout.addWidget(full_graph, stretch=1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        dlg_layout.addWidget(close_btn)

        dialog.exec()

    def display_graph(self, ttl_path: Path) -> None:
        """Load and display the RDF graph from a TTL file."""
        self._current_ttl_path = ttl_path
        self._graph_view.load_from_file(ttl_path)
        self._preview.load_file(ttl_path)
        self._fullscreen_btn.setEnabled(True)
        self._results_tabs.setCurrentIndex(1)  # Switch to Interactive Graph tab
        self._log_viewer.append_line(f"Loaded RDF graph from {ttl_path}")
