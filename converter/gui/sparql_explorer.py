"""SPARQL Query Explorer widget for investigating TTL data."""

from pathlib import Path
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QTextEdit, QTableWidget, QTableWidgetItem, QSplitter,
    QLabel, QComboBox, QGroupBox, QFileDialog, QMessageBox,
    QHeaderView, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from rdflib import Graph


# Pre-built SPARQL queries for common operations
PRESET_QUERIES = {
    "-- Select a preset query --": "",
    
    # === BASIC STATISTICS ===
    "📊 Count all triples": """SELECT (COUNT(*) AS ?count)
WHERE {
  ?s ?p ?o
}""",

    "📊 All entity types with counts": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?type (COUNT(?entity) AS ?count)
WHERE {
  ?entity rdf:type ?type .
}
GROUP BY ?type
ORDER BY DESC(?count)""",

    "📊 All predicates used": """SELECT DISTINCT ?predicate (COUNT(?predicate) AS ?usage)
WHERE {
  ?s ?predicate ?o .
}
GROUP BY ?predicate
ORDER BY DESC(?usage)""",

    # === MAZAL/NLI AUTHORITY ===
    "🔗 Count NLI Authority entities": """SELECT (COUNT(DISTINCT ?entity) AS ?nli_count)
WHERE {
  ?entity ?p ?o .
  FILTER(CONTAINS(STR(?entity), "nli.org.il"))
}""",

    "🔗 NLI entities by type": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?type (COUNT(?entity) AS ?count)
WHERE {
  ?entity rdf:type ?type .
  FILTER(CONTAINS(STR(?entity), "nli.org.il"))
}
GROUP BY ?type
ORDER BY DESC(?count)""",

    "🔗 Sample NLI Persons with labels": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>

SELECT ?person ?label
WHERE {
  ?person rdf:type cidoc:E21_Person ;
          rdfs:label ?label .
  FILTER(CONTAINS(STR(?person), "nli.org.il"))
}
LIMIT 30""",

    "🔗 Compare NLI vs Local URIs": """PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT 
  (COUNT(DISTINCT ?nli_person) AS ?nli_persons)
  (COUNT(DISTINCT ?local_person) AS ?local_persons)
WHERE {
  {
    ?nli_person rdf:type cidoc:E21_Person .
    FILTER(CONTAINS(STR(?nli_person), "nli.org.il"))
  }
  UNION
  {
    ?local_person rdf:type cidoc:E21_Person .
    FILTER(CONTAINS(STR(?local_person), "ontology.org.il"))
  }
}""",

    # === MANUSCRIPTS ===
    "📜 Sample Manuscripts": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?manuscript ?label
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?label .
}
LIMIT 30""",

    "📜 Manuscripts with dimensions": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?manuscript ?label ?height ?width ?folios
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?label .
  OPTIONAL { ?manuscript hm:has_height_mm ?height }
  OPTIONAL { ?manuscript hm:has_width_mm ?width }
  OPTIONAL { ?manuscript hm:has_number_of_folios ?folios }
}
LIMIT 30""",

    "📜 Manuscripts with NLI authors": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?ms_label ?author_label
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?ms_label ;
              lrmoo:R4_embodies ?expression .
  ?expression lrmoo:R3_is_realised_in ?work .
  ?creation lrmoo:R16_created ?work ;
            cidoc:P14_carried_out_by ?author .
  ?author rdfs:label ?author_label .
  FILTER(CONTAINS(STR(?author), "nli.org.il"))
}
LIMIT 30""",

    "📜 Manuscripts by production place": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>
PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>

SELECT ?place ?place_label (COUNT(?ms) AS ?manuscript_count)
WHERE {
  ?ms rdf:type lrmoo:F4_Manifestation_Singleton .
  ?production cidoc:P108_has_produced ?ms ;
              cidoc:P7_took_place_at ?place .
  ?place rdfs:label ?place_label .
}
GROUP BY ?place ?place_label
ORDER BY DESC(?manuscript_count)
LIMIT 30""",

    # === WORKS & AUTHORS ===
    "📖 Works with author info": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?work_label ?author_label
WHERE {
  ?work rdf:type lrmoo:F1_Work ;
        rdfs:label ?work_label .
  ?creation lrmoo:R16_created ?work ;
            cidoc:P14_carried_out_by ?author .
  ?author rdfs:label ?author_label .
}
LIMIT 30""",

    "👤 Most prolific authors": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?author ?author_label (COUNT(?work) AS ?work_count)
WHERE {
  ?creation lrmoo:R16_created ?work ;
            cidoc:P14_carried_out_by ?author .
  ?author rdfs:label ?author_label .
}
GROUP BY ?author ?author_label
ORDER BY DESC(?work_count)
LIMIT 30""",

    "👤 Sample Persons with labels": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>

SELECT ?person ?label
WHERE {
  ?person rdf:type cidoc:E21_Person ;
          rdfs:label ?label .
}
LIMIT 30""",

    # === ORGANIZATIONS ===
    "🏛️ Organizations/Corporate Bodies": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>

SELECT ?org ?label
WHERE {
  ?org rdf:type cidoc:E74_Group ;
       rdfs:label ?label .
}
LIMIT 30""",

    # === PLACES ===
    "📍 All Places": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>

SELECT ?place ?label
WHERE {
  ?place rdf:type cidoc:E53_Place ;
         rdfs:label ?label .
}
ORDER BY ?label""",

    # === EXPRESSIONS ===
    "📝 Expressions with language": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>
PREFIX hm: <http://www.ontology.org.il/HebrewManuscripts/2025-12-06#>

SELECT ?expression ?label ?language
WHERE {
  ?expression rdf:type lrmoo:F2_Expression ;
              rdfs:label ?label .
  OPTIONAL { ?expression hm:has_language ?language }
}
LIMIT 30""",

    # === DATA QUALITY ===
    "⚠️ Manuscripts without Expressions": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?manuscript ?label
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?label .
  FILTER NOT EXISTS { ?manuscript lrmoo:R4_embodies ?expression }
}
LIMIT 30""",

    "⚠️ Persons without labels": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>

SELECT ?person
WHERE {
  ?person rdf:type cidoc:E21_Person .
  FILTER NOT EXISTS { ?person rdfs:label ?label }
}
LIMIT 30""",

    # === FULL-TEXT SEARCH ===
    "🔎 Search by label (edit query)": """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?entity ?label
WHERE {
  ?entity rdfs:label ?label .
  FILTER(CONTAINS(LCASE(STR(?label)), "רמב"))
}
LIMIT 30""",

    "🔎 Search manuscripts by title": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?manuscript ?label
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?label .
  FILTER(CONTAINS(LCASE(STR(?label)), "תורה"))
}
LIMIT 30""",

    # === ADVANCED ===
    "🔬 Work-Expression-Manuscript chain": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?work_label ?expr_label ?ms_label
WHERE {
  ?manuscript rdf:type lrmoo:F4_Manifestation_Singleton ;
              rdfs:label ?ms_label ;
              lrmoo:R4_embodies ?expression .
  ?expression rdfs:label ?expr_label ;
              lrmoo:R3_is_realised_in ?work .
  ?work rdfs:label ?work_label .
}
LIMIT 20""",

    "🔬 Production events with details": """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX cidoc: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/>

SELECT ?production ?manuscript ?ms_label ?place ?place_label ?date
WHERE {
  ?production rdf:type cidoc:E12_Production ;
              cidoc:P108_has_produced ?manuscript .
  ?manuscript rdfs:label ?ms_label .
  OPTIONAL { 
    ?production cidoc:P7_took_place_at ?place .
    ?place rdfs:label ?place_label .
  }
  OPTIONAL { ?production cidoc:P4_has_time-span ?timespan }
}
LIMIT 20""",
}


class QueryWorker(QThread):
    """Background worker for SPARQL query execution."""
    
    finished = pyqtSignal(list, list, float)  # headers, rows, time
    error = pyqtSignal(str)
    
    def __init__(self, graph: Graph, query: str, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.query = query
    
    def run(self):
        import time
        start = time.time()
        try:
            results = self.graph.query(self.query)
            
            # Extract headers
            headers = list(results.vars) if results.vars else []
            
            # Extract rows
            rows = []
            for row in results:
                rows.append([str(val) if val else "" for val in row])
            
            elapsed = time.time() - start
            self.finished.emit(headers, rows, elapsed)
            
        except Exception as e:
            self.error.emit(str(e))


class LoadWorker(QThread):
    """Background worker for loading TTL files."""
    
    progress = pyqtSignal(str)
    finished = pyqtSignal(Graph, int, float)  # graph, triple_count, time
    error = pyqtSignal(str)
    
    def __init__(self, file_path: Path, parent=None):
        super().__init__(parent)
        self.file_path = file_path
    
    def run(self):
        import time
        start = time.time()
        try:
            self.progress.emit("Parsing TTL file...")
            graph = Graph()
            graph.parse(str(self.file_path), format='turtle')
            
            elapsed = time.time() - start
            self.finished.emit(graph, len(graph), elapsed)
            
        except Exception as e:
            self.error.emit(str(e))


class SparqlExplorer(QWidget):
    """Widget for exploring TTL data with SPARQL queries."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.graph: Optional[Graph] = None
        self.current_file: Optional[Path] = None
        self._worker: Optional[QueryWorker] = None
        self._load_worker: Optional[LoadWorker] = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # File loading section
        file_group = QGroupBox("TTL File")
        file_layout = QHBoxLayout(file_group)
        
        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet("color: #888; font-style: italic;")
        file_layout.addWidget(self.file_label, 1)
        
        self.load_btn = QPushButton("Load TTL File...")
        self.load_btn.clicked.connect(self._load_file)
        file_layout.addWidget(self.load_btn)
        
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #333;")
        file_layout.addWidget(self.stats_label)
        
        layout.addWidget(file_group)
        
        # Main splitter
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Query section
        query_widget = QWidget()
        query_layout = QVBoxLayout(query_widget)
        query_layout.setContentsMargins(0, 0, 0, 0)
        
        # Preset queries
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Preset Queries:"))
        
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(PRESET_QUERIES.keys())
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self.preset_combo, 1)
        
        query_layout.addLayout(preset_layout)
        
        # Query editor
        self.query_editor = QTextEdit()
        self.query_editor.setPlaceholderText("Enter SPARQL query here...")
        self.query_editor.setFont(QFont("Menlo", 12))
        self.query_editor.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }
        """)
        query_layout.addWidget(self.query_editor)
        
        # Execute button
        btn_layout = QHBoxLayout()
        
        self.execute_btn = QPushButton("▶ Execute Query")
        self.execute_btn.setEnabled(False)
        self.execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.execute_btn.clicked.connect(self._execute_query)
        btn_layout.addWidget(self.execute_btn)
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear_query)
        btn_layout.addWidget(self.clear_btn)
        
        btn_layout.addStretch()
        
        self.status_label = QLabel("")
        btn_layout.addWidget(self.status_label)
        
        query_layout.addLayout(btn_layout)
        
        splitter.addWidget(query_widget)
        
        # Results section
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        
        results_header = QHBoxLayout()
        results_header.addWidget(QLabel("Results:"))
        
        self.results_count = QLabel("")
        self.results_count.setStyleSheet("color: #666;")
        results_header.addWidget(self.results_count)
        
        results_header.addStretch()
        
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_results)
        results_header.addWidget(self.export_btn)
        
        results_layout.addLayout(results_header)
        
        self.results_table = QTableWidget()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e1e;
                color: #e0e0e0;
                gridline-color: #444;
                border: 1px solid #333;
            }
            QTableWidget::item {
                padding: 4px;
                color: #e0e0e0;
                background-color: #1e1e1e;
            }
            QTableWidget::item:alternate {
                background-color: #2d2d2d;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #333;
                color: #ffffff;
                font-weight: bold;
                padding: 6px;
                border: 1px solid #444;
            }
        """)
        results_layout.addWidget(self.results_table)
        
        splitter.addWidget(results_widget)
        
        splitter.setSizes([300, 400])
        layout.addWidget(splitter, 1)
    
    def _load_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open TTL File",
            str(Path.home()),
            "Turtle Files (*.ttl);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        self.load_btn.setEnabled(False)
        self.file_label.setText(f"Loading: {Path(file_path).name}...")
        self.file_label.setStyleSheet("color: #0078d4;")
        
        self._load_worker = LoadWorker(Path(file_path))
        self._load_worker.progress.connect(self._on_load_progress)
        self._load_worker.finished.connect(lambda g, c, t: self._on_file_loaded(Path(file_path), g, c, t))
        self._load_worker.error.connect(self._on_load_error)
        self._load_worker.start()
    
    def _on_load_progress(self, message: str):
        self.file_label.setText(message)
    
    def _on_file_loaded(self, file_path: Path, graph: Graph, triple_count: int, elapsed: float):
        self.graph = graph
        self.current_file = file_path
        
        self.file_label.setText(f"📄 {file_path.name}")
        self.file_label.setStyleSheet("color: #4fc3f7; font-weight: bold;")
        
        size_mb = file_path.stat().st_size / (1024 * 1024)
        self.stats_label.setText(f"({triple_count:,} triples, {size_mb:.1f} MB, loaded in {elapsed:.1f}s)")
        
        self.load_btn.setEnabled(True)
        self.execute_btn.setEnabled(True)
        
        self._load_worker = None
    
    def _on_load_error(self, error: str):
        self.file_label.setText("Load failed")
        self.file_label.setStyleSheet("color: red;")
        self.load_btn.setEnabled(True)
        
        QMessageBox.critical(self, "Load Error", f"Failed to load TTL file:\n\n{error}")
        
        self._load_worker = None
    
    def _on_preset_selected(self, name: str):
        query = PRESET_QUERIES.get(name, "")
        if query:
            self.query_editor.setPlainText(query)
    
    def _execute_query(self):
        if not self.graph:
            QMessageBox.warning(self, "No Data", "Please load a TTL file first.")
            return
        
        query = self.query_editor.toPlainText().strip()
        if not query:
            QMessageBox.warning(self, "Empty Query", "Please enter a SPARQL query.")
            return
        
        self.execute_btn.setEnabled(False)
        self.status_label.setText("Executing...")
        self.status_label.setStyleSheet("color: #0078d4;")
        
        self._worker = QueryWorker(self.graph, query)
        self._worker.finished.connect(self._on_query_finished)
        self._worker.error.connect(self._on_query_error)
        self._worker.start()
    
    def _on_query_finished(self, headers: list, rows: list, elapsed: float):
        self.execute_btn.setEnabled(True)
        self.status_label.setText(f"✓ Completed in {elapsed:.2f}s")
        self.status_label.setStyleSheet("color: green;")
        
        # Populate table
        self.results_table.clear()
        self.results_table.setRowCount(len(rows))
        self.results_table.setColumnCount(len(headers))
        self.results_table.setHorizontalHeaderLabels([str(h) for h in headers])
        
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                item = QTableWidgetItem(value)
                # Highlight NLI URIs with a distinct color on dark background
                if "nli.org.il" in value:
                    item.setBackground(QColor("#1a3a4a"))
                    item.setForeground(QColor("#4fc3f7"))
                self.results_table.setItem(i, j, item)
        
        self.results_table.resizeColumnsToContents()
        self.results_count.setText(f"{len(rows)} row(s)")
        self.export_btn.setEnabled(len(rows) > 0)
        
        self._worker = None
    
    def _on_query_error(self, error: str):
        self.execute_btn.setEnabled(True)
        self.status_label.setText("✗ Error")
        self.status_label.setStyleSheet("color: red;")
        
        QMessageBox.critical(self, "Query Error", f"SPARQL query failed:\n\n{error}")
        
        self._worker = None
    
    def _clear_query(self):
        self.query_editor.clear()
        self.preset_combo.setCurrentIndex(0)
    
    def _export_results(self):
        if self.results_table.rowCount() == 0:
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Results",
            str(Path.home() / "query_results.csv"),
            "CSV Files (*.csv)"
        )
        
        if not file_path:
            return
        
        try:
            import csv
            with open(file_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # Headers
                headers = []
                for j in range(self.results_table.columnCount()):
                    headers.append(self.results_table.horizontalHeaderItem(j).text())
                writer.writerow(headers)
                
                # Rows
                for i in range(self.results_table.rowCount()):
                    row = []
                    for j in range(self.results_table.columnCount()):
                        item = self.results_table.item(i, j)
                        row.append(item.text() if item else "")
                    writer.writerow(row)
            
            QMessageBox.information(self, "Export Complete", f"Results exported to:\n{file_path}")
            
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n\n{e}")
    
    def load_graph(self, graph: Graph, file_path: Path):
        """Load a graph directly (e.g., after conversion)."""
        self.graph = graph
        self.current_file = file_path
        
        self.file_label.setText(f"📄 {file_path.name}")
        self.file_label.setStyleSheet("color: #333; font-weight: bold;")
        
        size_mb = file_path.stat().st_size / (1024 * 1024) if file_path.exists() else 0
        self.stats_label.setText(f"({len(graph):,} triples, {size_mb:.1f} MB)")
        
        self.execute_btn.setEnabled(True)

