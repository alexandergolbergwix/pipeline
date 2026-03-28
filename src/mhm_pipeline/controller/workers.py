"""QThread workers for each pipeline stage."""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class StageWorker(QThread):
    """Base worker thread for a single pipeline stage.

    Subclasses implement run() and emit these signals:
      - progress(int): 0-100 completion percentage
      - log_line(str): a single log message
      - error(str): human-readable error (stage stops)
      - finished(Path): path to the stage output file on success
    """

    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(Path)

    def run(self) -> None:
        raise NotImplementedError


# ── Stage 0: MARC Parse ──────────────────────────────────────────────


class MarcParseWorker(StageWorker):
    """Parse MARC/CSV/TSV records and extract structured data."""

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        device: str,
        start: int = 0,
        end: int = 0,
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._device = device
        self._start = start
        self._end = end

    def run(self) -> None:
        try:
            from converter.parser.unified_reader import UnifiedReader
            from converter.transformer.field_handlers import extract_all_data

            self.log_line.emit(f"Parsing {self._input_path.name}")

            reader = UnifiedReader()
            all_records = list(reader.read_file(self._input_path))
            end = self._end if self._end > 0 else len(all_records)
            records = all_records[self._start:end]
            total = len(records)
            if total == 0:
                self.error.emit("No records found in input file")
                return

            extracted: list[dict[str, object]] = []
            for idx, record in enumerate(records):
                data = extract_all_data(record)
                entry = dataclasses.asdict(data)
                entry["_control_number"] = record.control_number
                extracted.append(entry)
                self.progress.emit(int((idx + 1) / total * 100))

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "marc_extracted.json"
            output_path.write_text(
                json.dumps(extracted, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            self.log_line.emit(f"Extracted {total} records")
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("MARC parse failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 1: NER ─────────────────────────────────────────────────────


class NerWorker(StageWorker):
    """Run NER inference on extracted MARC data."""

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        model_path: str,
        device: str,
        batch_size: int,
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._model_path = model_path
        self._device = device
        self._batch_size = batch_size

    def run(self) -> None:
        try:
            import sys as _sys
            from pathlib import Path as _Path
            # workers.py lives at src/mhm_pipeline/controller/workers.py
            # parents[3] is the repo root; ner/ is a sibling of src/
            _ner_dir = str(_Path(__file__).parents[3] / "ner")
            if _ner_dir not in _sys.path:
                _sys.path.insert(0, _ner_dir)
            from ner.inference_pipeline import JointNERPipeline

            self.log_line.emit("Loading NER model")

            records: list[dict[str, object]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            pipeline = JointNERPipeline(
                model_path=self._model_path,
                device=self._device,
            )

            self.log_line.emit(f"Processing {total} records")
            results: list[dict[str, object]] = []
            for idx, record in enumerate(records):
                texts: list[str] = []
                for note in (record.get("notes") or []):
                    if isinstance(note, str) and note.strip():
                        texts.append(note)
                colophon = record.get("colophon_text")
                if isinstance(colophon, str) and colophon.strip():
                    texts.append(colophon)

                ner_entities: list[dict[str, object]] = []
                for text in texts:
                    ner_entities.extend(pipeline.process_text(text))

                results.append({
                    "_control_number": record.get("_control_number"),
                    "entities": ner_entities,
                })
                self.progress.emit(int((idx + 1) / total * 100))

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "ner_results.json"
            output_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            self.log_line.emit(f"NER complete — {total} records processed")
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("NER failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 2: Authority matching ───────────────────────────────────────


class AuthorityWorker(StageWorker):
    """Match NER entities and MARC places against authority files.

    Sources:
      - Mazal (מז"ל / NLI): persons, places, works — local SQLite index
      - VIAF: persons — REST API
      - GeoNames: places from MARC extract — REST API
    """

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        marc_path: Path | None,
        enable_viaf: bool,
        enable_kima: bool,
        kima_db_path: str = "",
        mazal_db_path: str = "",
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._marc_path = marc_path
        self._enable_viaf = enable_viaf
        self._enable_kima = enable_kima
        self._kima_db_path = kima_db_path or None
        self._mazal_db_path = mazal_db_path or None

    def run(self) -> None:
        try:
            from converter.authority.mazal_matcher import MazalMatcher
            from converter.authority.viaf_matcher import VIAFMatcher
            from converter.authority.kima_matcher import KimaMatcher

            # ── load NER results ──────────────────────────────────────
            records: list[dict[str, object]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # ── load MARC extract for place matching ──────────────────
            marc_by_cn: dict[str, dict[str, object]] = {}
            if self._marc_path and self._marc_path.exists():
                marc_records: list[dict[str, object]] = json.loads(
                    self._marc_path.read_text(encoding="utf-8"),
                )
                marc_by_cn = {
                    str(r.get("_control_number", "")): r
                    for r in marc_records
                }

            # ── initialise matchers ───────────────────────────────────
            self.log_line.emit("Loading Mazal authority index")
            mazal = MazalMatcher(index_path=self._mazal_db_path)

            viaf: VIAFMatcher | None = None
            if self._enable_viaf:
                self.log_line.emit("VIAF matching enabled")
                viaf = VIAFMatcher()

            kima: KimaMatcher | None = None
            if self._enable_kima and marc_by_cn:
                self.log_line.emit("KIMA place matching enabled")
                kima = KimaMatcher(index_path=self._kima_db_path)

            # ── match ─────────────────────────────────────────────────
            for idx, record in enumerate(records):
                cn = str(record.get("_control_number", ""))

                # --- persons from NER entities ---
                entities = record.get("entities") or []
                for entity in entities:
                    name = str(entity.get("person", "")).strip()
                    if not name:
                        continue

                    mazal_id = mazal.match_person(name)
                    if mazal_id:
                        entity["mazal_id"] = mazal_id

                    if viaf:
                        viaf_uri = viaf.match_person(name)
                        if viaf_uri:
                            entity["viaf_uri"] = viaf_uri

                # --- places from MARC extract ---
                if kima and cn in marc_by_cn:
                    marc_rec = marc_by_cn[cn]
                    places: list[str] = [
                        str(p) for p in (marc_rec.get("related_places") or []) if p
                    ]
                    place_matches: dict[str, str] = {}
                    for place in places:
                        uri = kima.match_place(place)
                        if uri:
                            place_matches[place] = uri
                    if place_matches:
                        record["kima_places"] = place_matches

                self.progress.emit(int((idx + 1) / total * 100))

            mazal.close()
            if kima is not None:
                kima.close()

            # ── write output ─────────────────────────────────────────
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "authority_enriched.json"
            output_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            # summary
            n_viaf = sum(
                1 for r in records
                for e in (r.get("entities") or [])
                if e.get("viaf_uri")
            )
            n_mazal = sum(
                1 for r in records
                for e in (r.get("entities") or [])
                if e.get("mazal_id")
            )
            n_kima = sum(1 for r in records if r.get("kima_places"))
            self.log_line.emit(
                f"Authority matching complete — {total} records | "
                f"Mazal: {n_mazal} | VIAF: {n_viaf} | KIMA: {n_kima} records with place URIs"
            )
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("Authority matching failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Mazal Index Builder (utility worker) ─────────────────────────────


class MazalIndexWorker(StageWorker):
    """Rebuild the Mazal (NLI) SQLite authority index from XML source files.

    This is a utility worker, not a numbered pipeline stage.  It reads
    all NLIAUT*.xml files from *xml_dir* and writes a fresh SQLite database
    to *db_path*.  Progress is emitted as a percentage of files processed.
    """

    def __init__(self, xml_dir: Path, db_path: Path) -> None:
        super().__init__()
        self._xml_dir = xml_dir
        self._db_path = db_path

    def run(self) -> None:
        try:
            from converter.authority.mazal_index import MazalIndex, parse_xml_records

            xml_files = sorted(self._xml_dir.glob("NLIAUT*.xml"))
            if not xml_files:
                xml_files = sorted(self._xml_dir.glob("NLIAUT*.xml.gz"))
            if not xml_files:
                self.error.emit(f"No NLIAUT*.xml files found in {self._xml_dir}")
                return

            self.log_line.emit(
                f"Building Mazal index from {len(xml_files)} files in {self._xml_dir}"
            )

            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            index = MazalIndex(str(self._db_path))
            index.create_schema()

            total_records = 0
            for file_idx, xml_file in enumerate(xml_files):
                self.log_line.emit(f"Processing {xml_file.name}…")
                for nli_id, record in parse_xml_records(str(xml_file)):
                    index.insert_authority(
                        nli_id=nli_id,
                        entity_type=record["entity_type"],
                        preferred_name_heb=(
                            record["names_heb"][0] if record["names_heb"] else None
                        ),
                        preferred_name_lat=(
                            record["names_lat"][0] if record["names_lat"] else None
                        ),
                        dates=record.get("dates"),
                        aleph_id=record.get("aleph_id"),
                    )
                    for name in record.get("names_heb", []):
                        norm = MazalIndex.normalize_name(name)
                        if norm:
                            index.insert_name_variant(
                                norm, nli_id, record["entity_type"], "heb"
                            )
                    for name in record.get("names_lat", []):
                        norm = MazalIndex.normalize_name(name)
                        if norm:
                            index.insert_name_variant(
                                norm, nli_id, record["entity_type"], "lat"
                            )
                    total_records += 1
                    if total_records % 10_000 == 0:
                        index.conn.commit()

                index.conn.commit()
                self.progress.emit(int((file_idx + 1) / len(xml_files) * 100))

            index.close()
            self.log_line.emit(
                f"Mazal index complete — {total_records:,} records → {self._db_path}"
            )
            self.progress.emit(100)
            self.finished.emit(self._db_path)
        except Exception as exc:
            logger.error("Mazal index build failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── KIMA Index Builder (utility worker) ──────────────────────────────


class KimaIndexWorker(StageWorker):
    """Build the KIMA SQLite authority index from TSV source files.

    This is a utility worker, not a numbered pipeline stage.
    Progress is emitted as: 40 % after places, 85 % after variants, 100 % done.
    """

    def __init__(self, tsv_dir: Path, db_path: Path) -> None:
        super().__init__()
        self._tsv_dir = tsv_dir
        self._db_path = db_path

    def run(self) -> None:
        try:
            from converter.authority.kima_index import build_kima_index

            self.log_line.emit(
                f"Building KIMA index from {self._tsv_dir} → {self._db_path}"
            )

            def _progress(pct: int) -> None:
                self.progress.emit(pct)

            build_kima_index(
                tsv_dir=str(self._tsv_dir),
                db_path=str(self._db_path),
                verbose=True,
                progress_cb=_progress,
            )

            self.log_line.emit(f"KIMA index complete: {self._db_path}")
            self.progress.emit(100)
            self.finished.emit(self._db_path)
        except Exception as exc:
            logger.error("KIMA index build failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 3: RDF Build ───────────────────────────────────────────────


class RdfBuildWorker(StageWorker):
    """Build RDF graph from MARC records and serialize to Turtle."""

    def __init__(
        self, input_path: Path, output_dir: Path, rdf_format: str = "Turtle"
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._rdf_format = rdf_format

    def run(self) -> None:
        try:
            from converter.transformer.mapper import MarcToRdfMapper

            self.log_line.emit("Building RDF graph")

            mapper = MarcToRdfMapper()
            graph = mapper.map_file(self._input_path)

            fmt_map = {"Turtle": "turtle", "JSON-LD": "json-ld", "N-Triples": "nt"}
            fmt = fmt_map.get(self._rdf_format, "turtle")
            ext_map = {"turtle": ".ttl", "json-ld": ".jsonld", "nt": ".nt"}
            ext = ext_map.get(fmt, ".ttl")

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / f"output{ext}"
            graph.serialize(destination=str(output_path), format=fmt)

            self.log_line.emit(
                f"RDF graph built — {len(graph)} triples"
            )
            self.progress.emit(100)
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("RDF build failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 4: SHACL Validate ──────────────────────────────────────────


class ShaclValidateWorker(StageWorker):
    """Validate an RDF Turtle file against SHACL shapes."""

    def __init__(
        self,
        ttl_path: Path,
        shapes_path: Path,
        output_dir: Path,
    ) -> None:
        super().__init__()
        self._ttl_path = ttl_path
        self._shapes_path = shapes_path
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            from converter.validation.shacl_validator import ShaclValidator

            self.log_line.emit("Validating against SHACL shapes")
            self.progress.emit(10)

            validator = ShaclValidator(shapes_path=self._shapes_path)
            result = validator.validate_file(self._ttl_path)

            self._output_dir.mkdir(parents=True, exist_ok=True)
            report_path = self._output_dir / "shacl_report.txt"

            lines: list[str] = [f"Conforms: {result.conforms}"]
            if result.results_text:
                lines.append("")
                lines.append(result.results_text)
            report_path.write_text("\n".join(lines), encoding="utf-8")

            self.progress.emit(100)
            status = "passed" if result.conforms else "failed"
            self.log_line.emit(f"SHACL validation {status}")
            self.finished.emit(report_path)
        except Exception as exc:
            logger.error("SHACL validation failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 5: Wikidata Upload (stub) ──────────────────────────────────


class WikidataUploadWorker(StageWorker):
    """Upload RDF data to Wikidata (not yet implemented)."""

    def __init__(
        self,
        ttl_path: Path,
        token: str,
        dry_run: bool,
    ) -> None:
        super().__init__()
        self._ttl_path = ttl_path
        self._token = token
        self._dry_run = dry_run

    def run(self) -> None:
        try:
            self.log_line.emit("Wikidata upload not yet implemented")
            self.progress.emit(100)
            self.finished.emit(self._ttl_path)
        except Exception as exc:
            logger.error("Wikidata upload failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
