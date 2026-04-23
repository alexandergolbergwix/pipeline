# Hebrew Manuscripts Ontology (HMO) and MARC-to-RDF Workflow

This repository contains the Hebrew Manuscripts Ontology (HMO), the MARC-to-RDF transformation workflow used in the Mapping Hebrew Manuscripts project, validation materials, and supporting implementation code.

## Review and inspection links

For direct inspection of the materials cited in the Semantic Web Journal submission, use these links first:

- Stable review package on Zenodo: https://doi.org/10.5281/zenodo.19560383
- Project repository: https://github.com/alexandergolbergwix/pipeline
- Persistent ontology namespace: https://w3id.org/hebrew-manuscripts/

Note: the `w3id` URL is the persistent ontology namespace. By default many clients will resolve to the OWL serialization, while clients requesting HTML are redirected to the ontology directory on GitHub. For human-oriented inspection, Zenodo and this GitHub repository are the better entry points.

## What is in this repository

- `ontology/`: HMO ontology files, controlled vocabularies, SHACL shapes, and ontology documentation
- `data/`: pilot RDF outputs, validation inputs, and sample source data
- `mappings/`: MARC-to-HMO crosswalk materials
- `docs/`: manuscript, validation reports, verification queries, supplemental materials, and submission-facing documentation
- `scripts/`: conversion and support scripts
- `converter/`: workflow and RDF generation code

## Main research contribution

The project supports an ontology-driven representation of Hebrew manuscripts as linked data by combining:

- a corpus-specific ontology aligned with CIDOC CRM and LRMoo
- BU-CU-PU structural granularity for manuscript modelling
- a documented MARC-to-RDF transformation workflow
- pilot RDF data and validation materials released for inspection

## Quick reviewer path

1. Open the Zenodo archive and download the released package.
2. Inspect the ontology files under `ontology/`.
3. Load the pilot Turtle graphs from `data/output/`.
4. Run the SPARQL checks documented in `docs/`.
5. Compare the outputs with the released validation reports.

## Pipeline overview

The broader software workflow in this repository supports the following stages:

```text
MARC 21 record -> extraction -> authority matching -> RDF graph -> SHACL validation
```

Additional application code in the repository also supports desktop tooling and project-specific workflows used during development.

## Citation and contact

If you need the stable citable archive, use the Zenodo DOI above.

For project context and review materials, start with `docs/` and the Zenodo archive.

