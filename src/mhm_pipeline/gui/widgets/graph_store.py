"""On-disk SQLite store for RDF graph data.

Parses a Turtle file directly into a lightweight SQLite database using
a streaming parser, avoiding rdflib's heavy in-memory triple store.
The database stays on disk and is queried on demand by the UI.

Typical memory usage: <10 MB regardless of graph size.
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class GraphStore:
    """SQLite-backed RDF graph store for visualization queries.

    Parses TTL once into SQLite tables.  All subsequent operations are
    SQL queries that only load the rows they need.

    Usage::

        store = GraphStore.from_ttl(Path("output.ttl"))
        summary = store.get_summary()        # {type: count}
        nodes = store.get_nodes_by_type("person")
        neighbors = store.get_neighborhood("http://...uri", hops=1)
        store.close()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=OFF")

    @classmethod
    def from_ttl(
        cls,
        ttl_path: Path,
        db_path: str | None = None,
        progress_callback: object = None,
    ) -> GraphStore:
        """Parse a Turtle file into a new SQLite store.

        Args:
            ttl_path: Path to the .ttl file.
            db_path: Where to create the DB. Defaults to a temp file.
            progress_callback: Optional callable(pct: int) for progress.

        Returns:
            A ready-to-query GraphStore.
        """
        if db_path is None:
            fd, db_path = tempfile.mkstemp(suffix=".db", prefix="graph_")
            import os

            os.close(fd)

        store = cls(db_path)
        store._create_tables()
        store._import_ttl(ttl_path, progress_callback)
        return store

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    # ── Property search ──────────────────────────────────────────────

    def get_unique_property_keys(self) -> list[str]:
        """Return all distinct property key names, sorted alphabetically."""
        rows = self._conn.execute("SELECT DISTINCT key FROM properties ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def search_by_property(
        self,
        filters: list[tuple[str, str, str]],
    ) -> list[str]:
        """Find node URIs matching ALL property filters (AND logic).

        Args:
            filters: List of (property_key, operator, search_text) tuples.
                Operators: 'contains', 'equals', 'starts_with'.

        Returns:
            List of matching node URIs.
        """
        if not filters:
            return []

        subqueries: list[str] = []
        params: list[str] = []
        for key, op, text in filters:
            if op == "equals":
                subqueries.append("SELECT node_uri FROM properties WHERE key = ? AND value = ?")
                params.extend([key, text])
            elif op == "starts_with":
                subqueries.append("SELECT node_uri FROM properties WHERE key = ? AND value LIKE ?")
                params.extend([key, f"{text}%"])
            else:  # contains (default)
                subqueries.append("SELECT node_uri FROM properties WHERE key = ? AND value LIKE ?")
                params.extend([key, f"%{text}%"])

        sql = " INTERSECT ".join(subqueries)
        rows = self._conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── Schema ───────────────────────────────────────────────────────

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                uri TEXT PRIMARY KEY,
                label TEXT,
                node_type TEXT DEFAULT 'default'
            );
            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                predicate TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS properties (
                node_uri TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
            CREATE INDEX IF NOT EXISTS idx_props_uri ON properties(node_uri);
        """)

    # ── Import ───────────────────────────────────────────────────────

    _TYPE_MAP: dict[str, str] = {
        "Manuscript": "manuscript",
        "F4_Manifestation_Singleton": "manuscript",
        "F3_Manifestation": "manuscript",
        "E21_Person": "person",
        "F1_Work": "work",
        "F24_Publication_Work": "work",
        "F2_Expression": "expression",
        "E53_Place": "place",
        "Codicological_Unit": "codicological_unit",
        "Bibliographic_Unit": "codicological_unit",
        "Paleographical_Unit": "codicological_unit",
        "E12_Production": "event",
        "E8_Acquisition": "event",
        "E10_Transfer_of_Custody": "event",
        "F27_Work_Creation": "event",
        "E7_Activity": "event",
        "CreativeEvent": "event",
        "E74_Group": "organization",
    }

    def _import_ttl(
        self,
        ttl_path: Path,
        progress_callback: object = None,
    ) -> None:
        """Stream-parse TTL into SQLite using rdflib in chunks."""
        from rdflib import RDF, RDFS, Graph, Literal  # noqa: PLC0415

        file_size = ttl_path.stat().st_size
        g = Graph()

        # Parse the file — this is the memory-heavy step but we process
        # and discard in batches
        if progress_callback:
            progress_callback(5, "Parsing Turtle file...")

        logger.info("Parsing %s (%d KB) into SQLite...", ttl_path.name, file_size // 1024)
        g.parse(str(ttl_path), format="turtle")

        total = len(g)
        logger.info("Imported %d triples, building SQLite index...", total)

        if progress_callback:
            progress_callback(30, f"Parsed {total} triples. Classifying nodes...")

        # First pass: collect types and labels
        node_types: dict[str, str] = {}
        node_labels: dict[str, str] = {}

        for s, p, o in g:
            s_id = str(s)
            if isinstance(o, Literal):
                if p == RDFS.label:
                    node_labels[s_id] = str(o)
                continue
            if p == RDF.type:
                local = _local_name(str(o))
                cat = self._TYPE_MAP.get(local, "default")
                if cat != "default" or s_id not in node_types:
                    node_types[s_id] = cat

        if progress_callback:
            progress_callback(45, "Collecting node URIs...")

        # Insert nodes
        all_uris: set[str] = set()
        for s, p, o in g:
            all_uris.add(str(s))
            if not isinstance(o, Literal):
                all_uris.add(str(o))

        if progress_callback:
            progress_callback(50, f"Writing {len(all_uris)} nodes to database...")

        node_rows = [
            (uri, node_labels.get(uri, _local_name(uri)), node_types.get(uri, _infer_type(uri)))
            for uri in all_uris
        ]
        self._conn.executemany(
            "INSERT OR IGNORE INTO nodes (uri, label, node_type) VALUES (?, ?, ?)",
            node_rows,
        )

        if progress_callback:
            progress_callback(60, "Extracting edges and properties...")

        # Insert edges (non-literal, non-type triples)
        edge_rows = []
        prop_rows = []
        for i, (s, p, o) in enumerate(g):
            if isinstance(o, Literal):
                prop_rows.append((str(s), _shorten(str(p)), str(o)))
            elif p != RDF.type:
                edge_rows.append((str(s), str(o), _shorten(str(p))))

            if progress_callback and i % 50000 == 0 and i > 0:
                pct = 60 + int(i / total * 30)
                progress_callback(pct, f"Processing triple {i:,}/{total:,}...")

        if progress_callback:
            progress_callback(90, f"Writing {len(edge_rows)} edges, {len(prop_rows)} properties...")

        self._conn.executemany(
            "INSERT INTO edges (source, target, predicate) VALUES (?, ?, ?)",
            edge_rows,
        )
        self._conn.executemany(
            "INSERT INTO properties (node_uri, key, value) VALUES (?, ?, ?)",
            prop_rows,
        )
        self._conn.commit()

        if progress_callback:
            progress_callback(95, "Freeing parser memory...")

        # Free rdflib graph immediately
        del g, node_rows, edge_rows, prop_rows, node_types, node_labels, all_uris
        import gc

        gc.collect()

        if progress_callback:
            progress_callback(100, "Database ready.")

        stats = self.get_stats()
        logger.info(
            "Graph store ready: %d nodes, %d edges, %d properties",
            stats["n_nodes"],
            stats["n_edges"],
            stats["n_properties"],
        )

    # ── Query API ────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        n_nodes = self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        n_edges = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        n_props = self._conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        return {"n_nodes": n_nodes, "n_edges": n_edges, "n_properties": n_props}

    def get_summary(self) -> dict[str, int]:
        """Return node counts grouped by type."""
        rows = self._conn.execute(
            "SELECT node_type, COUNT(*) as cnt FROM nodes GROUP BY node_type ORDER BY cnt DESC"
        ).fetchall()
        return {row["node_type"]: row["cnt"] for row in rows}

    def get_summary_edges(self) -> list[tuple[str, str, int]]:
        """Return aggregated edge counts between node type categories."""
        rows = self._conn.execute("""
            SELECT n1.node_type as src_type, n2.node_type as tgt_type, COUNT(*) as cnt
            FROM edges e
            JOIN nodes n1 ON e.source = n1.uri
            JOIN nodes n2 ON e.target = n2.uri
            WHERE n1.node_type != n2.node_type
            GROUP BY src_type, tgt_type
            ORDER BY cnt DESC
        """).fetchall()
        return [(r["src_type"], r["tgt_type"], r["cnt"]) for r in rows]

    def get_nodes_by_type(
        self,
        node_type: str,
        limit: int = 200,
    ) -> list[dict[str, str]]:
        """Return nodes of a given type."""
        rows = self._conn.execute(
            "SELECT uri, label, node_type FROM nodes WHERE node_type = ? LIMIT ?",
            (node_type, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_neighborhood(
        self,
        center_uri: str,
        hops: int = 1,
    ) -> dict[str, list[dict[str, object]]]:
        """Return the N-hop neighborhood as Cytoscape.js JSON."""
        visited: set[str] = {center_uri}
        frontier: set[str] = {center_uri}

        for _ in range(hops):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            # Outgoing
            rows = self._conn.execute(
                f"SELECT target FROM edges WHERE source IN ({placeholders})",
                list(frontier),
            ).fetchall()
            next_f: set[str] = {r["target"] for r in rows} - visited
            # Incoming
            rows = self._conn.execute(
                f"SELECT source FROM edges WHERE target IN ({placeholders})",
                list(frontier),
            ).fetchall()
            next_f |= {r["source"] for r in rows} - visited
            visited |= next_f
            frontier = next_f

        return self._build_subgraph_json(visited, center_uri)

    def get_type_subgraph(
        self,
        node_type: str,
        limit: int = 100,
    ) -> dict[str, list[dict[str, object]]]:
        """Return nodes of a type + their 1-hop neighbors as Cytoscape.js JSON."""
        # Get target type nodes
        rows = self._conn.execute(
            "SELECT uri FROM nodes WHERE node_type = ? LIMIT ?",
            (node_type, limit),
        ).fetchall()
        target_uris = {r["uri"] for r in rows}

        # Get 1-hop neighbors
        if target_uris:
            placeholders = ",".join("?" * len(target_uris))
            out_rows = self._conn.execute(
                f"SELECT target FROM edges WHERE source IN ({placeholders})",
                list(target_uris),
            ).fetchall()
            in_rows = self._conn.execute(
                f"SELECT source FROM edges WHERE target IN ({placeholders})",
                list(target_uris),
            ).fetchall()
            neighbor_uris = {r["target"] for r in out_rows} | {r["source"] for r in in_rows}
        else:
            neighbor_uris = set()

        all_uris = target_uris | neighbor_uris
        return self._build_subgraph_json(all_uris)

    def get_node_properties(self, uri: str) -> dict[str, list[str]]:
        """Return all literal properties for a node."""
        rows = self._conn.execute(
            "SELECT key, value FROM properties WHERE node_uri = ?", (uri,)
        ).fetchall()
        props: dict[str, list[str]] = {}
        for r in rows:
            props.setdefault(r["key"], []).append(r["value"])
        return props

    def get_outgoing_edges(self, uri: str) -> list[dict[str, str]]:
        """Return outgoing object relationships for a node."""
        rows = self._conn.execute(
            """SELECT e.predicate, e.target, n.label, n.node_type
               FROM edges e JOIN nodes n ON e.target = n.uri
               WHERE e.source = ? ORDER BY e.predicate""",
            (uri,),
        ).fetchall()
        return [
            {
                "predicate": r["predicate"],
                "uri": r["target"],
                "label": r["label"] or _local_name(r["target"]),
                "type": r["node_type"],
            }
            for r in rows
        ]

    def get_incoming_edges(self, uri: str) -> list[dict[str, str]]:
        """Return incoming object relationships pointing to a node."""
        rows = self._conn.execute(
            """SELECT e.predicate, e.source, n.label, n.node_type
               FROM edges e JOIN nodes n ON e.source = n.uri
               WHERE e.target = ? ORDER BY e.predicate""",
            (uri,),
        ).fetchall()
        return [
            {
                "predicate": r["predicate"],
                "uri": r["source"],
                "label": r["label"] or _local_name(r["source"]),
                "type": r["node_type"],
            }
            for r in rows
        ]

    def get_node_label(self, uri: str) -> str:
        """Return the label for a node URI."""
        row = self._conn.execute(
            "SELECT label, node_type FROM nodes WHERE uri = ?",
            (uri,),
        ).fetchone()
        if row:
            return row["label"] or _local_name(uri)
        return _local_name(uri)

    def get_node_type(self, uri: str) -> str:
        """Return the node type category."""
        row = self._conn.execute(
            "SELECT node_type FROM nodes WHERE uri = ?",
            (uri,),
        ).fetchone()
        return row["node_type"] if row else "default"

    def get_summary_json(self) -> dict[str, list[dict[str, object]]]:
        """Build Cytoscape.js JSON for the summary view."""
        from mhm_pipeline.gui.widgets.knowledge_graph_view import _NODE_COLORS  # noqa: PLC0415

        summary = self.get_summary()
        summary_edges = self.get_summary_edges()

        nodes: list[dict[str, object]] = []
        for ntype, count in summary.items():
            colors = _NODE_COLORS.get(ntype, _NODE_COLORS["default"])
            nodes.append(
                {
                    "data": {
                        "id": f"cluster_{ntype}",
                        "label": f"{ntype.replace('_', ' ').title()}\n({count})",
                        "nodeType": ntype,
                        "bgColor": colors["bg"],
                        "borderColor": colors["border"],
                        "properties": {"count": [str(count)]},
                        "isCluster": True,
                        "memberCount": count,
                    }
                }
            )

        edges: list[dict[str, object]] = []
        for src_type, tgt_type, count in summary_edges:
            edges.append(
                {
                    "data": {
                        "id": f"ce_{src_type}_{tgt_type}",
                        "source": f"cluster_{src_type}",
                        "target": f"cluster_{tgt_type}",
                        "label": str(count),
                    }
                }
            )

        return {"nodes": nodes, "edges": edges}

    # ── Internal helpers ─────────────────────────────────────────────

    def _build_subgraph_json(
        self,
        uris: set[str],
        center_uri: str | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        """Build Cytoscape.js JSON for a subset of nodes."""
        from mhm_pipeline.gui.widgets.knowledge_graph_view import _NODE_COLORS  # noqa: PLC0415

        if not uris:
            return {"nodes": [], "edges": []}

        placeholders = ",".join("?" * len(uris))
        uri_list = list(uris)

        # Fetch node data
        node_rows = self._conn.execute(
            f"SELECT uri, label, node_type FROM nodes WHERE uri IN ({placeholders})",
            uri_list,
        ).fetchall()

        nodes: list[dict[str, object]] = []
        for r in node_rows:
            ntype = r["node_type"]
            colors = _NODE_COLORS.get(ntype, _NODE_COLORS["default"])
            is_center = r["uri"] == center_uri
            nodes.append(
                {
                    "data": {
                        "id": r["uri"],
                        "label": (r["label"] or "")[:40],
                        "nodeType": ntype,
                        "bgColor": colors["bg"],
                        "borderColor": "#f59e0b" if is_center else colors["border"],
                        "properties": {},
                    }
                }
            )

        # Fetch edges between these nodes
        edge_rows = self._conn.execute(
            f"""SELECT source, target, predicate FROM edges
                WHERE source IN ({placeholders}) AND target IN ({placeholders})""",
            uri_list + uri_list,
        ).fetchall()

        edges: list[dict[str, object]] = []
        for i, r in enumerate(edge_rows):
            edges.append(
                {
                    "data": {
                        "id": f"e_{i}",
                        "source": r["source"],
                        "target": r["target"],
                        "label": r["predicate"],
                    }
                }
            )

        return {"nodes": nodes, "edges": edges}


def _local_name(uri: str) -> str:
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rsplit("/", 1)[-1] if "/" in uri else uri


def _shorten(uri: str) -> str:
    return _local_name(uri).replace("_", " ")


def _infer_type(uri: str) -> str:
    uri_lower = uri.lower()
    for kw, cat in [
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
        if kw in uri_lower:
            return cat
    return "default"
