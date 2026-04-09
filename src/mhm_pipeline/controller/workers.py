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

            # Defensive: Deep copy to avoid thread-safety issues during JSON serialization
            # The crash (SIGABRT in escape_unicode) occurred when the list was being
            # modified while json.dumps was iterating over it
            import copy

            safe_extracted = copy.deepcopy(extracted)
            json_text = json.dumps(safe_extracted, ensure_ascii=False, indent=2, default=str)
            output_path.write_text(json_text, encoding="utf-8")

            self.log_line.emit(f"Extracted {total} records")
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("MARC parse failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 1: NER ─────────────────────────────────────────────────────


def _split_provenance_text(text: str) -> list[str]:
    """Split MARC 561 provenance text into single-entity segments.

    The provenance NER model was trained on single-entity samples, so we
    split aggressively at: pipes, commas before quotes, colons before
    quotes, and parenthetical boundaries.
    """
    import re  # noqa: PLC0415
    segments: list[str] = []
    for pipe_seg in text.split("|"):
        pipe_seg = pipe_seg.strip()
        if not pipe_seg:
            continue
        colon_parts = re.split(r':\s*(?=")', pipe_seg, maxsplit=1)
        if len(colon_parts) == 2:
            prefix = colon_parts[0].strip()
            rest = colon_parts[1].strip()
            name_segments = re.split(r',\s*(?=")', rest)
            for ns in name_segments:
                ns = ns.strip()
                if ns:
                    segments.append(ns)
            if prefix and not any(prefix in s for s in segments):
                segments.append(prefix)
        else:
            sub_segments = re.split(r',\s*(?=")', pipe_seg)
            segments.extend(s.strip() for s in sub_segments if s.strip())
    return segments


def _extract_texts_from_record(record: dict) -> list[str]:
    """Extract non-empty text segments from notes and colophon."""
    texts: list[str] = []
    for note in record.get("notes") or []:
        if isinstance(note, str) and note.strip():
            texts.append(note)
    colophon = record.get("colophon_text")
    if isinstance(colophon, str) and colophon.strip():
        texts.append(colophon)
    return texts


def _calculate_segment_offset(texts: list[str], index: int) -> int:
    """Calculate character offset for text at given index.

    The offset accounts for all previous text lengths plus newline separators.
    """
    offset = 0
    for i, text in enumerate(texts):
        if i >= index:
            break
        offset += len(text)
        if i < len(texts) - 1:
            offset += 1  # Account for "\n"
    return offset


def _adjust_entity_positions(
    entities: list[dict[str, object]], offset: int
) -> list[dict[str, object]]:
    """Adjust entity positions by adding offset to start/end."""
    for ent in entities:
        ent["start"] = ent.get("start", 0) + offset
        ent["end"] = ent.get("end", 0) + offset
    return entities


def _process_text_segment(
    pipeline: object, text: str, offset: int
) -> list[dict[str, object]]:
    """Process one text segment and return position-adjusted entities."""
    segment_entities = pipeline.process_text(text)
    return _adjust_entity_positions(segment_entities, offset)


class NerWorker(StageWorker):
    """Run NER inference on extracted MARC data.

    Runs up to three NER pipelines:
    1. Person NER (JointNERPipeline) on notes + colophon
    2. Provenance NER (NERInferencePipeline) on MARC 561
    3. Contents NER (NERInferencePipeline) on MARC 505
    """

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        model_path: str,
        device: str,
        batch_size: int,
        provenance_model_path: str = "",
        contents_model_path: str = "",
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._model_path = model_path
        self._device = device
        self._batch_size = batch_size
        self._provenance_model_path = provenance_model_path
        self._contents_model_path = contents_model_path

    @staticmethod
    def _resolve_model_path(explicit: str, env_var: str, fallback: str) -> str:
        """Resolve model path from explicit arg, env var, or fallback."""
        import os  # noqa: PLC0415
        if explicit:
            return explicit
        from_env = os.environ.get(env_var, "")
        if from_env and Path(from_env).exists():
            return from_env
        if Path(fallback).exists():
            return fallback
        return ""

    def run(self) -> None:  # noqa: C901
        try:
            import copy  # noqa: PLC0415
            import sys as _sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            _ner_dir = str(_Path(__file__).parents[3] / "ner")
            if _ner_dir not in _sys.path:
                _sys.path.insert(0, _ner_dir)
            from ner.inference_pipeline import JointNERPipeline  # noqa: PLC0415

            self.log_line.emit("Loading NER models...")

            records: list[dict[str, object]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # 1. Person NER (always loaded)
            person_pipeline = JointNERPipeline(
                model_path=self._model_path,
                device=self._device,
            )

            # 2. Provenance NER (optional — independent load to avoid weight corruption)
            provenance_pipeline = None
            prov_path = self._resolve_model_path(
                self._provenance_model_path,
                "MHM_BUNDLED_PROVENANCE_MODEL",
                str(_Path(__file__).parents[3] / "ner" / "provenance_ner_model.pt"),
            )
            if prov_path:
                from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
                self.log_line.emit("Loading provenance NER model...")
                provenance_pipeline = NERInferencePipeline(
                    model_path=prov_path, device=self._device,
                )

            # 3. Contents NER (optional)
            contents_pipeline = None
            cont_path = self._resolve_model_path(
                self._contents_model_path,
                "MHM_BUNDLED_CONTENTS_MODEL",
                str(_Path(__file__).parents[3] / "ner" / "contents_ner_model.pt"),
            )
            if cont_path:
                from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415
                self.log_line.emit("Loading contents NER model...")
                contents_pipeline = NERInferencePipeline(
                    model_path=cont_path, device=self._device,
                )

            models_loaded = 1 + (1 if provenance_pipeline else 0) + (1 if contents_pipeline else 0)
            self.log_line.emit(f"Processing {total} records with {models_loaded} NER model(s)")

            results: list[dict[str, object]] = []
            for idx, record in enumerate(records):
                all_entities: list[dict[str, object]] = []

                # Person NER on notes + colophon
                texts = _extract_texts_from_record(record)
                for i, text in enumerate(texts):
                    offset = _calculate_segment_offset(texts, i)
                    segment_entities = _process_text_segment(person_pipeline, text, offset)
                    for ent in segment_entities:
                        ent["source"] = "person_ner"
                    all_entities.extend(segment_entities)

                # Provenance NER on MARC 561
                if provenance_pipeline:
                    provenance_text = record.get("provenance") or ""
                    if isinstance(provenance_text, str) and provenance_text.strip():
                        clean_prov = str(provenance_text).replace('""', '"')
                        segments = _split_provenance_text(clean_prov)
                        for segment in segments:
                            if len(segment) >= 3:
                                try:
                                    prov_entities = provenance_pipeline.process_text(segment)
                                    for ent in prov_entities:
                                        ent["source"] = "provenance_ner"
                                    all_entities.extend(prov_entities)
                                except Exception as prov_exc:
                                    logger.warning("Provenance NER error: %s", prov_exc)

                # Contents NER on MARC 505
                if contents_pipeline:
                    for content in (record.get("contents") or []):
                        if isinstance(content, dict):
                            parts = []
                            if content.get("folio_range"):
                                parts.append(f"דף {content['folio_range']}:")
                            if content.get("responsibility"):
                                parts.append(f"{content['responsibility']}:")
                            if content.get("title"):
                                parts.append(str(content["title"]))
                            text_505 = " ".join(parts)
                        elif isinstance(content, str):
                            text_505 = content
                        else:
                            continue
                        if text_505.strip() and len(text_505) >= 5:
                            try:
                                cont_entities = contents_pipeline.process_text(text_505)
                                for ent in cont_entities:
                                    ent["source"] = "contents_ner"
                                all_entities.extend(cont_entities)
                            except Exception as cont_exc:
                                logger.warning("Contents NER error: %s", cont_exc)

                results.append({
                    "_control_number": record.get("_control_number"),
                    "text": "\n".join(texts),
                    "entities": all_entities,
                })
                self.progress.emit(int((idx + 1) / total * 100))

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "ner_results.json"

            safe_results = copy.deepcopy(results)
            json_text = json.dumps(safe_results, ensure_ascii=False, indent=2, default=str)
            output_path.write_text(json_text, encoding="utf-8")

            person_count = sum(
                1 for r in results for e in r["entities"] if e.get("source") == "person_ner"
            )
            prov_count = sum(
                1 for r in results for e in r["entities"] if e.get("source") == "provenance_ner"
            )
            cont_count = sum(
                1 for r in results for e in r["entities"] if e.get("source") == "contents_ner"
            )
            self.log_line.emit(
                f"NER complete — {total} records, "
                f"{person_count} person + {prov_count} provenance + {cont_count} contents entities"
            )
            self.finished.emit(output_path)
        except Exception as exc:
            logger.error("NER failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 2: Authority matching ───────────────────────────────────────


class AuthorityWorker(StageWorker):
    """Match NER entities and MARC places against authority files.

    Sources:
      - Mazal (מז"ל / NLI): persons, places, works — local SQLite index
      - VIAF: persons — public SRU API (no key required)
      - KIMA: places from MARC extract — local SQLite index
    """

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        ner_path: Path | None,
        enable_viaf: bool,
        enable_kima: bool,
        kima_db_path: str = "",
        mazal_db_path: str = "",
    ) -> None:
        super().__init__()
        self._input_path = input_path       # MARC extract (stage 0 output)
        self._output_dir = output_dir
        self._ner_path = ner_path           # NER results (stage 1 output, optional)
        self._enable_viaf = enable_viaf
        self._enable_kima = enable_kima
        self._kima_db_path = kima_db_path or None
        self._mazal_db_path = mazal_db_path or None

    # ── Pure predicate functions ───────────────────────────────────────

    @staticmethod
    def _has_valid_name(data: dict, key: str) -> bool:
        """Check if entity has a valid non-empty name."""
        name = str(data.get(key, "")).strip()
        return bool(name)

    @staticmethod
    def _is_already_matched(match_info: dict) -> bool:
        """Check if entity already has at least one authority match."""
        return bool(match_info.get("mazal_id") or match_info.get("viaf_uri"))

    # ── Pure transformation functions ──────────────────────────────────

    @staticmethod
    def _create_match_info(
        name: str,
        role: str,
        source: str,
        field: str,
        mazal_id: str | None,
        viaf_uri: str | None,
    ) -> dict[str, object]:
        """Create a match info dictionary with authority references."""
        info: dict[str, object] = {
            "name": name,
            "role": role,
            "source": source,
            "field": field,
        }
        if mazal_id:
            info["mazal_id"] = mazal_id
        if viaf_uri:
            info["viaf_uri"] = viaf_uri
        return info

    @staticmethod
    def _count_match_result(match_info: dict) -> dict[str, int]:
        """Count whether this match result should increment counters."""
        has_mazal = bool(match_info.get("mazal_id"))
        has_viaf = bool(match_info.get("viaf_uri"))
        return {
            "counted": 1,
            "matched": 1 if (has_mazal or has_viaf) else 0,
        }

    # ── Authority matching functions ─────────────────────────────────

    def _match_against_authorities(
        self,
        name: str,
        mazal: "MazalMatcher",
        viaf: "VIAFMatcher | None",
    ) -> tuple[str | None, str | None]:
        """Match a name against Mazal and optionally VIAF authorities.

        Returns tuple of (mazal_id, viaf_uri).
        """
        mazal_id = mazal.match_person(name)
        viaf_uri = None

        if viaf:
            viaf_uri = viaf.match_person(name)

        return mazal_id, viaf_uri

    def _match_ner_entity(
        self,
        entity: dict,
        mazal: "MazalMatcher",
        viaf: "VIAFMatcher | None",
    ) -> dict[str, int]:
        """Match a single NER entity against authority databases.

        Updates entity dict in place with authority IDs.
        Returns dict with counted/matched flags for statistics.
        """
        # Person entities from JointNER
        if self._has_valid_name(entity, "person"):
            name = str(entity.get("person", "")).strip()
        # OWNER and WORK_AUTHOR entities from provenance/contents NER
        elif entity.get("type") in ("OWNER", "WORK_AUTHOR") and self._has_valid_name(entity, "text"):
            name = str(entity.get("text", "")).strip()
        else:
            return {"counted": 0, "matched": 0}
        mazal_id, viaf_uri = self._match_against_authorities(name, mazal, viaf)

        if mazal_id:
            entity["mazal_id"] = mazal_id
        if viaf_uri:
            entity["viaf_uri"] = viaf_uri

        return {"counted": 1, "matched": 1 if (mazal_id or viaf_uri) else 0}

    def _match_marc_person_entry(
        self,
        person: dict,
        role: str,
        field: str,
        mazal: "MazalMatcher",
        viaf: "VIAFMatcher | None",
    ) -> dict[str, object] | None:
        """Match a single MARC person entry (author or contributor).

        Returns match info dict with count metadata, or None if no valid name.
        """
        # Guard clause: skip entries without valid names
        if not self._has_valid_name(person, "name"):
            return None

        name = str(person.get("name", "")).strip()
        role_value = str(person.get("role", role))
        mazal_id, viaf_uri = self._match_against_authorities(name, mazal, viaf)

        match_info = self._create_match_info(
            name=name,
            role=role_value,
            source="MARC",
            field=field,
            mazal_id=mazal_id,
            viaf_uri=viaf_uri,
        )

        counts = self._count_match_result(match_info)
        match_info.update(counts)

        return match_info

    def _match_marc_persons(
        self,
        control_number: str,
        marc_by_cn: dict[str, dict[str, object]],
        mazal: "MazalMatcher",
        viaf: "VIAFMatcher | None",
    ) -> list[dict[str, object]]:
        """Match all persons from MARC record (authors and contributors).

        Returns list of match info dicts with count metadata.
        """
        # Guard clause: return empty if record not found
        if control_number not in marc_by_cn:
            return []

        marc_rec = marc_by_cn[control_number]
        matches: list[dict[str, object]] = []

        # Match authors (100, 110 fields)
        for author in marc_rec.get("authors") or []:
            result = self._match_marc_person_entry(
                person=author,
                role="author",
                field="100/110/111",
                mazal=mazal,
                viaf=viaf,
            )
            if result:
                matches.append(result)

        # Match contributors (700, 710 fields)
        for contributor in marc_rec.get("contributors") or []:
            result = self._match_marc_person_entry(
                person=contributor,
                role="contributor",
                field="700/710/711",
                mazal=mazal,
                viaf=viaf,
            )
            if result:
                matches.append(result)

        return matches

    def _match_marc_places(
        self,
        control_number: str,
        marc_by_cn: dict[str, dict[str, object]],
        kima: "KimaMatcher | None",
    ) -> dict[str, str] | None:
        """Match places from MARC record against KIMA authority.

        Returns dict mapping place names to URIs, or None if no kima/kima not available.
        """
        # Guard clauses: return early if dependencies not available
        if kima is None:
            return None
        if control_number not in marc_by_cn:
            return None

        marc_rec = marc_by_cn[control_number]
        places: list[str] = [
            str(p) for p in (marc_rec.get("related_places") or []) if p
        ]

        if not places:
            return None

        place_matches: dict[str, str] = {}
        for place in places:
            uri = kima.match_place(place)
            if uri:
                place_matches[place] = uri

        return place_matches if place_matches else None

    # ── Helper methods for initialization ────────────────────────────

    def _load_ner_by_control_number(self) -> dict[str, dict[str, object]]:
        """Load NER records indexed by control number."""
        if not self._ner_path or not self._ner_path.exists():
            return {}

        ner_records: list[dict[str, object]] = json.loads(
            self._ner_path.read_text(encoding="utf-8"),
        )
        ner_by_cn = {
            str(r.get("_control_number", "")): r
            for r in ner_records
        }
        self.log_line.emit(f"Loaded {len(ner_by_cn)} NER records for entity merging")
        return ner_by_cn

    @staticmethod
    def _merge_ner_into_records(
        records: list[dict[str, object]],
        ner_by_cn: dict[str, dict[str, object]],
    ) -> int:
        """Merge NER entities into MARC records by control number.

        Adds ``ner_entities`` field to each record that has NER data.
        Returns the number of records enriched.
        """
        enriched = 0
        for record in records:
            cn = str(record.get("_control_number", ""))
            ner_rec = ner_by_cn.get(cn)
            if ner_rec and ner_rec.get("entities"):
                record["entities"] = ner_rec["entities"]
                enriched += 1
        return enriched

    def _init_viaf(self, viaf_matcher_class: type) -> "VIAFMatcher | None":
        """Initialize VIAF matcher if enabled."""
        if not self._enable_viaf:
            return None
        self.log_line.emit("VIAF matching enabled")
        return viaf_matcher_class()

    def _init_kima(
        self,
        kima_matcher_class: type,
    ) -> "KimaMatcher | None":
        """Initialize KIMA matcher if enabled."""
        if not self._enable_kima:
            return None
        self.log_line.emit("KIMA place matching enabled")
        return kima_matcher_class(index_path=self._kima_db_path)

    def run(self) -> None:
        try:
            from converter.authority.mazal_matcher import MazalMatcher
            from converter.authority.viaf_matcher import VIAFMatcher
            from converter.authority.kima_matcher import KimaMatcher

            # ── load MARC extract (primary input) ────────────────────
            self.log_line.emit(f"Loading MARC extract from {self._input_path.name}")
            records: list[dict[str, object]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # ── merge NER entities (optional) ────────────────────────
            ner_by_cn = self._load_ner_by_control_number()
            if ner_by_cn:
                enriched = self._merge_ner_into_records(records, ner_by_cn)
                self.log_line.emit(f"Merged NER entities into {enriched}/{total} records")

            # ── index records by control number (for place matching) ─
            marc_by_cn: dict[str, dict[str, object]] = {
                str(r.get("_control_number", "")): r for r in records
            }

            # ── initialise matchers ───────────────────────────────────
            self.log_line.emit("Loading Mazal authority index")
            mazal = MazalMatcher(index_path=self._mazal_db_path)
            self.log_line.emit(f"Mazal index available: {mazal.is_available} (path: {mazal.index_path})")

            viaf = self._init_viaf(VIAFMatcher)
            kima = self._init_kima(KimaMatcher)

            # ── match ─────────────────────────────────────────────────
            total_entities = 0
            total_matched = 0

            for idx, record in enumerate(records):
                cn = str(record.get("_control_number", ""))

                # --- persons from NER entities (merged from stage 1) ---
                ner_entities = record.get("entities") or []
                for entity in ner_entities:
                    result = self._match_ner_entity(entity, mazal, viaf)
                    total_entities += result["counted"]
                    total_matched += result["matched"]

                # --- persons from MARC name fields (100/110/111/700/710/711) ---
                marc_matches = self._match_marc_persons(cn, marc_by_cn, mazal, viaf)
                if marc_matches:
                    record["marc_authority_matches"] = marc_matches
                    total_entities += sum(m["counted"] for m in marc_matches)
                    total_matched += sum(m["matched"] for m in marc_matches)

                # --- places from MARC extract ---
                place_result = self._match_marc_places(cn, marc_by_cn, kima)
                if place_result:
                    record["kima_places"] = place_result

                self.progress.emit(int((idx + 1) / total * 100))

            # Log summary
            self.log_line.emit(f"Authority matching: {total_matched}/{total_entities} entities matched")

            mazal.close()
            if kima is not None:
                kima.close()

            # ── write output ─────────────────────────────────────────
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "authority_enriched.json"

            # Defensive: Deep copy to avoid thread-safety issues during JSON serialization
            import copy

            safe_records = copy.deepcopy(records)
            json_text = json.dumps(safe_records, ensure_ascii=False, indent=2, default=str)
            output_path.write_text(json_text, encoding="utf-8")

            # summary
            n_viaf = sum(
                1 for r in records
                for e in (r.get("entities") or [])
                if e.get("viaf_uri")
            ) + sum(
                1 for r in records
                for e in (r.get("marc_authority_matches") or [])
                if e.get("viaf_uri")
            )
            n_mazal = sum(
                1 for r in records
                for e in (r.get("entities") or [])
                if e.get("mazal_id")
            ) + sum(
                1 for r in records
                for e in (r.get("marc_authority_matches") or [])
                if e.get("mazal_id")
            )
            n_kima = sum(1 for r in records if r.get("kima_places"))
            n_ner = sum(1 for r in records if r.get("entities"))
            n_marc_matched = sum(
                len(r.get("marc_authority_matches", []))
                for r in records
            )
            self.log_line.emit(
                f"Authority matching complete — {total} records | "
                f"NER enriched: {n_ner} | "
                f"Mazal: {n_mazal} | VIAF: {n_viaf} | KIMA: {n_kima} | "
                f"MARC name fields matched: {n_marc_matched}"
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
    """Build Wikidata items from authority-enriched JSON, reconcile, and upload.

    Three-phase pipeline:
    1. Build WikidataItem objects from authority_enriched.json
    2. Reconcile against existing Wikidata entities (SPARQL)
    3. Upload to Wikidata (live) or export QuickStatements (dry run)
    """

    entity_status = pyqtSignal(str, str, str, str)  # (local_id, status, qid, message)

    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        token: str = "",
        dry_run: bool = True,
        batch_mode: bool = False,
    ) -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._token = token
        self._dry_run = dry_run
        self._batch_mode = batch_mode

    def run(self) -> None:  # noqa: C901
        try:
            from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415
            from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415
            from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

            records: list[dict[str, object]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # Phase 1: Build Wikidata items
            self.log_line.emit(f"Phase 1/3: Building items from {total} records...")
            builder = WikidataItemBuilder()
            items = builder.build_all(
                records,
                progress_cb=lambda i, t: self.progress.emit(int(i / t * 30)),
            )
            self.log_line.emit(
                f"Built {len(items)} items ({builder.person_count} persons, "
                f"{len(items) - builder.person_count} manuscripts)"
            )

            # Phase 2: Reconcile against Wikidata
            self.log_line.emit("Phase 2/3: Reconciling against Wikidata...")
            reconciler = WikidataReconciler()
            reconciled: dict[str, str | None] = {}
            for i, item in enumerate(items):
                if item.entity_type == "person":
                    # Check VIAF/NLI IDs
                    for stmt in item.statements:
                        if stmt.property_id == "P214":  # VIAF
                            qid = reconciler.reconcile_person_by_viaf(str(stmt.value))
                            if qid:
                                reconciled[item.local_id] = qid
                                break
                        elif stmt.property_id == "P8189":  # NLI
                            qid = reconciler.reconcile_person_by_nli_id(str(stmt.value))
                            if qid:
                                reconciled[item.local_id] = qid
                                break
                elif item.entity_type == "manuscript":
                    for stmt in item.statements:
                        if stmt.property_id == "P8189":
                            qid = reconciler.reconcile_manuscript_by_nli_id(str(stmt.value))
                            if qid:
                                reconciled[item.local_id] = qid
                                break
                if (i + 1) % 20 == 0:
                    self.progress.emit(30 + int((i + 1) / len(items) * 30))

            builder.apply_reconciliation(reconciled)
            n_found = sum(1 for v in reconciled.values() if v)
            self.log_line.emit(f"Reconciled: {n_found}/{len(items)} already on Wikidata")
            self.progress.emit(60)

            # Phase 3: Upload or export
            self._output_dir.mkdir(parents=True, exist_ok=True)

            if self._dry_run:
                self.log_line.emit("Phase 3/3: Exporting QuickStatements (dry run)...")
                exporter = QuickStatementsExporter()
                output_path = self._output_dir / "quickstatements.txt"
                exporter.export_to_file(items, output_path)

                # Also save item summary as JSON
                summary_path = self._output_dir / "wikidata_items.json"
                summary = []
                for item in items:
                    summary.append({
                        "local_id": item.local_id,
                        "entity_type": item.entity_type,
                        "labels": item.labels,
                        "existing_qid": item.existing_qid,
                        "statements_count": len(item.statements),
                    })
                summary_path.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                self.log_line.emit(
                    f"Dry run complete — {len(items)} items exported to {output_path.name}"
                )
                self.progress.emit(100)
                self.finished.emit(output_path)
            else:
                # Live upload
                self.log_line.emit("Phase 3/3: Uploading to Wikidata...")
                from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

                uploader = WikidataUploader(
                    token=self._token,
                    batch_mode=self._batch_mode,
                )

                def _entity_cb(
                    local_id: str, status: str, qid: str | None, msg: str,
                ) -> None:
                    self.entity_status.emit(local_id, status, qid or "", msg)

                results = uploader.upload_all(
                    items,
                    progress_cb=lambda i, t, msg: self.progress.emit(
                        60 + int(i / t * 40),
                    ),
                    entity_cb=_entity_cb,
                )

                success = sum(1 for r in results if r.status in ("success", "exists"))
                failed = sum(1 for r in results if r.status == "failed")

                # Save results
                output_path = self._output_dir / "upload_results.json"
                result_data = [
                    {
                        "local_id": r.local_id,
                        "status": r.status,
                        "qid": r.qid,
                        "message": r.message,
                    }
                    for r in results
                ]
                output_path.write_text(
                    json.dumps(result_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                self.log_line.emit(
                    f"Upload complete — {success} succeeded, {failed} failed"
                )
                self.progress.emit(100)
                self.finished.emit(output_path)

        except Exception as exc:
            logger.error("Wikidata upload failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
