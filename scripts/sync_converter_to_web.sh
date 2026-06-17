#!/usr/bin/env bash
# Sync vendored converter modules from the desktop pipeline into mhm-pipeline-web.
set -euo pipefail

PIPELINE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_ROOT="${PIPELINE_ROOT}/../mhm-pipeline-web"
DEST="${WEB_ROOT}/backend/converter"

if [[ ! -d "${WEB_ROOT}/backend" ]]; then
  echo "Web port not found at ${WEB_ROOT}" >&2
  exit 1
fi

copy_tree() {
  local src="$1"
  local rel="$2"
  rsync -a --delete "${src}/" "${DEST}/${rel}/"
}

copy_tree "${PIPELINE_ROOT}/converter/rdf" "rdf"
copy_tree "${PIPELINE_ROOT}/converter/transformer" "transformer"
copy_tree "${PIPELINE_ROOT}/converter/config" "config"

mkdir -p "${DEST}/wikidata"
rsync -a "${PIPELINE_ROOT}/converter/wikidata/projection_coverage.py" "${DEST}/wikidata/"

rsync -a "${PIPELINE_ROOT}/ontology/hebrew-manuscripts.ttl" "${WEB_ROOT}/backend/ontology/"
rsync -a "${PIPELINE_ROOT}/ontology/shacl-shapes.ttl" "${WEB_ROOT}/backend/ontology/"

echo "Synced converter + ontology to ${WEB_ROOT}"
