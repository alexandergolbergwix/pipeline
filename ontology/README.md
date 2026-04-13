# Hebrew Manuscripts Ontology (HMO)

This directory contains the canonical files of the **Hebrew Manuscripts
Ontology (HMO)** developed by the Mapping Hebrew Manuscripts (MHM) project
at Bar-Ilan University.

| File | Format | Purpose |
|---|---|---|
| `hebrew-manuscripts.ttl` | Turtle | The HMO ontology — primary source of truth |
| `hebrew-manuscripts.owl` | OWL/RDF-XML | Same ontology in RDF/XML serialisation |
| `shacl-shapes.ttl` | Turtle | 38 SHACL validation shapes for HMO instance data |
| `controlled-vocabularies.ttl` | Turtle | SKOS vocabularies for scripts, materials, formats, etc. |
| `catalog-v001.xml` | XML | Protégé catalog file (development convenience) |
| `documentation/` | — | Migration notes, usage guides, audiobook narration |
| `hebrew-manuscripts-original.ttl` | Turtle | Pre-refactor snapshot, kept for provenance |

## License

The ontology, SHACL shapes, and controlled vocabularies are released under
the Creative Commons Attribution 4.0 International license
([CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

## Permanent identifier

The HMO namespace is served via the W3ID Permanent Identifier Community
Group at:

> **https://w3id.org/hebrew-manuscripts/**

This URL uses HTTP content negotiation:

| `Accept:` header | Resolves to |
|---|---|
| `text/turtle` | `hebrew-manuscripts.ttl` (this directory) |
| `application/rdf+xml` (and default) | `hebrew-manuscripts.owl` (this directory) |
| `text/html` | this directory page on GitHub |

The redirect is defined by `perma-id/w3id.org/hebrew-manuscripts/.htaccess`
and is independent of the underlying hosting, so the namespace IRI
remains stable across hosting changes.

## Citing

If you use HMO in your research, please cite:

> Goldberg, A., Prebor, G., & Elmalech, A. (2026).
> Ontology-Driven Linked Data for Hebrew Manuscripts.
> *Submitted to the Semantic Web Journal.*

A persistent Zenodo deposit accompanies the paper; see the paper's
"Resource Availability" section for the DOI.

## Companion archive

A frozen snapshot bundling these files together with the SHACL conformance
report, MARC-to-HMO crosswalk, pilot RDF for the six case-study
manuscripts, and SPARQL competency queries is published on Zenodo and
referenced from the paper.

## Pipeline

The end-to-end MARC-to-HMO conversion pipeline (parser, transformer,
SHACL validator, Wikidata uploader) lives at the repository root —
see [`../README.md`](../README.md).

## Contact

Alexander Goldberg — `shvedbook@gmail.com`
