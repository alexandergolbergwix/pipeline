"""RDF namespace definitions for the Hebrew Manuscripts Ontology."""

from rdflib import Graph, Namespace

HM = Namespace("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#")
LRMOO = Namespace("http://iflastandards.info/ns/lrm/lrmoo/")
CIDOC = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
NLI = Namespace("https://www.nli.org.il/en/authorities/")

NAMESPACES = {
    "hm": HM,
    "lrmoo": LRMOO,
    "cidoc-crm": CIDOC,
    "rdf": RDF,
    "rdfs": RDFS,
    "xsd": XSD,
    "skos": SKOS,
    "nli": NLI,
}


def bind_namespaces(graph: Graph) -> Graph:
    """Bind all namespaces to an RDF graph for proper serialization."""
    for prefix, namespace in NAMESPACES.items():
        graph.bind(prefix, namespace)
    return graph
