"""QThread workers for each pipeline stage."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal

if TYPE_CHECKING:
    from converter.authority.kima_matcher import KimaMatcher
    from converter.authority.mazal_matcher import MazalMatcher
    from converter.authority.viaf_matcher import VIAFMatcher
    from converter.authority.wikidata_matcher import WikidataMatcher

logger = logging.getLogger(__name__)

# ── Classifier weight discovery ──────────────────────────────────────────────


def _find_classifier_weights(filename: str) -> Path | None:
    """Locate a ``.pt`` file for one of the sentence / genre classifiers.

    Search order: repo layout (``<repo>/ner/<filename>``) → bundle layout
    (``.app/Contents/Resources/pipeline/ner/<filename>`` or
    ``…/Contents/Resources/models/<filename>``). Returns None if missing.
    """
    # Repo / pipeline layout
    here = Path(__file__).resolve()
    primary = here.parents[3] / "ner" / filename
    if primary.exists():
        return primary
    # macOS bundle: walk up to find .app
    for parent in Path(__file__).parents:
        if parent.name.endswith(".app"):
            for rel in (
                Path("Contents/Resources/pipeline/ner") / filename,
                Path("Contents/Resources/models") / filename,
            ):
                cand = parent / rel
                if cand.exists():
                    return cand
            break
    return None


# ── MARC 500 sentence classifier — lazy singleton ─────────────────────────────
_MARC500_CLASSIFIER: object | None = "unloaded"


def _get_marc500_classifier() -> object | None:
    global _MARC500_CLASSIFIER
    if _MARC500_CLASSIFIER == "unloaded":
        model_path = _find_classifier_weights("marc500_classifier_model.pt")
        if model_path is not None:
            try:
                from converter.authority.marc500_classifier import Marc500Classifier  # noqa: PLC0415
                _MARC500_CLASSIFIER = Marc500Classifier(str(model_path))
            except Exception as _exc:
                logger.warning("Could not load MARC 500 classifier: %s", _exc)
                _MARC500_CLASSIFIER = None
        else:
            _MARC500_CLASSIFIER = None
    return _MARC500_CLASSIFIER


# ── Genre classifier (Stage 3 P136 fallback) — lazy singleton ─────────────────
_GENRE_CLASSIFIER: object | None = "unloaded"


def _get_genre_classifier() -> object | None:
    global _GENRE_CLASSIFIER
    if _GENRE_CLASSIFIER == "unloaded":
        model_path = _find_classifier_weights("genre_classifier_model.pt")
        if model_path is not None:
            try:
                from converter.authority.genre_classifier import GenreClassifier  # noqa: PLC0415
                _GENRE_CLASSIFIER = GenreClassifier(str(model_path))
            except Exception as _exc:
                logger.warning("Could not load genre classifier: %s", _exc)
                _GENRE_CLASSIFIER = None
        else:
            _GENRE_CLASSIFIER = None
    return _GENRE_CLASSIFIER


def _split_marc500_sentences(text: str) -> list[str]:
    import re as _re  # noqa: PLC0415
    parts = _re.split(r"(?<=[.!?])\s+|\n", text)
    return [s.strip() for s in parts if len(s.strip()) >= 10]


class StageWorker(QThread):
    """Base worker thread for a single pipeline stage.

    Subclasses implement run() and emit these signals:
      - progress(int): 0-100 completion percentage
      - log_line(str): a single log message
      - error(str): human-readable error (stage stops)
      - finished(Path): path to the stage output file on success
      - substep(str): freeform substep label rendered inside
        DynamicProgressBar. Emitted at structural boundaries only —
        never inside hot per-token loops.
    """

    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(Path)
    # Per the plan, panels listen to this to render the inner substep label
    # inside DynamicProgressBar. Workers emit a freeform string at boundaries
    # they cross — never inside hot per-token loops.
    substep = pyqtSignal(str)

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
            self.substep.emit(f"Reading {self._input_path.name}")

            reader = UnifiedReader()
            all_records = list(reader.read_file(self._input_path))
            end = self._end if self._end > 0 else len(all_records)
            records = all_records[self._start : end]
            total = len(records)
            if total == 0:
                self.error.emit("No records found in input file")
                return

            extracted: list[dict[str, Any]] = []
            substep_every = max(1, total // 100)
            for idx, record in enumerate(records):
                data = extract_all_data(record)
                entry = dataclasses.asdict(data)
                entry["_control_number"] = record.control_number
                extracted.append(entry)
                if idx % substep_every == 0 or idx + 1 == total:
                    self.substep.emit(
                        f"Parsing record {idx + 1}/{total}: {record.control_number}"
                    )
                self.progress.emit(int((idx + 1) / total * 100))

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "marc_extracted.json"

            self.substep.emit("Writing marc_extracted.json")
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
    """Split MARC 561 provenance text on pipe separators only.

    Preserves the full "ציון בעלים: ..." prefix in each segment so the
    model retains the entity-type context it was trained on.
    """
    return [seg.strip() for seg in text.split("|") if seg.strip()]


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


def _adjust_entity_positions(entities: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
    """Adjust entity positions by adding offset to start/end."""
    for ent in entities:
        ent["start"] = ent.get("start", 0) + offset
        ent["end"] = ent.get("end", 0) + offset
    return entities


def _process_text_segment(pipeline: Any, text: str, offset: int) -> list[dict[str, Any]]:
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
        """Resolve a model path from explicit arg, env var, or bundle auto-discovery.

        Search order:
          1. Explicit argument from the UI / caller.
          2. ``env_var`` (set by the macOS / Windows launcher).
          3. The ``.app`` bundle's ``Contents/Resources/models/<filename>``
             — discovered via ``sys.executable`` so it works regardless of
             whether the native Mach-O launcher remembered to export env
             vars.
          4. The repo-relative fallback (``<repo>/ner/<filename>``).

        Returns an empty string if nothing was found.
        """
        import os  # noqa: PLC0415
        import sys as _sys  # noqa: PLC0415

        if explicit:
            return explicit

        from_env = os.environ.get(env_var, "")
        if from_env and Path(from_env).exists():
            return from_env

        # Auto-discover from the macOS .app bundle. We walk up from
        # ``__file__`` (never resolved — so symlinks into Homebrew don't
        # escape the bundle) looking for the first ``.app`` ancestor,
        # then try ``Contents/Resources/models/<filename>`` there.
        filename = Path(fallback).name
        try:
            here = Path(__file__)
            for parent in here.parents:
                if parent.name.endswith(".app"):
                    candidate = parent / "Contents" / "Resources" / "models" / filename
                    if candidate.exists():
                        return str(candidate)
                    break
        except Exception:
            pass

        # Also try the sibling-of-pipeline layout that build_app.sh produces:
        #   Contents/Resources/pipeline/   (this file's parents[3])
        #   Contents/Resources/models/     (sibling dir)
        try:
            sibling = Path(__file__).parents[3].parent / "models" / filename
            if sibling.exists():
                return str(sibling)
        except Exception:
            pass

        # Legacy / repo-relative fallback
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

            records: list[dict[str, Any]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # 1. Person NER (always loaded)
            self.substep.emit("Loading person NER model")
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
            logger.debug("Provenance resolved path: %r", prov_path)
            if prov_path:
                from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

                self.log_line.emit("Loading provenance NER model...")
                self.substep.emit("Loading provenance NER model")
                try:
                    provenance_pipeline = NERInferencePipeline(
                        model_path=prov_path,
                        device=self._device,
                    )
                except Exception as _prov_load_err:
                    logger.error("Provenance NER load failed: %s", _prov_load_err, exc_info=True)
                    provenance_pipeline = None

            # 3. Contents NER (optional)
            contents_pipeline = None
            cont_path = self._resolve_model_path(
                self._contents_model_path,
                "MHM_BUNDLED_CONTENTS_MODEL",
                str(_Path(__file__).parents[3] / "ner" / "contents_ner_model.pt"),
            )
            logger.debug("Contents resolved path: %r", cont_path)
            if cont_path:
                from ner.ner_inference_pipeline import NERInferencePipeline  # noqa: PLC0415

                self.log_line.emit("Loading contents NER model...")
                self.substep.emit("Loading contents NER model")
                try:
                    contents_pipeline = NERInferencePipeline(
                        model_path=cont_path,
                        device=self._device,
                    )
                except Exception as _cont_load_err:
                    logger.error("Contents NER load failed: %s", _cont_load_err, exc_info=True)
                    contents_pipeline = None

            models_loaded = 1 + (1 if provenance_pipeline else 0) + (1 if contents_pipeline else 0)
            self.log_line.emit(f"Processing {total} records with {models_loaded} NER model(s)")

            results: list[dict[str, Any]] = []
            substep_every = max(1, total // 100)
            marc500_announced = False
            genre_announced = False
            for idx, record in enumerate(records):
                cn = str(record.get("_control_number", ""))
                if idx % substep_every == 0 or idx + 1 == total:
                    self.substep.emit(f"NER inference {idx + 1}/{total}: {cn}")
                all_entities: list[dict[str, Any]] = []

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
                    for content in record.get("contents") or []:
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

                # MARC 500 colophon sentence detection. Audit fix A6
                # (2026-05-06): classifier outputs no longer leak into
                # ``entities`` as fake spans with ``start=0, end=0`` —
                # they live in dedicated channels so Stage 3's source
                # filter (person_ner / provenance_ner / contents_ner)
                # is never bypassed and the GUI's authority editor
                # cannot accidentally route a classifier prediction
                # through Wikidata reconciliation.
                ml_colophon_sentences: list[str] = []
                if not marc500_announced:
                    self.substep.emit("Loading MARC 500 sentence classifier")
                    marc500_announced = True
                _marc500_clf = _get_marc500_classifier()
                if _marc500_clf is not None:
                    for note in record.get("notes") or []:
                        for sent in _split_marc500_sentences(str(note)):
                            # COLOPHON head — sentence becomes a colophon
                            # source for P1684 (audit fix A6: lives only
                            # in ml_colophon_sentences, not entities[]).
                            try:
                                col_above, col_conf = _marc500_clf.is_colophon(sent)
                                if col_above:
                                    ml_colophon_sentences.append(sent)
                            except Exception as _col_exc:
                                logger.debug("MARC 500 colophon clf error: %s", _col_exc)
                            # PROVENANCE head — Rule 35 says PROVENANCE
                            # MARC 500 sentences route through the
                            # provenance NER pipeline. Audit fix A2
                            # (2026-05-06): this routing was missing —
                            # zero entities had ``from_marc500: True``
                            # despite obvious provenance content in
                            # 27/68 records of the audit corpus.
                            try:
                                prov_above, prov_conf = _marc500_clf.is_provenance(sent)
                                if prov_above and provenance_pipeline is not None:
                                    from500_entities = provenance_pipeline.process_text(sent)
                                    for ent in from500_entities:
                                        ent["source"] = "provenance_ner"
                                        ent["from_marc500"] = True
                                        ent["marc500_confidence"] = float(prov_conf)
                                    all_entities.extend(from500_entities)
                            except Exception as _prov_exc:
                                logger.debug("MARC 500 provenance routing error: %s", _prov_exc)

                # Genre classifier (Stage 3 P136 fallback). Predictions
                # land in ``ml_genres`` (audit fix A6), NOT in the
                # entity list. The Wikidata Preview panel reads them
                # directly; the GUI editor surfaces them in a separate
                # read-only section if needed.
                if not genre_announced:
                    self.substep.emit("Loading genre classifier")
                    genre_announced = True
                ml_genres: list[dict[str, Any]] = []
                _genre_clf = _get_genre_classifier()
                if _genre_clf is not None:
                    try:
                        title = str(record.get("title") or "").strip()
                        notes_list = [
                            str(n) for n in (record.get("notes") or []) if n
                        ]
                        predictions = _genre_clf.predict(title, notes_list)
                        for item in predictions or []:
                            # GenreClassifier.predict returns list[(label, conf)]
                            if isinstance(item, tuple) and len(item) >= 2:
                                label, conf = item[0], float(item[1])
                            elif isinstance(item, dict):
                                label, conf = item.get("label", ""), float(item.get("confidence", 0.0))
                            else:
                                continue
                            if not label or label == "other":
                                continue
                            ml_genres.append({
                                "label": str(label),
                                "confidence": conf,
                            })
                    except Exception as _genre_exc:
                        logger.debug("Genre ML error: %s", _genre_exc)

                results.append(
                    {
                        "_control_number": record.get("_control_number"),
                        "text": "\n".join(texts),
                        "entities": all_entities,
                        "ml_colophon_sentences": ml_colophon_sentences,
                        "ml_genres": ml_genres,
                    }
                )
                self.progress.emit(int((idx + 1) / total * 100))

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "ner_results.json"

            self.substep.emit("Writing ner_results.json")
            safe_results = copy.deepcopy(results)
            json_text = json.dumps(safe_results, ensure_ascii=False, indent=2, default=str)
            output_path.write_text(json_text, encoding="utf-8")

            def _count(src: str) -> int:
                return sum(1 for r in results for e in r["entities"]
                           if e.get("source") == src)
            person_count = _count("person_ner")
            prov_count = _count("provenance_ner")
            cont_count = _count("contents_ner")
            col_count = sum(len(r.get("ml_colophon_sentences") or []) for r in results)
            genre_count = sum(len(r.get("ml_genres") or []) for r in results)
            self.log_line.emit(
                f"NER complete — {total} records, "
                f"{person_count} person + {prov_count} provenance + "
                f"{cont_count} contents entities; "
                f"{col_count} ml-colophon sentences + "
                f"{genre_count} ml-genre predictions"
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
        self._input_path = input_path  # MARC extract (stage 0 output)
        self._output_dir = output_dir
        self._ner_path = ner_path  # NER results (stage 1 output, optional)
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
    ) -> dict[str, Any]:
        """Create a match info dictionary with authority references."""
        info: dict[str, Any] = {
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
        """Count whether this match result should increment counters.

        ``matched`` is derived from ``confidence`` when present so legacy
        consumers (auto-approve, statistics) keep working — Stage 3 now
        reports ``confidence: high|medium|low`` and only ``high`` rolls
        forward as ``matched=1``.
        """
        confidence = match_info.get("confidence")
        if confidence:
            return {
                "counted": 1,
                "matched": 1 if confidence == "high" else 0,
            }
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
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
        entity_type: str = "person",
        on_substep: Callable[[str], None] | None = None,
    ) -> tuple[str | None, str | None]:
        """Match a name against Mazal and optionally VIAF authorities.

        entity_type: "person" (default), "organization", or "meeting".
        Corporate/meeting entities skip the person-specific VIAF search to
        prevent cross-type cluster assignment (root cause of the 2026-04-15
        library-items-with-person-VIAF-IDs incident).

        Returns tuple of (mazal_id, viaf_uri).

        Guard 4 (placeholder name filter) is applied here so cataloguer
        abbreviations like ``א"א``, ``מל"י``, ``N.N.`` never reach the
        VIAF / Mazal HTTP layer and can never produce a wrong cluster
        attachment.
        """
        # Organizations and meetings must not be matched via person-name VIAF
        # search — they have separate VIAF authority name types ("Corporate",
        # "Geographic"). Matching them via local.personalNames risks returning
        # a personal cluster with a coincidentally similar name.
        if entity_type in ("organization", "meeting"):
            return None, None

        # Stage 3 Guard 4 — placeholder/abbreviation filter
        from converter.authority.stage3_guards import is_placeholder_name  # noqa: PLC0415

        if is_placeholder_name(name):
            return None, None

        if on_substep is not None:
            on_substep("Stage 3.1 — Mazal lookup")
        mazal_id = mazal.match_person(name)
        viaf_uri = None

        if viaf:
            if on_substep is not None:
                on_substep("Stage 3.2 — VIAF SRU")
            viaf_uri = viaf.match_person(name)

        return mazal_id, viaf_uri

    def _match_ner_entity(
        self,
        entity: dict,
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
        wd_matcher: WikidataMatcher | None = None,
        on_substep: Callable[[str], None] | None = None,
    ) -> dict[str, int]:
        """Match a single NER entity against authority databases.

        Updates entity dict in place with authority IDs (mazal_id,
        viaf_uri, wikidata_qid). Returns dict with counted/matched
        flags for statistics.
        """
        # Person entities from JointNER
        if self._has_valid_name(entity, "person"):
            name = str(entity.get("person", "")).strip()
        # OWNER and WORK_AUTHOR entities from provenance/contents NER
        elif entity.get("type") in ("OWNER", "WORK_AUTHOR") and self._has_valid_name(
            entity, "text"
        ):
            name = str(entity.get("text", "")).strip()
        else:
            return {"counted": 0, "matched": 0}
        mazal_id, viaf_uri = self._match_against_authorities(
            name, mazal, viaf, on_substep=on_substep
        )

        if mazal_id:
            entity["mazal_id"] = mazal_id
        if viaf_uri:
            entity["viaf_uri"] = viaf_uri

        # ── 4-source — Wikidata triangulation + Hebrew label fallback ───
        wikidata_qid: str | None = None
        if wd_matcher is not None:
            if on_substep is not None:
                on_substep("Stage 3.3 — Wikidata SPARQL")
            try:
                if viaf_uri:
                    import re as _re_ner_wd  # noqa: PLC0415

                    _m = _re_ner_wd.search(r"/viaf/(\d+)", str(viaf_uri))
                    if _m:
                        wikidata_qid = wd_matcher.find_qid_by_viaf(_m.group(1))
                if wikidata_qid is None and mazal_id:
                    wikidata_qid = wd_matcher.find_qid_by_mazal(mazal_id)
                if wikidata_qid is None and not mazal_id and not viaf_uri:
                    wikidata_qid = wd_matcher.match_person(name)
                    if wikidata_qid is not None and wd_matcher.last_match_was_latin_only():
                        # Mode-3 Latin-only: keep the QID for the GUI but
                        # mark it so the consumer doesn't auto-promote.
                        entity["wikidata_latin_only"] = True
            except Exception as exc:  # defensive
                logger.debug(
                    "WikidataMatcher NER match failed for %s: %s", name, exc
                )

        if wikidata_qid:
            entity["wikidata_qid"] = wikidata_qid

        matched = bool(mazal_id or viaf_uri or wikidata_qid)
        return {"counted": 1, "matched": 1 if matched else 0}

    def _match_marc_person_entry(
        self,
        person: dict,
        role: str,
        field: str,
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
        ms_year: int | None = None,
        over_merge_table: Any | None = None,
        ms_title: str | None = None,
        ms_place: str | None = None,
        wd_matcher: WikidataMatcher | None = None,
        on_substep: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        """Match a single MARC person entry (author or contributor).

        Returns match info dict with count metadata, or None if no valid name.

        Stage 3 guards (added 2026-04-30 after E_AUTH_REVIEW):
          - Guard 4 (placeholder filter) is applied inside ``_match_against_authorities``.
          - Guards 1 (date conflict), 2 (short-name homonym), and 5 (confidence
            scoring) are applied here once VIAF / Mazal results are in hand.
          - Guard 3 (cluster collapse) is applied per-record by the caller via
            ``apply_cluster_collapse(matches)``.

        Hardening (added 2026-05-02 — F1/F2/F3/F4 integration):
          - F4 (NLI strict mode): consult Mazal first; if a hit, skip VIAF SRU.
          - F2 Wikidata cross-check: query WDQS for the VIAF ID's QIDs and
            Hebrew labels; flag the candidate as ``wikidata_confirms`` /
            ``wikidata_disagrees`` / ``over_merge_detected`` accordingly.
          - F3 Mazal-pair collision: record (marc_name, mazal_id, viaf_id)
            triples; the per-record post-pass downgrades over-merged clusters.
          - F1 LLM disambiguator: only on ``confidence=medium`` after F2/F3.
        """
        # Guard clause: skip entries without valid names
        if not self._has_valid_name(person, "name"):
            return None

        name = str(person.get("name", "")).strip()
        role_value = str(person.get("role", role))
        entity_type = str(person.get("type", "person"))

        # ── F4 — NLI strict mode (Plan §F4) ────────────────────────────
        # Try Mazal first; if it returns an authoritative NLI ID, skip
        # the VIAF SRU lookup entirely. F2's Wikidata cross-check resolves
        # VIAF from the NLI ID downstream more reliably than VIAF SRU.
        nli_strict_used = False
        nli_levenshtein_used = False
        match_alternatives: list[dict[str, str]] = []
        mazal_id: str | None = None
        viaf_uri: str | None = None
        if entity_type == "person":
            from converter.authority.nli_strict_mode import (  # noqa: PLC0415
                resolve_with_nli_priority,
            )

            if on_substep is not None:
                on_substep("Stage 3.1 — Mazal lookup")
            person_dates = person.get("dates")
            person_dates_str = str(person_dates) if person_dates else None
            try:
                strict = resolve_with_nli_priority(
                    name, mazal, name_dates=person_dates_str
                )
            except Exception as exc:  # defensive — never crash Stage 3
                logger.debug("NLI strict mode failed for %s: %s", name, exc)
                strict = None

            if strict is not None and strict.source == "nli_strict":
                mazal_id = strict.mazal_id
                viaf_uri = strict.viaf_uri  # currently always None
                nli_strict_used = True
            elif strict is not None and strict.source == "nli_levenshtein":
                mazal_id = strict.mazal_id
                viaf_uri = strict.viaf_uri
                nli_levenshtein_used = True
                if strict.near_miss_suggestion:
                    match_alternatives.append(
                        {
                            "kind": "nli_levenshtein",
                            "suggestion": strict.near_miss_suggestion,
                            "mazal_id": strict.mazal_id or "",
                        }
                    )

        if not nli_strict_used and not nli_levenshtein_used:
            # Path 3 (fallback) — existing VIAF-SRU + Mazal flow.
            mazal_id, viaf_uri = self._match_against_authorities(
                name, mazal, viaf, entity_type=entity_type, on_substep=on_substep
            )

        # ── Step 4 — Wikidata identifier triangulation (Mode 1) ────────
        # If Mazal or VIAF returned an authority ID, ask the
        # WikidataMatcher whether Wikidata stores that identifier on
        # exactly one item. ≤ 1 SPARQL probe per source.
        wikidata_qid: str | None = None
        wd_latin_only_in_step5 = False
        viaf_id_for_step4: str | None = None
        if wd_matcher is not None and entity_type == "person":
            if on_substep is not None:
                on_substep("Stage 3.3 — Wikidata SPARQL")
            try:
                if viaf_uri:
                    import re as _re_step4  # noqa: PLC0415

                    _match = _re_step4.search(r"/viaf/(\d+)", str(viaf_uri))
                    if _match:
                        viaf_id_for_step4 = _match.group(1)
                        wikidata_qid = wd_matcher.find_qid_by_viaf(viaf_id_for_step4)
                if wikidata_qid is None and mazal_id:
                    wikidata_qid = wd_matcher.find_qid_by_mazal(mazal_id)
                # ── Step 4a — canonical-QID preference ──────────────────
                # When Step 4 returns a high-numbered QID (≥ Q138_000_000)
                # the candidate is almost certainly a pipeline-created
                # duplicate — canonical Wikidata items for Hebrew authors
                # were created years ago and have far lower QIDs (e.g.
                # Rashi → Q189564, not Q139094451). Run an additional
                # Hebrew-label probe and prefer the lowest verified QID.
                # If the label probe finds nothing, keep the original.
                _PIPELINE_QID_THRESHOLD = 138_000_000
                if wikidata_qid is not None:
                    try:
                        if int(wikidata_qid[1:]) >= _PIPELINE_QID_THRESHOLD:
                            label_qid = wd_matcher.match_person(name)
                            if label_qid and int(label_qid[1:]) < int(wikidata_qid[1:]):
                                wikidata_qid = label_qid
                    except (ValueError, IndexError):
                        pass
                # ── Step 4b — backfill VIAF from QID ────────────────────
                # When NLI strict mode resolved a Mazal hit and triangulated
                # to a Wikidata QID, the Wikidata page nearly always has a
                # P214 (VIAF) statement. The audit on 2026-05-06 showed VIAF
                # coverage at 13% vs Mazal 72% — most of that gap closes by
                # reading P214 off the QID we already have.
                if wikidata_qid is not None and not viaf_uri:
                    backfilled = wd_matcher.find_viaf_by_qid(wikidata_qid)
                    if backfilled:
                        viaf_uri = f"https://viaf.org/viaf/{backfilled}"
                        viaf_id_for_step4 = backfilled
            except Exception as exc:  # defensive — Wikidata step never crashes Stage 3
                logger.debug(
                    "WikidataMatcher triangulation failed for %s: %s", name, exc
                )

        # ── Step 5 — Wikidata Hebrew label fallback (Mode 2) ───────────
        # Only fires when step 4 yielded nothing AND neither Mazal nor
        # VIAF matched. Skip auto-promotion if the matcher had to fall
        # through to the Latin transliteration mode.
        if (
            wd_matcher is not None
            and entity_type == "person"
            and wikidata_qid is None
            and not mazal_id
            and not viaf_uri
        ):
            try:
                marc_dates = person.get("dates")
                marc_dates_str = str(marc_dates) if marc_dates else None
                wikidata_qid = wd_matcher.match_person(name, dates=marc_dates_str)
                if wikidata_qid is not None and wd_matcher.last_match_was_latin_only():
                    wd_latin_only_in_step5 = True
            except Exception as exc:  # defensive
                logger.debug(
                    "WikidataMatcher label fallback failed for %s: %s", name, exc
                )

        # ``source`` is derived AT THE END from the IDs that survived the
        # verdict (see "Derive authority source" block below). The literal
        # placeholder here is overwritten — keeping the constructor shape
        # backward-compatible with existing tests.
        match_info = self._create_match_info(
            name=name,
            role=role_value,
            source="",
            field=field,
            mazal_id=mazal_id,
            viaf_uri=viaf_uri,
        )

        # Cluster identifiers + biographical years from VIAF
        viaf_birth: int | None = None
        viaf_death: int | None = None
        viaf_preferred_lat: str | None = None
        if viaf_uri and viaf:
            import re as _re  # noqa: PLC0415

            viaf_id_match = _re.search(r"/viaf/(\d+)", viaf_uri)
            if viaf_id_match:
                cluster = viaf.get_cluster_identifiers(viaf_id_match.group(1))
                if cluster.get("gnd"):
                    match_info["gnd_id"] = cluster["gnd"]
                if cluster.get("lc"):
                    match_info["lc_id"] = cluster["lc"]
                if cluster.get("isni"):
                    match_info["isni"] = cluster["isni"]
                if cluster.get("bnf"):
                    match_info["bnf_id"] = cluster["bnf"]
                # Stage 3 Guard 1 — pull biographical years from cluster
                from converter.authority.stage3_guards import _parse_year  # noqa: PLC0415

                viaf_birth = _parse_year(cluster.get("birth_date"))
                viaf_death = _parse_year(cluster.get("death_date"))
                # Latin preferred name for short-name homonym check.
                cluster_raw = viaf.get_cluster_raw(viaf_id_match.group(1)) or {}
                from converter.authority.viaf_matcher import (  # noqa: PLC0415
                    _extract_latin_main_heading,
                )

                viaf_preferred_lat = _extract_latin_main_heading(cluster_raw)

        # Enrich with dates and preferred names from Mazal DB. The
        # ``isinstance(..., str)`` guards keep this path tolerant of
        # malformed records (or mocks in tests) where ``get_person_details``
        # returns non-string values for the optional fields.
        mazal_birth: int | None = None
        mazal_death: int | None = None
        if mazal_id and mazal:
            details = mazal.get_person_details(mazal_id) or {}
            dates_value = details.get("dates") if isinstance(details, dict) else None
            if isinstance(dates_value, str) and dates_value.strip():
                match_info["dates"] = dates_value
                # Parse "1488-1575" → birth_year, death_year
                import re  # noqa: PLC0415

                parts = re.split(r"[-–]", dates_value.strip())
                for p in parts:
                    p = p.strip().rstrip("?")
                    if p and p.isdigit():
                        yr = int(p)
                        if 100 < yr < 2100:
                            if "birth_year" not in match_info:
                                match_info["birth_year"] = yr
                                mazal_birth = yr
                            else:
                                match_info["death_year"] = yr
                                mazal_death = yr
            preferred_lat = (
                details.get("preferred_name_lat") if isinstance(details, dict) else None
            )
            if isinstance(preferred_lat, str) and preferred_lat:
                match_info["preferred_name_lat"] = preferred_lat

        # ── Stage 3 guards 1, 2, 5 ───────────────────────────────────────
        from converter.authority.stage3_guards import evaluate_match  # noqa: PLC0415

        # Prefer VIAF biographical years (more authoritative + machine-checked
        # against the cluster) but fall back to Mazal when VIAF has none.
        person_birth = viaf_birth if viaf_birth is not None else mazal_birth
        person_death = viaf_death if viaf_death is not None else mazal_death
        # Pick the most-disambiguating Latin form for guard 2.
        preferred_lat_for_guard = (
            match_info.get("preferred_name_lat") or viaf_preferred_lat
        )
        # Detect MARC 100$d / 700$d biographical date subfield in source name.
        import re as _re_marc  # noqa: PLC0415

        bio_dates_in_marc = bool(
            person.get("dates")
            or _re_marc.search(r"\d{3,4}", str(person.get("name", "")))
        )

        # ── First pass: deterministic 5-guard layer ────────────────────
        # Treat a Mode-3 (Latin-only) Wikidata hit as soft signal — it
        # surfaces ``wikidata_qid`` for the GUI but does not contribute
        # to ``has_wikidata`` (so it cannot promote confidence).
        has_wikidata_first_pass = wikidata_qid is not None and not wd_latin_only_in_step5
        verdict = evaluate_match(
            marc_name=name,
            role=role_value,
            ms_year=ms_year,
            mazal_id=mazal_id,
            viaf_uri=viaf_uri,
            preferred_name_lat=preferred_lat_for_guard,
            person_birth_year=person_birth,
            person_death_year=person_death,
            biographical_dates_in_marc=bio_dates_in_marc,
            has_wikidata=has_wikidata_first_pass,
            wikidata_qid=wikidata_qid,
        )

        # ── F2 — Wikidata cross-check ──────────────────────────────────
        # Done AFTER the 5-guard layer so that hard-rejected matches do
        # not pay a SPARQL round-trip. Only fires when we have a VIAF ID
        # AND the deterministic layer did not already kill the match.
        wikidata_disagrees = False
        wikidata_confirms = False
        over_merge_detected = False
        viaf_id_for_signals: str | None = None
        viaf_uri_to_check = verdict.get("viaf_uri") or viaf_uri
        if viaf_uri_to_check and verdict["confidence"] != "low":
            from converter.authority.wikidata_crosscheck import (  # noqa: PLC0415
                hebrew_label_matches,
                is_enabled as wd_is_enabled,
                is_overmerged,
                lookup_viaf,
            )

            import re as _re_viaf  # noqa: PLC0415

            viaf_id_match = _re_viaf.search(r"/viaf/(\d+)", viaf_uri_to_check)
            if viaf_id_match and wd_is_enabled():
                viaf_id_for_signals = viaf_id_match.group(1)
                # Use the per-run table when available (memoises lookups);
                # fall back to a direct call when the caller didn't pass one.
                try:
                    if over_merge_table is not None:
                        wd_result = over_merge_table.get(viaf_id_for_signals)
                    else:
                        wd_result = lookup_viaf(viaf_id_for_signals)
                except Exception as exc:  # defensive — never crash Stage 3
                    logger.debug(
                        "Wikidata cross-check failed for VIAF %s: %s",
                        viaf_id_for_signals,
                        exc,
                    )
                    wd_result = None

                if wd_result is not None and wd_result.error is None:
                    if is_overmerged(wd_result):
                        over_merge_detected = True
                    if wd_result.qids:
                        if hebrew_label_matches(name, wd_result.hebrew_labels):
                            wikidata_confirms = True
                        else:
                            wikidata_disagrees = True

        # ── F3 — record (marc_name, mazal_id, viaf_id) for collision ───
        # This is the STRONGEST over-merge signal but only fires after
        # all matches in the record are resolved (post-pass in
        # ``_match_marc_persons``). Here we just *record* the triple.
        if (
            over_merge_table is not None
            and mazal_id
            and viaf_id_for_signals
        ):
            try:
                over_merge_table.record_mazal_pair(
                    name, mazal_id, viaf_id_for_signals
                )
            except Exception as exc:  # defensive
                logger.debug("OverMergeTable.record_mazal_pair failed: %s", exc)

        # ── Step 9 — cross-source conflict (4-source matrix) ───────────
        # When all three identifier sources resolved to *something*, ask
        # whether they tell the same story. The Wikidata cross-check
        # already returns the QIDs that have wdt:P214=<viaf_id>; if the
        # ``wikidata_qid`` we found via Mode 1 is NOT in that QID list,
        # the three sources disagree. Likewise, if Mazal returned an
        # NLI ID but ``find_qid_by_mazal`` mapped it to a different QID
        # than the VIAF-derived one, that's also a conflict.
        cross_source_conflict = False
        if (
            wd_matcher is not None
            and mazal_id
            and viaf_uri
            and wikidata_qid
        ):
            if on_substep is not None:
                on_substep("Stage 3.4 — Cross-source conflict probe")
            try:
                # If Mode 1 used the VIAF path, verify the Mazal/J9U
                # path lands on the SAME QID. If they disagree the IDs
                # belong to different real-world entities — a conflict
                # the deterministic 5-guard layer cannot detect.
                qid_via_mazal = wd_matcher.find_qid_by_mazal(mazal_id)
                qid_via_viaf = (
                    wd_matcher.find_qid_by_viaf(viaf_id_for_step4)
                    if viaf_id_for_step4
                    else None
                )
                if (
                    qid_via_mazal is not None
                    and qid_via_viaf is not None
                    and qid_via_mazal != qid_via_viaf
                ):
                    cross_source_conflict = True
            except Exception as exc:  # defensive
                logger.debug(
                    "Wikidata cross-source conflict check failed for %s: %s",
                    name,
                    exc,
                )

        # ── Re-evaluate after F2/F3 + Wikidata 4-source signal ─────────
        has_wikidata_final = wikidata_qid is not None and not wd_latin_only_in_step5
        verdict = evaluate_match(
            marc_name=name,
            role=role_value,
            ms_year=ms_year,
            mazal_id=mazal_id,
            viaf_uri=viaf_uri,
            preferred_name_lat=preferred_lat_for_guard,
            person_birth_year=person_birth,
            person_death_year=person_death,
            biographical_dates_in_marc=bio_dates_in_marc,
            wikidata_disagrees=wikidata_disagrees,
            wikidata_confirms=wikidata_confirms,
            over_merge_detected=over_merge_detected,
            has_wikidata=has_wikidata_final,
            cross_source_conflict=cross_source_conflict,
            wikidata_qid=wikidata_qid,
        )

        # F1 (LLM disambiguator) was removed in 2026-05-03 — its 13GB
        # weight bundle was incompatible with the desktop app's
        # distribution constraint (CLAUDE.md, DRIFT_LOG type 14). The
        # deterministic F2/F3/F4 + 5-guards layer hits 100% precision
        # on the 22-case audit; medium-confidence residuals route to
        # the GUI's manual-review queue.

        # Apply verdict — if Guard 1 / Guard 4 hard-rejected, the match
        # info now has cleared mazal_id / viaf_uri.
        match_info["confidence"] = verdict["confidence"]
        if verdict["mazal_id"] is None and "mazal_id" in match_info:
            match_info.pop("mazal_id", None)
        if verdict["viaf_uri"] is None and "viaf_uri" in match_info:
            match_info.pop("viaf_uri", None)
            # Cluster-derived enrichment is also no longer trustworthy
            for stale in ("gnd_id", "lc_id", "isni", "bnf_id"):
                match_info.pop(stale, None)
        # 4-source schema: always surface ``wikidata_qid``; True-only flag
        # ``cross_source_conflict`` is omitted when False per spec.
        match_info["wikidata_qid"] = verdict.get("wikidata_qid")
        if cross_source_conflict:
            match_info["cross_source_conflict"] = True
        if verdict.get("rejection_reason"):
            match_info["rejection_reason"] = verdict["rejection_reason"]
        if verdict.get("guard_flags"):
            match_info["guard_flags"] = verdict["guard_flags"]

        # Derive authority source from the IDs that survived the verdict.
        # Replaces the previous literal ``source="MARC"`` which never
        # carried provenance information. ``field`` already captures the
        # MARC field origin (e.g. "marc_100"); ``source`` now records
        # which authorities agreed on this entity.
        sources_present: list[str] = []
        if match_info.get("mazal_id"):
            sources_present.append("mazal")
        if match_info.get("viaf_uri"):
            sources_present.append("viaf")
        if match_info.get("wikidata_qid"):
            sources_present.append("wikidata")
        if len(sources_present) >= 2:
            match_info["source"] = "cross_source"
            match_info["sources"] = sources_present
            match_info["source_count"] = len(sources_present)
        elif len(sources_present) == 1:
            match_info["source"] = sources_present[0]
            match_info["source_count"] = 1
        else:
            match_info["source"] = "marc_only"
            match_info["source_count"] = 0

        # F4 alternatives surface for the GUI's manual-review panel.
        if match_alternatives:
            match_info["match_alternatives"] = match_alternatives
        if nli_strict_used:
            match_info.setdefault("source_path", "nli_strict")
        elif nli_levenshtein_used:
            match_info.setdefault("source_path", "nli_levenshtein")

        counts = self._count_match_result(match_info)
        match_info.update(counts)

        return match_info

    def _match_marc_persons(
        self,
        control_number: str,
        marc_by_cn: dict[str, dict[str, Any]],
        mazal: MazalMatcher,
        viaf: VIAFMatcher | None,
        wd_matcher: WikidataMatcher | None = None,
        on_substep: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Match all persons from MARC record (authors and contributors).

        Returns list of match info dicts with count metadata.

        Threads the catalogued manuscript year (Stage 0 ``record["dates"]
        ["year"]``) into ``_match_marc_person_entry`` so Guard 1
        (date-conflict) can fire. Then runs the cluster-collapse pass
        (Guard 3) over the per-record list.
        """
        # Guard clause: return empty if record not found
        if control_number not in marc_by_cn:
            return []

        marc_rec = marc_by_cn[control_number]
        matches: list[dict[str, Any]] = []

        # Stage 3 Guard 1 — manuscript year for date-conflict checking.
        from converter.authority.stage3_guards import (  # noqa: PLC0415
            apply_cluster_collapse,
            extract_manuscript_year,
        )
        from converter.authority.wikidata_crosscheck import (  # noqa: PLC0415
            OverMergeTable,
        )

        ms_year = extract_manuscript_year(marc_rec)
        # F1 context — ms_title / ms_place help disambiguate homonyms.
        title_field = marc_rec.get("title")
        if isinstance(title_field, dict):
            ms_title = str(title_field.get("preferred") or title_field.get("main") or "")
        else:
            ms_title = str(title_field or "")
        ms_title = ms_title.strip() or None
        related_places = marc_rec.get("related_places") or []
        ms_place = (
            str(related_places[0]).strip()
            if related_places and related_places[0]
            else None
        )

        # F3 — per-record OverMergeTable (also caches Wikidata lookups
        # across multiple persons in the same MS).
        over_merge_table = OverMergeTable()

        # Match authors (100, 110 fields)
        for author in marc_rec.get("authors") or []:
            result = self._match_marc_person_entry(
                person=author,
                role="author",
                field="100/110/111",
                mazal=mazal,
                viaf=viaf,
                ms_year=ms_year,
                over_merge_table=over_merge_table,
                ms_title=ms_title,
                ms_place=ms_place,
                wd_matcher=wd_matcher,
                on_substep=on_substep,
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
                ms_year=ms_year,
                over_merge_table=over_merge_table,
                ms_title=ms_title,
                ms_place=ms_place,
                wd_matcher=wd_matcher,
                on_substep=on_substep,
            )
            if result:
                matches.append(result)

        # Stage 3 Guard 3 — cluster collapse: if 2+ distinct MARC names
        # share a VIAF cluster within ONE manuscript record, demote both.
        downgraded = apply_cluster_collapse(matches)
        if downgraded:
            # Re-derive matched flag for the downgraded rows.
            for m in matches:
                if "cluster_collapse" in (m.get("guard_flags") or []):
                    m["matched"] = 0

        # F3 — post-pass: Mazal-pair collision. If two distinct MARC
        # names with two distinct Mazal IDs landed on the same VIAF
        # cluster, force ALL their matches to over_merge_detected=True.
        try:
            colliding = over_merge_table.detect_pair_collision()
        except Exception as exc:  # defensive
            logger.debug("OverMergeTable.detect_pair_collision failed: %s", exc)
            colliding = set()
        if colliding:
            for m in matches:
                viaf_uri = m.get("viaf_uri")
                if not viaf_uri:
                    continue
                import re as _re_collide  # noqa: PLC0415

                vid_match = _re_collide.search(r"/viaf/(\d+)", str(viaf_uri))
                if not vid_match:
                    continue
                if vid_match.group(1) in colliding:
                    m["confidence"] = "low"
                    m["matched"] = 0
                    flags = list(m.get("guard_flags") or [])
                    if "over_merge_detected" not in flags:
                        flags.append("over_merge_detected")
                    m["guard_flags"] = flags
                    # Drop cluster-derived enrichment (over-merged →
                    # cluster identity is unreliable).
                    m.pop("viaf_uri", None)
                    for stale in ("gnd_id", "lc_id", "isni", "bnf_id"):
                        m.pop(stale, None)

        return matches

    def _match_marc_places(
        self,
        control_number: str,
        marc_by_cn: dict[str, dict[str, Any]],
        kima: KimaMatcher | None,
        on_substep: Callable[[str], None] | None = None,
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
        places: list[str] = [str(p) for p in (marc_rec.get("related_places") or []) if p]

        if not places:
            return None

        if on_substep is not None:
            on_substep("Stage 3.5 — KIMA place match")
        place_matches: dict[str, str] = {}
        for place in places:
            uri = kima.match_place(place)
            if uri:
                place_matches[place] = uri

        return place_matches if place_matches else None

    # ── Helper methods for initialization ────────────────────────────

    def _load_ner_by_control_number(self) -> dict[str, dict[str, Any]]:
        """Load NER records indexed by control number."""
        if not self._ner_path or not self._ner_path.exists():
            return {}

        ner_records: list[dict[str, Any]] = json.loads(
            self._ner_path.read_text(encoding="utf-8"),
        )
        ner_by_cn = {str(r.get("_control_number", "")): r for r in ner_records}
        self.log_line.emit(f"Loaded {len(ner_by_cn)} NER records for entity merging")
        return ner_by_cn

    @staticmethod
    def _merge_ner_into_records(
        records: list[dict[str, Any]],
        ner_by_cn: dict[str, dict[str, Any]],
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
            else:
                # Schema-consistency guarantee: every record gets an
                # ``entities`` key (possibly empty) so downstream consumers
                # can rely on ``record["entities"]`` without defensive
                # ``.get(..., [])``. The audit on 2026-05-06 found 39/68
                # records missing the key entirely.
                record.setdefault("entities", [])
            ml_col = ner_rec.get("ml_colophon_sentences") if ner_rec else None
            if ml_col:
                existing = str(record.get("colophon_text") or "").strip()
                new_sents = [s for s in ml_col if s not in existing]
                if new_sents:
                    record["colophon_text"] = (
                        (existing + " " if existing else "") + " ".join(new_sents)
                    ).strip()
        return enriched

    def _init_viaf(self, viaf_matcher_class: type) -> VIAFMatcher | None:
        """Initialize VIAF matcher if enabled."""
        if not self._enable_viaf:
            return None
        self.log_line.emit("VIAF matching enabled")
        return viaf_matcher_class()

    def _init_kima(
        self,
        kima_matcher_class: type,
    ) -> KimaMatcher | None:
        """Initialize KIMA matcher if enabled."""
        if not self._enable_kima:
            return None
        self.log_line.emit("KIMA place matching enabled")
        return kima_matcher_class(index_path=self._kima_db_path or "")

    def run(self) -> None:
        try:
            from converter.authority.kima_matcher import KimaMatcher
            from converter.authority.mazal_matcher import MazalMatcher
            from converter.authority.viaf_matcher import VIAFMatcher
            from converter.authority.wikidata_matcher import (  # noqa: PLC0415
                WikidataMatcher,
            )

            # ── load MARC extract (primary input) ────────────────────
            self.substep.emit("Loading MARC extract")
            self.log_line.emit(f"Loading MARC extract from {self._input_path.name}")
            records: list[dict[str, Any]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # ── merge NER entities (optional) ────────────────────────
            self.substep.emit("Merging NER entities")
            ner_by_cn = self._load_ner_by_control_number()
            if ner_by_cn:
                enriched = self._merge_ner_into_records(records, ner_by_cn)
                self.log_line.emit(f"Merged NER entities into {enriched}/{total} records")

            # ── index records by control number (for place matching) ─
            marc_by_cn: dict[str, dict[str, Any]] = {
                str(r.get("_control_number", "")): r for r in records
            }

            # ── initialise matchers ───────────────────────────────────
            self.substep.emit("Loading Mazal authority index")
            self.log_line.emit("Loading Mazal authority index")
            mazal = MazalMatcher(index_path=self._mazal_db_path or "")
            self.log_line.emit(
                f"Mazal index available: {mazal.is_available} (path: {mazal.index_path})"
            )

            viaf = self._init_viaf(VIAFMatcher)
            if self._enable_kima:
                self.substep.emit("Loading KIMA place index")
            kima = self._init_kima(KimaMatcher)

            # 4-source authority — instantiate the Wikidata primary matcher.
            # The matcher self-disables when ``MHM_DISABLE_WIKIDATA_CROSSCHECK=1``
            # is set so the worker silently degrades to the legacy 3-source flow.
            try:
                wd_matcher: WikidataMatcher | None = WikidataMatcher()
            except Exception as exc:  # defensive — never crash Stage 3
                logger.debug("WikidataMatcher init failed: %s", exc)
                wd_matcher = None

            # ── match ─────────────────────────────────────────────────
            total_entities = 0
            total_matched = 0

            for idx, record in enumerate(records):
                cn = str(record.get("_control_number", ""))

                # Per-record substep wrapper — prepends "(record i/n)" so
                # the progress widget displays the granular Stage 3.x label
                # from inside the per-call matchers.
                def _emit_substep(label: str, _i: int = idx, _n: int = total) -> None:
                    self.substep.emit(f"{label} (record {_i + 1}/{_n})")

                # --- persons from NER entities (merged from stage 1) ---
                ner_entities = record.get("entities") or []
                for entity in ner_entities:
                    result = self._match_ner_entity(
                        entity, mazal, viaf, wd_matcher, on_substep=_emit_substep
                    )
                    total_entities += result["counted"]
                    total_matched += result["matched"]

                # --- persons from MARC name fields (100/110/111/700/710/711) ---
                marc_matches = self._match_marc_persons(
                    cn, marc_by_cn, mazal, viaf, wd_matcher, on_substep=_emit_substep
                )
                if marc_matches:
                    record["marc_authority_matches"] = marc_matches
                    total_entities += sum(m["counted"] for m in marc_matches)
                    total_matched += sum(m["matched"] for m in marc_matches)

                # --- places from MARC extract ---
                place_result = self._match_marc_places(
                    cn, marc_by_cn, kima, on_substep=_emit_substep
                )
                if place_result:
                    record["kima_places"] = place_result

                self.progress.emit(int((idx + 1) / total * 100))

            # Log summary
            self.log_line.emit(
                f"Authority matching: {total_matched}/{total_entities} entities matched"
            )

            mazal.close()
            if kima is not None:
                kima.close()

            # ── write output ─────────────────────────────────────────
            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / "authority_enriched.json"

            self.substep.emit("Writing authority_enriched.json")
            # Defensive: Deep copy to avoid thread-safety issues during JSON serialization
            import copy

            safe_records = copy.deepcopy(records)
            json_text = json.dumps(safe_records, ensure_ascii=False, indent=2, default=str)
            output_path.write_text(json_text, encoding="utf-8")

            # summary
            n_viaf = sum(
                1 for r in records for e in (r.get("entities") or []) if e.get("viaf_uri")
            ) + sum(
                1
                for r in records
                for e in (r.get("marc_authority_matches") or [])
                if e.get("viaf_uri")
            )
            n_mazal = sum(
                1 for r in records for e in (r.get("entities") or []) if e.get("mazal_id")
            ) + sum(
                1
                for r in records
                for e in (r.get("marc_authority_matches") or [])
                if e.get("mazal_id")
            )
            n_kima = sum(1 for r in records if r.get("kima_places"))
            n_ner = sum(1 for r in records if r.get("entities"))
            n_marc_matched = sum(len(r.get("marc_authority_matches", [])) for r in records)
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
            n_files = len(xml_files)
            for file_idx, xml_file in enumerate(xml_files):
                self.log_line.emit(f"Processing {xml_file.name}…")
                self.substep.emit(
                    f"Processing {xml_file.name} ({file_idx + 1}/{n_files})"
                )
                for nli_id, record in parse_xml_records(str(xml_file)):
                    index.insert_authority(
                        nli_id=nli_id,
                        entity_type=record["entity_type"],
                        preferred_name_heb=str(record["names_heb"][0])
                        if record["names_heb"]
                        else "",
                        preferred_name_lat=str(record["names_lat"][0])
                        if record["names_lat"]
                        else "",
                        dates=str(record.get("dates") or ""),
                        aleph_id=str(record.get("aleph_id") or ""),
                    )
                    for name in record.get("names_heb", []):
                        norm = MazalIndex.normalize_name(name)
                        if norm:
                            index.insert_name_variant(norm, nli_id, record["entity_type"], "heb")
                    for name in record.get("names_lat", []):
                        norm = MazalIndex.normalize_name(name)
                        if norm:
                            index.insert_name_variant(norm, nli_id, record["entity_type"], "lat")
                    total_records += 1
                    if total_records % 10_000 == 0:
                        index.conn.commit()

                index.conn.commit()
                self.progress.emit(int((file_idx + 1) / len(xml_files) * 100))

            self.substep.emit("Writing index DB")
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

            self.log_line.emit(f"Building KIMA index from {self._tsv_dir} → {self._db_path}")
            self.substep.emit(f"Processing TSV files in {self._tsv_dir.name}")

            def _progress(pct: int) -> None:
                self.progress.emit(pct)

            build_kima_index(
                tsv_dir=str(self._tsv_dir),
                db_path=str(self._db_path),
                verbose=True,
                progress_cb=_progress,
            )

            self.substep.emit("Writing index DB")
            self.log_line.emit(f"KIMA index complete: {self._db_path}")
            self.progress.emit(100)
            self.finished.emit(self._db_path)
        except Exception as exc:
            logger.error("KIMA index build failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Stage 3: RDF Build ───────────────────────────────────────────────


class RdfBuildWorker(StageWorker):
    """Build RDF graph from MARC records and serialize to Turtle."""

    def __init__(self, input_path: Path, output_dir: Path, rdf_format: str = "Turtle") -> None:
        super().__init__()
        self._input_path = input_path
        self._output_dir = output_dir
        self._rdf_format = rdf_format

    def run(self) -> None:
        try:
            from converter.transformer.mapper import MarcToRdfMapper

            self.log_line.emit("Building RDF graph")
            self.substep.emit(f"Loading {self._input_path.name}")

            mapper = MarcToRdfMapper()

            # Throttle substep emissions to once per ~1% of records.
            substep_state = {"every": 1, "last_total": 0}

            def _progress_cb(i: int, total: int, cn: str) -> None:
                if total > 0 and total != substep_state["last_total"]:
                    substep_state["every"] = max(1, total // 100)
                    substep_state["last_total"] = total
                every = substep_state["every"]
                if i % every == 0 or (total > 0 and i == total):
                    if total > 0:
                        self.substep.emit(f"Mapping record {i}/{total} to RDF")
                    else:
                        self.substep.emit(f"Mapping record {i} to RDF")

            graph = mapper.map_file(self._input_path, progress_cb=_progress_cb)

            fmt_map = {"Turtle": "turtle", "JSON-LD": "json-ld", "N-Triples": "nt"}
            fmt = fmt_map.get(self._rdf_format, "turtle")
            ext_map = {"turtle": ".ttl", "json-ld": ".jsonld", "nt": ".nt"}
            ext = ext_map.get(fmt, ".ttl")

            self._output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._output_dir / f"output{ext}"
            self.substep.emit(f"Serializing RDF {self._rdf_format}")
            graph.serialize(destination=str(output_path), format=fmt)

            self.substep.emit(f"Writing {output_path.name}")
            self.log_line.emit(f"RDF graph built — {len(graph)} triples")
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
            self.substep.emit("Loading SHACL shapes")

            validator = ShaclValidator(shapes_path=self._shapes_path)
            self.substep.emit("Running SHACL engine (this may take 10–30 s)")
            result = validator.validate_file(self._ttl_path)

            self._output_dir.mkdir(parents=True, exist_ok=True)
            report_path = self._output_dir / "shacl_report.txt"

            self.substep.emit("Writing shacl_report.txt")
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
    """Build Wikidata items from authority-enriched JSON and upload.

    Two-phase pipeline:
    1. Build WikidataItem objects from authority_enriched.json
    2. Upload to Wikidata (live, with OAuth 2.0 / bot password) or
       export QuickStatements (dry run)

    SPARQL reconciliation is skipped; items with existing_qid from
    authority matching (VIAF/NLI IDs) are updated, others are created.
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
        # SAFETY: Force dry_run in CI environments to prevent accidental uploads
        if os.environ.get("CI") and not self._dry_run:
            logger.warning("SAFETY: Forcing dry_run=True in CI environment")
            self._dry_run = True

        try:
            from converter.wikidata.item_builder import WikidataItemBuilder  # noqa: PLC0415
            from converter.wikidata.quickstatements import QuickStatementsExporter  # noqa: PLC0415

            records: list[dict[str, Any]] = json.loads(
                self._input_path.read_text(encoding="utf-8"),
            )
            total = len(records)
            if total == 0:
                self.error.emit("No records to process")
                return

            # Phase 1: Build Wikidata items
            self.log_line.emit(f"Phase 1/2: Building items from {total} records...")
            builder = WikidataItemBuilder()
            build_substep_every = max(1, total // 100)

            def _build_progress(i: int, t: int) -> None:
                self.progress.emit(int(i / t * 40))
                if i % build_substep_every == 0 or i == t:
                    self.substep.emit(f"Building items for record {i}/{t}")

            items = builder.build_all(
                records,
                progress_cb=_build_progress,
            )
            self.log_line.emit(
                f"Built {len(items)} items ({builder.person_count} persons, "
                f"{len(items) - builder.person_count} manuscripts)"
            )

            # Phase 1.5: Reconciliation — find existing Wikidata items to avoid duplicates
            self.log_line.emit("Reconciling against Wikidata...")
            self.substep.emit("Reconciling against Wikidata (local cache)")

            # Step A: Load prior upload results (fast, local)
            prev_results_path = self._output_dir / "upload_results.json"
            if prev_results_path.exists():
                try:
                    prev_results = json.loads(prev_results_path.read_text(encoding="utf-8"))
                    prev_qids: dict[str, str] = {}
                    for pr in prev_results:
                        if pr.get("qid") and pr.get("status") in ("success", "exists", "updated"):
                            prev_qids[pr["local_id"]] = pr["qid"]
                    matched = 0
                    for item in items:
                        if item.local_id in prev_qids and not item.existing_qid:
                            item.existing_qid = prev_qids[item.local_id]
                            matched += 1
                    if matched:
                        self.log_line.emit(f"  Local: {matched} items from prior results")
                except Exception:
                    pass

            # Step B: Batch SPARQL for items with NLI IDs not yet reconciled
            items_with_nli = []
            nli_to_item: dict[str, list] = {}
            for item in items:
                if item.existing_qid:
                    continue
                for stmt in item.statements:
                    if stmt.property_id == "P8189":
                        nli_id = str(stmt.value)
                        items_with_nli.append(nli_id)
                        nli_to_item.setdefault(nli_id, []).append(item)
                        break

            if items_with_nli:
                try:
                    from converter.wikidata.reconciler import WikidataReconciler  # noqa: PLC0415

                    reconciler = WikidataReconciler()
                    self.substep.emit(
                        f"Reconciling against Wikidata (SPARQL batch of {len(items_with_nli)} NLI IDs)"
                    )
                    self.log_line.emit(f"  SPARQL: checking {len(items_with_nli)} NLI IDs...")
                    batch_qids = reconciler.reconcile_batch_by_nli_id(items_with_nli)
                    for nli_id, qid in batch_qids.items():
                        for item in nli_to_item.get(nli_id, []):
                            item.existing_qid = qid
                    self.log_line.emit(f"  SPARQL: {len(batch_qids)} existing items found")
                except Exception as exc:
                    self.log_line.emit(f"  SPARQL reconciliation failed: {exc}")

            n_existing = sum(1 for i in items if i.existing_qid)
            n_new = len(items) - n_existing
            self.log_line.emit(
                f"Reconciliation: {n_existing} existing + {n_new} new = {len(items)} total"
            )
            self.progress.emit(45)

            self._output_dir.mkdir(parents=True, exist_ok=True)

            # Emit total count so the panel sets the overall progress bar
            self.entity_status.emit("__total__", "total", str(len(items)), "")

            if self._dry_run:
                self.log_line.emit("Phase 2/2: Exporting QuickStatements (dry run)...")
                self.substep.emit("Exporting QuickStatements")
                exporter = QuickStatementsExporter()
                output_path = self._output_dir / "quickstatements.txt"
                exporter.export_to_file(items, output_path)

                # Also save item summary as JSON
                summary_path = self._output_dir / "wikidata_items.json"
                summary = []
                for item in items:
                    summary.append(
                        {
                            "local_id": item.local_id,
                            "entity_type": item.entity_type,
                            "labels": item.labels,
                            "existing_qid": item.existing_qid,
                            "statements_count": len(item.statements),
                        }
                    )
                self.substep.emit(f"Writing {output_path.name}")
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
                # Live upload directly in QThread
                self.log_line.emit(f"Phase 2/2: Uploading {len(items)} items to Wikidata...")
                self.substep.emit("Initializing Wikidata client")
                from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

                uploader = WikidataUploader(
                    token=self._token,
                    batch_mode=self._batch_mode,
                )

                # Per-item label lookup so the substep label is human-readable.
                item_label_by_id: dict[str, tuple[str, str]] = {
                    str(it.local_id): (
                        str(it.entity_type or "item"),
                        str(
                            (it.labels or {}).get("en")
                            or (it.labels or {}).get("he")
                            or it.local_id
                        ),
                    )
                    for it in items
                }

                def _entity_cb(
                    local_id: str,
                    status: str,
                    qid: str | None,
                    msg: str | None,
                ) -> None:
                    self.entity_status.emit(
                        str(local_id or ""),
                        str(status or ""),
                        str(qid or ""),
                        str(msg or ""),
                    )

                upload_total = len(items)

                def _upload_progress(i: int, t: int, msg: str) -> None:
                    self.progress.emit(40 + int(i / t * 60))
                    # i is 1-indexed when this fires, but defend either way.
                    idx = max(1, min(i, upload_total))
                    label_pair = None
                    if idx - 1 < len(items):
                        it = items[idx - 1]
                        label_pair = item_label_by_id.get(str(it.local_id))
                    if label_pair is None:
                        label_pair = ("item", str(msg or ""))
                    entity_type, label = label_pair
                    self.substep.emit(
                        f"Uploading {entity_type} '{label}' ({idx}/{t})"
                    )

                results = uploader.upload_all(
                    items,
                    progress_cb=_upload_progress,
                    entity_cb=_entity_cb,
                )

                created = sum(1 for r in results if r.status == "success")
                updated = sum(1 for r in results if r.status == "updated")
                unchanged = sum(1 for r in results if r.status == "exists")
                failed = sum(1 for r in results if r.status == "failed")
                created + updated + unchanged

                output_path = self._output_dir / "upload_results.json"
                self.substep.emit("Writing upload_results.json")
                result_data = [
                    {
                        "local_id": r.local_id,
                        "status": r.status,
                        "qid": r.qid,
                        "message": r.message,
                        "added_properties": getattr(r, "added_properties", []),
                    }
                    for r in results
                ]
                output_path.write_text(
                    json.dumps(result_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                self.log_line.emit(
                    f"Upload complete — {created} created, {updated} updated, "
                    f"{unchanged} unchanged, {failed} failed"
                )
                self.progress.emit(100)
                self.finished.emit(output_path)

        except Exception as exc:
            logger.error("Wikidata upload failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
