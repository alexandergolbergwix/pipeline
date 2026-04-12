"""Main window for the MARC to TTL converter application."""

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..authority.mazal_matcher import create_matcher
from ..parser.unified_reader import UnifiedReader
from ..transformer.mapper import MarcToRdfMapper
from ..validation.shacl_validator import ShaclValidator
from .sparql_explorer import SparqlExplorer
from .widgets import FileSelector, LogViewer, ProgressWidget, TtlPreview, ValidationReport


class ConversionWorker(QThread):
    """Background worker for MARC to TTL conversion."""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, bool, str, dict)  # ttl, passed, report, stats
    error = pyqtSignal(str)
    log = pyqtSignal(str, str)

    # Path to ontology file
    ONTOLOGY_PATH = Path(__file__).parent.parent.parent / "ontology" / "hebrew-manuscripts.ttl"

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        validate: bool = True,
        include_ontology: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path
        self.validate = validate
        self.include_ontology = include_ontology
        self._cancelled = False
        self.mazal_matcher = None

    def run(self):
        try:
            self.log.emit(f"Starting conversion of {self.input_path.name}", "info")
            self.progress.emit(5, "Initializing Mazal authority matcher...")

            # Initialize Mazal authority matcher
            self.mazal_matcher = create_matcher()
            if self.mazal_matcher and self.mazal_matcher._available:
                self.log.emit("✓ Mazal authority integration enabled (NLI lookup)", "success")
            else:
                self.log.emit("⚠ Mazal authority index not available - using local URIs", "warning")

            self.progress.emit(10, "Reading input file...")

            reader = UnifiedReader(self.input_path)

            try:
                total_records = reader.count_records()
                self.log.emit(
                    f"Found {total_records} records (format: {reader.detected_format.value})",
                    "info",
                )
            except:
                total_records = 0

            self.progress.emit(20, "Converting records...")

            mapper = MarcToRdfMapper(mazal_matcher=self.mazal_matcher)

            records_processed = 0
            for record in reader.read_file():
                if self._cancelled:
                    self.log.emit("Conversion cancelled", "warning")
                    return

                try:
                    mapper.map_record(record)
                    records_processed += 1

                    if total_records > 0:
                        progress = 20 + int((records_processed / total_records) * 50)
                        self.progress.emit(
                            progress, f"Converted {records_processed}/{total_records} records"
                        )
                except Exception as e:
                    self.log.emit(
                        f"Error converting record {record.control_number}: {e}", "warning"
                    )

            self.progress.emit(70, "Generating TTL output...")

            from rdflib import Graph

            from ..config.namespaces import bind_namespaces

            combined_graph = Graph()
            bind_namespaces(combined_graph)

            # Optionally include ontology definitions
            if self.include_ontology and self.ONTOLOGY_PATH.exists():
                self.log.emit("Including ontology definitions in output...", "info")
                try:
                    combined_graph.parse(str(self.ONTOLOGY_PATH), format="turtle")
                    self.log.emit(f"Loaded ontology with {len(combined_graph)} triples", "info")
                except Exception as e:
                    self.log.emit(f"Warning: Could not load ontology: {e}", "warning")

            for record in reader.read_file():
                try:
                    record_graph = mapper.map_record(record)
                    for triple in record_graph:
                        combined_graph.add(triple)
                except:
                    pass

            self.progress.emit(80, "Saving TTL file...")

            ttl_content = combined_graph.serialize(format="turtle")

            with open(self.output_path, "w", encoding="utf-8") as f:
                f.write(ttl_content)

            self.log.emit(f"Saved output to {self.output_path}", "success")

            # Collect statistics
            self.progress.emit(82, "Collecting statistics...")
            stats = self._collect_statistics(combined_graph, records_processed)

            validation_report = ""
            validation_passed = True

            if self.validate:
                self.progress.emit(90, "Validating output...")
                self.log.emit("Running SHACL validation...", "info")

                validator = ShaclValidator()
                result = validator.validate(combined_graph)

                validation_passed = result.conforms
                validation_report = result.to_report()
                stats["validation_issues"] = result.violation_count
                stats["validation_errors"] = len(result.get_violations_by_severity("Violation"))
                stats["validation_warnings"] = len(result.get_violations_by_severity("Warning"))

                if validation_passed:
                    self.log.emit("Validation passed", "success")
                else:
                    self.log.emit(f"Validation found {result.violation_count} issues", "warning")

                # Generate detailed validation report
                self.progress.emit(95, "Generating validation report...")
                report_path = result.to_detailed_report(
                    output_path=str(self.output_path), input_file=str(self.input_path), stats=stats
                )
                stats["validation_report_path"] = report_path
                self.log.emit(f"Saved detailed report to: {report_path}", "info")

            self.progress.emit(100, "Done!")
            self.finished.emit(ttl_content, validation_passed, validation_report, stats)

        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True

    def _collect_statistics(self, graph, records_processed: int) -> dict:
        """Collect statistics about the generated graph."""
        from rdflib import RDF, URIRef
        from rdflib.namespace import Namespace

        LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
        CIDOC = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
        Namespace("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#")
        NLI = Namespace("https://www.nli.org.il/en/authorities/")

        stats = {
            "records_processed": records_processed,
            "total_triples": len(graph),
            "output_path": str(self.output_path),
            "output_size_mb": self.output_path.stat().st_size / (1024 * 1024)
            if self.output_path.exists()
            else 0,
        }

        # Count entity types
        entity_counts = {}

        # Manuscripts (F4_Manifestation_Singleton)
        entity_counts["manuscripts"] = len(
            list(graph.subjects(RDF.type, LRMOO.F4_Manifestation_Singleton))
        )

        # Persons
        entity_counts["persons"] = len(list(graph.subjects(RDF.type, CIDOC.E21_Person)))

        # Organizations
        entity_counts["organizations"] = len(list(graph.subjects(RDF.type, CIDOC.E74_Group)))

        # Works
        entity_counts["works"] = len(list(graph.subjects(RDF.type, LRMOO.F1_Work)))

        # Expressions
        entity_counts["expressions"] = len(list(graph.subjects(RDF.type, LRMOO.F2_Expression)))

        # Places
        entity_counts["places"] = len(list(graph.subjects(RDF.type, CIDOC.E53_Place)))

        # Production events
        entity_counts["production_events"] = len(
            list(graph.subjects(RDF.type, CIDOC.E12_Production))
        )

        stats["entity_counts"] = entity_counts

        # Count unique predicates
        stats["unique_predicates"] = len(set(graph.predicates()))

        # Count unique subjects
        stats["unique_subjects"] = len(set(graph.subjects()))

        # Mazal integration statistics - count NLI URIs
        nli_uri_prefix = str(NLI)
        nli_entities = 0
        nli_persons = 0
        nli_places = 0
        nli_works = 0
        nli_orgs = 0

        for subj in set(graph.subjects()):
            if isinstance(subj, URIRef) and str(subj).startswith(nli_uri_prefix):
                nli_entities += 1
                # Determine type
                types = list(graph.objects(subj, RDF.type))
                for t in types:
                    if t == CIDOC.E21_Person:
                        nli_persons += 1
                    elif t == CIDOC.E53_Place:
                        nli_places += 1
                    elif t == LRMOO.F1_Work:
                        nli_works += 1
                    elif t == CIDOC.E74_Group:
                        nli_orgs += 1

        stats["mazal_stats"] = {
            "total_nli_entities": nli_entities,
            "nli_persons": nli_persons,
            "nli_places": nli_places,
            "nli_works": nli_works,
            "nli_organizations": nli_orgs,
            "matcher_available": self.mazal_matcher._available if self.mazal_matcher else False,
        }

        # Get matcher stats if available
        if self.mazal_matcher and hasattr(self.mazal_matcher, "get_stats"):
            matcher_stats = self.mazal_matcher.get_stats()
            stats["mazal_stats"].update(matcher_stats)

        return stats


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hebrew Manuscripts MARC to TTL Converter")
        self.setMinimumSize(1000, 700)

        self._worker: ConversionWorker | None = None

        self._setup_ui()
        self._setup_menu()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)

        # Create tab widget for main functionality
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QTabBar::tab {
                padding: 8px 20px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #0078d4;
                color: white;
            }
        """)

        # === CONVERTER TAB ===
        converter_tab = QWidget()
        converter_layout = QVBoxLayout(converter_tab)

        input_group = QGroupBox("Input")
        input_layout = QVBoxLayout(input_group)

        self.input_selector = FileSelector(
            label="Input File:",
            mode="file",
            file_filter="All Supported Files (*.mrc *.csv *.tsv);;MARC Files (*.mrc);;CSV Files (*.csv);;TSV Files (*.tsv);;All Files (*.*)",
        )
        self.input_selector.pathChanged.connect(self._on_input_changed)
        input_layout.addWidget(self.input_selector)

        self.output_label = QLabel("Output: (select input file)")
        self.output_label.setStyleSheet("color: #666; font-style: italic;")
        input_layout.addWidget(self.output_label)

        converter_layout.addWidget(input_group)

        options_layout = QHBoxLayout()

        self.validate_check = QCheckBox("Validate output with SHACL")
        self.validate_check.setChecked(True)
        options_layout.addWidget(self.validate_check)

        self.include_ontology_check = QCheckBox("Include ontology definitions")
        self.include_ontology_check.setChecked(False)
        self.include_ontology_check.setToolTip(
            "When checked, the output TTL will include all class and property definitions\n"
            "from the Hebrew Manuscripts Ontology. This makes the output self-contained\n"
            "and allows running SPARQL queries on both schema and data in Protégé."
        )
        options_layout.addWidget(self.include_ontology_check)

        options_layout.addStretch()

        self.convert_btn = QPushButton("Convert")
        self.convert_btn.setMinimumWidth(120)
        self.convert_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.convert_btn.clicked.connect(self._start_conversion)
        options_layout.addWidget(self.convert_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_conversion)
        options_layout.addWidget(self.cancel_btn)

        converter_layout.addLayout(options_layout)

        self.progress = ProgressWidget()
        converter_layout.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.log_viewer = LogViewer("Conversion Log")
        left_layout.addWidget(self.log_viewer)

        self.validation_report = ValidationReport()
        left_layout.addWidget(self.validation_report)

        splitter.addWidget(left_panel)

        self.ttl_preview = TtlPreview()
        splitter.addWidget(self.ttl_preview)

        splitter.setSizes([400, 600])

        converter_layout.addWidget(splitter, 1)

        self.tab_widget.addTab(converter_tab, "🔄 Converter")

        # === SPARQL EXPLORER TAB ===
        self.sparql_explorer = SparqlExplorer()
        self.tab_widget.addTab(self.sparql_explorer, "🔍 SPARQL Explorer")

        main_layout.addWidget(self.tab_widget)

        self.log_viewer.info("Ready. Select input MARC file and output location.")

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")

        open_action = QAction("Open MARC File...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _on_input_changed(self, path: str):
        """Update output path when input is selected."""
        if path:
            output_path = Path(path).with_suffix(".ttl")
            self.output_label.setText(f"Output: {output_path}")
            self.output_label.setStyleSheet("color: #333;")
        else:
            self.output_label.setText("Output: (select input file)")
            self.output_label.setStyleSheet("color: #666; font-style: italic;")

    def _get_output_path(self) -> Path | None:
        """Get the auto-generated output path."""
        input_path = self.input_selector.get_path()
        if input_path:
            return Path(input_path).with_suffix(".ttl")
        return None

    def _open_file(self):
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Input File",
            str(Path.home()),
            "All Supported Files (*.mrc *.csv *.tsv);;MARC Files (*.mrc);;CSV Files (*.csv);;TSV Files (*.tsv);;All Files (*.*)",
        )
        if path:
            self.input_selector.set_path(path)

    def _show_about(self):
        QMessageBox.about(
            self,
            "About",
            "Hebrew Manuscripts MARC to TTL Converter\n\n"
            "Version 1.5.0 (Ontology v1.5)\n\n"
            "Converts MARC bibliographic records to RDF/TTL format\n"
            "using the Hebrew Manuscripts Ontology.\n\n"
            "Features:\n"
            "• MARC/CSV/TSV to TTL conversion\n"
            "• Mazal Authority Integration (NLI URIs)\n"
            "• Ontology inclusion option\n"
            "• SHACL validation\n"
            "• Scholarly annotation support\n\n"
            "Author: Alexander Goldberg\n"
            "Supervisor: Prof. Gila Prebor",
        )

    def _start_conversion(self):
        input_path = self.input_selector.get_path()

        if not input_path:
            QMessageBox.warning(self, "Error", "Please select an input file.")
            return

        input_path = Path(input_path)
        output_path = self._get_output_path()

        if not input_path.exists():
            QMessageBox.warning(self, "Error", f"Input file not found: {input_path}")
            return

        self.log_viewer.clear()
        self.ttl_preview.clear()
        self.validation_report.clear()

        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self._worker = ConversionWorker(
            input_path,
            output_path,
            validate=self.validate_check.isChecked(),
            include_ontology=self.include_ontology_check.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.log.connect(self._on_log)
        self._worker.start()

    def _cancel_conversion(self):
        if self._worker:
            self._worker.cancel()
            self._worker.wait()
            self._worker = None

        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.reset()
        self.log_viewer.warning("Conversion cancelled by user")

    def _on_progress(self, value: int, status: str):
        self.progress.set_progress(value, status)

    def _on_finished(self, ttl_content: str, validation_passed: bool, report: str, stats: dict):
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        self.ttl_preview.set_content(ttl_content[:50000])

        if self.validate_check.isChecked():
            self.validation_report.set_result(validation_passed, report)

        # Log detailed statistics
        self.log_viewer.success("=" * 50)
        self.log_viewer.success("CONVERSION COMPLETE")
        self.log_viewer.success("=" * 50)

        # Output file info
        output_path = stats.get("output_path", "")
        output_size = stats.get("output_size_mb", 0)
        self.log_viewer.success(f"📁 Output saved to: {output_path}")
        self.log_viewer.info(f"   File size: {output_size:.2f} MB")

        # Triple count
        total_triples = stats.get("total_triples", 0)
        self.log_viewer.info(f"📊 Total triples: {total_triples:,}")

        # Entity statistics
        self.log_viewer.info("")
        self.log_viewer.info("Entity Statistics:")
        entities = stats.get("entity_counts", {})

        entity_labels = {
            "manuscripts": "📜 Manuscripts",
            "persons": "👤 Persons",
            "organizations": "🏛️ Organizations",
            "works": "📖 Works",
            "expressions": "📝 Expressions",
            "places": "📍 Places",
            "production_events": "🔨 Production Events",
        }

        for key, label in entity_labels.items():
            count = entities.get(key, 0)
            if count > 0:
                self.log_viewer.info(f"   {label}: {count:,}")

        # Other stats
        unique_subjects = stats.get("unique_subjects", 0)
        unique_predicates = stats.get("unique_predicates", 0)
        self.log_viewer.info(f"   🔗 Unique subjects: {unique_subjects:,}")
        self.log_viewer.info(f"   🔗 Unique predicates: {unique_predicates:,}")

        # Mazal integration statistics
        mazal_stats = stats.get("mazal_stats", {})
        if mazal_stats.get("matcher_available", False):
            self.log_viewer.info("")
            self.log_viewer.info("Mazal Authority Integration:")
            nli_total = mazal_stats.get("total_nli_entities", 0)
            if nli_total > 0:
                self.log_viewer.success(f"   🔗 NLI Authority URIs: {nli_total:,}")
                if mazal_stats.get("nli_persons", 0) > 0:
                    self.log_viewer.info(f"      👤 Persons: {mazal_stats['nli_persons']:,}")
                if mazal_stats.get("nli_places", 0) > 0:
                    self.log_viewer.info(f"      📍 Places: {mazal_stats['nli_places']:,}")
                if mazal_stats.get("nli_works", 0) > 0:
                    self.log_viewer.info(f"      📖 Works: {mazal_stats['nli_works']:,}")
                if mazal_stats.get("nli_organizations", 0) > 0:
                    self.log_viewer.info(
                        f"      🏛️ Organizations: {mazal_stats['nli_organizations']:,}"
                    )
            else:
                self.log_viewer.warning("   ⚠ No NLI matches found (using local URIs)")

            # Show lookup stats if available
            if "lookups" in mazal_stats:
                total_lookups = mazal_stats.get("lookups", 0)
                hits = mazal_stats.get("hits", 0)
                hit_rate = (hits / total_lookups * 100) if total_lookups > 0 else 0
                self.log_viewer.info(
                    f"   📊 Lookup stats: {hits:,}/{total_lookups:,} matches ({hit_rate:.1f}%)"
                )

        # Validation summary
        if self.validate_check.isChecked():
            validation_issues = stats.get("validation_issues", 0)
            if validation_passed:
                self.log_viewer.success("✅ Validation: PASSED")
            else:
                self.log_viewer.warning(f"⚠️ Validation: {validation_issues} issues found")

        self.log_viewer.success("")
        self.log_viewer.success("=" * 50)

        # Show summary dialog
        self._show_summary_dialog(stats, validation_passed)

        self._worker = None

    def _show_summary_dialog(self, stats: dict, validation_passed: bool):
        """Show a summary dialog after conversion."""
        output_path = stats.get("output_path", "")
        output_size = stats.get("output_size_mb", 0)
        total_triples = stats.get("total_triples", 0)
        records = stats.get("records_processed", 0)
        entities = stats.get("entity_counts", {})
        validation_errors = stats.get("validation_errors", 0)
        validation_warnings = stats.get("validation_warnings", 0)
        report_path = stats.get("validation_report_path", "")
        mazal_stats = stats.get("mazal_stats", {})

        validation_status = "✅ PASSED" if validation_passed else f"❌ {validation_errors} error(s)"
        if validation_warnings > 0:
            validation_status += f", {validation_warnings} warning(s)"

        # Mazal integration status
        mazal_section = ""
        if mazal_stats.get("matcher_available", False):
            nli_total = mazal_stats.get("total_nli_entities", 0)
            if nli_total > 0:
                mazal_section = f"""
<h3>🔗 Mazal Authority Integration</h3>
<table>
<tr><td><b>NLI Authority URIs:</b></td><td style="color: green;">{nli_total:,}</td></tr>
<tr><td>&nbsp;&nbsp;• Persons:</td><td>{mazal_stats.get("nli_persons", 0):,}</td></tr>
<tr><td>&nbsp;&nbsp;• Places:</td><td>{mazal_stats.get("nli_places", 0):,}</td></tr>
<tr><td>&nbsp;&nbsp;• Works:</td><td>{mazal_stats.get("nli_works", 0):,}</td></tr>
<tr><td>&nbsp;&nbsp;• Organizations:</td><td>{mazal_stats.get("nli_organizations", 0):,}</td></tr>
</table>
"""
            else:
                mazal_section = """
<h3>🔗 Mazal Authority Integration</h3>
<p style="color: orange;">No NLI matches found (using local URIs)</p>
"""
        else:
            mazal_section = """
<h3>🔗 Mazal Authority Integration</h3>
<p style="color: gray;">Not available (index not found)</p>
"""

        summary = f"""
<h2>{"✅" if validation_passed else "⚠️"} Conversion Complete</h2>

<h3>Output Files</h3>
<p><b>TTL File:</b> <code>{output_path}</code></p>
<p><b>Size:</b> {output_size:.2f} MB</p>
{f"<p><b>Validation Report:</b> <code>{report_path}</code></p>" if report_path else ""}

<h3>Statistics</h3>
<table>
<tr><td><b>Records processed:</b></td><td>{records:,}</td></tr>
<tr><td><b>Total triples:</b></td><td>{total_triples:,}</td></tr>
<tr><td><b>Manuscripts:</b></td><td>{entities.get("manuscripts", 0):,}</td></tr>
<tr><td><b>Persons:</b></td><td>{entities.get("persons", 0):,}</td></tr>
<tr><td><b>Works:</b></td><td>{entities.get("works", 0):,}</td></tr>
<tr><td><b>Expressions:</b></td><td>{entities.get("expressions", 0):,}</td></tr>
</table>

{mazal_section}

<h3>Validation</h3>
<p>{validation_status}</p>
"""

        msg = QMessageBox(self)
        msg.setWindowTitle("Conversion Complete")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(summary)
        msg.setIcon(QMessageBox.Icon.Information if validation_passed else QMessageBox.Icon.Warning)

        # Store buttons for later reference
        self._dialog_buttons = {}
        self._last_output_path = output_path

        # Add "Explore with SPARQL" button
        explore_btn = msg.addButton("🔍 Explore with SPARQL", QMessageBox.ButtonRole.ActionRole)
        self._dialog_buttons["explore"] = explore_btn

        # Add "Open Output Folder" button
        open_folder_btn = msg.addButton("Open Output Folder", QMessageBox.ButtonRole.ActionRole)
        self._dialog_buttons["folder"] = open_folder_btn

        # Add "View Report" button if report exists
        if report_path:
            view_report_btn = msg.addButton(
                "View Validation Report", QMessageBox.ButtonRole.ActionRole
            )
            self._dialog_buttons["report"] = view_report_btn

        msg.addButton(QMessageBox.StandardButton.Ok)

        msg.exec()

        import subprocess

        clicked = msg.clickedButton()

        if clicked == self._dialog_buttons.get("explore"):
            # Switch to SPARQL Explorer tab and load the output file
            self.tab_widget.setCurrentIndex(1)  # SPARQL Explorer is tab index 1
            # Load the TTL file into the explorer
            from rdflib import Graph

            try:
                graph = Graph()
                graph.parse(output_path, format="turtle")
                self.sparql_explorer.load_graph(graph, Path(output_path))
            except Exception as e:
                QMessageBox.warning(self, "Load Error", f"Could not load TTL for exploration:\n{e}")
        elif clicked == self._dialog_buttons.get("folder"):
            import sys as _sys

            if _sys.platform == "darwin":
                subprocess.run(["open", "-R", output_path])
            elif _sys.platform == "win32":
                subprocess.run(["explorer", "/select,", output_path])
            else:
                subprocess.run(["xdg-open", str(Path(output_path).parent)])
        elif clicked == self._dialog_buttons.get("report") and report_path:
            import sys as _sys

            if _sys.platform == "darwin":
                subprocess.run(["open", report_path])
            elif _sys.platform == "win32":
                import os

                os.startfile(report_path)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", report_path])

    def _on_error(self, error_msg: str):
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.reset()

        self.log_viewer.error(f"Conversion failed: {error_msg}")

        QMessageBox.critical(self, "Error", f"Conversion failed:\n\n{error_msg}")

        self._worker = None

    def _on_log(self, message: str, level: str):
        self.log_viewer.log(message, level)


def create_app():
    """Create and return the application instance."""
    app = QApplication(sys.argv)
    app.setApplicationName("MARC to TTL Converter")
    app.setOrganizationName("Hebrew Manuscripts Project")
    return app


def run_gui():
    """Run the GUI application."""
    app = create_app()
    window = MainWindow()
    window.show()
    return app.exec()
