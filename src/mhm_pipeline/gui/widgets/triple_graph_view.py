"""Interactive RDF triple graph visualization widget."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from rdflib import Graph


class TripleGraphView(QWidget):
    """Interactive graph visualization of RDF triples.

    Uses PyQt6's QGraphicsView for rendering nodes and edges.
    Supports zooming, panning, and displays nodes with different
    colors based on their semantic type (manuscript, person, work, place, event).

    The widget can efficiently handle up to 100 triples using a simple
    circular layout algorithm.
    """

    # Background color, text/border color for each node type
    NODE_COLORS: dict[str, tuple[str, str]] = {
        "manuscript": ("#dbeafe", "#1e40af"),  # Blue
        "person": ("#fce7f3", "#be185d"),  # Pink
        "work": ("#dcfce7", "#166534"),  # Green
        "place": ("#fef3c7", "#92400e"),  # Yellow
        "event": ("#e5dbff", "#5b21b6"),  # Purple
        "default": ("#f3f4f6", "#374151"),  # Gray
    }

    NODE_WIDTH = 100
    NODE_HEIGHT = 60
    LAYOUT_RADIUS = 250
    CENTER_X = 400
    CENTER_Y = 300

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the TripleGraphView widget.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)

        self._nodes: dict[str, QGraphicsEllipseItem] = {}
        self._edges: list[QGraphicsLineItem] = []
        self._labels: list[QGraphicsTextItem] = []
        self._triple_count = 0

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(0, 0, 800, 600)

        self._view = QGraphicsView(self._scene)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Use transparent background to inherit palette
        self._view.setStyleSheet("background: transparent; border: none;")

        layout.addWidget(self._view)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802
        """Handle mouse wheel events for zooming.

        Args:
            event: The wheel event.
        """
        if event is None:
            return
        if event.angleDelta().y() > 0:
            self._view.scale(1.15, 1.15)
        else:
            self._view.scale(0.87, 0.87)
        event.accept()

    def load_from_graph(self, graph: Graph) -> None:
        """Build visual graph from an rdflib Graph.

        Clears any existing visualization and creates a new graph
        layout based on the triples in the provided graph.

        Args:
            graph: An rdflib Graph containing RDF triples.

        Example:
            >>> from rdflib import Graph
            >>> g = Graph()
            >>> g.parse("data.ttl", format="turtle")
            >>> view.load_from_graph(g)
        """
        self._clear_scene()

        triples = list(graph)
        self._triple_count = len(triples)

        if not triples:
            self._show_empty_message()
            return

        # Collect unique nodes (subjects and objects that are URIs)
        nodes: set[str] = set()
        edges: list[tuple[str, str, str]] = []

        # Explicitly type variables to avoid rdflib Node type inference
        subject: object
        predicate: object
        obj: object

        for subject, predicate, obj in triples:
            s_str = str(subject)
            nodes.add(s_str)

            o_str = str(obj)
            # Check if object should be treated as a node (URI or BNode)
            obj_type = type(obj).__name__
            if obj_type in ("URIRef", "BNode"):
                nodes.add(o_str)

            edges.append((s_str, o_str, self._shorten_predicate(str(predicate))))

        # Calculate positions using circular layout
        positions = self._calculate_layout(list(nodes))

        # Create nodes
        for uri, pos in positions.items():
            node_type = self._infer_node_type(uri, graph)
            self._create_node(uri, pos, node_type)

        # Create edges
        for source, target, predicate in edges:
            if source in self._nodes and target in self._nodes:
                self._create_edge(source, target, predicate)

        self._fit_view()

    def _clear_scene(self) -> None:
        """Clear the scene and reset internal state."""
        self._scene.clear()
        self._nodes.clear()
        self._edges.clear()
        self._labels.clear()
        self._triple_count = 0

    def _show_empty_message(self) -> None:
        """Display a message when the graph is empty."""
        text = self._scene.addText("No triples to display")
        if text:
            text.setPos(self.CENTER_X - 60, self.CENTER_Y)

    def _calculate_layout(self, nodes: list[str]) -> dict[str, QPointF]:
        """Calculate node positions using a circular layout.

        Distributes nodes evenly around a circle centered at CENTER_X, CENTER_Y.
        For fewer than 5 nodes, uses a grid layout instead for better spacing.

        Args:
            nodes: List of node URIs to position.

        Returns:
            Dictionary mapping node URIs to their positions.
        """
        positions: dict[str, QPointF] = {}
        count = len(nodes)

        if count == 0:
            return positions

        if count <= 4:
            # Grid layout for small number of nodes
            cols = 2
            spacing_x = 200
            spacing_y = 150
            start_x = self.CENTER_X - (spacing_x * (cols - 1)) // 2
            start_y = self.CENTER_Y - (spacing_y * ((count - 1) // cols)) // 2

            for i, node in enumerate(nodes):
                col = i % cols
                row = i // cols
                x = start_x + col * spacing_x
                y = start_y + row * spacing_y
                positions[node] = QPointF(x, y)
        else:
            # Circular layout for larger graphs
            angle_step = 2 * math.pi / count
            for i, node in enumerate(nodes):
                angle = i * angle_step - math.pi / 2  # Start from top
                x = self.CENTER_X + int(self.LAYOUT_RADIUS * math.cos(angle))
                y = self.CENTER_Y + int(self.LAYOUT_RADIUS * math.sin(angle))
                positions[node] = QPointF(x, y)

        return positions

    def _infer_node_type(self, uri: str, graph: Graph) -> str:
        """Infer the node type based on URI patterns and RDF type.

        Attempts to determine if a node represents a manuscript, person,
        work, place, or event based on its URI and any RDF type statements.

        Args:
            uri: The node URI as a string.
            graph: The rdflib Graph containing the node.

        Returns:
            The inferred node type key (manuscript, person, work, place, event).
        """
        from rdflib import URIRef

        uri_ref = URIRef(uri)

        # Check for RDF type statements
        for _, _, obj in graph.triples((uri_ref, None, None)):
            obj_str = str(obj).lower()
            if "person" in obj_str or "agent" in obj_str:
                return "person"
            if "place" in obj_str or "location" in obj_str:
                return "place"
            if "manuscript" in obj_str or "manuscript" in uri.lower():
                return "manuscript"
            if "work" in obj_str or "text" in obj_str:
                return "work"
            if "event" in obj_str:
                return "event"

        # Fallback to URI pattern matching
        uri_lower = uri.lower()
        if "/person" in uri_lower or "#person" in uri_lower:
            return "person"
        if "/place" in uri_lower or "#place" in uri_lower:
            return "place"
        if "/work" in uri_lower or "#work" in uri_lower:
            return "work"
        if "/event" in uri_lower or "#event" in uri_lower:
            return "event"
        if "/ms" in uri_lower or "#ms" in uri_lower or "manuscript" in uri_lower:
            return "manuscript"

        return "default"

    def _create_node(self, uri: str, pos: QPointF, node_type: str) -> QGraphicsEllipseItem:
        """Create a visual node at the specified position.

        Args:
            uri: The full URI of the node.
            pos: The position for the node center.
            node_type: The type of node (determines color).

        Returns:
            The created ellipse item.
        """
        bg_color_str, text_color_str = self.NODE_COLORS.get(node_type, self.NODE_COLORS["default"])
        bg_color = QColor(bg_color_str)
        text_color = QColor(text_color_str)

        # Create ellipse centered at position
        ellipse = QGraphicsEllipseItem(
            pos.x() - self.NODE_WIDTH / 2,
            pos.y() - self.NODE_HEIGHT / 2,
            self.NODE_WIDTH,
            self.NODE_HEIGHT,
        )
        ellipse.setBrush(bg_color)
        ellipse.setPen(QPen(text_color, 2))
        ellipse.setZValue(1)  # Nodes above edges
        ellipse.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

        # Add shortened label
        label_text = self._shorten_uri(uri)
        label_item = self._scene.addText(label_text)
        if label_item:
            label_item.setDefaultTextColor(text_color)
            label_item.setFont(QFont("Menlo, Consolas, monospace", 8))

            # Center text within ellipse
            text_rect = label_item.boundingRect()
            label_item.setPos(
                pos.x() - text_rect.width() / 2,
                pos.y() - text_rect.height() / 2,
            )
            label_item.setZValue(2)  # Labels above nodes
            self._labels.append(label_item)

        self._scene.addItem(ellipse)
        self._nodes[uri] = ellipse

        return ellipse

    def _create_edge(self, source: str, target: str, predicate: str) -> QGraphicsLineItem | None:
        """Create a visual edge between two nodes with a predicate label.

        Args:
            source: The URI of the source node.
            target: The URI of the target node.
            predicate: The shortened predicate label.

        Returns:
            The created line item, or None if nodes don't exist.
        """
        source_node = self._nodes.get(source)
        target_node = self._nodes.get(target)

        if source_node is None or target_node is None:
            return None

        # Get center points
        source_rect = source_node.rect()
        target_rect = target_node.rect()
        source_center = source_rect.center()
        target_center = target_rect.center()

        # Create line - use palette-aware mid color
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app:
            palette = app.palette()  # type: ignore[attr-defined]
            line_color = palette.color(palette.ColorRole.Mid)
        else:
            line_color = QColor("#9ca3af")
        line = self._scene.addLine(
            source_center.x(),
            source_center.y(),
            target_center.x(),
            target_center.y(),
            QPen(line_color, 1.5),
        )
        if line:
            line.setZValue(0)  # Edges below nodes
            self._edges.append(line)

        # Add predicate label at midpoint
        mid_x = (source_center.x() + target_center.x()) / 2
        mid_y = (source_center.y() + target_center.y()) / 2

        label = self._scene.addText(predicate)
        if label:
            # Use palette-aware text color
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                palette = app.palette()  # type: ignore[attr-defined]
                text_color = palette.color(palette.ColorRole.Text)
            else:
                text_color = QColor("#4b5563")
            label.setDefaultTextColor(text_color)
            label.setFont(QFont("Menlo, Consolas, monospace", 7))

            # Center label
            text_rect = label.boundingRect()
            label.setPos(mid_x - text_rect.width() / 2, mid_y - text_rect.height() / 2)
            label.setZValue(0.5)  # Labels above edges but below nodes
            self._labels.append(label)

        return line

    def _shorten_uri(self, uri: str) -> str:
        """Shorten a URI for display.

        Takes the last segment of the URI (after # or /) and truncates
        if necessary to fit within the node width.

        Args:
            uri: The full URI string.

        Returns:
            A shortened display string.
        """
        # Try to extract the last segment
        if "#" in uri:
            short = uri.split("#")[-1]
        elif "/" in uri:
            short = uri.split("/")[-1]
        else:
            short = uri

        # Truncate if too long
        max_len = 15
        if len(short) > max_len:
            short = short[: max_len - 3] + "..."

        return short or uri[:20]

    def _shorten_predicate(self, predicate: str) -> str:
        """Shorten a predicate URI to a readable label.

        Args:
            predicate: The full predicate URI.

        Returns:
            A shortened display label.
        """
        # Extract local name
        if "#" in predicate:
            short = predicate.split("#")[-1]
        elif "/" in predicate:
            short = predicate.split("/")[-1]
        else:
            short = predicate

        # CamelCase or snake_case to readable
        short = short.replace("_", " ")

        # Truncate if needed
        if len(short) > 20:
            short = short[:17] + "..."

        return short

    def _fit_view(self) -> None:
        """Fit the view to show all items with some padding."""
        if self._scene.items():
            self._view.fitInView(
                self._scene.itemsBoundingRect().adjusted(-50, -50, 50, 50),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def get_triple_count(self) -> int:
        """Return the number of triples currently displayed.

        Returns:
            The count of triples loaded from the last graph.
        """
        return self._triple_count

    def reset_view(self) -> None:
        """Reset the view to show all items with default zoom."""
        self._fit_view()

    def zoom_in(self) -> None:
        """Zoom in by 20%."""
        self._view.scale(1.2, 1.2)

    def zoom_out(self) -> None:
        """Zoom out by 20%."""
        self._view.scale(0.83, 0.83)

    def clear(self) -> None:
        """Clear the graph visualization."""
        self._clear_scene()
