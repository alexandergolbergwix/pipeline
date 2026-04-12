"""Transformer module for MARC to RDF conversion."""


# Use lazy imports to avoid circular dependency with rdf module
def __getattr__(name):
    if name == "MarcToRdfMapper":
        from .mapper import MarcToRdfMapper

        return MarcToRdfMapper
    elif name == "UriGenerator":
        from .uri_generator import UriGenerator

        return UriGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MarcToRdfMapper", "UriGenerator"]
