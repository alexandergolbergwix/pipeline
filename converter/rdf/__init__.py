"""RDF module for graph construction and serialization."""

# Use lazy imports to avoid circular dependency with transformer module
def __getattr__(name):
    if name == "GraphBuilder":
        from .graph_builder import GraphBuilder
        return GraphBuilder
    elif name == "TurtleSerializer":
        from .serializer import TurtleSerializer
        return TurtleSerializer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["GraphBuilder", "TurtleSerializer"]
