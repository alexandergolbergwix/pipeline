Measure HMO ontology class/property coverage using the committed golden corpus ratchet.

## Golden corpus (CI ratchet — 100% hm: coverage)

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python scripts/check_ontology_coverage.py \
  --fixture data/fixtures/ontology_golden \
  --baseline data/fixtures/ontology_golden/expected_coverage.json \
  --fail-on-regression
```

This builds RDF from `data/fixtures/ontology_golden/records.json` via `MarcToRdfMapper`
and asserts **73/73 classes** and **all hm: properties** are covered at least once.

## Ad-hoc TTL check

```bash
PYTHONPATH=src:. .venv/bin/python scripts/check_ontology_coverage.py \
  --ttl /path/to/manuscripts.ttl \
  --output /path/to/ontology_coverage.json
```

## Production corpus sample (informational only)

The gitignored `data/tsvs/17th_century_samples.tsv` remains useful for spot checks but is
**not** the CI ratchet. Use the golden fixture for regression gates.

## Emission matrix

```bash
PYTHONPATH=src:. .venv/bin/python scripts/generate_ontology_emission_matrix.py
```

Report the coverage percentages. For Wikidata projection boundaries see
`converter/wikidata/projection_coverage.py` and `tests/unit/test_wikidata_projection_coverage.py`.
