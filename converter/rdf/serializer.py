"""TTL serialization utilities."""

from pathlib import Path

from rdflib import Graph

from ..config.namespaces import bind_namespaces


class TurtleSerializer:
    """Serializer for outputting RDF graphs as Turtle files."""

    def __init__(self, graph: Graph | None = None):
        """Initialize the serializer.

        Args:
            graph: Optional RDF graph to serialize
        """
        self.graph = graph

    def serialize(self, graph: Graph | None = None, output_path: Path | None = None) -> str:
        """Serialize graph to Turtle format.

        Args:
            graph: RDF graph to serialize (overrides constructor graph)
            output_path: Optional path to save the file

        Returns:
            Turtle string representation
        """
        g = graph or self.graph
        if g is None:
            raise ValueError("No graph provided for serialization")

        bind_namespaces(g)

        ttl_string = g.serialize(format="turtle")

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(ttl_string)

        return ttl_string

    def serialize_to_file(self, output_path: Path, graph: Graph | None = None):
        """Serialize graph directly to a file.

        Args:
            output_path: Path to save the TTL file
            graph: Optional RDF graph (overrides constructor graph)
        """
        g = graph or self.graph
        if g is None:
            raise ValueError("No graph provided for serialization")

        bind_namespaces(g)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        g.serialize(destination=str(output_path), format="turtle")

    @staticmethod
    def merge_graphs(*graphs: Graph) -> Graph:
        """Merge multiple graphs into one.

        Args:
            *graphs: Graphs to merge

        Returns:
            Combined graph
        """
        combined = Graph()
        bind_namespaces(combined)

        for g in graphs:
            for triple in g:
                combined.add(triple)

        return combined


def serialize_to_turtle(graph: Graph, output_path: Path | None = None) -> str:
    """Convenience function for serialization.

    Args:
        graph: RDF graph to serialize
        output_path: Optional path to save file

    Returns:
        Turtle string
    """
    serializer = TurtleSerializer(graph)
    return serializer.serialize(output_path=output_path)
