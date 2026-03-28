Measure how many ontology classes and properties from `ontology/hebrew-manuscripts.ttl` are populated in a real pipeline run over `data/tsvs/17th_century_samples.tsv`.

Run this Python script:

```bash
cd /Users/alexandergo/Documents/Doctorat/pipeline
PYTHONPATH=src:. .venv/bin/python - <<'EOF'
from rdflib import Graph, Namespace, RDF, OWL
from pathlib import Path
from converter.reader.unified_reader import UnifiedReader
from converter.rdf.graph_builder import GraphBuilder

HM = Namespace("http://www.ontology.org.il/HebrewManuscripts/2025-12-06#")

# Load ontology
onto = Graph()
onto.parse("ontology/hebrew-manuscripts.ttl", format="turtle")
onto_classes = {s for s, _, _ in onto.triples((None, RDF.type, OWL.Class)) if str(s).startswith(str(HM))}
onto_obj_props = {s for s, _, _ in onto.triples((None, RDF.type, OWL.ObjectProperty)) if str(s).startswith(str(HM))}
onto_dt_props = {s for s, _, _ in onto.triples((None, RDF.type, OWL.DatatypeProperty)) if str(s).startswith(str(HM))}

# Build data graph from TSV
reader = UnifiedReader()
records = reader.read_file(Path("data/tsvs/17th_century_samples.tsv"))
builder = GraphBuilder()
data_graph = Graph()
for rec in records[:200]:  # sample 200 for speed
    data_graph += builder.build_graph(rec, rec.control_number or "UNKNOWN")

data_types = {o for _, _, o in data_graph.triples((None, RDF.type, None)) if str(o).startswith(str(HM))}
used_preds = {p for _, p, _ in data_graph if str(p).startswith(str(HM))}

used_classes = onto_classes & data_types
used_obj = onto_obj_props & used_preds
used_dt = onto_dt_props & used_preds

print(f"Classes:    {len(used_classes)}/{len(onto_classes)} ({100*len(used_classes)/len(onto_classes):.1f}%)")
print(f"Obj props:  {len(used_obj)}/{len(onto_obj_props)} ({100*len(used_obj)/len(onto_obj_props):.1f}%)")
print(f"Data props: {len(used_dt)}/{len(onto_dt_props)} ({100*len(used_dt)/len(onto_dt_props):.1f}%)")
print(f"\nUnused classes ({len(onto_classes-used_classes)}):")
for c in sorted(onto_classes - used_classes):
    print(f"  {str(c).split('#')[-1]}")
EOF
```

Report the coverage percentages and list unused classes. Compare against the v1.4 baseline (28 classes / 25.2%, 66 properties / 23.9%).
