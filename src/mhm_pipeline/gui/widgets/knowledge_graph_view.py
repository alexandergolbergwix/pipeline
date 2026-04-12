"""Interactive RDF knowledge graph viewer using Cytoscape.js in QWebEngineView.

Converts an ``rdflib.Graph`` to Cytoscape.js JSON, renders it in an embedded
Chromium widget, and communicates click events back to Python via QWebChannel.
"""

from __future__ import annotations

import json
import logging
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot

if TYPE_CHECKING:
    import rdflib

    from mhm_pipeline.gui.widgets.graph_store import GraphStore
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# ── Ontology type → display category mapping ────────────────────────────────

_TYPE_MAP: dict[str, str] = {
    # Manuscripts / Manifestations
    "Manuscript": "manuscript",
    "F4_Manifestation_Singleton": "manuscript",
    "F3_Manifestation": "manuscript",
    # Persons
    "E21_Person": "person",
    # Works
    "F1_Work": "work",
    "F24_Publication_Work": "work",
    # Expressions
    "F2_Expression": "expression",
    # Places
    "E53_Place": "place",
    # Codicological units
    "Codicological_Unit": "codicological_unit",
    "Bibliographic_Unit": "codicological_unit",
    "Paleographical_Unit": "codicological_unit",
    # Events
    "E12_Production": "event",
    "E8_Acquisition": "event",
    "E10_Transfer_of_Custody": "event",
    "F27_Work_Creation": "event",
    "E7_Activity": "event",
    "CreativeEvent": "event",
    # Organizations
    "E74_Group": "organization",
}

_NODE_COLORS: dict[str, dict[str, str]] = {
    "manuscript": {"bg": "#dbeafe", "border": "#3b82f6"},
    "person": {"bg": "#fce7f3", "border": "#ec4899"},
    "work": {"bg": "#dcfce7", "border": "#22c55e"},
    "expression": {"bg": "#ccfbf1", "border": "#14b8a6"},
    "place": {"bg": "#fef3c7", "border": "#eab308"},
    "codicological_unit": {"bg": "#ffedd5", "border": "#f97316"},
    "event": {"bg": "#ede9fe", "border": "#8b5cf6"},
    "organization": {"bg": "#e0e7ff", "border": "#6366f1"},
    "default": {"bg": "#f3f4f6", "border": "#6b7280"},
}


# ── Pure-function converter ──────────────────────────────────────────────────


class RdfToJsonConverter:
    """Convert an rdflib.Graph to Cytoscape.js JSON (nodes + edges).

    Literals are stored as properties on their subject node, not as
    separate graph nodes.  This dramatically reduces visual clutter.
    """

    @staticmethod
    def convert(graph: rdflib.Graph) -> dict[str, list[dict[str, object]]]:
        """Return ``{"nodes": [...], "edges": [...]}`` for Cytoscape.js."""
        from rdflib import RDF, RDFS, Literal  # noqa: PLC0415

        # Collect types and labels
        node_types: dict[str, str] = {}
        node_labels: dict[str, str] = {}
        node_props: dict[str, dict[str, list[str]]] = {}

        for s, p, o in graph:
            s_id = str(s)
            p_str = str(p)

            if isinstance(o, Literal):
                # Store literals as properties on the subject
                node_props.setdefault(s_id, {}).setdefault(_shorten_uri(p_str), []).append(str(o))
                if p == RDFS.label:
                    node_labels[s_id] = str(o)
                continue

            if p == RDF.type:
                local = _local_name(str(o))
                category = _TYPE_MAP.get(local, "default")
                # Keep the most specific non-default type
                if category != "default" or s_id not in node_types:
                    node_types[s_id] = category

        # Build node set (only URI/BNode subjects and objects that appear as endpoints)
        node_ids: set[str] = set()
        edges: list[dict[str, object]] = []

        for s, p, o in graph:
            if isinstance(o, Literal):
                continue
            s_id, o_id = str(s), str(o)
            node_ids.add(s_id)
            node_ids.add(o_id)
            if p != RDF.type:
                edges.append(
                    {
                        "data": {
                            "id": f"e_{len(edges)}",
                            "source": s_id,
                            "target": o_id,
                            "label": _shorten_uri(str(p)),
                        }
                    }
                )

        # Build nodes
        nodes: list[dict[str, object]] = []
        for nid in node_ids:
            ntype = node_types.get(nid, _infer_type_from_uri(nid))
            colors = _NODE_COLORS.get(ntype, _NODE_COLORS["default"])
            label = node_labels.get(nid, _local_name(nid))
            nodes.append(
                {
                    "data": {
                        "id": nid,
                        "label": label[:40],
                        "nodeType": ntype,
                        "bgColor": colors["bg"],
                        "borderColor": colors["border"],
                        "properties": node_props.get(nid, {}),
                    }
                }
            )

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def convert_summary(graph: rdflib.Graph) -> dict[str, list[dict[str, object]]]:
        """Build an aggregated summary: one node per type category.

        Shows ~10 nodes (one per ontology class), with edge counts between
        categories.  Much cheaper to render than the full graph.
        """
        from rdflib import RDF, Literal  # noqa: PLC0415

        # Classify nodes by type category
        node_to_cat: dict[str, str] = {}
        cat_counts: dict[str, int] = {}

        for s, p, o in graph.triples((None, RDF.type, None)):
            local = _local_name(str(o))
            cat = _TYPE_MAP.get(local, "default")
            s_id = str(s)
            # Prefer non-default
            if cat != "default" or s_id not in node_to_cat:
                node_to_cat[s_id] = cat

        for cat in node_to_cat.values():
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Count cross-category edges
        edge_counts: dict[tuple[str, str], int] = {}
        for s, p, o in graph:
            if isinstance(o, Literal) or p == RDF.type:
                continue
            s_cat = node_to_cat.get(str(s), "default")
            o_cat = node_to_cat.get(str(o), "default")
            if s_cat == o_cat:
                continue
            key = (s_cat, o_cat)
            edge_counts[key] = edge_counts.get(key, 0) + 1

        # Build summary nodes
        nodes: list[dict[str, object]] = []
        for cat, count in cat_counts.items():
            colors = _NODE_COLORS.get(cat, _NODE_COLORS["default"])
            nodes.append(
                {
                    "data": {
                        "id": f"cluster_{cat}",
                        "label": f"{cat.replace('_', ' ').title()}\n({count})",
                        "nodeType": cat,
                        "bgColor": colors["bg"],
                        "borderColor": colors["border"],
                        "properties": {"count": [str(count)]},
                        "isCluster": True,
                        "memberCount": count,
                    }
                }
            )

        # Build summary edges
        edges: list[dict[str, object]] = []
        for (src, tgt), count in edge_counts.items():
            edges.append(
                {
                    "data": {
                        "id": f"ce_{src}_{tgt}",
                        "source": f"cluster_{src}",
                        "target": f"cluster_{tgt}",
                        "label": str(count),
                    }
                }
            )

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def convert_neighborhood(
        graph: rdflib.Graph,
        center_uri: str,
        hops: int = 1,
    ) -> dict[str, list[dict[str, object]]]:
        """Extract the N-hop neighborhood around a single node."""
        from rdflib import RDF, RDFS, Literal, URIRef  # noqa: PLC0415

        URIRef(center_uri)
        visited: set[str] = {center_uri}
        frontier: set[str] = {center_uri}

        for _ in range(hops):
            next_frontier: set[str] = set()
            for uri_str in frontier:
                uri = URIRef(uri_str)
                for _s, _p, o in graph.triples((uri, None, None)):
                    if not isinstance(o, Literal):
                        o_str = str(o)
                        if o_str not in visited:
                            visited.add(o_str)
                            next_frontier.add(o_str)
                for s, _p, _o in graph.triples((None, None, uri)):
                    s_str = str(s)
                    if s_str not in visited:
                        visited.add(s_str)
                        next_frontier.add(s_str)
            frontier = next_frontier

        # Build subgraph for visited nodes only
        node_types: dict[str, str] = {}
        node_labels: dict[str, str] = {}
        node_props: dict[str, dict[str, list[str]]] = {}

        for s, p, o in graph:
            s_id = str(s)
            if s_id not in visited:
                continue
            if isinstance(o, Literal):
                node_props.setdefault(s_id, {}).setdefault(
                    _shorten_uri(str(p)),
                    [],
                ).append(str(o))
                if p == RDFS.label:
                    node_labels[s_id] = str(o)
                continue
            if p == RDF.type:
                local = _local_name(str(o))
                cat = _TYPE_MAP.get(local, "default")
                if cat != "default" or s_id not in node_types:
                    node_types[s_id] = cat

        edges: list[dict[str, object]] = []
        edge_nodes: set[str] = set()
        for s, p, o in graph:
            if isinstance(o, Literal) or p == RDF.type:
                continue
            s_id, o_id = str(s), str(o)
            if s_id in visited and o_id in visited:
                edge_nodes.add(s_id)
                edge_nodes.add(o_id)
                edges.append(
                    {
                        "data": {
                            "id": f"e_{len(edges)}",
                            "source": s_id,
                            "target": o_id,
                            "label": _shorten_uri(str(p)),
                        }
                    }
                )

        nodes: list[dict[str, object]] = []
        for nid in edge_nodes:
            ntype = node_types.get(nid, _infer_type_from_uri(nid))
            colors = _NODE_COLORS.get(ntype, _NODE_COLORS["default"])
            label = node_labels.get(nid, _local_name(nid))
            is_center = nid == center_uri
            nodes.append(
                {
                    "data": {
                        "id": nid,
                        "label": label[:40],
                        "nodeType": ntype,
                        "bgColor": colors["bg"],
                        "borderColor": "#f59e0b" if is_center else colors["border"],
                        "properties": node_props.get(nid, {}),
                    }
                }
            )

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def get_members_of_type(
        graph: rdflib.Graph,
        category: str,
    ) -> list[tuple[str, str]]:
        """Return (uri, label) pairs for all nodes of a given type category."""
        from rdflib import RDF, RDFS, Literal  # noqa: PLC0415

        members: list[tuple[str, str]] = []
        for s, _p, o in graph.triples((None, RDF.type, None)):
            local = _local_name(str(o))
            cat = _TYPE_MAP.get(local, "default")
            if cat == category:
                s_id = str(s)
                label = _local_name(s_id)
                # Try to find rdfs:label
                for _, _, lbl in graph.triples((s, RDFS.label, None)):
                    if isinstance(lbl, Literal):
                        label = str(lbl)
                        break
                members.append((s_id, label))
        return members


def _local_name(uri: str) -> str:
    """Extract the local name from a URI (after # or last /)."""
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rsplit("/", 1)[-1] if "/" in uri else uri


def _shorten_uri(uri: str) -> str:
    """Shorten a URI to a readable label."""
    local = _local_name(uri)
    return local.replace("_", " ")


def _infer_type_from_uri(uri: str) -> str:
    """Guess node type from URI patterns when no rdf:type is available."""
    uri_lower = uri.lower()
    for keyword, category in [
        ("person", "person"),
        ("ms_", "manuscript"),
        ("manuscript", "manuscript"),
        ("work", "work"),
        ("expression", "expression"),
        ("place", "place"),
        ("cu_", "codicological_unit"),
        ("event", "event"),
        ("creation", "event"),
        ("production", "event"),
        ("group", "organization"),
    ]:
        if keyword in uri_lower:
            return category
    return "default"


# ── QWebChannel bridge ──────────────────────────────────────────────────────


class _GraphBridge(QObject):
    """Bridge object exposed to JavaScript via QWebChannel."""

    node_selected = pyqtSignal(str, str)  # (node_id, json_properties)
    edge_selected = pyqtSignal(str, str)  # (edge_id, json_data)
    cluster_expand = pyqtSignal(str)  # (node_type category)

    @pyqtSlot(str, str)
    def onNodeSelected(self, node_id: str, properties_json: str) -> None:  # noqa: N802
        self.node_selected.emit(node_id, properties_json)

    @pyqtSlot(str, str)
    def onEdgeSelected(self, edge_id: str, data_json: str) -> None:  # noqa: N802
        self.edge_selected.emit(edge_id, data_json)

    @pyqtSlot(str)
    def onClusterExpand(self, node_type: str) -> None:  # noqa: N802
        self.cluster_expand.emit(node_type)


# ── Entity detail panel (Protégé-style) ─────────────────────────────────────


class _CollapsibleSection(QWidget):
    """A section with a clickable header that toggles content visibility."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._header = QPushButton(f"▼ {title}")
        self._header.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; font-size: 11px; "
            "border: none; padding: 4px 0; }"
        )
        self._header.clicked.connect(self._toggle)
        layout.addWidget(self._header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 0, 0, 4)
        self._body_layout.setSpacing(2)
        layout.addWidget(self._body)

        self._title = title
        self._expanded = True

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        self._header.setText(f"{arrow} {self._title}")

    def set_title(self, title: str) -> None:
        self._title = title
        arrow = "▼" if self._expanded else "▶"
        self._header.setText(f"{arrow} {title}")

    def clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)


class _EntityDetailPanel(QWidget):
    """Protégé-style entity detail panel with collapsible sections."""

    navigate_to_node = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Header
        self._title_label = QLabel("Click a node to inspect")
        self._title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._type_label = QLabel("")
        self._type_label.setWordWrap(True)
        layout.addWidget(self._type_label)

        self._uri_label = QLabel("")
        self._uri_label.setWordWrap(True)
        layout.addWidget(self._uri_label)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_content = QWidget()
        self._content_layout = QVBoxLayout(self._scroll_content)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setSpacing(8)
        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, stretch=1)

        # Sections (created once, reused)
        self._props_section = _CollapsibleSection("Properties")
        self._content_layout.addWidget(self._props_section)

        self._outgoing_section = _CollapsibleSection("Outgoing Relationships")
        self._content_layout.addWidget(self._outgoing_section)

        self._incoming_section = _CollapsibleSection("Incoming Relationships")
        self._content_layout.addWidget(self._incoming_section)

        self._authority_section = _CollapsibleSection("Authority Links")
        self._content_layout.addWidget(self._authority_section)

    _MAX_VISIBLE_ROWS = 15  # Show first N rows, then "Show more..."

    def show_entity(self, uri: str, store: GraphStore) -> None:
        """Load and display all data for the given entity URI."""
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        # Header
        label = store.get_node_label(uri)
        ntype = store.get_node_type(uri)
        nc = theme.node_color(ntype)

        self._title_label.setText(label)
        self._title_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {nc.text};")
        self._type_label.setText(
            f'<span style="background:{nc.bg}; color:{nc.text}; '
            f'padding:2px 8px; border-radius:3px; font-size:10px;">'
            f"{ntype.replace('_', ' ').title()}</span>"
        )
        self._uri_label.setText(
            f'<span style="color:{theme.ui("subtext")}; font-size:10px;">{_local_name(uri)}</span>'
        )

        # Properties
        props = store.get_node_properties(uri)
        self._props_section.clear_body()
        authority_links: list[tuple[str, str]] = []

        if props:
            self._props_section.set_title(f"Properties ({sum(len(v) for v in props.values())})")
            for key, values in props.items():
                for val in values:
                    if "viaf.org" in val or "wikidata.org" in val or val.startswith("987"):
                        authority_links.append((key, val))
                        continue
                    row = QLabel(
                        f'<span style="color:{theme.ui("subtext")}; font-size:10px;">{key}:</span> '
                        f'<span style="font-size:11px;">{val}</span>'
                    )
                    row.setWordWrap(True)
                    self._props_section.add_widget(row)
        else:
            self._props_section.set_title("Properties (0)")

        # Outgoing relationships (paginated)
        outgoing = store.get_outgoing_edges(uri)
        self._outgoing_section.clear_body()
        self._outgoing_section.set_title(f"Outgoing ({len(outgoing)})")
        self._add_edge_rows_paginated(
            self._outgoing_section,
            outgoing,
            "→",
            theme,
        )

        # Incoming relationships (paginated)
        incoming = store.get_incoming_edges(uri)
        self._incoming_section.clear_body()
        self._incoming_section.set_title(f"Incoming ({len(incoming)})")
        self._add_edge_rows_paginated(
            self._incoming_section,
            incoming,
            "←",
            theme,
        )

        # Authority links
        self._authority_section.clear_body()
        if authority_links:
            self._authority_section.set_title(f"Authority Links ({len(authority_links)})")
            self._authority_section.show()
            for key, val in authority_links:
                icon = "🌐" if "viaf" in val.lower() else "🏛️" if val.startswith("987") else "📚"
                lbl = QLabel(f"{icon} <b>{key}</b>: {val}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 10px;")
                self._authority_section.add_widget(lbl)
        else:
            self._authority_section.hide()

    def _add_edge_rows_paginated(
        self,
        section: _CollapsibleSection,
        edges: list[dict[str, str]],
        arrow: str,
        theme: types.ModuleType,
    ) -> None:
        """Add edge rows with pagination to avoid creating hundreds of widgets."""
        visible = edges[: self._MAX_VISIBLE_ROWS]
        for edge in visible:
            self._add_edge_row(
                section,
                edge["predicate"],
                edge["uri"],
                edge["label"],
                edge["type"],
                arrow,
                theme,
            )
        remaining = len(edges) - len(visible)
        if remaining > 0:
            more_btn = QPushButton(f"... {remaining} more (click to show)")
            more_btn.setStyleSheet(
                f"QPushButton {{ border: none; color: {theme.ui('subtext')}; "
                f"font-size: 10px; font-style: italic; padding: 4px; }}"
                f"QPushButton:hover {{ color: {theme.ui('text')}; }}"
            )
            more_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            def expand(
                checked: bool,
                s: _CollapsibleSection = section,
                all_edges: list[dict[str, str]] = edges,
                btn: QPushButton = more_btn,
            ) -> None:
                btn.hide()
                for edge in all_edges[self._MAX_VISIBLE_ROWS :]:
                    self._add_edge_row(
                        s,
                        edge["predicate"],
                        edge["uri"],
                        edge["label"],
                        edge["type"],
                        arrow,
                        theme,
                    )

            more_btn.clicked.connect(expand)
            section.add_widget(more_btn)

    def _add_edge_row(
        self,
        section: _CollapsibleSection,
        predicate: str,
        target_uri: str,
        target_label: str,
        target_type: str,
        arrow: str,
        theme: types.ModuleType,
    ) -> None:
        """Add a clickable relationship row to a section."""
        nc = theme.node_color(target_type)
        btn = QPushButton(f"{predicate} {arrow} {target_label[:35]}")
        btn.setStyleSheet(
            f"QPushButton {{ text-align: left; border: none; padding: 2px 4px; "
            f"font-size: 10px; color: {nc.text}; }}"
            f"QPushButton:hover {{ background-color: {nc.bg}; border-radius: 3px; }}"
        )
        btn.setToolTip(
            f"{predicate} {arrow} {target_label}\nType: {target_type}\nURI: {_local_name(target_uri)}"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked, u=target_uri: self.navigate_to_node.emit(u))
        section.add_widget(btn)


# ── Main widget ─────────────────────────────────────────────────────────────


class KnowledgeGraphView(QWidget):
    """Interactive RDF knowledge graph viewer with Cytoscape.js.

    Embeds a QWebEngineView rendering Cytoscape.js with:
    - Force-directed, hierarchical, and radial layouts
    - Node type filtering
    - Search by label
    - Click-to-inspect detail panel
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._graph_json: dict[str, list[dict[str, object]]] | None = None
        self._node_types: set[str] = set()
        self._bridge = _GraphBridge()
        self._bridge.node_selected.connect(self._on_node_selected)
        self._bridge.edge_selected.connect(self._on_edge_selected)
        self._bridge.cluster_expand.connect(self._on_cluster_expand)
        self._ttl_path: Path | None = None
        self._pending_ttl: Path | None = None
        self._store: GraphStore | None = None
        self._mode: str = "summary"  # "summary" or "full" or "neighborhood"
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── Toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Layout:"))
        self._layout_combo = QComboBox()
        self._layout_combo.addItems(["Force-directed", "Hierarchical", "Radial"])
        self._layout_combo.currentTextChanged.connect(self._on_layout_changed)
        toolbar.addWidget(self._layout_combo)

        toolbar.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Find node...")
        self._search_edit.returnPressed.connect(self._on_search)
        toolbar.addWidget(self._search_edit)

        search_btn = QPushButton("Go")
        search_btn.clicked.connect(self._on_search)
        toolbar.addWidget(search_btn)

        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self._on_reset)
        toolbar.addWidget(reset_btn)

        self._back_btn = QPushButton("Back to Summary")
        self._back_btn.setStyleSheet(
            "QPushButton { background-color: #6366f1; color: white; "
            "padding: 4px 12px; border-radius: 4px; font-weight: bold; border: none; }"
            "QPushButton:hover { background-color: #4f46e5; }"
        )
        self._back_btn.clicked.connect(self._on_back_to_summary)
        self._back_btn.hide()  # Only shown when drilled in
        toolbar.addWidget(self._back_btn)

        self._edge_labels_cb = QCheckBox("Edge labels")
        self._edge_labels_cb.toggled.connect(self._on_toggle_edge_labels)
        toolbar.addWidget(self._edge_labels_cb)

        # Depth slider — controls neighbor hops in cluster expansion
        toolbar.addWidget(QLabel("Depth:"))
        self._depth_slider = QSpinBox()
        self._depth_slider.setRange(0, 3)
        self._depth_slider.setValue(1)
        self._depth_slider.setToolTip("Neighbor expansion depth (0 = type only, 1 = +neighbors)")
        self._depth_slider.setFixedWidth(50)
        toolbar.addWidget(self._depth_slider)

        # Max nodes spinner
        toolbar.addWidget(QLabel("Max:"))
        self._max_nodes_spin = QSpinBox()
        self._max_nodes_spin.setRange(50, 2000)
        self._max_nodes_spin.setValue(300)
        self._max_nodes_spin.setSingleStep(100)
        self._max_nodes_spin.setToolTip("Maximum nodes to render")
        self._max_nodes_spin.setFixedWidth(70)
        toolbar.addWidget(self._max_nodes_spin)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── Filter bar ──
        self._filter_layout = QHBoxLayout()
        self._filter_layout.setSpacing(6)
        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet("font-weight: bold;")
        self._filter_layout.addWidget(filter_label)
        self._filter_checkboxes: dict[str, QCheckBox] = {}
        self._filter_layout.addStretch()
        layout.addLayout(self._filter_layout)

        # ── Advanced property search ──
        self._adv_search = QWidget()
        adv_layout = QHBoxLayout(self._adv_search)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(4)

        self._prop_combo = QComboBox()
        self._prop_combo.setEditable(True)
        self._prop_combo.setPlaceholderText("Property...")
        self._prop_combo.setMinimumWidth(160)
        self._prop_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        adv_layout.addWidget(self._prop_combo)

        self._op_combo = QComboBox()
        self._op_combo.addItems(["contains", "equals", "starts with"])
        self._op_combo.setFixedWidth(90)
        adv_layout.addWidget(self._op_combo)

        self._adv_text = QLineEdit()
        self._adv_text.setPlaceholderText("Value...")
        self._adv_text.returnPressed.connect(self._on_add_filter)
        adv_layout.addWidget(self._adv_text)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        add_btn.setToolTip("Add filter")
        add_btn.clicked.connect(self._on_add_filter)
        adv_layout.addWidget(add_btn)

        adv_go_btn = QPushButton("Search")
        adv_go_btn.clicked.connect(self._on_advanced_search)
        adv_layout.addWidget(adv_go_btn)

        adv_clear_btn = QPushButton("Clear")
        adv_clear_btn.clicked.connect(self._on_clear_filters)
        adv_layout.addWidget(adv_clear_btn)

        layout.addWidget(self._adv_search)

        # Active filters display
        self._active_filters: list[tuple[str, str, str]] = []
        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(4)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._search_results_label = QLabel("")
        self._chips_layout.addWidget(self._search_results_label)
        self._chips_layout.addStretch()
        layout.addLayout(self._chips_layout)

        # ── Splitter: graph + detail panel ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # WebEngineView (lazy-loaded to avoid import at module level)
        self._web_container = QWidget()
        self._web_layout = QVBoxLayout(self._web_container)
        self._web_layout.setContentsMargins(0, 0, 0, 0)
        self._web_view: Any = None  # Lazy init (QWebEngineView)
        splitter.addWidget(self._web_container)

        # Detail panel (Protégé-style)
        self._detail_panel = _EntityDetailPanel()
        self._detail_panel.navigate_to_node.connect(self._on_navigate_to_node)
        self._detail_panel.setMinimumWidth(250)
        self._detail_panel.setMaximumWidth(400)
        splitter.addWidget(self._detail_panel)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

        # ── Loading overlay (shown during graph build) ──
        self._loading_overlay = QWidget(self)
        overlay_layout = QVBoxLayout(self._loading_overlay)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._loading_title = QLabel("Building graph...")
        self._loading_title.setStyleSheet(
            "font-size: 18px; font-weight: bold; background: transparent;"
        )
        self._loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self._loading_title)

        self._loading_detail = QLabel("")
        self._loading_detail.setStyleSheet("font-size: 12px; background: transparent;")
        self._loading_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self._loading_detail)

        self._loading_overlay.hide()

    def _ensure_web_view(self) -> None:
        """Lazily create the QWebEngineView (avoids import at module level)."""
        if self._web_view is not None:
            return
        try:
            from PyQt6.QtWebChannel import QWebChannel  # noqa: PLC0415
            from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: PLC0415

            self._web_view = QWebEngineView()
            self._web_channel = QWebChannel()
            self._web_channel.registerObject("bridge", self._bridge)
            self._web_view.page().setWebChannel(self._web_channel)
            self._web_view.page().renderProcessTerminated.connect(
                self._on_render_crash,
            )
            self._web_layout.addWidget(self._web_view)
        except Exception as exc:
            logger.error("Failed to create WebEngine view: %s", exc, exc_info=True)
            self._web_view = None
            lbl = QLabel(
                f"Graph viewer failed to initialize:\n{exc}\n\n"
                "Try: uv pip install 'PyQt6-WebEngine==6.10.0'"
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self._web_layout.addWidget(lbl)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        """Keep the loading overlay covering the full widget."""
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._loading_overlay.setGeometry(self.rect())

    def _show_loading(self, message: str = "Building graph...") -> None:
        from mhm_pipeline.gui.widgets.base_visualization_widget import is_dark_mode  # noqa: PLC0415

        dark = is_dark_mode(self)
        bg = "rgba(30, 30, 46, 210)" if dark else "rgba(255, 255, 255, 210)"
        text = "#cdd6f4" if dark else "#1f2937"
        sub = "#a6adc8" if dark else "#6b7280"
        self._loading_overlay.setStyleSheet(f"background-color: {bg}; border-radius: 8px;")
        self._loading_title.setStyleSheet(
            f"color: {text}; font-size: 18px; font-weight: bold; background: transparent;"
        )
        self._loading_detail.setStyleSheet(
            f"color: {sub}; font-size: 12px; background: transparent;"
        )

        self._loading_overlay.setGeometry(self.rect())
        self._loading_detail.setText(message)
        self._loading_overlay.raise_()
        self._loading_overlay.show()
        QApplication.processEvents()

    def _hide_loading(self) -> None:
        self._loading_overlay.hide()

    # ── Public API ───────────────────────────────────────────────────

    def load_from_file(self, ttl_path: Path) -> None:
        """Load a Turtle file into a disk-backed store, then show summary."""
        self._ensure_web_view()
        if self._web_view is None:
            return

        self._ttl_path = ttl_path
        self._show_loading(f"Indexing {ttl_path.name}...")
        self._pending_ttl = ttl_path
        QTimer.singleShot(50, self._do_build_store)

    def load_graph(self, graph: rdflib.Graph) -> None:
        """Load an rdflib Graph by writing it to a temp file first.

        This avoids holding the rdflib graph in memory — it's serialized
        to disk and then parsed into SQLite.
        """
        import tempfile  # noqa: PLC0415

        self._ensure_web_view()
        if self._web_view is None:
            return

        self._show_loading("Preparing graph...")
        fd, tmp_path = tempfile.mkstemp(suffix=".ttl", prefix="graph_")
        import os  # noqa: PLC0415

        os.close(fd)

        graph.serialize(destination=tmp_path, format="turtle")
        del graph  # free immediately

        self._ttl_path = Path(tmp_path)
        self._pending_ttl = self._ttl_path
        QTimer.singleShot(50, self._do_build_store)

    def _do_build_store(self) -> None:
        """Build the SQLite graph store from the pending TTL file."""
        from mhm_pipeline.gui.widgets.graph_store import GraphStore  # noqa: PLC0415

        ttl_path = self._pending_ttl
        self._pending_ttl = None
        if ttl_path is None:
            self._hide_loading()
            return

        def on_progress(pct: int, message: str = "") -> None:
            self._loading_detail.setText(f"{message}  ({pct}%)")
            QApplication.processEvents()

        # Close previous store if any
        if hasattr(self, "_store") and self._store:
            self._store.close()

        self._store = GraphStore.from_ttl(ttl_path, progress_callback=on_progress)

        # Force garbage collection
        import gc  # noqa: PLC0415

        gc.collect()

        stats = self._store.get_stats()

        self._loading_detail.setText(
            f"Indexed {stats['n_nodes']} nodes, {stats['n_edges']} edges. Building summary..."
        )
        QApplication.processEvents()

        # Populate the property search dropdown
        try:
            prop_keys = self._store.get_unique_property_keys()
            self._prop_combo.clear()
            self._prop_combo.addItems(prop_keys)
        except Exception:
            pass

        # Always start with summary view (cheap — just SQL aggregation)
        self._render_summary()

    def _render_summary(self) -> None:
        """Render the summary view from the store."""
        assert self._store is not None
        graph_json = self._store.get_summary_json()
        self._mode = "summary"
        self._render_json(graph_json)

    def _render_json(self, graph_json: dict[str, list[dict[str, object]]]) -> None:
        """Render a Cytoscape.js JSON into the web view."""
        self._graph_json = graph_json
        from typing import Any, cast  # noqa: PLC0415

        nodes: list[Any] = cast(list, graph_json["nodes"])
        self._node_types = {n["data"]["nodeType"] for n in nodes if n["data"].get("nodeType")}
        self._rebuild_filter_checkboxes()

        n_vis = len(graph_json["nodes"])
        n_edges = len(graph_json["edges"])
        self._loading_detail.setText(f"Rendering {n_vis} nodes, {n_edges} edges...")
        QApplication.processEvents()

        # Write HTML + JS to a temp directory, then load via file:// URL.
        # setHtml() crashes on macOS with large inlined content.
        import shutil  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        from PyQt6.QtCore import QUrl  # noqa: PLC0415

        tmp_dir = Path(tempfile.mkdtemp(prefix="graph_"))

        # Copy JS assets to temp dir (so <script src="..."> works)
        for js_file in ("cytoscape.min.js", "dagre.min.js", "cytoscape-dagre.js", "qwebchannel.js"):
            src = _ASSETS_DIR / js_file
            if src.exists():
                shutil.copy2(src, tmp_dir / js_file)

        html = self._build_html_file_refs(graph_json)
        html_path = tmp_dir / "graph.html"
        html_path.write_text(html, encoding="utf-8")
        del html
        self._current_html_dir = tmp_dir

        self._web_view.loadFinished.connect(self._on_web_load_finished)
        self._web_view.setUrl(QUrl.fromLocalFile(str(html_path)))

    def _on_render_crash(self, status: object, exit_code: int) -> None:
        """Handle Chromium render process crash."""
        logger.error("WebEngine render process terminated: status=%s code=%d", status, exit_code)
        self._hide_loading()
        if self._web_view:
            self._web_view.setHtml(
                "<html><body style='background:#1e1e2e;color:#cdd6f4;text-align:center;padding-top:40%'>"
                "<h2>Graph rendering failed</h2>"
                "<p>The graph may be too large. Try a smaller dataset.</p>"
                "</body></html>"
            )

    def _on_web_load_finished(self, ok: bool) -> None:
        """Called when the QWebEngineView finishes loading the HTML."""
        self._hide_loading()
        try:
            self._web_view.loadFinished.disconnect(self._on_web_load_finished)
        except TypeError:
            pass

        if not ok:
            logger.warning("WebEngine page failed to load")

        # Inject QWebChannel bridge via JS (since qrc:// doesn't work from file://)
        self._inject_webchannel_bridge()

        # Show/hide back button based on mode
        self._back_btn.setVisible(
            self._mode != "summary" and hasattr(self, "_store") and self._store is not None
        )
        # Build lightweight node index for detail panel, then free the JSON
        if self._graph_json:
            self._node_index = {
                n["data"]["id"]: {  # type: ignore[index]
                    "label": n["data"].get("label", ""),  # type: ignore[attr-defined]
                    "nodeType": n["data"].get("nodeType", "unknown"),  # type: ignore[attr-defined]
                }
                for n in self._graph_json["nodes"]
            }
            self._graph_json = None

    def _inject_webchannel_bridge(self) -> None:
        """Inject QWebChannel JS bridge after page load (for file:// URLs)."""
        if self._web_view is None:
            return
        # The QWebChannel JS API is provided by Qt — we inline a minimal version
        # that connects to qt.webChannelTransport
        js = """
        if (typeof qt !== 'undefined' && qt.webChannelTransport) {
            new QWebChannel(qt.webChannelTransport, function(channel) {
                window.bridge = channel.objects.bridge;
                if (typeof cy !== 'undefined') {
                    cy.on('tap', 'node', function(evt) {
                        var node = evt.target;
                        var props = JSON.stringify(node.data('properties') || {});
                        if (window.bridge) window.bridge.onNodeSelected(node.id(), props);
                    });
                    cy.on('tap', 'edge', function(evt) {
                        var edge = evt.target;
                        var data = JSON.stringify({
                            source: edge.data('source'),
                            target: edge.data('target'),
                            label: edge.data('label')
                        });
                        if (window.bridge) window.bridge.onEdgeSelected(edge.id(), data);
                    });
                    cy.on('dblclick', 'node', function(evt) {
                        var node = evt.target;
                        if (node.data('isCluster') && window.bridge) {
                            window.bridge.onClusterExpand(node.data('nodeType'));
                        }
                    });
                }
            });
        }
        """
        self._web_view.page().runJavaScript(js)

    # ── HTML builder ─────────────────────────────────────────────────

    def _build_html_file_refs(self, graph_json: dict[str, list[dict[str, object]]]) -> str:
        """Build HTML using local file <script src> references (not inlined).

        This avoids the setHtml() crash on macOS caused by large inline JS.
        The JS files are copied to the same temp directory as the HTML.
        """

        template_path = _ASSETS_DIR / "graph_template.html"
        template = template_path.read_text(encoding="utf-8")

        # Use <script src="..."> references instead of inlining
        template = template.replace(
            "<!-- JS_CYTOSCAPE -->",
            '<script src="cytoscape.min.js"></script>',
        )
        template = template.replace(
            "<!-- JS_DAGRE -->",
            '<script src="dagre.min.js"></script>',
        )
        template = template.replace(
            "<!-- JS_CYTOSCAPE_DAGRE -->",
            '<script src="cytoscape-dagre.js"></script>',
        )
        template = template.replace(
            "<!-- JS_QWEBCHANNEL -->",
            '<script src="qwebchannel.js"></script>',
        )

        # Inject theme and data
        from mhm_pipeline.gui import theme  # noqa: PLC0415

        dark = theme.is_dark()
        template = template.replace(
            "/* IS_DARK_MODE */ false",
            "true" if dark else "false",
        )
        template = template.replace(
            "/* GRAPH_DATA_JSON */ { nodes: [], edges: [] }",
            json.dumps(graph_json, ensure_ascii=False),
        )

        return template

    # ── Filter checkboxes ────────────────────────────────────────────

    def _rebuild_filter_checkboxes(self) -> None:
        # Clear old checkboxes
        for cb in self._filter_checkboxes.values():
            self._filter_layout.removeWidget(cb)
            cb.deleteLater()
        self._filter_checkboxes.clear()

        for ntype in sorted(self._node_types):
            cb = QCheckBox(ntype.replace("_", " ").title())
            cb.setChecked(True)
            cb.stateChanged.connect(self._on_filter_changed)
            colors = _NODE_COLORS.get(ntype, _NODE_COLORS["default"])
            cb.setStyleSheet(f"QCheckBox {{ color: {colors['border']}; font-weight: bold; }}")
            self._filter_checkboxes[ntype] = cb
            # Insert before the stretch
            self._filter_layout.insertWidget(self._filter_layout.count() - 1, cb)

    # ── Slots ────────────────────────────────────────────────────────

    def _on_layout_changed(self, text: str) -> None:
        if self._web_view is None:
            return
        layout_map = {
            "Force-directed": "cose",
            "Hierarchical": "dagre",
            "Radial": "concentric",
        }
        js_name = layout_map.get(text, "cose")
        self._web_view.page().runJavaScript(f"window.setLayout('{js_name}');")

    def _on_search(self) -> None:
        if self._web_view is None:
            return
        query = self._search_edit.text().strip()
        escaped = json.dumps(query)
        self._web_view.page().runJavaScript(f"window.searchNode({escaped});")

    def _on_reset(self) -> None:
        if self._web_view is None:
            return
        self._search_edit.clear()
        self._on_clear_filters()
        self._web_view.page().runJavaScript("window.resetView();")
        for cb in self._filter_checkboxes.values():
            cb.setChecked(True)

    # ── Advanced property search ─────────────────────────────────────

    def _on_add_filter(self) -> None:
        """Add a property filter to the active chain."""
        prop = self._prop_combo.currentText().strip()
        text = self._adv_text.text().strip()
        if not prop or not text:
            return

        op_text = self._op_combo.currentText()
        op_map = {"contains": "contains", "equals": "equals", "starts with": "starts_with"}
        op = op_map.get(op_text, "contains")

        self._active_filters.append((prop, op, text))
        self._adv_text.clear()

        # Show chip
        chip = QPushButton(f'{prop} {op_text} "{text}"  ×')
        chip.setStyleSheet(
            "QPushButton { background: #374151; color: #e5e7eb; border-radius: 10px; "
            "padding: 2px 10px; border: 1px solid #6b7280; font-size: 11px; }"
            "QPushButton:hover { background: #dc2626; }"
        )
        idx = len(self._active_filters) - 1
        chip.clicked.connect(lambda _, i=idx: self._remove_filter(i))
        self._chips_layout.insertWidget(self._chips_layout.count() - 2, chip)

        self._search_results_label.setText(f"{len(self._active_filters)} filter(s)")

    def _on_advanced_search(self) -> None:
        """Execute the chained property search."""
        if not self._active_filters:
            # If no filters but text entered, add it as a quick filter
            prop = self._prop_combo.currentText().strip()
            text = self._adv_text.text().strip()
            if prop and text:
                self._on_add_filter()

        if not self._active_filters or not hasattr(self, "_store") or not self._store:
            return

        uris = self._store.search_by_property(self._active_filters)
        self._search_results_label.setText(f"{len(uris)} nodes matched")

        if self._web_view is not None and uris:
            escaped = json.dumps(json.dumps(uris))
            self._web_view.page().runJavaScript(f"window.highlightNodes({escaped});")
        elif self._web_view is not None:
            self._web_view.page().runJavaScript("window.resetView();")
            self._search_results_label.setText("0 nodes matched")

    def _on_clear_filters(self) -> None:
        """Clear all active filters."""
        self._active_filters.clear()
        # Remove chip widgets (everything before the results label and stretch)
        while self._chips_layout.count() > 2:
            item = self._chips_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._search_results_label.setText("")
        self._adv_text.clear()
        if self._web_view is not None:
            self._web_view.page().runJavaScript("window.resetView();")

    def _remove_filter(self, index: int) -> None:
        """Remove a filter by index and refresh."""
        if 0 <= index < len(self._active_filters):
            self._active_filters.pop(index)
            # Rebuild chips
            while self._chips_layout.count() > 2:
                item = self._chips_layout.takeAt(0)
                w = item.widget() if item else None
                if w is not None:
                    w.deleteLater()
            for i, (prop, op, text) in enumerate(self._active_filters):
                chip = QPushButton(f'{prop} {op} "{text}"  ×')
                chip.setStyleSheet(
                    "QPushButton { background: #374151; color: #e5e7eb; border-radius: 10px; "
                    "padding: 2px 10px; border: 1px solid #6b7280; font-size: 11px; }"
                    "QPushButton:hover { background: #dc2626; }"
                )
                chip.clicked.connect(lambda _, ii=i: self._remove_filter(ii))
                self._chips_layout.insertWidget(self._chips_layout.count() - 2, chip)
            self._search_results_label.setText(
                f"{len(self._active_filters)} filter(s)" if self._active_filters else ""
            )

    def _on_toggle_edge_labels(self, checked: bool) -> None:
        if self._web_view is None:
            return
        self._web_view.page().runJavaScript(
            f"window.toggleEdgeLabels({'true' if checked else 'false'});"
        )

    def _on_filter_changed(self) -> None:
        if self._web_view is None:
            return
        visible = [t for t, cb in self._filter_checkboxes.items() if cb.isChecked()]
        self._web_view.page().runJavaScript(f"window.filterByTypes({json.dumps(visible)});")

    def _on_node_selected(self, node_id: str, properties_json: str) -> None:
        store = getattr(self, "_store", None)
        if store:
            self._detail_panel.show_entity(node_id, store)

    def _on_edge_selected(self, edge_id: str, data_json: str) -> None:
        data = json.loads(data_json) if data_json else {}
        store = getattr(self, "_store", None)
        if store:
            # Show the source node's details when an edge is clicked
            source_uri = data.get("source", "")
            if source_uri:
                self._detail_panel.show_entity(source_uri, store)

    def _on_navigate_to_node(self, uri: str) -> None:
        """Navigate the graph to a node and show its details."""
        # Update detail panel first (always works — reads from SQLite)
        store = getattr(self, "_store", None)
        if store:
            self._detail_panel.show_entity(uri, store)

        # Try to navigate the graph view (only works if node is rendered)
        if self._web_view:
            escaped = json.dumps(uri)
            self._web_view.page().runJavaScript(
                f"if (cy.getElementById({escaped}).length > 0) {{ window.navigateToNode({escaped}); }}"
            )

    def _on_cluster_expand(self, node_type: str) -> None:
        """Handle double-click on a summary cluster node — query store for members."""
        if not hasattr(self, "_store") or not self._store:
            return

        depth = self._depth_slider.value()
        max_total = self._max_nodes_spin.value()
        total = self._store.get_type_count(node_type)
        limit = min(50, total)

        self._show_loading(
            f"Loading {node_type} ({total} total, showing {limit} + {depth}-hop neighbors, max {max_total})..."
        )
        QApplication.processEvents()

        if depth == 0:
            # Type-only view — no neighbors
            graph_json = self._store.get_type_subgraph(node_type, limit=limit, max_total=limit)
        else:
            graph_json = self._store.get_type_subgraph(node_type, limit=limit, max_total=max_total)

        self._mode = "neighborhood"
        self._back_btn.show()
        self._render_json(graph_json)

    def _on_back_to_summary(self) -> None:
        """Navigate back to the summary view."""
        if not hasattr(self, "_store") or not self._store:
            return
        self._show_loading("Returning to summary view...")
        self._render_summary()
